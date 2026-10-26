"""Shared customer validation, menu projection and order placement."""

import logging
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from backend import rate_limit, storage, voucher

logger = logging.getLogger(__name__)


class RegisterRequest(BaseModel):
    first_name: str = Field(
        min_length=1, max_length=50, pattern=r"^[\w \-'’.]+$",
    )
    last_name: str = Field(
        min_length=1, max_length=50, pattern=r"^[\w \-'’.]+$",
    )
    city: str = Field(
        min_length=1, max_length=80, pattern=r"^[\w \-'’,.]+$",
    )

    @field_validator("first_name", "last_name", "city", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class OrderRequest(BaseModel):
    flavor_id: str = Field(min_length=1, max_length=100)
    customer_id: str = Field(
        min_length=8, max_length=8, pattern=r"^[2-9A-HJKMNP-Z]{8}$",
    )
    kind: Literal["test", "real"]
    voucher_code: str = Field(min_length=4, max_length=12)

    @field_validator("customer_id", mode="before")
    @classmethod
    def normalize_customer_id(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value


class OrderError(Exception):
    def __init__(self, status_code: int, message: str, code: str, **details) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code
        self.details = details


_FIELD_LABELS = {
    "first_name": "First name",
    "last_name": "Last name",
    "city": "City",
}
_NAME_CHARACTERS = "letters, spaces, hyphens, apostrophes and periods"
_ALLOWED_CHARACTERS = {
    "first_name": _NAME_CHARACTERS,
    "last_name": _NAME_CHARACTERS,
    "city": "letters, spaces, hyphens, apostrophes, periods and commas",
}
CUSTOMER_ID_HELP = (
    "Customer IDs are 8 characters using the digits 2-9 and letters A-Z "
    "without I, L or O. Please re-check it."
)
_FIXED_FIELD_MESSAGES = {
    "customer_id": CUSTOMER_ID_HELP,
    "voucher_code": (
        "Voucher codes are 6 characters. Please re-read the code on the "
        "dashboard screen."
    ),
    "flavor_id": "Please choose an arepa from the current menu.",
    "kind": "The order type must be either test or real.",
}


def _field_message(field: str, error: dict) -> str:
    if field in _FIXED_FIELD_MESSAGES:
        return _FIXED_FIELD_MESSAGES[field]
    label = _FIELD_LABELS.get(field, field.replace("_", " ").capitalize())
    kind = error.get("type", "")
    if kind in ("missing", "string_too_short"):
        return f"Please provide a {label.lower()}."
    if kind == "string_too_long":
        limit = (error.get("ctx") or {}).get("max_length")
        return f"{label} must be {limit} characters or fewer." if limit else f"{label} is too long."
    if kind == "string_pattern_mismatch" and field in _ALLOWED_CHARACTERS:
        return f"{label} can only contain {_ALLOWED_CHARACTERS[field]}."
    return f"{label} is not valid."


def describe_validation_error(exc: ValidationError) -> dict[str, str]:
    """Map each invalid field to one plain-language message, in input order."""
    fields: dict[str, str] = {}
    for error in exc.errors():
        field = ".".join(str(part) for part in error["loc"])
        fields.setdefault(field, _field_message(field, error))
    return fields


def board_name(full: str) -> str:
    """Name as shown on the public dashboard: 'Henk Boelman' -> 'Henk B.'."""
    parts = (full or "").strip().split()
    if not parts:
        return ""
    first = parts[0]
    if len(first) > 8:
        first = first[:8] + "."
    if len(parts) == 1:
        return first
    return f"{first} {parts[-1][0].upper()}."


def register_customer(customer: RegisterRequest) -> dict:
    customer_id = storage.create_customer(
        customer.first_name, customer.last_name, customer.city,
    )
    return {
        "customer_id": customer_id,
        "display_name": f"{customer.first_name} {customer.last_name}",
        **customer.model_dump(),
    }


def list_arepas(*, include_sold_out: bool = False) -> list[dict]:
    items = []
    for flavor in storage.list_flavors():
        stock = int(flavor.get("stock", 0))
        if stock <= 0 and not include_sold_out:
            continue
        status, label = storage.stock_label(stock)
        item = {
            "id": flavor["RowKey"],
            "name": flavor["name"],
            "description": flavor.get("description", ""),
            "price": float(flavor.get("price", 0.0)),
            "stock_status": status,
            "stock_label": label,
        }
        if include_sold_out:
            item["image_url"] = flavor.get("image_url", "")
        items.append(item)
    return items


def place_order(order: OrderRequest) -> dict:
    customer = storage.get_customer(order.customer_id)
    if not customer:
        raise OrderError(
            404, "Unknown customer_id. Please re-check your customer ID.", "unknown_customer",
        )

    if rate_limit.is_locked_out(order.customer_id):
        raise OrderError(
            429, "Too many wrong voucher attempts. Please wait about a minute and try again.",
            "voucher_locked",
            retry_after_s=rate_limit.seconds_until_unlock(order.customer_id),
        )
    # A code that cannot exist is a reading mistake, not a guess, so it does
    # not count toward the lockout.
    if not voucher.is_well_formed(order.voucher_code):
        bad = voucher.invalid_characters(order.voucher_code)
        if bad:
            message = (
                "That voucher code contains " + ", ".join(f"'{c}'" for c in bad)
                + ", which never appears in voucher codes (they use the digits 2-9 and "
                "letters A-Z without I, L or O). Please re-read the dashboard screen."
            )
        else:
            message = (
                "Voucher codes are exactly 6 characters. Please re-read the code on "
                "the dashboard screen."
            )
        raise OrderError(403, message, "malformed_voucher", invalid_characters=bad)
    if not voucher.is_valid_voucher(order.voucher_code):
        rate_limit.record_failure(order.customer_id)
        raise OrderError(
            403,
            "Voucher code is wrong or expired. Please read the current "
            "6-character code from the dashboard screen and try again.",
            "invalid_voucher",
            seconds_until_rotation=voucher.seconds_until_rotation(),
        )
    rate_limit.clear(order.customer_id)

    # Check the active order first: a pending real order also counts toward
    # the lifetime limit, but "you've had your arepa" would be wrong for it.
    if storage.get_active_order_for_customer(order.customer_id, order.kind):
        raise OrderError(
            409, f"You already have an active {order.kind} order. Please pick it up first.",
            "active_order_exists",
        )
    if order.kind == "real" and int(customer.get("real_orders_count", 0)) >= 1:
        raise OrderError(
            409,
            "You have already had your one real arepa at this workshop. Hope it was tasty!",
            "real_limit_reached",
        )

    flavor = storage.get_flavor(order.flavor_id)
    if not flavor:
        raise OrderError(404, f"Flavour '{order.flavor_id}' not found.", "flavor_not_found")

    order_id = storage.create_order(
        order.flavor_id, order.customer_id, customer["display_name"], order.kind,
    )
    if order.kind == "real":
        stock_reserved = False
        try:
            stock_reserved = (
                int(flavor.get("stock", 0)) > 0
                and storage.decrement_stock(order.flavor_id)
            )
        except Exception:
            # A failed response does not prove the stock write was rejected.
            logger.exception(
                "Stock reservation failed with an uncertain outcome; "
                "staff must reconcile inventory before retrying. No stock was refunded."
            )
            raise
        finally:
            if not stock_reserved:
                try:
                    storage.update_order_status(order_id, "cancelled")
                except Exception:
                    logger.exception("Could not cancel an order after its stock reservation failed")
                    raise
        if not stock_reserved:
            raise OrderError(409, f"Sorry, {flavor['name']} just sold out!", "sold_out")
        storage.increment_real_orders(order.customer_id)
    else:
        storage.update_order_status(order_id, "collected")

    return {
        "order_id": order_id,
        "customer_id": order.customer_id,
        "customer_name": customer["display_name"],
        "board_name": board_name(customer["display_name"]),
        "flavor": flavor["name"],
        "kind": order.kind,
        "status": "collected" if order.kind == "test" else "pending",
    }
