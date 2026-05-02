"""Seed dummy orders into the database for dashboard testing.

Usage (from repo root, with the same env as the app):

    python -m backend.seed_dummy_orders          # default: 50 pending + 50 ready
    python -m backend.seed_dummy_orders 30 20    # 30 pending + 20 ready

Creates fake customers as needed and ensures at least one flavour exists.
Real-order lifetime cap and stock are bypassed (stock is decremented
only if available; otherwise the order is still created).
"""
from __future__ import annotations

import random
import sys
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

from backend import storage  # noqa: E402

_FIRST_NAMES = [
    "Henk", "Anna", "Lars", "Eva", "Mo", "Sara", "Liam", "Mei",
    "Jasper", "Nora", "Theo", "Imani", "Yuki", "Pavel", "Inge",
    "Cher", "Olu", "Rin", "Bram", "Aisha", "Diego", "Freya",
    "Karim", "Lotte", "Niko", "Priya", "Quinn", "Rafa", "Suki",
    "Tomas", "Uma", "Vera", "Wout", "Xander", "Yara", "Zofia",
]
_LAST_NAMES = [
    "Boelman", "Smith", "Vermeer", "Dubois", "Rossi", "Tanaka",
    "Nguyen", "Müller", "Kowalski", "Andersson", "Okafor",
    "Patel", "Garcia", "Johansson", "O'Brien", "Bakker", "Singh",
    "Cohen", "Hernandez", "Park",
]
_CITIES = [
    "Amsterdam", "Rotterdam", "Berlin", "Paris", "Tokyo", "Lagos",
    "São Paulo", "Stockholm", "Krakow", "Lisbon", "Dublin",
    "Mumbai", "Toronto", "Seoul", "Cape Town",
]


def _random_name() -> tuple[str, str, str]:
    return random.choice(_FIRST_NAMES), random.choice(_LAST_NAMES), random.choice(_CITIES)


def _ensure_flavour() -> str:
    flavours = storage.list_flavors()
    if flavours:
        return random.choice(flavours)["RowKey"]
    storage.create_flavor(
        "demo-vanilla", "Demo Vanilla", "Seeded for dashboard demo.",
        2.5, 999, "",
    )
    return "demo-vanilla"


def _create_dummy_customer() -> tuple[str, str]:
    first, last, city = _random_name()
    cid = storage.create_customer(first, last, city)
    return cid, f"{first} {last}"


def _create_order(status: str) -> None:
    flavor_id = _ensure_flavour()
    customer_id, display_name = _create_dummy_customer()

    # Decrement stock if available (don't fail if not).
    flavor = storage.get_flavor(flavor_id)
    if flavor and int(flavor.get("stock", 0)) > 0:
        storage.decrement_stock(flavor_id)

    order_id = str(uuid.uuid4())
    storage._client().get_table_client(storage.TABLE_ORDERS).create_entity(
        {
            "PartitionKey": "order",
            "RowKey": order_id,
            "flavor_id": flavor_id,
            "customer_id": customer_id,
            "customer_name": display_name,
            "kind": "real",
            "status": status,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    storage.increment_real_orders(customer_id)


def main() -> None:
    storage.ensure_tables_and_seed()

    n_pending = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    n_ready = int(sys.argv[2]) if len(sys.argv) > 2 else 50

    print(f"Seeding {n_pending} pending and {n_ready} ready orders…")
    for i in range(n_pending):
        _create_order("pending")
        if (i + 1) % 10 == 0:
            print(f"  pending: {i + 1}/{n_pending}")
    for i in range(n_ready):
        _create_order("ready")
        if (i + 1) % 10 == 0:
            print(f"  ready:   {i + 1}/{n_ready}")
    print("Done.")


if __name__ == "__main__":
    main()
