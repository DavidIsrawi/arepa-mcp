from pathlib import Path
from datetime import datetime, timedelta, timezone
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from backend import ordering, storage, voucher
from backend.auth import require_admin
from backend.branding import workshop_context
from backend.ordering import OrderRequest, RegisterRequest

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["workshop"] = workshop_context

# Dashboard tunables
_DASH_LANE_CAP = 39          # max tiles rendered per lane
_DASH_TEST_WINDOW_MIN = 5    # how long test orders linger on screen
_DASH_PICKED_LINGER_MIN = 5  # how long collected orders linger on the ready lane
_DASH_CACHE_TTL_S = 1.0      # server-side cache for /dashboard/data
_dash_cache: dict = {"at": 0.0, "data": None}


@router.get("/")
async def root():
    return RedirectResponse(url="/dashboard")


@router.post("/customers", status_code=201)
def register_customer(req: RegisterRequest):
    """Register a customer and receive a unique customer_id."""
    return ordering.register_customer(req)


@router.get("/arepas")
def get_arepas():
    """List arepa flavors with qualitative stock status."""
    return ordering.list_arepas(include_sold_out=True)


@router.post("/orders", status_code=201)
def place_order(order: OrderRequest):
    """Place an order (test or real). Real requires voucher + 1-arepa cap."""
    try:
        return ordering.place_order(order)
    except ordering.OrderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("/orders", dependencies=[Depends(require_admin)])
async def get_orders():
    """List all orders for authenticated administrators."""
    orders = storage.list_orders()
    flavors = {f["RowKey"]: f for f in storage.list_flavors()}
    return [
        {
            "id": o["RowKey"],
            "customer_id": o.get("customer_id", ""),
            "customer_name": o.get("customer_name", ""),
            "flavor_id": o["flavor_id"],
            "flavor_name": flavors.get(o["flavor_id"], {}).get("name", o["flavor_id"]),
            "status": o["status"],
            "kind": o.get("kind", "real"),
            "created_at": o.get("created_at", ""),
            "ready_at": o.get("ready_at", ""),
            "status_changed_at": o.get("status_changed_at", ""),
        }
        for o in orders
    ]


@router.get("/dashboard")
async def dashboard(request: Request):
    """Projector-friendly dashboard: live order queue + current voucher code."""
    data = _dashboard_data()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        context={
            "pending": data["pending"],
            "ready": data["ready"],
            "tests": data["tests"],
            "totals": data["totals"],
            "voucher": data["voucher"],
            "voucher_seconds": data["voucher_seconds"],
            "voucher_window": data["voucher_window"],
        },
    )


@router.get("/dashboard/data")
async def dashboard_data():
    """JSON payload used by the dashboard's in-page poller."""
    return _dashboard_data()


def _short_name(full: str) -> str:
    """'Henk Boelman' -> 'Henk B.'  ·  long firsts truncated to 8 chars + '.'"""
    parts = (full or "").strip().split()
    if not parts:
        return ""
    first = parts[0]
    if len(first) > 8:
        first = first[:8] + "."
    if len(parts) == 1:
        return first
    return f"{first} {parts[-1][0].upper()}."


def _dashboard_data() -> dict:
    # Coalesce concurrent viewers behind a tiny TTL cache so the table
    # store doesn't get hammered when multiple browsers poll in lockstep.
    now = time.monotonic()
    if _dash_cache["data"] is not None and (now - _dash_cache["at"]) < _DASH_CACHE_TTL_S:
        return _dash_cache["data"]

    orders = storage.list_orders()
    flavors = {f["RowKey"]: f for f in storage.list_flavors()}

    def _annotate(o):
        return {
            "id": o["RowKey"],
            "customer_name": _short_name(o.get("customer_name", "")),
            "flavor_name": flavors.get(o["flavor_id"], {}).get("name", o["flavor_id"]),
            "kind": o.get("kind", "real"),
            "status": o.get("status", ""),
            "created_at": o.get("created_at", ""),
            "ready_at": o.get("ready_at", ""),
            "picked_up": o.get("status") == "collected",
        }

    real_orders = [o for o in orders if o.get("kind", "real") == "real"]

    # Pending: newest order first (list_orders is already created_at desc).
    pending_raw = [o for o in real_orders if o.get("status") == "pending"]
    pending = [_annotate(o) for o in pending_raw[:_DASH_LANE_CAP]]

    # Ready lane: includes 'ready' orders plus recently-collected ones so
    # picked-up arepas stay visible (with a strike-through) for a few
    # minutes. Sort strictly by ready_at (fall back to created_at for legacy
    # rows) so picking an order up does NOT reshuffle its position.
    picked_cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=_DASH_PICKED_LINGER_MIN)
    ).isoformat()

    def _show_in_ready(o) -> bool:
        st = o.get("status")
        if st == "ready":
            return True
        if st == "collected":
            # status_changed_at is set every transition; falls back gracefully.
            return o.get("status_changed_at", "") >= picked_cutoff
        return False

    ready_raw = sorted(
        (o for o in real_orders if _show_in_ready(o)),
        key=lambda o: o.get("ready_at") or o.get("created_at", ""),
        reverse=True,
    )
    ready = [_annotate(o) for o in ready_raw[:_DASH_LANE_CAP]]

    # Tests: newest created_at first; only show last N minutes.
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=_DASH_TEST_WINDOW_MIN)).isoformat()
    test_orders = sorted(
        (o for o in orders
         if o.get("kind") == "test" and o.get("created_at", "") >= cutoff),
        key=lambda o: o.get("created_at", ""),
        reverse=True,
    )
    tests = [_annotate(o) for o in test_orders[:_DASH_LANE_CAP]]

    ready_total = sum(1 for o in real_orders if o.get("status") == "ready")
    picked_total = sum(1 for o in real_orders if o.get("status") == "collected")

    payload = {
        "pending": pending,
        "ready": ready,
        "tests": tests,
        "totals": {
            "pending": len(pending_raw),
            "ready": ready_total,
            "picked": picked_total,
            "tests": len(test_orders),
        },
        "voucher": voucher.current_voucher(),
        "voucher_seconds": voucher.seconds_until_rotation(),
        "voucher_window": voucher.WINDOW_SECONDS,
    }
    _dash_cache["data"] = payload
    _dash_cache["at"] = now
    return payload
