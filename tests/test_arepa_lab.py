import copy
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
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
        self.enterContext(patch.dict(rate_limit._attempts, {}, clear=True))
        public._dash_cache.update(at=0.0, data=None)
        storage.ensure_tables_and_seed()
        storage.create_flavor("lab-special", "Arepa Lab Special", "Demo filling", 0.0, 12)
        storage.create_flavor("queso", "Arepa de Queso", "Demo cheese filling", 0.0, 0)
        self.customer_id = storage.create_customer("Ana", "Perez", "Indianapolis")
        self.code = voucher.current_voucher()

    def order(self, kind="real", **kwargs):
        args = dict(flavor_id="lab-special", customer_id=self.customer_id, kind=kind, voucher_code=self.code)
        args.update(kwargs)
        return mcp_server.order_arepa(**args)


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
            self.assertFalse(self.order("test", voucher_code="WRONG")["success"])
        self.assertIn("Too many", self.order("test")["message"])
        self.assertEqual(storage.list_orders(), [])

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
    async def test_discovery_prompts_and_real_tool_calls(self):
        async with Client(mcp_server.mcp) as client:
            tools = await client.list_tools()
            self.assertEqual({tool.name for tool in tools}, {
                "register_customer", "list_arepas", "order_arepa",
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


if __name__ == "__main__":
    unittest.main()
