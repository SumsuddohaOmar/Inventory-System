"""Real SQLite tests for exact stock calculations, upgrades and competing issues.

No live user data, HTTP sockets, or operating-system processes are involved.
Business dates are fixed so these tests keep working after the calendar changes.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from pydantic import ValidationError
from app import database, stock
from app.main import StockInput, create_app


class StockTests(unittest.TestCase):
    """Exercise observable balances and ledger preservation, including failure cases."""

    def setUp(self):
        """Use an isolated database and a deterministic company date."""
        self.folder = TemporaryDirectory(dir=Path.cwd())
        self.path = Path(self.folder.name) / "inventory.sqlite3"
        self.clock = patch.object(stock, "today", return_value="2026-11-02")
        self.clock.start()
        database.initialize(self.path)
        self.product = database.insert_product(self.path, {
            "id": str(uuid4()), "name": "Rice", "unit": "KG", "category_id": "dry-foods",
            "created_at": "2026-11-02T10:00:00+00:00", "precision": 3,
        })

    def tearDown(self):
        """Close all connections in the tested code, then remove this fixture only."""
        self.clock.stop()
        self.folder.cleanup()

    def entry(self, kind, quantity, day="2026-11-01", **overrides):
        """Build a new operation; reusing the returned dictionary simulates retries."""
        return dict(id=str(uuid4()), kind=kind, quantity=quantity, effective_date=day,
                    notes="", **overrides)

    def save(self, kind, quantity, day="2026-11-01"):
        """Save one fixture entry against this test's product."""
        return stock.save_entry(self.path, self.product["id"], self.entry(kind, quantity, day))

    def test_open_receive_issue_and_restart(self):
        """Opening 100 + receiving 20 − issuing 15 gives 105 after reopening SQLite."""
        self.save("opening", "100")
        self.save("receive", "20")
        self.save("issue", "15")
        database.initialize(self.path)
        summary = stock.get_stock(self.path, self.product["id"])
        self.assertEqual((summary["opening"], summary["received"], summary["issued"], summary["balance"]),
                         ("100.000", "20.000", "15.000", "105.000"))
        self.assertEqual(len(summary["entries"]), 3)

    def test_decimal_quantities_are_exact(self):
        """0.1 + 0.2 − 0.3 must equal exactly zero, never a float residue."""
        self.save("opening", "0")
        self.save("receive", "0.1")
        self.save("receive", "0.2")
        self.save("issue", "0.3")
        self.assertEqual(stock.get_stock(self.path, self.product["id"])["balance"], "0.000")

    def test_opening_required_once_and_zero_is_allowed(self):
        """A zero opening is meaningful, but receive/issue cannot be zero."""
        with self.assertRaises(stock.StockError): self.save("receive", "1")
        self.save("opening", "0")
        with self.assertRaises(stock.StockError): self.save("opening", "5")
        for kind in ("receive", "issue"):
            with self.assertRaises(stock.StockError): self.save(kind, "0")
        self.assertEqual(len(stock.get_stock(self.path, self.product["id"])["entries"]), 1)

    def test_insufficient_stock_rolls_back(self):
        """A rejected issue must change neither the ledger nor the balance."""
        self.save("opening", "10")
        before = stock.get_stock(self.path, self.product["id"])
        with self.assertRaises(stock.StockError): self.save("issue", "10.001")
        self.assertEqual(stock.get_stock(self.path, self.product["id"]), before)

    def test_backdated_issue_validates_historical_balances(self):
        """Today's receipt cannot justify an issue yesterday that exceeded stock."""
        self.save("opening", "10", "2026-10-30")
        self.save("issue", "8", "2026-10-31")
        self.save("receive", "100", "2026-11-02")
        with self.assertRaises(stock.StockError): self.save("issue", "3", "2026-10-31")
        self.assertEqual(stock.get_stock(self.path, self.product["id"])["balance"], "102.000")

    def test_date_limits_and_valid_backdated_receipt(self):
        """Reject pre-opening/future entries but accept a safe historical receipt."""
        self.save("opening", "10", "2026-10-30")
        for day in ("2026-10-29", "2026-11-03"):
            with self.assertRaises(stock.StockError): self.save("receive", "1", day)
        self.save("receive", "2", "2026-10-31")
        self.assertEqual(stock.get_stock(self.path, self.product["id"])["balance"], "12.000")

    def test_invalid_quantities_are_never_rounded(self):
        """Reject unsafe formats, too much precision and extremely long fractions."""
        for quantity in ("-1", "+1", "1e3", "NaN", "Infinity", "1,5", "0.0001",
                         "0.999999999999999999999999999999999", "100000000000000000000000"):
            with self.subTest(quantity=quantity), self.assertRaises(stock.StockError):
                self.save("opening", quantity)
        self.assertFalse(stock.get_stock(self.path, self.product["id"])["initialized"])
        self.assertEqual(stock.to_minor("1.25", 2), 125)
        with self.assertRaises(stock.StockError): stock.to_minor("1.5", 0)

    def test_retries_are_idempotent_and_changed_payload_conflicts(self):
        """Same operation ID applies once, including a retry after stock changes."""
        opening = self.entry("opening", "10")
        first = stock.save_entry(self.path, self.product["id"], opening)
        self.save("issue", "5")
        self.assertEqual(stock.save_entry(self.path, self.product["id"], opening), first)
        with self.assertRaises(stock.StockError):
            stock.save_entry(self.path, self.product["id"], {**opening, "quantity": "20"})
        self.assertEqual(stock.get_stock(self.path, self.product["id"])["balance"], "5.000")

    def test_competing_issues_cannot_overspend(self):
        """Two concurrent issues of 7 against 10: exactly one commits."""
        self.save("opening", "10")
        def issue():
            try:
                self.save("issue", "7")
                return "saved"
            except stock.StockError:
                return "rejected"
        with ThreadPoolExecutor(max_workers=2) as workers:
            outcomes = list(workers.map(lambda _: issue(), range(2)))
        self.assertCountEqual(outcomes, ["saved", "rejected"])
        self.assertEqual(stock.get_stock(self.path, self.product["id"])["balance"], "3.000")

    def test_schema_two_upgrade_preserves_products_and_creates_backup(self):
        """A category-milestone database upgrades with no invented opening balance."""
        path = Path(self.folder.name) / "schema2.sqlite3"
        with database.connect(path) as connection:
            connection.execute((database.MIGRATIONS / "001_products.sql").read_text())
            connection.execute((database.MIGRATIONS / "002_categories.sql").read_text())
            connection.execute("ALTER TABLE products ADD COLUMN category_id TEXT REFERENCES categories(id)")
            connection.execute("INSERT INTO categories VALUES ('dry-foods', 'Dry Foods', 0)")
            connection.execute("INSERT INTO products VALUES (?, 'Old Rice', 'KG', '2026-01-01', 'dry-foods')", (self.product["id"],))
            connection.execute("PRAGMA user_version = 2")
        database.initialize(path)
        self.assertEqual(database.find_product(path, self.product["id"])["name"], "Old Rice")
        self.assertEqual(database.find_product(path, self.product["id"])["precision"], 3)
        self.assertFalse(stock.get_stock(path, self.product["id"])["initialized"])
        copies = list((path.parent / "backups").glob("*.sqlite3"))
        self.assertEqual(len(copies), 1)
        with database.connect(copies[0]) as backup:
            self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], 2)

    def test_missing_products_and_invalid_api_input(self):
        """Bad dates/types fail model validation; missing products do not get ledgers."""
        with self.assertRaises(stock.MissingProduct): stock.get_stock(self.path, str(uuid4()))
        with self.assertRaises(stock.MissingProduct): stock.save_entry(self.path, str(uuid4()), self.entry("opening", "0"))
        for changes in ({"quantity": 1.5}, {"effective_date": "not-a-date"}, {"kind": "adjust"}):
            with self.assertRaises(ValidationError): StockInput(**{**self.entry("opening", "0"), **changes})

    def test_stock_routes_use_validated_inputs(self):
        """Exercise actual API handlers without a socket, alongside database tests."""
        app = create_app(self.path)
        routes = [route for route in app.routes if getattr(route, "path", None) == "/api/products/{product_id}/stock"]
        post = next(route.endpoint for route in routes if "POST" in route.methods)
        get = next(route.endpoint for route in routes if "GET" in route.methods)
        entry = StockInput(**self.entry("opening", "42.125"))
        post(UUID(self.product["id"]), entry)
        self.assertEqual(get(UUID(self.product["id"]))["balance"], "42.125")


if __name__ == "__main__":
    unittest.main()
