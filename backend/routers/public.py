from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from backend import rate_limit, storage, voucher

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


class RegisterRequest(BaseModel):
    display_name: str = Field(
        ...,
        min_length=1,
        max_length=50,
        pattern=r"^[a-zA-Z0-9 \-]+$",
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
    customer_id = storage.create_customer(req.display_name)
    return {"customer_id": customer_id, "display_name": req.display_name}


@router.get("/cupcakes")
async def get_cupcakes():
    """List cupcake flavors with qualitative stock status."""
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
    """Place an order (test or real). Real requires voucher + 1-cupcake cap."""
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
            detail="You have already had your one real cupcake at this workshop.",
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


@router.get("/orders")
async def get_orders():
    """List all orders (used by the dashboard and admin UIs)."""
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
        }
        for o in orders
    ]


@router.get("/dashboard")
async def dashboard(request: Request):
    """Beamer-optimised dashboard: live order queue + current voucher code."""
    orders = storage.list_orders()
    flavors = {f["RowKey"]: f for f in storage.list_flavors()}

    def _annotate(o):
        return {
            "id": o["RowKey"],
            "customer_name": o.get("customer_name", ""),
            "flavor_name": flavors.get(o["flavor_id"], {}).get("name", o["flavor_id"]),
            "kind": o.get("kind", "real"),
        }

    real_orders = [o for o in orders if o.get("kind", "real") == "real"]
    pending = [_annotate(o) for o in real_orders if o.get("status") == "pending"]
    ready = [_annotate(o) for o in real_orders if o.get("status") == "ready"]

    # Most recent 8 test orders, newest first
    test_orders = [o for o in orders if o.get("kind") == "test"]
    tests = [_annotate(o) for o in test_orders[-8:]][::-1]

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        context={
            "pending": pending,
            "ready": ready,
            "tests": tests,
            "voucher": voucher.current_voucher(),
            "voucher_seconds": voucher.seconds_until_rotation(),
            "voucher_window": voucher.WINDOW_SECONDS,
        },
    )
