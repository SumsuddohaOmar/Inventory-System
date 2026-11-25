"""Check the permission boundary between employees and inventory users."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

import httpx

from app import accounts, database, stock
from app.main import create_app


class MaterialCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_employee_can_read_catalog_without_inventory_access(self):
        with TemporaryDirectory(dir=Path.cwd()) as folder:
            path = Path(folder) / "inventory.sqlite3"
            database.initialize(path)
            for username, permissions in (
                ("employee", ("mrf_catalog", "mrf_submit")), ("reader", ("read",)),
                ("storekeeper", ("read", "mrf_review")),
                ("manager", ("read", "mrf_review", "mrf_approve")),
                ("issuer", ("read", "mrf_review", "issue")),
            ):
                accounts.create_user(path, username, "testing-only-password", permissions)
            visible_id = str(uuid4())
            archived_id = str(uuid4())
            for product_id in (visible_id, archived_id):
                database.insert_product(path, {
                    "id": product_id, "name": "Rice", "unit": "kg", "category_id": "dry-foods",
                    "created_at": "2026-11-24T00:00:00+00:00",
                })
            with database.connect(path) as connection:
                connection.execute("UPDATE products SET archived=1 WHERE id=?", (archived_id,))
            transport = httpx.ASGITransport(app=create_app(path))
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
                catalog = "/api/mrf/catalog"
                self.assertEqual((await client.get(catalog)).status_code, 401)
                credentials = {"username": "employee", "password": "testing-only-password"}
                session = await client.post("/api/session", json=credentials)
                self.assertEqual(session.status_code, 200)
                response = await client.get(catalog)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["products"], [
                    {"id": visible_id, "name": "Rice", "unit": "kg", "category_id": "dry-foods"},
                ])
                self.assertEqual((await client.get("/api/products")).status_code, 403)
                self.assertEqual((await client.get(f"/api/products/{visible_id}/stock")).status_code, 403)
                endpoint = "/api/mrf/requests"
                headers = {"X-Inventory-Owner": session.json()["id"]}
                values = {
                    "id": str(uuid4()), "requester_name": "Sample Employee",
                    "employee_id": "0007", "shift": "night", "work_date": "2026-11-25",
                    "requester_position": "Shift supervisor",
                    "items": [{"product_id": visible_id, "quantity": "1.25"}],
                }
                self.assertEqual((await client.post(endpoint, json=values)).status_code, 409)
                without_position = {key: value for key, value in values.items() if key != "requester_position"}
                self.assertEqual((await client.post(endpoint, json=without_position, headers=headers)).status_code, 422)
                bad = {**values, "items": [*values["items"], {"product_id": archived_id, "quantity": "1"}]}
                self.assertEqual((await client.post(endpoint, json=bad, headers=headers)).status_code, 422)
                response = await client.post(endpoint, json=values, headers=headers)
                self.assertEqual(response.status_code, 201)
                self.assertEqual((await client.get(endpoint, headers=headers)).status_code, 403)
                self.assertEqual((await client.post(endpoint, json=values, headers=headers)).json(), response.json())
                with database.connect(path) as connection:
                    row = connection.execute("SELECT * FROM material_requests").fetchone()
                    self.assertEqual((row["employee_id"], row["work_date"], row["account_id"]),
                                     ("0007", "2026-11-25", session.json()["id"]))
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM material_requests").fetchone()[0], 1)
                    self.assertEqual(connection.execute("SELECT quantity_minor FROM material_request_lines").fetchone()[0], 1250)
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM stock_entries").fetchone()[0], 0)
                client.cookies.clear()
                credentials["username"] = "reader"
                session = await client.post("/api/session", json=credentials)
                self.assertEqual(session.status_code, 200)
                self.assertEqual((await client.get(catalog)).status_code, 403)
                self.assertEqual((await client.get("/api/products")).status_code, 200)
                self.assertEqual((await client.post(endpoint, json=values, headers={
                    "X-Inventory-Owner": session.json()["id"],
                })).status_code, 403)
                client.cookies.clear()
                credentials["username"] = "storekeeper"
                session = await client.post("/api/session", json=credentials)
                review_headers = {"X-Inventory-Owner": session.json()["id"]}
                self.assertEqual((await client.get(endpoint)).status_code, 409)
                listing = await client.get(endpoint, headers=review_headers)
                self.assertEqual(listing.status_code, 200)
                self.assertEqual(listing.json()["requests"][0]["employee_id"], "0007")
                self.assertEqual(listing.json()["requests"][0]["requester_position"], "Shift supervisor")
                self.assertIsNone(listing.json()["requests"][0]["approved_at"])
                self.assertEqual(listing.json()["requests"][0]["items"], [
                    {"product": "Rice", "unit": "kg", "quantity": "1.250", "product_id": visible_id,
                     "issued": "0.000", "pending": "1.250"},
                ])
                approval_url = f"{endpoint}/{values['id']}/approval"
                self.assertEqual((await client.post(approval_url, headers=review_headers)).status_code, 403)
                client.cookies.clear()
                credentials["username"] = "manager"
                session = await client.post("/api/session", json=credentials)
                manager_headers = {"X-Inventory-Owner": session.json()["id"]}
                approval = await client.post(approval_url, headers=manager_headers)
                self.assertEqual(approval.status_code, 200)
                self.assertEqual(approval.json()["approver_name"], "manager")
                self.assertTrue(approval.json()["approved_at"])
                retry = await client.post(approval_url, headers=manager_headers)
                self.assertEqual(retry.json(), approval.json())
                updated = await client.get(endpoint, headers=manager_headers)
                self.assertEqual(updated.json()["requests"][0]["approved_at"], approval.json()["approved_at"])
                with database.connect(path) as connection:
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM material_request_approvals").fetchone()[0], 1)
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM stock_entries").fetchone()[0], 0)
                issuance_url = f"{endpoint}/{values['id']}/issues"
                issuance = {
                    "id": str(uuid4()), "stock_date": "2026-01-01", "work_date": "2026-11-26",
                    "items": [{"product_id": visible_id, "quantity": "0.75"}],
                }
                self.assertEqual((await client.post(issuance_url, json=issuance, headers=manager_headers)).status_code, 403)
                stock.save_entry(path, visible_id, {
                    "id": str(uuid4()), "kind": "opening", "effective_date": "2026-01-01",
                    "quantity": "10", "notes": "",
                })
                client.cookies.clear()
                credentials["username"] = "issuer"
                session = await client.post("/api/session", json=credentials)
                issue_headers = {"X-Inventory-Owner": session.json()["id"]}
                bad_issue = {**issuance, "items": [*issuance["items"], {"product_id": archived_id, "quantity": "1"}]}
                self.assertEqual((await client.post(issuance_url, json=bad_issue, headers=issue_headers)).status_code, 422)
                saved = await client.post(issuance_url, json=issuance, headers=issue_headers)
                self.assertEqual(saved.status_code, 201)
                replay = await client.post(issuance_url, json=issuance, headers=issue_headers)
                self.assertEqual(replay.json(), saved.json())
                updated = (await client.get(endpoint, headers=issue_headers)).json()["requests"][0]
                self.assertEqual(updated["items"][0]["issued"], "0.750")
                self.assertEqual(updated["items"][0]["pending"], "0.500")
                self.assertEqual((updated["issues"][0]["stock_date"], updated["issues"][0]["mrf_date"]),
                                 ("2026-01-01", "2026-11-26"))
                excess = {**issuance, "id": str(uuid4())}
                self.assertEqual((await client.post(issuance_url, json=excess, headers=issue_headers)).status_code, 422)
                excess["excess_reason"] = "Additional quantity requested at handover"
                self.assertEqual((await client.post(issuance_url, json=excess, headers=issue_headers)).status_code, 201)
                self.assertEqual(stock.get_stock(path, visible_id)["balance"], "8.500")
