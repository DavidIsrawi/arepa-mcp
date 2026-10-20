"""Add illustrative Arepa Lab menu items without modifying existing inventory."""

import argparse

from azure.core.exceptions import ResourceExistsError
from dotenv import load_dotenv

load_dotenv()

from backend import storage  # noqa: E402

DEMO_MENU = (
    ("arepa-lab-special", "Arepa Lab Special", "Demo recipe: shredded beef, cheese and black beans."),
    ("arepa-de-queso", "Arepa de Queso", "Demo recipe: grilled corn arepa with cheese."),
    ("arepa-de-caraotas", "Arepa de Caraotas", "Demo recipe: grilled corn arepa with black beans."),
)


def seed_menu(stock: int = 0) -> int:
    if stock < 0:
        raise ValueError("Stock cannot be negative.")
    added = 0
    for item_id, name, description in DEMO_MENU:
        if storage.get_flavor(item_id):
            continue
        try:
            storage.create_flavor(item_id, name, description, 0.0, stock)
        except ResourceExistsError:
            # Another initializer added this item; never overwrite its stock.
            continue
        added += 1
    return added


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stock", type=int, default=0, help="Initial demo stock per new item (default: 0).")
    args = parser.parse_args()
    if args.stock < 0:
        parser.error("--stock must be zero or greater")
    storage.ensure_tables_and_seed()
    print(f"Added {seed_menu(args.stock)} demo menu items. Existing items were unchanged.")
    print("Demo prices are USD 0.00. Confirm recipes, prices and physical stock in Admin > Menu before serving.")


if __name__ == "__main__":
    main()
