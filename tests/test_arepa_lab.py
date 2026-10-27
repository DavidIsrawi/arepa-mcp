import copy
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import bcrypt
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError, ServiceRequestError
from azure.data.tables import UpdateMode
from fastapi.testclient import TestClient
from fastmcp import Client

from backend import branding, mcp_server, rate_limit, seed_menu, storage, voucher
from backend.main import app
from backend.routers import public


class MemoryTable:
    """Azure table boundary double; the application's CRUD code still executes."""

    def __init__(self):
        self.rows = {}

    def create_entity(self, entity):
        key = (entity["PartitionKey"], entity["RowKey"])
        if key in self.rows:
            raise ResourceExistsError("Entity exists")
        self.rows[key] = copy.deepcopy(entity)

    def get_entity(self, partition_key, row_key):
        try:
            return copy.deepcopy(self.rows[(partition_key, row_key)])
        except KeyError:
            raise ResourceNotFoundError("Entity not found")

    def update_entity(self, entity, mode):
        key = (entity["PartitionKey"], entity["RowKey"])
        if key not in self.rows:
            raise ResourceNotFoundError("Entity not found")
        if mode == UpdateMode.MERGE:
            self.rows[key].update(copy.deepcopy(entity))
        else:
            self.rows[key] = copy.deepcopy(entity)

    def delete_entity(self, partition_key, row_key):
        del self.rows[(partition_key, row_key)]

    def query_entities(self, query):
        clauses = []
        for clause in query.split(" and "):
            match = re.fullmatch(r"(\w+) eq '((?:[^']|'')*)'", clause)
            if not match:
                raise AssertionError(f"Unsupported query in test double: {query}")
            field, value = match.groups()
            clauses.append((field, value.replace("''", "'")))
        return [
            copy.deepcopy(row) for row in self.rows.values()
            if all(row.get(field) == value for field, value in clauses)
        ]


class MemoryService:
    def __init__(self):
        self.tables = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def create_table(self, name):
        if name in self.tables:
            raise ResourceExistsError("Table exists")
        self.tables[name] = MemoryTable()

    def get_table_client(self, name):
        return self.tables[name]


class StoreFixture:
    def setUp(self):
        super().setUp()
        self.service = MemoryService()
        self.enterContext(patch.object(storage, "_client", return_value=self.service))
        self.enterContext(patch.dict(os.environ, {
            "WORKSHOP_URL": "", "WORKSHOP_TRAINING_KEY": "", "VOUCHER_SECRET": "test-only",
        }))
        os.environ.pop("INITIAL_ADMIN_PASSWORD", None)
        self.enterContext(patch.dict(rate_limit._attempts, {}, clear=True))
        public._dash_cache.update(at=0.0, data=None)
        storage.ensure_tables_and_seed()
        storage.create_flavor("lab-special", "Arepa Lab Special", "Demo filling", 0.0, 12)
        storage.create_flavor("queso", "Arepa de Queso", "Demo cheese filling", 0.0, 0)
        self.customer_id = storage.create_customer("Ana", "Perez", "Indianapolis")
        self.code = voucher.current_voucher()
        self.wrong_code = next(
            code for code in ("ZZZZZZ", "YYYYYY", "XXXXXX") if not voucher.is_valid_voucher(code)
        )

    def order(self, kind="real", **kwargs):
        args = dict(flavor_id="lab-special", customer_id=self.customer_id, kind=kind, voucher_code=self.code)
        args.update(kwargs)
        return mcp_server.order_arepa(**args)


class AdminBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.service = MemoryService()
        self.enterContext(patch.object(storage, "_client", return_value=self.service))
        self.enterContext(patch.dict(os.environ))
        os.environ.pop("INITIAL_ADMIN_PASSWORD", None)

    def password_hash(self):
        return self.service.get_table_client(storage.TABLE_USERS).get_entity("user", "admin")["password_hash"].encode()

    def test_local_default_is_preserved(self):
        storage.ensure_tables_and_seed()
        self.assertTrue(bcrypt.checkpw(b"admin", self.password_hash()))

    def test_deployment_password_and_existing_account_are_preserved(self):
        os.environ["INITIAL_ADMIN_PASSWORD"] = "a-strong-deployment-password"
        storage.ensure_tables_and_seed()
        original = self.password_hash()
        self.assertTrue(bcrypt.checkpw(b"a-strong-deployment-password", original))
        os.environ["INITIAL_ADMIN_PASSWORD"] = "another-strong-password"
        storage.ensure_tables_and_seed()
        self.assertEqual(self.password_hash(), original)

    def test_invalid_password_is_rejected_before_admin_is_created(self):
        for password in ["", "short", "x" * 73, "\u00e9" * 37]:
            with self.subTest(password_length=len(password)):
                os.environ["INITIAL_ADMIN_PASSWORD"] = password
                with self.assertRaisesRegex(ValueError, "16-72 UTF-8 bytes"):
                    storage.ensure_tables_and_seed()
                with self.assertRaises(ResourceNotFoundError):
                    self.service.get_table_client(storage.TABLE_USERS).get_entity("user", "admin")


class OrderTests(StoreFixture, unittest.TestCase):
    def test_menu_hides_sold_out_and_numeric_inventory(self):
        items = mcp_server.list_arepas()
        self.assertEqual([item["id"] for item in items], ["lab-special"])
        self.assertEqual(items[0]["stock_status"], "plenty")
        self.assertNotIn("stock", items[0])

    def test_stock_label_thresholds(self):
        for count, status in [(0, "sold_out"), (1, "almost_finished"), (3, "almost_finished"),
                              (4, "limited"), (10, "limited"), (11, "plenty")]:
            with self.subTest(stock=count):
                self.assertEqual(storage.stock_label(count)[0], status)

    def test_real_order_consumes_stock_and_stores_existing_schema(self):
        result = self.order()
        self.assertTrue(result["success"])
        self.assertEqual(storage.get_flavor("lab-special")["stock"], 11)
        row = storage.get_order(result["order_id"])
        self.assertEqual(row["flavor_id"], "lab-special")
        self.assertEqual(row["status"], "pending")
        self.assertEqual(storage.get_customer(self.customer_id)["real_orders_count"], 1)
        self.assertEqual(mcp_server.check_order_status(self.customer_id)["status"], "pending")

    def test_test_order_auto_collects_without_consuming_stock_or_allowance(self):
        for _ in range(2):
            result = self.order("test")
            self.assertTrue(result["success"])
            self.assertEqual(storage.get_order(result["order_id"])["status"], "collected")
        self.assertEqual(storage.get_flavor("lab-special")["stock"], 12)
        self.assertEqual(storage.get_customer(self.customer_id)["real_orders_count"], 0)
        self.assertFalse(mcp_server.check_order_status(self.customer_id)["found"])

    def test_real_limit_persists_after_collection(self):
        result = self.order()
        storage.update_order_status(result["order_id"], "collected")
        self.assertFalse(self.order()["success"])
        self.assertTrue(self.order("test")["success"])

    def test_pending_cancellation_restores_stock_and_allowance_once(self):
        self.order()
        self.assertTrue(mcp_server.cancel_order(self.customer_id, "real")["success"])
        self.assertFalse(mcp_server.cancel_order(self.customer_id, "real")["success"])
        self.assertEqual(storage.get_flavor("lab-special")["stock"], 12)
        self.assertEqual(storage.get_customer(self.customer_id)["real_orders_count"], 0)
        self.assertTrue(self.order()["success"])

    def test_ready_order_cannot_be_cancelled_through_mcp(self):
        result = self.order()
        storage.update_order_status(result["order_id"], "ready")
        self.assertEqual(mcp_server.check_order_status(self.customer_id)["status"], "ready")
        self.assertFalse(mcp_server.cancel_order(self.customer_id, "real")["success"])

    def test_invalid_inputs_do_not_create_orders(self):
        for args in [
            {"customer_id": "invalid"}, {"customer_id": "ZZZZZZZZ"},
            {"kind": "invalid"}, {"voucher_code": "WRONG"},
            {"flavor_id": "missing"},
        ]:
            with self.subTest(args=args):
                self.assertFalse(self.order(**args)["success"])
        self.assertEqual(storage.list_orders(), [])
        self.assertEqual(storage.get_flavor("lab-special")["stock"], 12)

    def test_sold_out_real_order_does_not_leave_active_order(self):
        self.assertFalse(self.order(flavor_id="queso")["success"])
        self.assertIsNone(storage.get_active_order_for_customer(self.customer_id, "real"))
        self.assertEqual(storage.get_customer(self.customer_id)["real_orders_count"], 0)

    def test_voucher_required_for_tests_and_rate_limited(self):
        for _ in range(10):
            self.assertFalse(self.order("test", voucher_code=self.wrong_code)["success"])
        locked = self.order("test")
        self.assertIn("Too many", locked["message"])
        self.assertEqual(locked["error_code"], "voucher_locked")
        self.assertTrue(1 <= locked["retry_after_s"] <= 60)
        self.assertEqual(storage.list_orders(), [])

    def test_malformed_vouchers_explain_and_do_not_count_toward_lockout(self):
        for code, bad in [("WRONG0", ["O", "0"]), ("ABCDE", []), ("A1B2C3D", ["1"])]:
            with self.subTest(code=code):
                result = self.order("test", voucher_code=code)
                self.assertEqual(result["error_code"], "malformed_voucher")
                self.assertEqual(result["invalid_characters"], bad)
                for char in bad:
                    self.assertIn(f"'{char}'", result["message"])
        for _ in range(20):
            self.assertEqual(self.order("test", voucher_code="WRONG0")["error_code"], "malformed_voucher")
        self.assertNotIn(self.customer_id, rate_limit._attempts)
        spaced = f"{self.code[:3]} {self.code[3:].lower()}"
        self.assertTrue(self.order("test", voucher_code=spaced)["success"])

    def test_wrong_voucher_reports_rotation_timing_for_the_agent(self):
        result = self.order("test", voucher_code=self.wrong_code)
        self.assertEqual(result["error_code"], "invalid_voucher")
        self.assertTrue(1 <= result["seconds_until_rotation"] <= voucher.WINDOW_SECONDS)
        self.assertIn("re-read", result["agent_note"])
        self.assertIn("no need to wait", result["agent_note"])
        self.assertNotIn(result["agent_note"], result["message"])
        with patch.object(voucher, "seconds_until_rotation", return_value=5):
            status = mcp_server.voucher_status()
        self.assertNotIn("wait", status["message"])
        self.assertIn("3 more minutes", status["message"])

    def test_business_errors_have_stable_codes(self):
        self.assertEqual(self.order(customer_id="ZZZZZZZZ")["error_code"], "unknown_customer")
        self.assertEqual(self.order(flavor_id="missing")["error_code"], "flavor_not_found")
        self.assertEqual(self.order(flavor_id="queso")["error_code"], "sold_out")
        real = self.order()
        self.assertTrue(real["success"])
        pending_again = self.order()
        self.assertEqual(pending_again["error_code"], "active_order_exists")
        self.assertNotIn("tasty", pending_again["message"])
        self.assertEqual(mcp_server.cancel_order(self.customer_id, "test")["error_code"], "no_active_order")
        storage.update_order_status(real["order_id"], "ready")
        self.assertEqual(self.order()["error_code"], "active_order_exists")
        self.assertEqual(mcp_server.cancel_order(self.customer_id, "real")["error_code"], "order_not_cancellable")
        storage.update_order_status(real["order_id"], "collected")
        self.assertEqual(self.order()["error_code"], "real_limit_reached")

    def test_validation_errors_are_friendly_and_list_every_field(self):
        result = mcp_server.register_customer(" ", "G@rcia", "x" * 81)
        self.assertEqual(result["error_code"], "invalid_input")
        self.assertEqual(result["fields"], {
            "first_name": "Please provide a first name.",
            "last_name": "Last name can only contain letters, spaces, hyphens, apostrophes and periods.",
            "city": "City must be 80 characters or fewer.",
        })
        for message in result["fields"].values():
            self.assertIn(message, result["message"])
        self.assertNotIn("pattern", result["message"])
        result = self.order(customer_id="abc", voucher_code="AB")
        self.assertEqual(set(result["fields"]), {"customer_id", "voucher_code"})
        for tool in (mcp_server.get_customer, mcp_server.check_order_status):
            self.assertEqual(tool("abc")["error_code"], "invalid_input")
        self.assertEqual(mcp_server.cancel_order("abc", "real")["fields"], {"customer_id": result["fields"]["customer_id"]})

    def test_get_customer_reports_eligibility_without_private_details(self):
        result = mcp_server.get_customer(f" {self.customer_id.lower()} ")
        self.assertEqual(result, {
            "found": True, "customer_id": self.customer_id, "first_name": "Ana",
            "board_name": "Ana P.", "can_order_real": True, "active_real_order": None,
            "message": "Welcome back, Ana! You can place test orders and your one real arepa order.",
        })
        self.assertTrue(self.order()["success"])
        result = mcp_server.get_customer(self.customer_id)
        self.assertFalse(result["can_order_real"])
        self.assertEqual(result["active_real_order"], {"status": "pending", "flavor": "Arepa Lab Special"})
        self.assertIn("being prepared", result["message"])
        order_id = storage.get_active_order_for_customer(self.customer_id, "real")["RowKey"]
        storage.update_order_status(order_id, "collected")
        result = mcp_server.get_customer(self.customer_id)
        self.assertIsNone(result["active_real_order"])
        self.assertIn("already had your real arepa", result["message"])
        unknown = mcp_server.get_customer("ZZZZZZZZ")
        self.assertEqual((unknown["found"], unknown["error_code"]), (False, "unknown_customer"))

    def test_pickup_copy_uses_dashboard_name_not_voucher(self):
        test = self.order("test")
        self.assertEqual(test["board_name"], "Ana P.")
        self.assertIn('"Ana P."', test["message"])
        real = self.order()
        self.assertIn('"Ana P."', real["message"])
        self.assertIn("give your name", real["message"])
        self.assertNotIn("voucher", real["message"].lower())
        status = mcp_server.check_order_status(self.customer_id)
        self.assertEqual(status["board_name"], "Ana P.")
        self.assertIn('"Ana P."', status["message"])
        storage.update_order_status(real["order_id"], "ready")
        self.assertIn("give your name", mcp_server.check_order_status(self.customer_id)["message"])

    def test_registration_keeps_agent_directives_out_of_user_message(self):
        result = mcp_server.register_customer("Ana", "Perez", "Indianapolis")
        self.assertNotIn("register_customer", result["message"])
        self.assertIn("register_customer", result["agent_note"])
        self.assertIn(result["customer_id"], result["message"])
        self.assertEqual(result["board_name"], "Ana P.")

    def test_lockout_countdown_tracks_oldest_blocking_failure(self):
        with patch.object(rate_limit.time, "time", return_value=1000.0):
            for _ in range(10):
                rate_limit.record_failure("key")
        with patch.object(rate_limit.time, "time", return_value=1030.2):
            self.assertEqual(rate_limit.seconds_until_unlock("key"), 30)
        with patch.object(rate_limit.time, "time", return_value=1060.5):
            self.assertEqual(rate_limit.seconds_until_unlock("key"), 0)
        self.assertEqual(rate_limit.seconds_until_unlock("other"), 0)

    def test_customer_normalization_and_accented_names_in_mcp(self):
        registered = mcp_server.register_customer(" María ", " O'Brien ", " Bogotá ")
        self.assertTrue(registered["success"])
        self.assertEqual(registered["first_name"], "María")
        self.assertTrue(self.order(customer_id=registered["customer_id"].lower())["success"])
        self.assertFalse(mcp_server.register_customer("", "Name", "City")["success"])

    def test_clear_orders_does_not_restock_or_reset_customer(self):
        self.order()
        self.assertEqual(storage.clear_all_orders(), 1)
        self.assertEqual(storage.get_flavor("lab-special")["stock"], 11)
        self.assertEqual(storage.get_customer(self.customer_id)["real_orders_count"], 1)

    def test_menu_seed_is_opt_in_and_non_destructive(self):
        self.assertEqual(seed_menu.seed_menu(), 3)
        self.assertEqual(storage.get_flavor("arepa-lab-special")["stock"], 0)
        storage.update_flavor("arepa-lab-special", stock=7, price=9.5)
        self.assertEqual(seed_menu.seed_menu(stock=25), 0)
        item = storage.get_flavor("arepa-lab-special")
        self.assertEqual((item["stock"], item["price"]), (7, 9.5))
        with self.assertRaises(ValueError):
            seed_menu.seed_menu(-1)


class ProtocolTests(StoreFixture, unittest.IsolatedAsyncioTestCase):
    async def test_tool_annotations_describe_effects_and_retry_safety(self):
        expected = {
            "register_customer": (False, False, False),
            "get_customer": (True, False, True),
            "list_arepas": (True, False, True),
            "order_arepa": (False, True, False),
            "check_order_status": (True, False, True),
            "cancel_order": (False, True, False),
            "voucher_status": (True, False, True),
        }
        async with Client(mcp_server.mcp) as client:
            tools = await client.list_tools()
        self.assertEqual({tool.name for tool in tools}, set(expected))
        for tool in tools:
            with self.subTest(tool=tool.name):
                self.assertIsNotNone(tool.annotations)
                self.assertEqual((
                    tool.annotations.readOnlyHint,
                    tool.annotations.destructiveHint,
                    tool.annotations.idempotentHint,
                ), expected[tool.name])
                self.assertIs(tool.annotations.openWorldHint, False)

    async def test_discovery_prompts_and_real_tool_calls(self):
        async with Client(mcp_server.mcp) as client:
            tools = await client.list_tools()
            self.assertEqual({tool.name for tool in tools}, {
                "register_customer", "get_customer", "list_arepas", "order_arepa",
                "check_order_status", "cancel_order", "voucher_status",
            })
            order = next(tool for tool in tools if tool.name == "order_arepa")
            self.assertEqual(set(order.inputSchema["required"]), {
                "flavor_id", "customer_id", "kind", "voucher_code",
            })
            self.assertEqual({prompt.name for prompt in await client.list_prompts()}, {
                "agent_instructions", "welcome_banner",
            })
            prompt = await client.get_prompt("agent_instructions")
            self.assertIn("2026 SHPE National Convention", prompt.messages[0].content.text)
            self.assertIn("Arepa Lab", (await client.get_prompt("welcome_banner")).messages[0].content.text.title())
            result = await client.call_tool("list_arepas", {})
            self.assertEqual(result.data[0]["name"], "Arepa Lab Special")
            result = await client.call_tool("order_arepa", {
                "flavor_id": "lab-special", "customer_id": self.customer_id,
                "kind": "test", "voucher_code": self.code,
            })
            self.assertTrue(result.data["success"])


class WebTests(StoreFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.client = self.enterContext(TestClient(app))

    def login(self):
        response = self.client.post("/admin/login", data={"username": "admin", "password": "admin"})
        self.assertEqual(response.status_code, 200)
        return re.search(r'name="csrf-token" content="([^"]+)"', response.text).group(1)

    def test_public_contract_and_removed_old_endpoint(self):
        self.assertEqual(self.client.get("/").url.path, "/dashboard")
        response = self.client.get("/arepas")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 2)  # REST retains sold-out menu entries.
        self.assertEqual(self.client.get("/cupcakes").status_code, 404)
        schema = self.client.get("/openapi.json").json()
        self.assertEqual(schema["info"]["title"], "Arepa Lab")
        self.assertIn("/arepas", schema["paths"])

    def test_static_assets_use_same_origin_paths_behind_https_proxy(self):
        self.login()
        for path in ["/dashboard", "/admin/login", "/admin/orders", "/admin/flavors"]:
            with self.subTest(path=path):
                response = self.client.get(path, headers={"X-Forwarded-Proto": "https"})
                self.assertEqual(response.status_code, 200)
                assets = [
                    url for url in re.findall(r'(?:href|src)="([^"]+)"', response.text)
                    if "/static/" in url
                ]
                self.assertTrue(assets)
                self.assertIn("/static/brand.css", assets)
                if path == "/dashboard":
                    self.assertIn("/static/dashboard.js", assets)
                for url in assets:
                    self.assertTrue(url.startswith("/static/"), url)
                    self.assertEqual(self.client.get(url).status_code, 200)

    def test_static_asset_paths_preserve_proxy_mount_prefix(self):
        with TestClient(app, root_path="/workshop") as client:
            response = client.get("/workshop/dashboard")
            self.assertEqual(response.status_code, 200)
            for asset in ["brand.css", "dashboard.js", "arepa-lab.svg", "favicon.svg"]:
                self.assertIn(f'"/workshop/static/{asset}"', response.text)
                self.assertEqual(client.get(f"/workshop/static/{asset}").status_code, 200)

    def test_all_pages_use_dedicated_svg_favicon(self):
        self.login()
        for path in ["/dashboard", "/admin/login", "/admin/orders", "/admin/flavors",
                     "/admin/preparing", "/admin/pickup", "/admin/customers", "/admin/users"]:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                icons = re.findall(r'<link rel="icon"[^>]+>', response.text)
                self.assertEqual(len(icons), 1)
                self.assertIn('href="/static/favicon.svg"', icons[0])
                self.assertIn('type="image/svg+xml"', icons[0])
                self.assertIn('sizes="any"', icons[0])
        icon = self.client.get("/static/favicon.svg")
        self.assertEqual(icon.status_code, 200)
        self.assertIn("image/svg+xml", icon.headers["content-type"])

    def test_rest_registration_and_test_order(self):
        registered = self.client.post("/customers", json={
            "first_name": "Ana", "last_name": "Perez", "city": "Indianapolis",
        })
        self.assertEqual(registered.status_code, 201)
        result = self.client.post("/orders", json={
            "customer_id": registered.json()["customer_id"], "flavor_id": "lab-special",
            "kind": "test", "voucher_code": self.code,
        })
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.json()["status"], "collected")
        self.assertEqual(storage.get_flavor("lab-special")["stock"], 12)

    def test_registration_validation_and_normalization_match_mcp(self):
        fields = {"first_name": " María ", "last_name": " O'Brien ", "city": " Bogotá "}
        mcp_result = mcp_server.register_customer(**fields)
        response = self.client.post("/customers", json=fields)
        self.assertEqual(response.status_code, 201)
        self.assertTrue(mcp_result["success"])
        expected = {
            "first_name": "María", "last_name": "O'Brien", "city": "Bogotá",
            "display_name": "María O'Brien",
        }
        for result in [mcp_result, response.json()]:
            self.assertEqual({key: result[key] for key in expected}, expected)
            customer = storage.get_customer(result["customer_id"])
            self.assertEqual({key: customer[key] for key in expected}, expected)

        original_count = len(storage.list_customers())
        for invalid in [
            {"first_name": "  "}, {"last_name": "x" * 51},
            {"city": "x" * 81}, {"first_name": "<script>"},
        ]:
            with self.subTest(invalid=invalid):
                args = {**fields, **invalid}
                self.assertFalse(mcp_server.register_customer(**args)["success"])
                self.assertEqual(self.client.post("/customers", json=args).status_code, 422)
        self.assertEqual(len(storage.list_customers()), original_count)

    def test_rest_order_normalizes_customer_id_like_mcp(self):
        response = self.client.post("/orders", json={
            "customer_id": f" {self.customer_id.lower()} ",
            "flavor_id": "lab-special", "kind": "real", "voucher_code": self.code,
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["customer_id"], self.customer_id)
        self.assertEqual(storage.get_flavor("lab-special")["stock"], 11)
        self.assertEqual(storage.get_customer(self.customer_id)["real_orders_count"], 1)

    def test_menu_projection_matches_mcp_without_changing_rest_visibility(self):
        storage.update_flavor("lab-special", image_url="https://example.com/arepa.png")
        rest_menu = self.client.get("/arepas").json()
        mcp_menu = mcp_server.list_arepas()
        self.assertEqual(len(rest_menu), 2)
        self.assertEqual(len(mcp_menu), 1)
        rest_item = next(item for item in rest_menu if item["id"] == mcp_menu[0]["id"])
        self.assertEqual(rest_item.pop("image_url"), "https://example.com/arepa.png")
        self.assertEqual(rest_item, mcp_menu[0])

    def test_order_validation_matches_mcp_without_side_effects(self):
        args = {"customer_id": self.customer_id, "flavor_id": "lab-special",
                "kind": "real", "voucher_code": self.code}
        for invalid in [
            {"customer_id": "invalid"}, {"kind": "invalid"},
            {"flavor_id": ""}, {"flavor_id": "x" * 101},
            {"voucher_code": ""}, {"voucher_code": "x" * 13},
        ]:
            with self.subTest(invalid=invalid):
                request = {**args, **invalid}
                self.assertFalse(mcp_server.order_arepa(**request)["success"])
                self.assertEqual(self.client.post("/orders", json=request).status_code, 422)
        self.assertEqual(storage.list_orders(), [])
        self.assertEqual(storage.get_flavor("lab-special")["stock"], 12)

    def test_order_business_errors_match_mcp(self):
        args = {"customer_id": self.customer_id, "flavor_id": "lab-special",
                "kind": "real", "voucher_code": self.code}
        for invalid, status in [
            ({"customer_id": "ZZZZZZZZ"}, 404),
            ({"voucher_code": "WRONG"}, 403),
            ({"voucher_code": "ZZZZZZ" if self.code != "ZZZZZZ" else "YYYYYY"}, 403),
            ({"flavor_id": "missing"}, 404),
            ({"flavor_id": "queso"}, 409),
        ]:
            with self.subTest(invalid=invalid):
                request = {**args, **invalid}
                mcp_result = mcp_server.order_arepa(**request)
                response = self.client.post("/orders", json=request)
                self.assertFalse(mcp_result["success"])
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json()["detail"], mcp_result["message"])
        self.assertIsNone(storage.get_active_order_for_customer(self.customer_id, "real"))
        self.assertEqual(storage.get_customer(self.customer_id)["real_orders_count"], 0)
        self.assertEqual(storage.get_flavor("lab-special")["stock"], 12)

    def test_lifetime_and_active_order_errors_match_mcp(self):
        self.order()
        args = {"customer_id": self.customer_id, "flavor_id": "lab-special",
                "kind": "real", "voucher_code": self.code}
        for count in [1, 0]:
            with self.subTest(real_orders_count=count):
                storage_table = self.service.get_table_client(storage.TABLE_CUSTOMERS)
                storage_table.rows[("customer", self.customer_id)]["real_orders_count"] = count
                mcp_result = mcp_server.order_arepa(**args)
                response = self.client.post("/orders", json=args)
                self.assertFalse(mcp_result["success"])
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["detail"], mcp_result["message"])
        self.assertEqual(len(storage.list_orders()), 1)
        self.assertEqual(storage.get_flavor("lab-special")["stock"], 11)

    def test_voucher_throttling_is_shared_between_transports(self):
        args = {"customer_id": self.customer_id, "flavor_id": "lab-special",
                "kind": "test", "voucher_code": self.wrong_code}
        for _ in range(5):
            self.assertFalse(mcp_server.order_arepa(**args)["success"])
            self.assertEqual(self.client.post("/orders", json=args).status_code, 403)
        args["voucher_code"] = self.code
        mcp_result = mcp_server.order_arepa(**args)
        response = self.client.post("/orders", json=args)
        self.assertFalse(mcp_result["success"])
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["detail"], mcp_result["message"])
        self.assertEqual(response.headers["Retry-After"], str(mcp_result["retry_after_s"]))
        self.assertEqual(storage.list_orders(), [])

    def test_valid_voucher_resets_failures_even_when_order_is_rejected(self):
        for transport in ["mcp", "rest"]:
            with self.subTest(transport=transport):
                rate_limit.record_failure(self.customer_id)
                args = {"customer_id": self.customer_id, "flavor_id": "missing",
                        "kind": "real", "voucher_code": self.code}
                if transport == "mcp":
                    self.assertFalse(mcp_server.order_arepa(**args)["success"])
                else:
                    self.assertEqual(self.client.post("/orders", json=args).status_code, 404)
                self.assertNotIn(self.customer_id, rate_limit._attempts)

    def test_stock_decrement_failure_cancels_order_in_both_transports(self):
        args = {"customer_id": self.customer_id, "flavor_id": "lab-special",
                "kind": "real", "voucher_code": self.code}
        with patch.object(storage, "decrement_stock", return_value=False):
            mcp_result = mcp_server.order_arepa(**args)
            response = self.client.post("/orders", json=args)
        self.assertFalse(mcp_result["success"])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], mcp_result["message"])
        self.assertEqual([row["status"] for row in storage.list_orders()],
                         ["cancelled", "cancelled"])
        self.assertEqual(storage.get_flavor("lab-special")["stock"], 12)
        self.assertEqual(storage.get_customer(self.customer_id)["real_orders_count"], 0)

    def test_storage_errors_are_not_reported_as_business_rejections(self):
        args = {"customer_id": self.customer_id, "flavor_id": "lab-special",
                "kind": "real", "voucher_code": self.code}
        for transport in ["mcp", "rest"]:
            with self.subTest(transport=transport), patch.object(
                storage, "create_order", side_effect=ServiceRequestError("Storage unavailable"),
            ):
                with self.assertRaises(ServiceRequestError):
                    if transport == "mcp":
                        mcp_server.order_arepa(**args)
                    else:
                        self.client.post("/orders", json=args)
        self.assertEqual(storage.list_orders(), [])
        self.assertEqual(storage.get_flavor("lab-special")["stock"], 12)

    def test_stock_write_exceptions_cancel_provisional_orders_without_refunding(self):
        decrement_stock = storage.decrement_stock
        for transport in ["mcp", "rest"]:
            for committed in [False, True]:
                with self.subTest(transport=transport, committed=committed):
                    customer_id = storage.create_customer("Test", "Customer", "Indianapolis")
                    args = {"customer_id": customer_id, "flavor_id": "lab-special",
                            "kind": "real", "voucher_code": self.code}
                    stock_before = storage.get_flavor("lab-special")["stock"]
                    error = ServiceRequestError("Stock response unavailable")

                    def fail_stock_write(flavor_id):
                        if committed:
                            decrement_stock(flavor_id)
                        raise error

                    with (
                        patch.object(storage, "decrement_stock", side_effect=fail_stock_write),
                        patch.object(storage, "increment_stock") as refund,
                        patch.object(storage, "increment_real_orders") as allowance,
                        self.assertLogs("backend.ordering", level="ERROR") as logs,
                        self.assertRaises(ServiceRequestError) as raised,
                    ):
                        if transport == "mcp":
                            mcp_server.order_arepa(**args)
                        else:
                            self.client.post("/orders", json=args)
                    self.assertIs(raised.exception, error)
                    refund.assert_not_called()
                    allowance.assert_not_called()
                    self.assertIn("staff must reconcile inventory", logs.output[0])
                    self.assertIsNone(storage.get_active_order_for_customer(customer_id, "real"))
                    orders = [row for row in storage.list_orders()
                              if row["customer_id"] == customer_id]
                    self.assertEqual([row["status"] for row in orders], ["cancelled"])
                    self.assertEqual(storage.get_flavor("lab-special")["stock"],
                                     stock_before - int(committed))
                    self.assertEqual(storage.get_customer(customer_id)["real_orders_count"], 0)
                    if not committed:
                        if transport == "mcp":
                            self.assertTrue(mcp_server.order_arepa(**args)["success"])
                        else:
                            self.assertEqual(self.client.post("/orders", json=args).status_code, 201)

    def test_stock_write_and_cleanup_errors_are_both_preserved(self):
        for transport in ["mcp", "rest"]:
            with self.subTest(transport=transport):
                customer_id = storage.create_customer("Test", "Customer", "Indianapolis")
                args = {"customer_id": customer_id, "flavor_id": "lab-special",
                        "kind": "real", "voucher_code": self.code}
                stock_error = ServiceRequestError("Stock response unavailable")
                cleanup_error = ServiceRequestError("Cleanup unavailable")
                with (
                    patch.object(storage, "decrement_stock", side_effect=stock_error),
                    patch.object(storage, "update_order_status", side_effect=cleanup_error),
                    self.assertLogs("backend.ordering", level="ERROR") as logs,
                    self.assertRaises(ServiceRequestError) as raised,
                ):
                    if transport == "mcp":
                        mcp_server.order_arepa(**args)
                    else:
                        self.client.post("/orders", json=args)
                self.assertIs(raised.exception, cleanup_error)
                self.assertIs(cleanup_error.__context__, stock_error)
                self.assertEqual(len(logs.output), 2)
                self.assertIn("uncertain outcome", logs.output[0])
                self.assertIn("Could not cancel an order", logs.output[1])

    def test_failed_sold_out_cleanup_is_logged_and_propagated(self):
        args = {"customer_id": self.customer_id, "flavor_id": "lab-special",
                "kind": "real", "voucher_code": self.code}
        for transport in ["mcp", "rest"]:
            args["customer_id"] = storage.create_customer("Test", "Customer", "Indianapolis")
            with (
                self.subTest(transport=transport),
                patch.object(storage, "decrement_stock", return_value=False),
                patch.object(storage, "update_order_status",
                             side_effect=ServiceRequestError("Cleanup unavailable")),
                self.assertLogs("backend.ordering", level="ERROR") as logs,
                self.assertRaises(ServiceRequestError),
            ):
                if transport == "mcp":
                    mcp_server.order_arepa(**args)
                else:
                    self.client.post("/orders", json=args)
            self.assertIn("Could not cancel an order", logs.output[0])

    def test_rest_validation_voucher_and_lifetime_cap(self):
        args = {"customer_id": self.customer_id, "flavor_id": "lab-special",
                "kind": "real", "voucher_code": self.code}
        self.assertEqual(self.client.post("/orders", json={**args, "kind": "invalid"}).status_code, 422)
        self.assertEqual(self.client.post("/orders", json={**args, "voucher_code": "WRONG"}).status_code, 403)
        self.assertEqual(self.client.post("/orders", json=args).status_code, 201)
        self.assertEqual(self.client.post("/orders", json=args).status_code, 409)

    def test_dashboard_renders_fallbacks_and_local_assets(self):
        text = self.client.get("/dashboard").text
        for expected in ["Arepa Lab", "2026 SHPE National Convention", "Order voucher",
                         "Ask your facilitator", "Try your first order", "arepa-lab.svg"]:
            self.assertIn(expected, text)
        self.assertNotIn("Training key", text)
        self.assertNotIn("cdn.tailwindcss", text)
        for asset in ["arepa-lab.svg", "wood-grain.svg", "brand.css", "dashboard.js"]:
            self.assertEqual(self.client.get(f"/static/{asset}").status_code, 200)

    def test_configured_workshop_details_are_escaped(self):
        with patch.dict(os.environ, {
            "WORKSHOP_URL": "https://example.com/shpe?x=1&y=2",
            "WORKSHOP_TRAINING_KEY": "<b>Arepas</b>",
        }):
            text = self.client.get("/dashboard").text
        self.assertIn("https://example.com/shpe?x=1&amp;y=2", text)
        self.assertIn("&lt;b&gt;Arepas&lt;/b&gt;", text)
        self.assertIn("not the order voucher", text)

    def test_dashboard_data_preserves_order_lanes_and_private_name_format(self):
        self.order("test")
        real = self.order()
        storage.update_order_status(real["order_id"], "ready")
        response = self.client.get("/dashboard/data").json()
        self.assertEqual(len(response["tests"]), 1)
        self.assertEqual(response["totals"]["ready"], 1)
        self.assertEqual(response["ready"][0]["customer_name"], "Ana P.")
        self.assertNotIn("customer_id", response["ready"][0])

    def test_admin_auth_and_csrf_are_preserved(self):
        self.assertEqual(self.client.get("/admin/orders", follow_redirects=False).status_code, 303)
        self.login()
        self.assertEqual(self.client.post("/admin/reset").status_code, 403)
        self.assertEqual(self.client.post("/admin/logout").status_code, 403)

    def test_login_controls_are_inside_the_branded_panel(self):
        for response in [
            self.client.get("/admin/login"),
            self.client.post("/admin/login", data={"username": "admin", "password": "wrong"}),
        ]:
            with self.subTest(status=response.status_code):
                text = response.text
                self.assertLess(text.index('<main class="login-card">'), text.index("<form"))
                self.assertLess(text.index("</form>"), text.index("</main>"))

    def test_all_admin_pages_use_new_brand(self):
        self.login()
        for path in ["/admin/orders", "/admin/flavors", "/admin/preparing",
                     "/admin/pickup", "/admin/customers", "/admin/users", "/admin/login"]:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn("Arepa Lab", response.text)
                self.assertIn("arepa-lab.svg", response.text)
                self.assertNotIn("Sparkles", response.text)
                self.assertNotIn("€", response.text)

    def test_admin_menu_crud_and_order_transitions(self):
        token = self.login()
        response = self.client.post("/admin/flavors", data={
            "csrf_token": token, "name": "Arepa de Queso Nueva", "description": "Cheese",
            "price": "0", "stock": "5",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(storage.get_flavor("arepa-de-queso-nueva")["stock"], 5)
        result = self.order()
        headers = {"X-CSRF-Token": token}
        path = f"/admin/orders/{result['order_id']}"
        self.assertEqual(self.client.post(f"{path}/ready", headers=headers).status_code, 200)
        self.assertEqual(self.client.post(f"{path}/ready", headers=headers).status_code, 200)
        self.assertEqual(self.client.post(f"{path}/collected", headers=headers).status_code, 200)
        self.assertEqual(self.client.post(f"{path}/ready", headers=headers).status_code, 409)
        self.assertEqual(self.client.post("/admin/flavors/arepa-de-queso-nueva/delete",
                                         data={"csrf_token": token}).status_code, 200)
        self.assertIsNone(storage.get_flavor("arepa-de-queso-nueva"))


class ConfigurationTests(unittest.TestCase):
    def test_invalid_workshop_urls_are_rejected(self):
        for url in ["javascript:alert(1)", "//example.com", "https://user:password@example.com", "not-a-url"]:
            with self.subTest(url=url), patch.dict(os.environ, {"WORKSHOP_URL": url}):
                with self.assertRaises(ValueError):
                    branding.workshop_context()

    def test_voucher_current_and_previous_windows(self):
        with patch.dict(os.environ, {"VOUCHER_SECRET": "test-only"}), patch.object(voucher.time, "time", return_value=3600):
            self.assertTrue(voucher.is_valid_voucher(voucher._code_for_window(20)))
            self.assertTrue(voucher.is_valid_voucher(voucher._code_for_window(19)))
            self.assertFalse(voucher.is_valid_voucher(voucher._code_for_window(18)))
            self.assertEqual(voucher.seconds_until_rotation(), 180)
            self.assertNotIn("voucher", mcp_server.voucher_status())

    def test_svg_assets_are_well_formed(self):
        root = Path(__file__).resolve().parents[1] / "backend" / "static"
        for path in root.glob("*.svg"):
            ET.parse(path)

    def test_favicon_has_a_tightly_cropped_square_canvas(self):
        path = Path(__file__).resolve().parents[1] / "backend" / "static" / "favicon.svg"
        icon = ET.parse(path).getroot()
        _, _, width, height = map(float, icon.attrib["viewBox"].split())
        self.assertEqual(width, height)
        self.assertLessEqual(width, 220)


if __name__ == "__main__":
    unittest.main()
