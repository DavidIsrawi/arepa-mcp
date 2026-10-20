"""Offline storage lifetime tests: python -m unittest discover -s tests -v."""

from contextlib import contextmanager
from datetime import datetime, timezone
import unittest
from unittest.mock import Mock, call, patch

from azure.core.credentials import AzureNamedKeyCredential
from azure.core.exceptions import (
    ResourceExistsError,
    ResourceNotFoundError,
    ServiceRequestError,
)
from azure.core.pipeline.transport import RequestsTransport
from azure.data.tables import TableClient, TableServiceClient, UpdateMode

from backend import storage

with patch("dotenv.load_dotenv"):
    from backend import seed_dummy_orders


OPERATIONS = [
    ("ensure_tables_and_seed", ()),
    ("list_flavors", ()),
    ("get_flavor", ("flavor",)),
    ("create_flavor", ("flavor", "Flavor", "Description", 2.5, 3)),
    ("update_flavor", ("flavor",)),
    ("delete_flavor", ("flavor",)),
    ("decrement_stock", ("flavor",)),
    ("increment_stock", ("flavor",)),
    ("list_orders", ()),
    ("list_orders_by_status", ("pending", "real")),
    ("get_active_order_for_customer", ("23456789", "real")),
    ("create_order", ("flavor", "23456789", "Test Customer", "test")),
    ("get_order", ("order",)),
    ("update_order_status", ("order", "ready")),
    ("clear_all_orders", ()),
    ("list_users", ()),
    ("get_user", ("user",)),
    ("create_user", ("user", "test-password")),
    ("update_user_password", ("user", "test-password")),
    ("delete_user", ("user",)),
    ("verify_password", ("user", "test-password")),
    ("create_customer", ("Test", "Customer", "Test City")),
    ("get_customer", ("23456789",)),
    ("list_customers", ()),
    ("increment_real_orders", ("23456789",)),
    ("decrement_real_orders", ("23456789",)),
]

QUERY_OPERATIONS = [
    ("list_flavors", ()),
    ("list_orders", ()),
    ("list_orders_by_status", ("pending",)),
    ("get_active_order_for_customer", ("23456789", "real")),
    ("clear_all_orders", ()),
    ("list_users", ()),
    ("list_customers", ()),
]


class StorageLifetimeTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(storage.bcrypt, "gensalt", return_value=b"salt"))
        self.enterContext(patch.object(storage.bcrypt, "hashpw", return_value=b"test-hash"))
        self.enterContext(patch.object(storage.bcrypt, "checkpw", return_value=True))
        self.enterContext(patch.object(storage, "_generate_customer_id", return_value="23456789"))

    @contextmanager
    def client(self, rows=None):
        # Exercise real SDK ownership/close semantics, but never open a network socket.
        session = Mock(spec=["close"])
        transport = RequestsTransport(session=session)
        service = TableServiceClient(
            endpoint="https://storage.invalid",
            credential=AzureNamedKeyCredential("testaccount", "dGVzdC1vbmx5"),
            transport=transport,
        )
        table = Mock(spec=TableClient)
        table.get_entity.return_value = {
            "RowKey": "row", "stock": 3, "real_orders_count": 1,
            "password_hash": "test-hash",
        }
        if rows is None:
            rows = [{"RowKey": "row", "status": "pending", "created_at": "2026-01-01"}]

        def require_open(*args, **kwargs):
            self.assertIs(transport.session, session, "Client closed before storage work finished")
            session.close.assert_not_called()

        def get_entity(*args, **kwargs):
            require_open()
            return table.get_entity.return_value

        def query_entities(*args, **kwargs):
            for row in rows:
                require_open()
                yield row.copy()
            require_open()

        table.get_entity.side_effect = get_entity
        table.query_entities.side_effect = query_entities
        for method in ("create_entity", "update_entity", "delete_entity"):
            getattr(table, method).side_effect = require_open

        try:
            with (
                patch.object(storage.TableServiceClient, "from_connection_string",
                             return_value=service) as factory,
                patch.object(service, "get_table_client", return_value=table),
                patch.object(service, "create_table", side_effect=require_open),
            ):
                yield service, table
                factory.assert_called_once_with(storage.CONN_STR)
                session.close.assert_called_once_with()
                self.assertIsNone(transport.session)
        finally:
            service.close()

    def test_every_storage_operation_closes_the_owning_transport(self):
        for name, args in OPERATIONS:
            with self.subTest(operation=name), self.client():
                getattr(storage, name)(*args)

    def test_storage_errors_propagate_and_close_the_owning_transport(self):
        for name, args in OPERATIONS:
            with self.subTest(operation=name), self.client() as (service, table):
                error = ServiceRequestError("Storage unavailable")
                service.create_table.side_effect = error
                for method in ("get_entity", "query_entities", "create_entity",
                               "update_entity", "delete_entity"):
                    getattr(table, method).side_effect = error
                with self.assertRaises(ServiceRequestError) as raised:
                    getattr(storage, name)(*args)
                self.assertIs(raised.exception, error)

    def test_table_client_acquisition_error_closes_the_service(self):
        with self.client() as (service, _):
            error = ValueError("Cannot construct table client")
            service.get_table_client.side_effect = error
            with self.assertRaises(ValueError) as raised:
                storage.list_flavors()
            self.assertIs(raised.exception, error)

    def test_write_errors_after_reads_close_the_service(self):
        operations = [
            ("update_flavor", ("flavor",)),
            ("decrement_stock", ("flavor",)),
            ("increment_stock", ("flavor",)),
            ("update_order_status", ("order", "ready")),
            ("update_user_password", ("user", "test-password")),
            ("increment_real_orders", ("23456789",)),
            ("decrement_real_orders", ("23456789",)),
        ]
        for name, args in operations:
            with self.subTest(operation=name), self.client() as (_, table):
                table.update_entity.side_effect = ServiceRequestError("Write failed")
                with self.assertRaises(ServiceRequestError):
                    getattr(storage, name)(*args)
                table.get_entity.assert_called_once()
                table.update_entity.assert_called_once()

    def test_missing_entities_preserve_existing_return_values(self):
        operations = [
            ("get_flavor", None), ("get_order", None), ("get_user", None),
            ("get_customer", None), ("decrement_stock", False), ("increment_stock", False),
            ("verify_password", False),
        ]
        for name, expected in operations:
            with self.subTest(operation=name), self.client() as (_, table):
                table.get_entity.side_effect = ResourceNotFoundError("Not found")
                args = ("missing", "password") if name == "verify_password" else ("missing",)
                self.assertIs(getattr(storage, name)(*args), expected)
                table.update_entity.assert_not_called()

    def test_lazy_queries_finish_before_the_service_closes(self):
        older = {"RowKey": "older", "status": "collected", "created_at": "2026-01-01"}
        newer = {"RowKey": "newer", "status": "ready", "created_at": "2026-01-02"}
        for name, args in QUERY_OPERATIONS:
            with self.subTest(operation=name), self.client([older, newer]) as (_, table):
                result = getattr(storage, name)(*args)
                if name in ("list_orders", "list_orders_by_status"):
                    self.assertEqual(result, [newer, older])
                elif name == "get_active_order_for_customer":
                    self.assertEqual(result, newer)
                elif name == "clear_all_orders":
                    self.assertEqual(result, 2)
                    self.assertEqual(table.delete_entity.call_args_list, [
                        call("order", "older"), call("order", "newer"),
                    ])
                else:
                    self.assertEqual(result, [older, newer])

    def test_later_page_error_closes_the_service(self):
        error = ServiceRequestError("Next page unavailable")

        def failing_pages(*args, **kwargs):
            yield {"RowKey": "first"}
            raise error

        for name, args in QUERY_OPERATIONS:
            with self.subTest(operation=name), self.client() as (_, table):
                table.query_entities.side_effect = failing_pages
                with self.assertRaises(ServiceRequestError) as raised:
                    getattr(storage, name)(*args)
                self.assertIs(raised.exception, error)
                table.delete_entity.assert_not_called()

    def test_empty_queries_preserve_existing_return_values(self):
        for name, args in QUERY_OPERATIONS:
            with self.subTest(operation=name), self.client([]):
                result = getattr(storage, name)(*args)
                expected = None if name == "get_active_order_for_customer" else (
                    0 if name == "clear_all_orders" else []
                )
                self.assertEqual(result, expected)

    def test_query_filters_are_unchanged(self):
        cases = [
            ("list_flavors", (), "PartitionKey eq 'flavor'"),
            ("list_orders", (), "PartitionKey eq 'order'"),
            ("list_users", (), "PartitionKey eq 'user'"),
            ("list_customers", (), "PartitionKey eq 'customer'"),
            ("list_orders_by_status", ("pending",), "PartitionKey eq 'order' and status eq 'pending'"),
            ("list_orders_by_status", ("pend'ing", "re'al"),
             "PartitionKey eq 'order' and status eq 'pend''ing' and kind eq 're''al'"),
            ("get_active_order_for_customer", ("cus'tomer", "re'al"),
             "PartitionKey eq 'order' and customer_id eq 'cus''tomer' and kind eq 're''al'"),
        ]
        for name, args, query in cases:
            with self.subTest(operation=name, args=args), self.client() as (_, table):
                getattr(storage, name)(*args)
                table.query_entities.assert_called_once_with(query)

    def test_stock_updates_preserve_values_and_modes(self):
        cases = [
            ("decrement_stock", 0, 0, False), ("decrement_stock", 3, 2, True),
            ("increment_stock", 0, 1, True), ("increment_stock", 3, 4, True),
        ]
        for name, before, after, expected in cases:
            with self.subTest(operation=name, stock=before), self.client() as (_, table):
                entity = table.get_entity.return_value
                entity["stock"] = before
                self.assertIs(getattr(storage, name)("flavor"), expected)
                self.assertEqual(entity["stock"], after)
                if expected:
                    table.update_entity.assert_called_once_with(entity, mode=UpdateMode.REPLACE)
                else:
                    table.update_entity.assert_not_called()

    def test_customer_count_updates_preserve_zero_floor(self):
        for name, before, after in [
            ("increment_real_orders", 0, 1),
            ("decrement_real_orders", 2, 1),
            ("decrement_real_orders", 0, 0),
        ]:
            with self.subTest(operation=name, count=before), self.client() as (_, table):
                entity = table.get_entity.return_value
                entity["real_orders_count"] = before
                self.assertEqual(getattr(storage, name)("23456789"), after)
                self.assertEqual(entity["real_orders_count"], after)
                table.update_entity.assert_called_once_with(entity, mode=UpdateMode.MERGE)

    def test_ready_timestamp_is_set_once(self):
        now = datetime(2026, 1, 2, tzinfo=timezone.utc)
        for ready_at in (None, "2026-01-01T00:00:00+00:00"):
            with self.subTest(ready_at=ready_at), self.client() as (_, table):
                entity = table.get_entity.return_value
                entity["ready_at"] = ready_at
                with patch.object(storage, "datetime") as clock:
                    clock.now.return_value = now
                    storage.update_order_status("order", "ready")
                self.assertEqual(entity["status"], "ready")
                self.assertEqual(entity["ready_at"], ready_at or now.isoformat())
                self.assertEqual(entity["status_changed_at"], now.isoformat())
                table.update_entity.assert_called_once_with(entity, mode=UpdateMode.MERGE)

    def test_bootstrap_seeds_missing_admin_and_tolerates_existing_tables(self):
        with self.client() as (service, table):
            service.create_table.side_effect = ResourceExistsError("Already exists")
            table.get_entity.side_effect = ResourceNotFoundError("No admin")
            storage.ensure_tables_and_seed()
            self.assertEqual(service.create_table.call_args_list, [
                call(storage.TABLE_FLAVORS), call(storage.TABLE_ORDERS),
                call(storage.TABLE_USERS), call(storage.TABLE_CUSTOMERS),
            ])
            table.create_entity.assert_called_once_with({
                "PartitionKey": "user", "RowKey": "admin", "password_hash": "test-hash",
            })

    def test_bootstrap_does_not_replace_existing_admin(self):
        with self.client() as (_, table):
            storage.ensure_tables_and_seed()
            table.get_entity.assert_called_once_with("user", "admin")
            table.create_entity.assert_not_called()

    def test_admin_seed_failure_closes_the_service(self):
        with self.client() as (_, table):
            table.get_entity.side_effect = ResourceNotFoundError("No admin")
            table.create_entity.side_effect = ServiceRequestError("Seed failed")
            with self.assertRaises(ServiceRequestError):
                storage.ensure_tables_and_seed()

    def test_customer_collision_retry_keeps_the_same_service(self):
        with self.client() as (_, table):
            table.create_entity.side_effect = [ResourceExistsError("Collision"), None]
            with patch.object(storage, "_generate_customer_id", side_effect=["23456789", "ABCDEFGH"]):
                self.assertEqual(storage.create_customer("Test", "Customer", "City"), "ABCDEFGH")
            self.assertEqual(
                [args.args[0]["RowKey"] for args in table.create_entity.call_args_list],
                ["23456789", "ABCDEFGH"],
            )

    def test_exhausted_customer_collisions_close_the_service(self):
        with self.client() as (_, table):
            table.create_entity.side_effect = ResourceExistsError("Collision")
            with self.assertRaisesRegex(RuntimeError, "after 5 attempts"):
                storage.create_customer("Test", "Customer", "City")
            self.assertEqual(table.create_entity.call_count, 5)

    def test_partial_bulk_delete_failure_closes_the_service(self):
        with self.client([{"RowKey": "first"}, {"RowKey": "second"}]) as (_, table):
            table.delete_entity.side_effect = [None, ServiceRequestError("Delete failed")]
            with self.assertRaises(ServiceRequestError):
                storage.clear_all_orders()
            self.assertEqual(table.delete_entity.call_count, 2)

    def test_repeated_dashboard_storage_reads_release_each_transport(self):
        for _ in range(100):
            for operation in (storage.list_orders, storage.list_flavors):
                with self.client():
                    operation()

    def test_dummy_order_closes_its_direct_storage_client(self):
        for fail in (False, True):
            with self.subTest(fail=fail), self.client() as (_, table):
                with (
                    patch.object(seed_dummy_orders, "_ensure_flavour", return_value="flavor"),
                    patch.object(seed_dummy_orders, "_create_dummy_customer",
                                 return_value=("23456789", "Test Customer")),
                    patch.object(storage, "get_flavor", return_value={"stock": 0}),
                    patch.object(storage, "increment_real_orders") as increment,
                ):
                    if fail:
                        table.create_entity.side_effect = ServiceRequestError("Write failed")
                        with self.assertRaises(ServiceRequestError):
                            seed_dummy_orders._create_order("ready")
                        increment.assert_not_called()
                    else:
                        seed_dummy_orders._create_order("ready")
                        increment.assert_called_once_with("23456789")
                        entity = table.create_entity.call_args.args[0]
                        self.assertEqual(entity["status"], "ready")
                        self.assertEqual(entity["kind"], "real")
                        self.assertEqual(entity["customer_id"], "23456789")


if __name__ == "__main__":
    unittest.main()
