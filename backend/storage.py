import os
import secrets
import uuid
from datetime import datetime, timezone

import bcrypt
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.data.tables import TableServiceClient, UpdateMode

CONN_STR = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")

TABLE_FLAVORS = "Flavors"
TABLE_ORDERS = "Orders"
TABLE_USERS = "Users"
TABLE_CUSTOMERS = "Customers"

# Crockford-ish alphabet: no 0, 1, I, L, O — easy to read aloud.
_CUSTOMER_ID_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
_CUSTOMER_ID_LENGTH = 8


def _generate_customer_id() -> str:
    return "".join(secrets.choice(_CUSTOMER_ID_ALPHABET) for _ in range(_CUSTOMER_ID_LENGTH))


def _client() -> TableServiceClient:
    return TableServiceClient.from_connection_string(CONN_STR)


def _odata_escape(value: str) -> str:
    """Escape single quotes for safe OData filter strings (OData spec: '' == ')."""
    return value.replace("'", "''")


# ── Bootstrap ──────────────────────────────────────────────────────────────────

def ensure_tables_and_seed() -> None:
    """Create tables if absent, then seed the default admin user."""
    svc = _client()
    for name in [TABLE_FLAVORS, TABLE_ORDERS, TABLE_USERS, TABLE_CUSTOMERS]:
        try:
            svc.create_table(name)
        except ResourceExistsError:
            pass

    users = svc.get_table_client(TABLE_USERS)
    try:
        users.get_entity("user", "admin")
    except ResourceNotFoundError:
        pw_hash = bcrypt.hashpw(b"admin", bcrypt.gensalt()).decode()
        users.create_entity(
            {"PartitionKey": "user", "RowKey": "admin", "password_hash": pw_hash}
        )


# ── Flavors ────────────────────────────────────────────────────────────────────

def list_flavors() -> list:
    return list(_client().get_table_client(TABLE_FLAVORS).query_entities("PartitionKey eq 'flavor'"))


def get_flavor(flavor_id: str) -> dict | None:
    try:
        return _client().get_table_client(TABLE_FLAVORS).get_entity("flavor", flavor_id)
    except ResourceNotFoundError:
        return None


def create_flavor(
    flavor_id: str, name: str, description: str, price: float, stock: int, image_url: str = ""
) -> None:
    _client().get_table_client(TABLE_FLAVORS).create_entity(
        {
            "PartitionKey": "flavor",
            "RowKey": flavor_id,
            "name": name,
            "description": description,
            "price": float(price),
            "stock": int(stock),
            "image_url": image_url,
        }
    )


def update_flavor(flavor_id: str, **fields) -> None:
    table = _client().get_table_client(TABLE_FLAVORS)
    entity = table.get_entity("flavor", flavor_id)
    entity.update(fields)
    table.update_entity(entity, mode=UpdateMode.MERGE)


def delete_flavor(flavor_id: str) -> None:
    _client().get_table_client(TABLE_FLAVORS).delete_entity("flavor", flavor_id)


def decrement_stock(flavor_id: str) -> bool:
    """
    Decrement stock by 1. Returns False if already at 0.
    Not atomic — race condition is negligible at workshop scale.
    """
    table = _client().get_table_client(TABLE_FLAVORS)
    try:
        entity = table.get_entity("flavor", flavor_id)
    except ResourceNotFoundError:
        return False
    if int(entity.get("stock", 0)) <= 0:
        return False
    entity["stock"] = int(entity["stock"]) - 1
    table.update_entity(entity, mode=UpdateMode.REPLACE)
    return True


def stock_label(stock: int) -> tuple[str, str]:
    """Return (machine_status, human_label) for a given numeric stock count."""
    if stock <= 0:
        return ("sold_out", "Sold out")
    if stock <= 3:
        return ("almost_finished", "Almost finished")
    if stock <= 10:
        return ("limited", "Limited")
    return ("plenty", "Plenty available")


# ── Orders ─────────────────────────────────────────────────────────────────────

def list_orders() -> list:
    orders = list(
        _client().get_table_client(TABLE_ORDERS).query_entities("PartitionKey eq 'order'")
    )
    return sorted(orders, key=lambda o: o.get("created_at", ""))


def get_active_order_for_customer(customer_id: str, kind: str) -> dict | None:
    """Return a pending or ready order of the given kind for this customer_id."""
    safe_cid = _odata_escape(customer_id)
    safe_kind = _odata_escape(kind)
    results = list(
        _client()
        .get_table_client(TABLE_ORDERS)
        .query_entities(
            f"PartitionKey eq 'order' and customer_id eq '{safe_cid}' "
            f"and kind eq '{safe_kind}'"
        )
    )
    active = [o for o in results if o.get("status") in ("pending", "ready")]
    return active[0] if active else None


def create_order(flavor_id: str, customer_id: str, customer_name: str, kind: str) -> str:
    order_id = str(uuid.uuid4())
    _client().get_table_client(TABLE_ORDERS).create_entity(
        {
            "PartitionKey": "order",
            "RowKey": order_id,
            "flavor_id": flavor_id,
            "customer_id": customer_id,
            "customer_name": customer_name,
            "kind": kind,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    return order_id


def update_order_status(order_id: str, status: str) -> None:
    table = _client().get_table_client(TABLE_ORDERS)
    entity = table.get_entity("order", order_id)
    entity["status"] = status
    table.update_entity(entity, mode=UpdateMode.MERGE)


def clear_all_orders() -> int:
    """Delete every order row. Returns number of rows deleted."""
    table = _client().get_table_client(TABLE_ORDERS)
    orders = list(table.query_entities("PartitionKey eq 'order'"))
    for order in orders:
        table.delete_entity("order", order["RowKey"])
    return len(orders)


# ── Users ──────────────────────────────────────────────────────────────────────

def list_users() -> list:
    return list(_client().get_table_client(TABLE_USERS).query_entities("PartitionKey eq 'user'"))


def get_user(username: str) -> dict | None:
    try:
        return _client().get_table_client(TABLE_USERS).get_entity("user", username)
    except ResourceNotFoundError:
        return None


def create_user(username: str, password: str) -> None:
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    _client().get_table_client(TABLE_USERS).create_entity(
        {"PartitionKey": "user", "RowKey": username, "password_hash": pw_hash}
    )


def update_user_password(username: str, password: str) -> None:
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    table = _client().get_table_client(TABLE_USERS)
    entity = table.get_entity("user", username)
    entity["password_hash"] = pw_hash
    table.update_entity(entity, mode=UpdateMode.MERGE)


def delete_user(username: str) -> None:
    _client().get_table_client(TABLE_USERS).delete_entity("user", username)


def verify_password(username: str, password: str) -> bool:
    user = get_user(username)
    if not user:
        return False
    return bcrypt.checkpw(password.encode(), user["password_hash"].encode())


# ── Customers ─────────────────────────────────────────────────────────────────

def create_customer(display_name: str) -> str:
    """Allocate a fresh customer_id and persist the customer row. Retries on collision."""
    table = _client().get_table_client(TABLE_CUSTOMERS)
    for _ in range(5):
        cid = _generate_customer_id()
        try:
            table.create_entity(
                {
                    "PartitionKey": "customer",
                    "RowKey": cid,
                    "display_name": display_name,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "real_orders_count": 0,
                }
            )
            return cid
        except ResourceExistsError:
            continue
    raise RuntimeError("Could not allocate a unique customer_id after 5 attempts")


def get_customer(customer_id: str) -> dict | None:
    try:
        return _client().get_table_client(TABLE_CUSTOMERS).get_entity("customer", customer_id)
    except ResourceNotFoundError:
        return None


def list_customers() -> list:
    return list(
        _client().get_table_client(TABLE_CUSTOMERS).query_entities("PartitionKey eq 'customer'")
    )


def increment_real_orders(customer_id: str) -> int:
    table = _client().get_table_client(TABLE_CUSTOMERS)
    entity = table.get_entity("customer", customer_id)
    entity["real_orders_count"] = int(entity.get("real_orders_count", 0)) + 1
    table.update_entity(entity, mode=UpdateMode.MERGE)
    return entity["real_orders_count"]
