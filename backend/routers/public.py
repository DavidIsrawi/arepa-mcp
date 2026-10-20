from pathlib import Path
from typing import Literal
from datetime import datetime, timedelta, timezone
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from backend import rate_limit, storage, voucher
from backend.auth import require_admin
from backend.branding import workshop_context

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["workshop"] = workshop_context

# Dashboard tunables
_DASH_LANE_CAP = 39          # max tiles rendered per lane
_DASH_TEST_WINDOW_MIN = 5    # how long test orders linger on screen
_DASH_PICKED_LINGER_MIN = 5  # how long collected orders linger on the ready lane
_DASH_CACHE_TTL_S = 1.0      # server-side cache for /dashboard/data
_dash_cache: dict = {"at": 0.0, "data": None}


class RegisterRequest(BaseModel):
    first_name: str = Field(
        ..., min_length=1, max_length=50, pattern=r"^[a-zA-Z0-9 \-]+$",
    )
    last_name: str = Field(
        ..., min_length=1, max_length=50, pattern=r"^[a-zA-Z0-9 \-]+$",
    )
    city: str = Field(
        ..., min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9 \-',.]+$",
    )


class OrderRequest(BaseModel):
    flavor_id: str = Field(..., min_length=1, max_length=100)
    customer_id: str = Field(
        ...,
        min_length=8,
        max_length=8,
        pattern=r"^[2-9A-HJKMNP-Z]{8}$",
    )
    kind: Literal["test", "real"]
    voucher_code: str = Field(..., min_length=4, max_length=12)


@router.get("/")
async def root():
    return RedirectResponse(url="/dashboard")


@router.post("/customers", status_code=201)
async def register_customer(req: RegisterRequest):
    """Register a customer and receive a unique customer_id."""
    customer_id = storage.create_customer(req.first_name, req.last_name, req.city)
    display_name = f"{req.first_name} {req.last_name}"
    return {
        "customer_id": customer_id,
        "display_name": display_name,
        "first_name": req.first_name,
        "last_name": req.last_name,
        "city": req.city,
    }


@router.get("/arepas")
async def get_arepas():
    """List arepa flavors with qualitative stock status."""
    out = []
    for f in storage.list_flavors():
        stock = int(f.get("stock", 0))
        status, label = storage.stock_label(stock)
        out.append(
            {
                "id": f["RowKey"],
                "name": f["name"],
                "description": f.get("description", ""),
                "price": float(f.get("price", 0.0)),
                "stock_status": status,
                "stock_label": label,
                "image_url": f.get("image_url", ""),
            }
        )
    return out


@router.post("/orders", status_code=201)
async def place_order(order: OrderRequest):
    """Place an order (test or real). Real requires voucher + 1-arepa cap."""
    customer_id = order.customer_id.strip().upper()

    customer = storage.get_customer(customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Unknown customer_id.")

    if rate_limit.is_locked_out(customer_id):
        raise HTTPException(
            status_code=429,
            detail="Too many wrong voucher attempts. Please wait a minute.",
        )

    if not voucher.is_valid_voucher(order.voucher_code):
        rate_limit.record_failure(customer_id)
        raise HTTPException(status_code=403, detail="Voucher code is wrong or expired.")

    if order.kind == "real" and int(customer.get("real_orders_count", 0)) >= 1:
        raise HTTPException(
            status_code=409,
            detail="You have already had your one real arepa at this workshop.",
        )

    if storage.get_active_order_for_customer(customer_id, order.kind):
        raise HTTPException(
            status_code=409,
            detail=f"You already have an active {order.kind} order. Pick it up first.",
        )

    flavor = storage.get_flavor(order.flavor_id)
    if not flavor:
        raise HTTPException(status_code=404, detail=f"Flavour '{order.flavor_id}' not found.")

    if order.kind == "real":
        if int(flavor.get("stock", 0)) <= 0 or not storage.decrement_stock(order.flavor_id):
            raise HTTPException(status_code=409, detail=f"{flavor['name']} just sold out!")

    order_id = storage.create_order(
        order.flavor_id, customer_id, customer["display_name"], order.kind
    )
    if order.kind == "real":
        storage.increment_real_orders(customer_id)
    else:
        storage.update_order_status(order_id, "collected")

    rate_limit.clear(customer_id)

    return {
        "order_id": order_id,
        "customer_id": customer_id,
        "customer_name": customer["display_name"],
        "flavor": flavor["name"],
        "kind": order.kind,
        "status": "collected" if order.kind == "test" else "pending",
    }


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
