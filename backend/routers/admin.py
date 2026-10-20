import re
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from backend import rate_limit, storage
from backend.auth import require_admin
from backend.branding import workshop_context
from backend.csrf import csrf_token, require_csrf

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
# Expose csrf_token() to every template.
templates.env.globals["csrf_token"] = csrf_token
templates.env.globals["workshop"] = workshop_context

# ── Order status constants ────────────────────────────────────────────────────
STATUS_PENDING = "pending"
STATUS_READY = "ready"
STATUS_COLLECTED = "collected"
STATUS_CANCELLED = "cancelled"
ALL_STATUSES = (STATUS_PENDING, STATUS_READY, STATUS_COLLECTED, STATUS_CANCELLED)

# Legal forward transitions. "cancelled" is allowed from any non-terminal state.
_ALLOWED_TRANSITIONS = {
    STATUS_PENDING: {STATUS_READY, STATUS_CANCELLED},
    STATUS_READY: {STATUS_COLLECTED, STATUS_CANCELLED},
    STATUS_COLLECTED: set(),
    STATUS_CANCELLED: set(),
}

# Login throttling — same sliding window as the public voucher limiter.
_LOGIN_RL_PREFIX = "admin-login:"


# ── Flash helpers ──────────────────────────────────────────────────────────────

def _flash(request: Request, kind: str, message: str) -> None:
    flashes = request.session.get("flashes", [])
    flashes.append({"kind": kind, "message": message})
    request.session["flashes"] = flashes


def _pop_flashes(request: Request) -> list:
    flashes = request.session.get("flashes", [])
    request.session["flashes"] = []
    return flashes


# ── Auth routes (no require_admin dependency) ──────────────────────────────────

@router.get("")
@router.get("/")
async def admin_index(request: Request):
    if request.session.get("admin_user"):
        return RedirectResponse(url="/admin/preparing", status_code=302)
    return RedirectResponse(url="/admin/login", status_code=302)


@router.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", context={"error": None})


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    # Rate-limit by client IP. With a few admins behind one NAT this is
    # generous enough not to lock real users out, but stops automated brute
    # force after the 10th miss inside a minute.
    client_ip = request.client.host if request.client else "unknown"
    rl_key = _LOGIN_RL_PREFIX + client_ip
    if rate_limit.is_locked_out(rl_key):
        return templates.TemplateResponse(
            request,
            "login.html",
            context={"error": "Too many failed attempts. Please wait a minute and try again."},
            status_code=429,
        )
    # Cap password length defensively (bcrypt truncates at 72 anyway).
    if len(password) > 256 or not storage.verify_password(username, password):
        rate_limit.record_failure(rl_key)
        return templates.TemplateResponse(
            request,
            "login.html",
            context={"error": "Invalid username or password."},
            status_code=401,
        )
    rate_limit.clear(rl_key)
    # Rotate session id on login: clear old session contents (incl. CSRF
    # token) so a pre-login token can't be replayed post-auth.
    request.session.clear()
    request.session["admin_user"] = username
    return RedirectResponse(url="/admin/preparing", status_code=302)


@router.post("/logout")
async def logout(request: Request, _csrf: None = Depends(require_csrf)):
    request.session.clear()
    return RedirectResponse(url="/admin/login", status_code=302)


# ── Orders ─────────────────────────────────────────────────────────────────────

@router.get("/orders")
async def orders_page(request: Request, _: str = Depends(require_admin)):
    orders = storage.list_orders()
    flavors = {f["RowKey"]: f for f in storage.list_flavors()}
    annotated = [
        {
            "id": o["RowKey"],
            "customer_name": o.get("customer_name", ""),
            "customer_id": o.get("customer_id", ""),
            "flavor_name": flavors.get(o["flavor_id"], {}).get("name", o["flavor_id"]),
            "status": o["status"],
            "kind": o.get("kind", "real"),
            "created_at": o.get("created_at", "")[:16].replace("T", " "),
        }
        for o in orders
    ]
    return templates.TemplateResponse(
        request,
        "admin_orders.html",
        context={"orders": annotated, "flash": _pop_flashes(request)},
    )


@router.post("/orders/{order_id}/status")
async def update_order_status(
    order_id: str,
    request: Request,
    status: str = Form(...),
    _: str = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    if status not in ALL_STATUSES:
        _flash(request, "error", "Invalid status value.")
    else:
        try:
            _transition_order(
                order_id, status, refund_stock=(status == STATUS_CANCELLED)
            )
        except HTTPException as exc:
            _flash(request, "error", exc.detail)
        else:
            labels = {
                STATUS_PENDING: "Pending",
                STATUS_READY: "Ready for pickup",
                STATUS_COLLECTED: "Collected",
                STATUS_CANCELLED: "Cancelled",
            }
            _flash(request, "success", f"Order marked as '{labels[status]}'.")
    return RedirectResponse(url="/admin/orders", status_code=302)


@router.post("/reset")
async def reset_orders(
    request: Request,
    _: str = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    count = storage.clear_all_orders()
    _flash(request, "success", f"Cleared {count} order(s). Store is ready for the next round!")
    return RedirectResponse(url="/admin/orders", status_code=302)


# ── Preparing / Pickup work queues ────────────────────────────────────────────

def _flavors_full() -> tuple[dict, list]:
    """Single fetch for both name lookup and stock summary."""
    flavors = storage.list_flavors()
    name_map = {f["RowKey"]: f.get("name", f["RowKey"]) for f in flavors}
    summary = sorted(
        [
            {
                "id": f["RowKey"],
                "name": f.get("name", f["RowKey"]),
                "stock": int(f.get("stock", 0)),
            }
            for f in flavors
        ],
        key=lambda x: x["name"].lower(),
    )
    return name_map, summary


def _queue_payload(status: str, name_map: dict) -> list:
    """Real orders in `status`, oldest first; for pickup sort by ready_at."""
    rows = [
        o for o in storage.list_orders_by_status(status, kind="real")
    ]
    if status == STATUS_READY:
        rows.sort(key=lambda o: o.get("ready_at") or o.get("created_at", ""))
    else:
        rows.sort(key=lambda o: o.get("created_at", ""))
    return [
        {
            "id": o["RowKey"],
            "customer_name": o.get("customer_name", ""),
            "customer_id": o.get("customer_id", ""),
            "flavor_id": o.get("flavor_id", ""),
            "flavor_name": name_map.get(o.get("flavor_id", ""), o.get("flavor_id", "")),
            "created_at": o.get("created_at", ""),
            "ready_at": o.get("ready_at", ""),
        }
        for o in rows
    ]


@router.get("/preparing")
async def preparing_page(request: Request, _: str = Depends(require_admin)):
    name_map, stock = _flavors_full()
    return templates.TemplateResponse(
        request,
        "admin_queue.html",
        context={
            "mode": "preparing",
            "orders": _queue_payload(STATUS_PENDING, name_map),
            "stock": stock,
            "flash": _pop_flashes(request),
        },
    )


@router.get("/preparing/data")
async def preparing_data(_: str = Depends(require_admin)):
    name_map, stock = _flavors_full()
    return {"orders": _queue_payload(STATUS_PENDING, name_map), "stock": stock}


@router.get("/pickup")
async def pickup_page(request: Request, _: str = Depends(require_admin)):
    name_map, stock = _flavors_full()
    return templates.TemplateResponse(
        request,
        "admin_queue.html",
        context={
            "mode": "pickup",
            "orders": _queue_payload(STATUS_READY, name_map),
            "stock": stock,
            "flash": _pop_flashes(request),
        },
    )


@router.get("/pickup/data")
async def pickup_data(_: str = Depends(require_admin)):
    name_map, stock = _flavors_full()
    return {"orders": _queue_payload(STATUS_READY, name_map), "stock": stock}


def _transition_order(order_id: str, new_status: str, refund_stock: bool) -> dict:
    """Idempotent, status-aware order transition.

    - Re-applying the same status is a no-op (returns ok).
    - Illegal transitions raise 409.
    - Stock is refunded only on the FIRST transition into 'cancelled' from a
      state that still held inventory ('pending' or 'ready'), preventing
      double-refunds on accidental double-clicks / duplicate POSTs.
    """
    if new_status not in ALL_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")
    order = storage.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    current = order.get("status", "")
    if current == new_status:
        return {"ok": True, "id": order_id, "status": new_status, "noop": True}
    if current in _ALLOWED_TRANSITIONS and new_status not in _ALLOWED_TRANSITIONS[current]:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot move order from '{current}' to '{new_status}'.",
        )
    storage.update_order_status(order_id, new_status)
    should_refund = (
        refund_stock
        and new_status == STATUS_CANCELLED
        and current in (STATUS_PENDING, STATUS_READY)
        and order.get("kind", "real") == "real"
        and order.get("flavor_id")
    )
    if should_refund:
        storage.increment_stock(order["flavor_id"])
    return {"ok": True, "id": order_id, "status": new_status}


@router.post("/orders/{order_id}/ready")
async def action_ready(
    order_id: str,
    _: str = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    return JSONResponse(_transition_order(order_id, STATUS_READY, refund_stock=False))


@router.post("/orders/{order_id}/collected")
async def action_collected(
    order_id: str,
    _: str = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    return JSONResponse(_transition_order(order_id, STATUS_COLLECTED, refund_stock=False))


@router.post("/orders/{order_id}/cancel")
async def action_cancel(
    order_id: str,
    _: str = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    return JSONResponse(_transition_order(order_id, STATUS_CANCELLED, refund_stock=True))


# ── Flavors ────────────────────────────────────────────────────────────────────

@router.get("/flavors")
async def flavors_page(request: Request, _: str = Depends(require_admin)):
    return templates.TemplateResponse(
        request,
        "admin_flavors.html",
        context={"flavors": storage.list_flavors(), "flash": _pop_flashes(request)},
    )


@router.post("/flavors")
async def create_flavor(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    price: float = Form(...),
    stock: int = Form(...),
    image_url: str = Form(""),
    _: str = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    flavor_id = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50]
    if not flavor_id:
        _flash(request, "error", "Could not generate a valid ID from that name.")
    elif storage.get_flavor(flavor_id):
        _flash(request, "error", f"An arepa with id '{flavor_id}' already exists.")
    else:
        storage.create_flavor(flavor_id, name, description, price, max(0, stock), image_url)
        _flash(request, "success", f"Arepa '{name}' added!")
    return RedirectResponse(url="/admin/flavors", status_code=302)


@router.post("/flavors/{flavor_id}/edit")
async def edit_flavor(
    flavor_id: str,
    request: Request,
    name: str = Form(None),
    description: str = Form(None),
    price: str = Form(None),   # accept as string to allow empty
    stock: str = Form(None),
    image_url: str = Form(None),
    _: str = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    fields: dict = {}
    if name is not None and name.strip():
        fields["name"] = name.strip()
    if description is not None:
        fields["description"] = description
    if price is not None and price.strip():
        try:
            fields["price"] = float(price)
        except ValueError:
            _flash(request, "error", "Price must be a number.")
            return RedirectResponse(url="/admin/flavors", status_code=302)
    if stock is not None and stock.strip():
        try:
            fields["stock"] = max(0, int(stock))
        except ValueError:
            _flash(request, "error", "Stock must be a whole number.")
            return RedirectResponse(url="/admin/flavors", status_code=302)
    if image_url is not None:
        fields["image_url"] = image_url

    if fields:
        storage.update_flavor(flavor_id, **fields)
        _flash(request, "success", "Arepa updated.")
    return RedirectResponse(url="/admin/flavors", status_code=302)


@router.post("/flavors/{flavor_id}/delete")
async def delete_flavor(
    flavor_id: str,
    request: Request,
    _: str = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    storage.delete_flavor(flavor_id)
    _flash(request, "success", "Arepa deleted.")
    return RedirectResponse(url="/admin/flavors", status_code=302)


# ── Users ──────────────────────────────────────────────────────────────────────

@router.get("/users")
async def users_page(request: Request, current_user: str = Depends(require_admin)):
    return templates.TemplateResponse(
        request,
        "admin_users.html",
        context={
            "users": storage.list_users(),
            "current_user": current_user,
            "flash": _pop_flashes(request),
        },
    )


@router.post("/users")
async def create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    _: str = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    if not re.match(r"^[a-zA-Z0-9_\-]{1,50}$", username):
        _flash(request, "error", "Username: 1–50 chars, letters/numbers/underscores/hyphens only.")
    elif storage.get_user(username):
        _flash(request, "error", f"User '{username}' already exists.")
    elif len(password) < 8 or len(password) > 256:
        _flash(request, "error", "Password must be 8–256 characters.")
    else:
        storage.create_user(username, password)
        _flash(request, "success", f"User '{username}' created.")
    return RedirectResponse(url="/admin/users", status_code=302)


@router.post("/users/{username}/delete")
async def delete_user(
    username: str,
    request: Request,
    current_user: str = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    if username == current_user:
        _flash(request, "error", "You cannot delete your own account.")
    else:
        storage.delete_user(username)
        _flash(request, "success", f"User '{username}' deleted.")
    return RedirectResponse(url="/admin/users", status_code=302)


@router.post("/users/{username}/password")
async def change_password(
    username: str,
    request: Request,
    password: str = Form(...),
    _: str = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    if not storage.get_user(username):
        _flash(request, "error", f"User '{username}' not found.")
    elif len(password) < 8 or len(password) > 256:
        _flash(request, "error", "Password must be 8–256 characters.")
    else:
        storage.update_user_password(username, password)
        _flash(request, "success", f"Password for '{username}' updated.")
    return RedirectResponse(url="/admin/users", status_code=302)


# ── Customers ─────────────────────────────────────────────────────────────────

@router.get("/customers")
async def customers_page(request: Request, _: str = Depends(require_admin)):
    customers = sorted(
        storage.list_customers(),
        key=lambda c: c.get("created_at", ""),
        reverse=True,
    )
    annotated = [
        {
            "customer_id": c["RowKey"],
            "display_name": c.get("display_name", ""),
            "city": c.get("city", ""),
            "real_orders_count": int(c.get("real_orders_count", 0)),
            "created_at": c.get("created_at", "")[:16].replace("T", " "),
        }
        for c in customers
    ]
    return templates.TemplateResponse(
        request,
        "admin_customers.html",
        context={"customers": annotated, "flash": _pop_flashes(request)},
    )
