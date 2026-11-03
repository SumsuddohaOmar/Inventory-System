"""Behavior tests with real SQLite files isolated from the user's inventory.

Run from the project root: python -m unittest discover -s tests -v
TestClient is a context manager so FastAPI's startup migration really runs.
"""

from pathlib import Path
from contextlib import contextmanager
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient
from app.main import create_app
from app import database


@contextmanager
def signed_in(application):
    """Exercise real account setup/login before protected HTTP integration routes."""
    with TestClient(application) as client:
        state=client.get("/api/session").json()
        route="/api/session" if state["configured"] else "/api/setup"
        response=client.post(route,json={"username":"integration-owner","password":"test-only-passphrase"})
        if response.status_code!=200:raise AssertionError(response.text)
        yield client


class ProductTests(unittest.TestCase):
    """Verify the milestone's observable behavior, including restart durability."""

    def setUp(self):
        """Allocate a new database location for each independent test."""
        self.folder = TemporaryDirectory(dir=Path.cwd())
        self.path = Path(self.folder.name) / "nested" / "test.sqlite3"

    def tearDown(self):
        """Remove only this test's temporary folder after connections close."""
        self.folder.cleanup()

    def test_create_read_and_restart(self):
        """A product survives a new app instance, proving this is not RAM storage."""
        with signed_in(create_app(self.path)) as client:
            self.assertEqual(client.get("/api/products").json(), [])
            response = client.post("/api/products", json={"name": " Sugar ", "unit": " KG ", "category_id": "dry-foods"})
            self.assertEqual(response.status_code, 201)
            product = response.json()
            self.assertEqual(product["name"], "Sugar")
            self.assertEqual(product["unit"], "KG")
            self.assertEqual(product["category_id"], "dry-foods")
            self.assertEqual(client.get(f'/api/products/{product["id"]}').json(), product)
        with signed_in(create_app(self.path)) as restarted:
            self.assertEqual(restarted.get("/api/products").json(), [product])

    def test_invalid_input_never_writes(self):
        """Whitespace, missing fields, oversize values and extra stock are rejected."""
        with signed_in(create_app(self.path)) as client:
            for payload in ({"name": "   ", "unit": "KG"}, {"name": "Sugar"},
                            {"name": "x" * 121, "unit": "KG"},
                            {"name": "Sugar", "unit": "x" * 31},
                            {"name": "Sugar", "unit": "KG", "stock": 10}):
                with self.subTest(payload=payload):
                    payload["category_id"] = "dry-foods"
                    self.assertEqual(client.post("/api/products", json=payload).status_code, 422)
            self.assertEqual(client.get("/api/products").json(), [])

    def test_missing_product_and_bad_id(self):
        """Clients can distinguish an absent product from a malformed UUID."""
        with signed_in(create_app(self.path)) as client:
            self.assertEqual(client.get(f"/api/products/{uuid4()}").status_code, 404)
            self.assertEqual(client.get("/api/products/not-an-id").status_code, 422)

    def test_special_characters_are_data(self):
        """SQL-looking and HTML-looking names must not execute or destroy storage."""
        name = "茶 <script>alert(1)</script> '; DROP TABLE products; --"
        with signed_in(create_app(self.path)) as client:
            self.assertEqual(client.post("/api/products", json={"name": name, "unit": "Piece", "category_id": "dry-foods"}).status_code, 201)
            self.assertEqual(client.get("/api/products").json()[0]["name"], name)

    def test_ui_and_local_assets(self):
        """The browser entry point and its own scripts/styles are served locally."""
        with signed_in(create_app(self.path)) as client:
            response = client.get("/")
            self.assertEqual(response.status_code, 200)
            self.assertIn('for="name"', response.text)
            self.assertIn("Content-Security-Policy", response.headers)
            for asset in ("app.js", "styles.css"):
                self.assertEqual(client.get(f"/static/{asset}").status_code, 200)
            self.assertEqual(client.get("/", headers={"host": "untrusted.example"}).status_code, 400)

    def test_category_routes(self):
        """Create, filter and reassign through HTTP without changing product identity."""
        with signed_in(create_app(self.path)) as client:
            self.assertEqual(len(client.get("/api/categories").json()), 5)
            product = client.post("/api/products", json={
                "name": "Rice", "unit": "KG", "category_id": "dry-foods"
            }).json()
            self.assertEqual(client.get("/api/products?category_id=chemicals").json(), [])
            changed = client.patch(f'/api/products/{product["id"]}/category', json={
                "category_id": "chemicals"
            })
            self.assertEqual(changed.status_code, 200)
            self.assertEqual(changed.json()["id"], product["id"])
            self.assertEqual(len(client.get("/api/products?category_id=chemicals").json()), 1)
            self.assertEqual(client.post("/api/products", json={
                "name": "Rice", "unit": "KG", "category_id": "missing"
            }).status_code, 422)
            self.assertEqual(client.post("/api/products", json={
                "name": "Rice", "unit": "KG"
            }).status_code, 422)
            self.assertEqual(client.patch(f"/api/products/{uuid4()}/category", json={
                "category_id": "dry-foods"
            }).status_code, 404)

    def test_future_database_is_not_downgraded(self):
        """Opening a future schema fails safely and preserves its version."""
        database.initialize(self.path)
        with database.connect(self.path) as connection:
            connection.execute("PRAGMA user_version = 99")
        with self.assertRaises(RuntimeError):
            database.initialize(self.path)
        with database.connect(self.path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 99)


if __name__ == "__main__":
    unittest.main()
