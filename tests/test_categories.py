"""Schema-upgrade and category behavior tests using real, disposable SQLite files.

These tests do not need a server or sockets. In particular, they prove an old
database can be upgraded without assigning a guessed category or losing data.
"""

from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import ValidationError
from app import database
from app.main import CategoryAssignment, ProductInput, create_app


class CategoryTests(unittest.TestCase):
    """Check persistent results, migration safeguards and route boundary rules."""

    def setUp(self):
        """Place temporary files inside the workspace for restricted Windows runs."""
        # Passing dir avoids relying on Windows' redirected process Temp folder.
        self.folder = TemporaryDirectory(dir=Path.cwd())
        self.path = Path(self.folder.name) / "inventory.sqlite3"

    def tearDown(self):
        """Only discard this test's disposable files after every connection closes."""
        self.folder.cleanup()

    def record(self, category_id="dry-foods"):
        """Return a distinct product fixture with a stable timestamp."""
        return dict(id=str(uuid4()), name="Rice", unit="KG",
                    created_at="2026-11-02T10:00:00+00:00", category_id=category_id)

    def legacy_database(self):
        """Create the original schema and one product, exactly as Phase 1 did."""
        product = self.record()
        product.pop("category_id")
        with database.connect(self.path) as connection:
            connection.execute((database.MIGRATIONS / "001_products.sql").read_text())
            connection.execute("PRAGMA user_version = 1")
            connection.execute("INSERT INTO products VALUES (?, ?, ?, ?)", tuple(product.values()))
        return product

    def test_five_categories_seed_once(self):
        """A fresh install and repeated startup retain exactly the five categories."""
        database.initialize(self.path)
        original = database.list_categories(self.path)
        database.initialize(self.path)
        self.assertEqual(database.list_categories(self.path), original)
        self.assertEqual([(row["id"], row["name"]) for row in original], list(database.INITIAL_CATEGORIES))
        self.assertEqual(len(original), 5)

    def test_old_products_survive_and_backup_is_recoverable(self):
        """Upgrade preserves every old field, leaving category explicitly unassigned."""
        old = self.legacy_database()
        database.initialize(self.path)
        self.assertEqual(database.find_product(self.path, old["id"]), {**old, "category_id": None, "precision": 3})
        copies = list((self.path.parent / "backups").glob("*.sqlite3"))
        self.assertEqual(len(copies), 1)
        with database.connect(copies[0]) as backup:
            self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(dict(backup.execute("SELECT * FROM products").fetchone()), old)
        database.initialize(self.path)
        self.assertEqual(len(list((self.path.parent / "backups").glob("*.sqlite3"))), 1)

    def test_assignment_keeps_identity_and_survives_restart(self):
        """A legacy item can be categorized without creating a duplicate product."""
        old = self.legacy_database()
        database.initialize(self.path)
        changed = database.assign_category(self.path, old["id"], "dry-foods")
        self.assertEqual(changed, {**old, "category_id": "dry-foods", "precision": 3})
        database.initialize(self.path)
        self.assertEqual(database.list_products(self.path), [changed])
        self.assertEqual(database.list_products(self.path, "unassigned"), [])

    def test_failed_upgrade_rolls_back_schema_and_products(self):
        """A failure halfway through seeding must leave the old database usable."""
        old = self.legacy_database()
        # Duplicate category IDs force a real constraint error after CREATE TABLE.
        with patch.object(database, "INITIAL_CATEGORIES", (("same", "One"), ("same", "Two"))):
            with self.assertRaises(sqlite3.IntegrityError):
                database.initialize(self.path)
        with database.connect(self.path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(dict(connection.execute("SELECT * FROM products").fetchone()), old)
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='categories'").fetchone())

    def test_filters_separate_categories_and_unassigned(self):
        """All, category and unassigned views return only their intended products."""
        old = self.legacy_database()
        database.initialize(self.path)
        rice = database.insert_product(self.path, self.record())
        cleaner = database.insert_product(self.path, self.record("chemicals"))
        self.assertEqual(database.list_products(self.path, "dry-foods"), [rice])
        self.assertEqual(database.list_products(self.path, "chemicals"), [cleaner])
        self.assertEqual(database.list_products(self.path, "unassigned"), [{**old, "category_id": None, "precision": 3}])
        self.assertEqual(len(database.list_products(self.path)), 3)

    def test_foreign_keys_block_invalid_writes(self):
        """Invalid assignments fail without adding or corrupting product records."""
        database.initialize(self.path)
        rice = database.insert_product(self.path, self.record())
        with self.assertRaises(sqlite3.IntegrityError):
            database.insert_product(self.path, self.record("not-a-category"))
        with self.assertRaises(sqlite3.IntegrityError):
            database.assign_category(self.path, rice["id"], "not-a-category")
        self.assertEqual(database.list_products(self.path), [rice])

    def test_new_products_require_category(self):
        """Pydantic rejects incomplete new entries and accepts a valid selection."""
        with self.assertRaises(ValidationError):
            ProductInput(name="Rice", unit="KG")
        with self.assertRaises(ValidationError):
            CategoryAssignment(category_id="   ")
        self.assertEqual(ProductInput(name=" Rice ", unit="KG", category_id="dry-foods").name, "Rice")

    def test_api_handlers_reject_unknown_category_and_missing_product(self):
        """Exercise route error rules directly without needing an HTTP socket.

        Full HTTP integration remains in test_products.py; this focused unit
        check still exercises the actual route functions and their persistence.
        """
        database.initialize(self.path)
        app = create_app(self.path)
        create = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/api/products" and "POST" in route.methods)
        assign = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/api/products/{product_id}/category")
        with self.assertRaises(HTTPException) as unknown:
            create(ProductInput(name="Rice", unit="KG", category_id="missing"))
        self.assertEqual(unknown.exception.status_code, 422)
        rice = create(ProductInput(name="Rice", unit="KG", category_id="dry-foods"))
        changed = assign(UUID(rice["id"]), CategoryAssignment(category_id="chemicals"))
        self.assertEqual(changed["id"], rice["id"])
        with self.assertRaises(HTTPException) as missing:
            assign(uuid4(), CategoryAssignment(category_id="dry-foods"))
        self.assertEqual(missing.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
