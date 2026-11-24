"""Check the permission boundary between employees and inventory users."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

import httpx

from app import accounts, database
from app.main import create_app


class MaterialCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_employee_can_read_catalog_without_inventory_access(self):
        with TemporaryDirectory(dir=Path.cwd()) as folder:
            path = Path(folder) / "inventory.sqlite3"
            database.initialize(path)
            for username, permissions in (("employee", ("mrf_catalog",)), ("reader", ("read",))):
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
                self.assertEqual((await client.post("/api/session", json=credentials)).status_code, 200)
                response = await client.get(catalog)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["products"], [
                    {"id": visible_id, "name": "Rice", "unit": "kg", "category_id": "dry-foods"},
                ])
                self.assertEqual((await client.get("/api/products")).status_code, 403)
                self.assertEqual((await client.get(f"/api/products/{visible_id}/stock")).status_code, 403)
                client.cookies.clear()
                credentials["username"] = "reader"
                self.assertEqual((await client.post("/api/session", json=credentials)).status_code, 200)
                self.assertEqual((await client.get(catalog)).status_code, 403)
                self.assertEqual((await client.get("/api/products")).status_code, 200)
