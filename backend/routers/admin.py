import re
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from backend import storage
from backend.auth import require_admin

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


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
        return RedirectResponse(url="/admin/orders", status_code=302)
    return RedirectResponse(url="/admin/login", status_code=302)


@router.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if storage.verify_password(username, password):
        request.session["admin_user"] = username
        return RedirectResponse(url="/admin/orders", status_code=302)
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": "Invalid username or password."},
        status_code=401,
    )


@router.post("/logout")
async def logout(request: Request):
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
            "customer_name": o["customer_name"],
            "flavor_name": flavors.get(o["flavor_id"], {}).get("name", o["flavor_id"]),
            "status": o["status"],
            "created_at": o.get("created_at", "")[:16].replace("T", " "),
        }
        for o in orders
    ]
    return templates.TemplateResponse(
        "admin_orders.html",
        {"request": request, "orders": annotated, "flash": _pop_flashes(request)},
    )


@router.post("/orders/{order_id}/status")
async def update_order_status(
    order_id: str,
    request: Request,
    status: str = Form(...),
    _: str = Depends(require_admin),
):
    if status not in ("pending", "ready", "collected"):
        _flash(request, "error", "Invalid status value.")
    else:
        storage.update_order_status(order_id, status)
        labels = {"pending": "Pending", "ready": "Ready for pickup", "collected": "Collected"}
        _flash(request, "success", f"Order marked as '{labels[status]}'.")
    return RedirectResponse(url="/admin/orders", status_code=302)


@router.post("/reset")
async def reset_orders(request: Request, _: str = Depends(require_admin)):
    count = storage.clear_all_orders()
    _flash(request, "success", f"Cleared {count} order(s). Store is ready for the next round!")
    return RedirectResponse(url="/admin/orders", status_code=302)


# ── Flavors ────────────────────────────────────────────────────────────────────

@router.get("/flavors")
async def flavors_page(request: Request, _: str = Depends(require_admin)):
    return templates.TemplateResponse(
        "admin_flavors.html",
        {"request": request, "flavors": storage.list_flavors(), "flash": _pop_flashes(request)},
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
):
    flavor_id = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50]
    if not flavor_id:
        _flash(request, "error", "Could not generate a valid ID from that name.")
    elif storage.get_flavor(flavor_id):
        _flash(request, "error", f"A flavour with id '{flavor_id}' already exists.")
    else:
        storage.create_flavor(flavor_id, name, description, price, max(0, stock), image_url)
        _flash(request, "success", f"Flavour '{name}' added!")
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
        _flash(request, "success", "Flavour updated.")
    return RedirectResponse(url="/admin/flavors", status_code=302)


@router.post("/flavors/{flavor_id}/delete")
async def delete_flavor(
    flavor_id: str, request: Request, _: str = Depends(require_admin)
):
    storage.delete_flavor(flavor_id)
    _flash(request, "success", "Flavour deleted.")
    return RedirectResponse(url="/admin/flavors", status_code=302)


# ── Users ──────────────────────────────────────────────────────────────────────

@router.get("/users")
async def users_page(request: Request, current_user: str = Depends(require_admin)):
    return templates.TemplateResponse(
        "admin_users.html",
        {
            "request": request,
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
):
    if not re.match(r"^[a-zA-Z0-9_\-]{1,50}$", username):
        _flash(request, "error", "Username: 1–50 chars, letters/numbers/underscores/hyphens only.")
    elif storage.get_user(username):
        _flash(request, "error", f"User '{username}' already exists.")
    else:
        storage.create_user(username, password)
        _flash(request, "success", f"User '{username}' created.")
    return RedirectResponse(url="/admin/users", status_code=302)


@router.post("/users/{username}/delete")
async def delete_user(
    username: str,
    request: Request,
    current_user: str = Depends(require_admin),
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
):
    if not storage.get_user(username):
        _flash(request, "error", f"User '{username}' not found.")
    elif len(password) < 4:
        _flash(request, "error", "Password must be at least 4 characters.")
    else:
        storage.update_user_password(username, password)
        _flash(request, "success", f"Password for '{username}' updated.")
    return RedirectResponse(url="/admin/users", status_code=302)
