import re

from fastmcp import FastMCP

from backend import storage

mcp = FastMCP(
    "Cupcake Store 🧁",
    instructions=(
        "You help users order cupcakes for pickup. "
        "Use list_cupcakes to see what's available, then order_cupcake to place an order. "
        "Each person can only have ONE active order at a time. "
        "Orders are for pickup only — listen for your name to be called."
    ),
)

_NAME_RE = re.compile(r"^[a-zA-Z0-9 \-]{1,50}$")


@mcp.tool()
def list_cupcakes() -> list[dict]:
    """List all cupcake flavors that are currently in stock."""
    flavors = storage.list_flavors()
    return [
        {
            "id": f["RowKey"],
            "name": f["name"],
            "description": f.get("description", ""),
            "price": f.get("price", 0.0),
            "stock": int(f.get("stock", 0)),
        }
        for f in flavors
        if int(f.get("stock", 0)) > 0
    ]


@mcp.tool()
def order_cupcake(flavor_id: str, customer_name: str) -> dict:
    """
    Order one cupcake for pickup. Listen for your name to be called at the counter.

    Args:
        flavor_id: The ID of the flavour (from list_cupcakes).
        customer_name: Your first name for the pickup call-out (e.g. "Alice").
                       Letters, numbers, spaces and hyphens only, max 50 chars.
    """
    if not _NAME_RE.match(customer_name):
        return {
            "success": False,
            "message": "Name must be 1–50 characters: letters, numbers, spaces or hyphens only.",
        }

    # One active order per name
    existing = storage.get_active_order_for_name(customer_name)
    if existing:
        return {
            "success": False,
            "message": (
                f"There is already an active order for '{customer_name}' "
                f"(status: {existing['status']}). Please pick it up first!"
            ),
        }

    # Validate flavor
    flavor = storage.get_flavor(flavor_id)
    if not flavor:
        available = [f["RowKey"] for f in storage.list_flavors()]
        return {
            "success": False,
            "message": f"Flavour '{flavor_id}' not found. Available: {available}",
        }

    # Check stock, then decrement
    if int(flavor.get("stock", 0)) <= 0:
        return {"success": False, "message": f"Sorry, {flavor['name']} is sold out!"}

    if not storage.decrement_stock(flavor_id):
        return {"success": False, "message": f"Sorry, {flavor['name']} just sold out!"}

    order_id = storage.create_order(flavor_id, customer_name)
    return {
        "success": True,
        "order_id": order_id,
        "message": (
            f"Order placed! Your {flavor['name']} is being prepared. "
            f"Listen for '{customer_name}' to be called at the counter 🧁"
        ),
    }


@mcp.tool()
def check_order_status(customer_name: str) -> dict:
    """
    Check the current order status for a given pickup name.

    Args:
        customer_name: The name used when placing the order.
    """
    order = storage.get_active_order_for_name(customer_name)
    if not order:
        return {"found": False, "message": f"No active order found for '{customer_name}'."}

    flavor = storage.get_flavor(order["flavor_id"])
    flavor_name = flavor["name"] if flavor else order["flavor_id"]

    status_messages = {
        "pending": f"Your {flavor_name} is being prepared — we'll call '{customer_name}' when it's ready.",
        "ready": f"🎉 Your {flavor_name} is READY! Come pick it up, '{customer_name}'!",
        "collected": f"Order collected. Enjoy your {flavor_name}! 🧁",
    }

    return {
        "found": True,
        "order_id": order["RowKey"],
        "flavor": flavor_name,
        "status": order["status"],
        "message": status_messages.get(order["status"], order["status"]),
    }
