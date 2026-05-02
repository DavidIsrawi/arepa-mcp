import functools
import logging
import re
from typing import Literal

from fastmcp import FastMCP

from backend import rate_limit, storage, voucher

logger = logging.getLogger("mcp.tools")


def _log_call(func):
    """Decorator: log incoming params (with secrets masked) and the returned result."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        safe_kwargs = {
            k: ("***" if k in ("voucher_code",) else v) for k, v in kwargs.items()
        }
        logger.info("→ %s %s", func.__name__, safe_kwargs or args)
        result = func(*args, **kwargs)
        logger.info("← %s %s", func.__name__, result)
        return result

    return wrapper


mcp = FastMCP(
    "Cupcake Store 🧁",
    instructions=(
        "You help users order cupcakes for pickup at a workshop.\n\n"
        "FLOW:\n"
        "1. REGISTER ONCE: ask the user for their name, then call "
        "register_customer(display_name). REMEMBER the returned customer_id for the "
        "rest of the conversation — do NOT call register_customer again.\n"
        "2. BROWSE: list_cupcakes shows flavors and qualitative stock "
        "('plenty', 'limited', 'almost_finished'). No customer_id needed for browsing.\n"
        "3. ORDER: before calling order_cupcake, ASK THE USER:\n"
        "   (a) which flavor,\n"
        "   (b) is this a TEST order (just practising) or a REAL order "
        "(you actually want a cupcake), and\n"
        "   (c) read the current 6-character voucher code shown on the dashboard "
        "screen at the front of the room.\n"
        "   Then call order_cupcake(flavor_id, customer_id, kind, voucher_code).\n"
        "4. STATUS: check_order_status(customer_id) for the latest real order.\n\n"
        "Each customer gets ONE real cupcake total. Test orders are unlimited but still "
        "require the current voucher code."
    ),
)

_NAME_RE = re.compile(r"^[a-zA-Z0-9 \-]{1,50}$")
_CID_RE = re.compile(r"^[2-9A-HJKMNP-Z]{8}$")


@mcp.tool()
@_log_call
def register_customer(display_name: str) -> dict:
    """
    Register the user once at the start of the conversation. Returns a customer_id
    you MUST save and reuse for every order in this conversation.
    Do not call this tool more than once.

    Args:
        display_name: The user's name for the pickup call-out (1–50 chars,
                      letters / numbers / spaces / hyphens).
    """
    if not _NAME_RE.match(display_name):
        return {
            "success": False,
            "message": "Name must be 1–50 chars: letters, numbers, spaces or hyphens only.",
        }
    customer_id = storage.create_customer(display_name)
    return {
        "success": True,
        "customer_id": customer_id,
        "display_name": display_name,
        "message": (
            f"Welcome, {display_name}! SAVE THIS customer_id and use it for every "
            f"order in this conversation: {customer_id}. "
            f"Do NOT call register_customer again."
        ),
    }


@mcp.tool()
@_log_call
def list_cupcakes() -> list[dict]:
    """List all cupcake flavors currently in stock with qualitative availability."""
    out = []
    for f in storage.list_flavors():
        stock = int(f.get("stock", 0))
        if stock <= 0:
            continue
        status, label = storage.stock_label(stock)
        out.append(
            {
                "id": f["RowKey"],
                "name": f["name"],
                "description": f.get("description", ""),
                "price": f.get("price", 0.0),
                "stock_status": status,
                "stock_label": label,
            }
        )
    return out


@mcp.tool()
@_log_call
def order_cupcake(
    flavor_id: str,
    customer_id: str,
    kind: Literal["test", "real"],
    voucher_code: str,
) -> dict:
    """
    Place an order. ASK THE USER WHETHER THIS IS A TEST OR A REAL ORDER before calling.

    Args:
        flavor_id: ID of the flavor (from list_cupcakes).
        customer_id: The customer_id returned by register_customer.
        kind: "real" for an actual cupcake (counts toward the 1-cupcake lifetime
              limit, decrements stock); "test" for practising the flow (no stock
              impact, auto-collected, but voucher still required).
        voucher_code: The current 6-character code shown on the dashboard screen.
    """
    customer_id = (customer_id or "").strip().upper()
    if not _CID_RE.match(customer_id):
        return {
            "success": False,
            "message": "Invalid customer_id format. Call register_customer first.",
        }

    customer = storage.get_customer(customer_id)
    if not customer:
        return {
            "success": False,
            "message": "Unknown customer_id. Call register_customer first.",
        }

    if kind not in ("test", "real"):
        return {"success": False, "message": "kind must be 'test' or 'real'."}

    # Rate-limit voucher attempts per customer
    if rate_limit.is_locked_out(customer_id):
        return {
            "success": False,
            "message": "Too many wrong voucher attempts. Please wait about a minute and try again.",
        }

    if not voucher.is_valid_voucher(voucher_code):
        rate_limit.record_failure(customer_id)
        return {
            "success": False,
            "message": (
                "Voucher code is wrong or expired. Please read the current "
                "6-character code from the dashboard screen and try again."
            ),
        }

    # Lifetime cap: real orders only
    if kind == "real" and int(customer.get("real_orders_count", 0)) >= 1:
        return {
            "success": False,
            "message": "You have already had your one real cupcake at this workshop. Hope it was tasty!",
        }

    # One active order per kind
    if storage.get_active_order_for_customer(customer_id, kind):
        return {
            "success": False,
            "message": f"You already have an active {kind} order. Please pick it up first.",
        }

    flavor = storage.get_flavor(flavor_id)
    if not flavor:
        available = [f["RowKey"] for f in storage.list_flavors()]
        return {
            "success": False,
            "message": f"Flavour '{flavor_id}' not found. Available: {available}",
        }

    if kind == "real":
        if int(flavor.get("stock", 0)) <= 0 or not storage.decrement_stock(flavor_id):
            return {
                "success": False,
                "message": f"Sorry, {flavor['name']} just sold out!",
            }

    order_id = storage.create_order(
        flavor_id, customer_id, customer["display_name"], kind
    )
    if kind == "real":
        storage.increment_real_orders(customer_id)
    else:
        # Test orders auto-collect so they don't sit in the prep queue.
        storage.update_order_status(order_id, "collected")

    rate_limit.clear(customer_id)

    if kind == "real":
        message = (
            f"REAL order placed! Listen for '{customer['display_name']}' to be called "
            f"at the counter to pick up your {flavor['name']} 🧁"
        )
    else:
        message = (
            f"TEST order recorded for {flavor['name']}. No actual cupcake will be made — "
            f"this was just to practise the flow."
        )

    return {
        "success": True,
        "order_id": order_id,
        "kind": kind,
        "flavor": flavor["name"],
        "message": message,
    }


@mcp.tool()
@_log_call
def check_order_status(customer_id: str) -> dict:
    """
    Check the latest active REAL order for a customer_id.

    Args:
        customer_id: The customer_id returned by register_customer.
    """
    customer_id = (customer_id or "").strip().upper()
    if not _CID_RE.match(customer_id):
        return {"found": False, "message": "Invalid customer_id format."}

    order = storage.get_active_order_for_customer(customer_id, "real")
    if not order:
        return {
            "found": False,
            "message": f"No active real order found for '{customer_id}'.",
        }

    flavor = storage.get_flavor(order["flavor_id"])
    flavor_name = flavor["name"] if flavor else order["flavor_id"]
    name = order.get("customer_name", "")

    status_messages = {
        "pending": f"Your {flavor_name} is being prepared — listen for '{name}'.",
        "ready": f"🎉 Your {flavor_name} is READY! Come pick it up, '{name}'!",
    }

    return {
        "found": True,
        "order_id": order["RowKey"],
        "flavor": flavor_name,
        "status": order["status"],
        "message": status_messages.get(order["status"], order["status"]),
    }
import functools
import logging
import re

from fastmcp import FastMCP

from backend import storage

logger = logging.getLogger("mcp.tools")


def _log_call(func):
    """Decorator: log the incoming parameters and the returned result for each tool call."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        logger.info("→ %s %s", func.__name__, kwargs or args)
        result = func(*args, **kwargs)
        logger.info("← %s %s", func.__name__, result)
        return result

    return wrapper


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
@_log_call
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
@_log_call
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
@_log_call
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
