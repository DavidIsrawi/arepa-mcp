"""Shared customer validation, menu projection and order placement."""

import logging
from typing import Literal

from pydantic import BaseModel, Field, field_validator

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
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


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
        raise OrderError(404, "Unknown customer_id. Please re-check your customer ID.")

    if rate_limit.is_locked_out(order.customer_id):
        raise OrderError(
            429, "Too many wrong voucher attempts. Please wait about a minute and try again.",
        )
    if not voucher.is_valid_voucher(order.voucher_code):
        rate_limit.record_failure(order.customer_id)
        raise OrderError(
            403,
            "Voucher code is wrong or expired. Please read the current "
            "6-character code from the dashboard screen and try again.",
        )
    rate_limit.clear(order.customer_id)

    if order.kind == "real" and int(customer.get("real_orders_count", 0)) >= 1:
        raise OrderError(
            409,
            "You have already had your one real arepa at this workshop. Hope it was tasty!",
        )
    if storage.get_active_order_for_customer(order.customer_id, order.kind):
        raise OrderError(
            409, f"You already have an active {order.kind} order. Please pick it up first.",
        )

    flavor = storage.get_flavor(order.flavor_id)
    if not flavor:
        raise OrderError(404, f"Flavour '{order.flavor_id}' not found.")

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
            raise OrderError(409, f"Sorry, {flavor['name']} just sold out!")
        storage.increment_real_orders(order.customer_id)
    else:
        storage.update_order_status(order_id, "collected")

    return {
        "order_id": order_id,
        "customer_id": order.customer_id,
        "customer_name": customer["display_name"],
        "flavor": flavor["name"],
        "kind": order.kind,
        "status": "collected" if order.kind == "test" else "pending",
    }
