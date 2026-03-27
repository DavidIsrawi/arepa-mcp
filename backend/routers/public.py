from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from backend import storage

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


class OrderRequest(BaseModel):
    flavor_id: str = Field(..., min_length=1, max_length=100)
    customer_name: str = Field(
        ...,
        min_length=1,
        max_length=50,
        pattern=r"^[a-zA-Z0-9 \-]+$",
        description="Pickup name — letters, numbers, spaces and hyphens only.",
    )


@router.get("/")
async def root():
    return RedirectResponse(url="/dashboard")


@router.get("/cupcakes")
async def get_cupcakes():
    """List all cupcake flavors with current stock."""
    flavors = storage.list_flavors()
    return [
        {
            "id": f["RowKey"],
            "name": f["name"],
            "description": f.get("description", ""),
            "price": float(f.get("price", 0.0)),
            "stock": int(f.get("stock", 0)),
            "image_url": f.get("image_url", ""),
        }
        for f in flavors
    ]


@router.post("/orders", status_code=201)
async def place_order(order: OrderRequest):
    """Place a single cupcake order for pickup."""
    # Guard: one active order per name
    existing = storage.get_active_order_for_name(order.customer_name)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{order.customer_name}' already has an active order "
                f"(status: {existing['status']}). Pick it up before ordering again!"
            ),
        )

    # Guard: flavor must exist
    flavor = storage.get_flavor(order.flavor_id)
    if not flavor:
        raise HTTPException(status_code=404, detail=f"Flavour '{order.flavor_id}' not found.")

    # Guard: must be in stock
    if int(flavor.get("stock", 0)) <= 0:
        raise HTTPException(status_code=409, detail=f"{flavor['name']} is sold out!")

    if not storage.decrement_stock(order.flavor_id):
        raise HTTPException(status_code=409, detail=f"{flavor['name']} just sold out!")

    order_id = storage.create_order(order.flavor_id, order.customer_name)
    return {
        "order_id": order_id,
        "customer_name": order.customer_name,
        "flavor": flavor["name"],
        "status": "pending",
        "message": f"Order placed! Listen for '{order.customer_name}' to be called.",
    }


@router.get("/orders")
async def get_orders():
    """List all orders (used by the dashboard and admin UIs)."""
    orders = storage.list_orders()
    flavors = {f["RowKey"]: f for f in storage.list_flavors()}
    return [
        {
            "id": o["RowKey"],
            "customer_name": o["customer_name"],
            "flavor_id": o["flavor_id"],
            "flavor_name": flavors.get(o["flavor_id"], {}).get("name", o["flavor_id"]),
            "status": o["status"],
            "created_at": o.get("created_at", ""),
        }
        for o in orders
    ]


@router.get("/dashboard")
async def dashboard(request: Request):
    """Beamer-optimised dashboard showing the live order queue."""
    orders = storage.list_orders()
    flavors = {f["RowKey"]: f for f in storage.list_flavors()}

    def _annotate(o):
        return {
            "id": o["RowKey"],
            "customer_name": o["customer_name"],
            "flavor_name": flavors.get(o["flavor_id"], {}).get("name", o["flavor_id"]),
        }

    pending = [_annotate(o) for o in orders if o.get("status") == "pending"]
    ready = [_annotate(o) for o in orders if o.get("status") == "ready"]

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        context={"pending": pending, "ready": ready},
    )
