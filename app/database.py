"""SQLite persistence: open short-lived connections and apply versioned SQL.

This module owns connection handling and schema upgrades. Business modules
also use SQL through these connections, so transaction boundaries stay explicit.
Every connection closes explicitly; successful writes commit atomically, while
failed writes roll back. Never construct SQL by concatenating user input.
"""

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from uuid import uuid4
from app import postgres

MIGRATIONS = Path(__file__).resolve().parent / "migrations"
# Stable IDs separate category identity from a label we may rename later.
INITIAL_CATEGORIES = (
    ("dry-foods", "Dry Foods"),
    ("disposables", "Disposables"),
    ("chemicals", "Chemicals"),
    ("stationery", "Stationery"),
    ("cutlery-cookeries", "Cutlery & Cookeries"),
)


@contextmanager
def connect(path: Path):
    """Yield a connection; commit on success, roll back on error, always close.

    Parameters: path is the SQLite file. SQLite errors propagate to the caller.
    A five-second timeout gives a concurrent short write time to finish.
    """
    if isinstance(path, postgres.PostgresTarget):
        with postgres.connect(path) as connection:
            yield connection
        return
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row  # Access columns by name, not position.
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize(path: Path):
    """Create the data directory and apply missing migrations in one transaction.

    user_version belongs to SQLite itself. Refuse newer schemas rather than
    accidentally opening a database written by a future application version.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as connection:
        # Protect an existing product/category database before changing its structure.
        # SQLite's backup API makes a consistent copy even if a reader is open.
        # Do this BEFORE BEGIN IMMEDIATE: backing up our own write transaction
        # would wait for itself. Failure aborts startup instead of risking data.
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version in (1, 2, 3):
            backup_folder = path.parent / "backups"
            backup_folder.mkdir(exist_ok=True)
            backup_path = backup_folder / f"pre-schema-4-{uuid4()}.sqlite3"
            with connect(backup_path) as backup:
                connection.backup(backup)
                if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Pre-upgrade backup verification failed.")
        # Serialize initialization if two processes start at the same time.
        connection.execute("BEGIN IMMEDIATE")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > 4:
            raise RuntimeError("Database schema is newer than this app supports.")
        if version == 0:
            # This migration deliberately uses one CREATE TABLE statement.
            # execute (rather than executescript) preserves our transaction.
            connection.execute((MIGRATIONS / "001_products.sql").read_text())
            connection.execute("PRAGMA user_version = 1")
            version = 1
        if version == 1:
            connection.execute((MIGRATIONS / "002_categories.sql").read_text(encoding="utf-8"))
            connection.executemany(
                "INSERT INTO categories (id, name, position) VALUES (?, ?, ?)",
                [(identity, name, position) for position, (identity, name) in enumerate(INITIAL_CATEGORIES)],
            )
            # Old products deliberately remain unassigned. Guessing their
            # category would turn an upgrade into an unrequested data change.
            connection.execute("ALTER TABLE products ADD COLUMN category_id TEXT REFERENCES categories(id)")
            connection.execute("CREATE INDEX products_category ON products(category_id)")
            connection.execute("PRAGMA user_version = 2")
            version = 2
        if version == 2:
            connection.execute((MIGRATIONS / "003_stock.sql").read_text(encoding="utf-8"))
            # Existing products retain a useful default; precision is fixed once
            # created so old integer quantities can never be reinterpreted.
            connection.execute("ALTER TABLE products ADD COLUMN precision INTEGER NOT NULL DEFAULT 3 CHECK(precision BETWEEN 0 AND 6)")
            connection.execute("CREATE UNIQUE INDEX one_opening_per_product ON stock_entries(product_id) WHERE kind = 'opening'")
            connection.execute("CREATE INDEX stock_product_date ON stock_entries(product_id, effective_date)")
            connection.execute("PRAGMA user_version = 3")
            version = 3
        if version == 3:
            # complete_statement respects comments/quoted SQL and lets each
            # statement execute INSIDE our transaction. executescript would
            # implicitly commit, breaking migration rollback guarantees.
            statement = ""
            for line in (MIGRATIONS / "004_workflows.sql").read_text(encoding="utf-8").splitlines(True):
                statement += line
                if sqlite3.complete_statement(statement):
                    connection.execute(statement)
                    statement = ""
            if statement.strip():
                raise RuntimeError("Incomplete workflow migration.")
            connection.execute("PRAGMA user_version = 4")
        # A local attachment can be prepared as a first-run template seed. It
        # never imports products/stock. Source archives deliberately omit data/.
        seed_path=path.parent/"template-seed.json"
        if seed_path.is_file():
            import base64,json
            seed=json.loads(seed_path.read_text(encoding="utf-8"))
            if not connection.execute("SELECT 1 FROM templates WHERE id=?",(seed["id"],)).fetchone():
                workbook=(path.parent/seed["file"]).resolve()
                if not workbook.is_relative_to(path.parent.resolve()):raise RuntimeError("Template seed must stay in the data folder.")
                encoded=base64.b64encode(workbook.read_bytes()).decode("ascii")
                from app.templates import validate
                validate(encoded,seed["mapping"])
                connection.execute("INSERT INTO templates VALUES (?,?,?,?)",(seed["id"],seed["name"],encoded,json.dumps(seed["mapping"])))


def insert_product(path: Path, product: dict) -> dict:
    """Persist a validated product and return its complete stored record.

    The route supplies the UUID and timestamp. Keeping identity independent of
    database row numbers leaves room for future offline device-created records.
    """
    with connect(path) as connection:
        connection.execute(
            "INSERT INTO products (id, name, unit, created_at, category_id, precision) VALUES (?, ?, ?, ?, ?, ?)",
            (product["id"], product["name"], product["unit"], product["created_at"], product["category_id"], product.get("precision", 3)),
        )
        from app.audit import record
        record(connection, "product.created", product["id"], after={**product, "precision": product.get("precision", 3)})
    return {**product, "precision": product.get("precision", 3)}


def list_products(path: Path, category_id: str | None = None) -> list[dict]:
    """Return all products, one category, or the special unassigned selection.

    None means no filter. 'unassigned' selects only old products awaiting a
    category. Values are bound parameters, never interpolated SQL fragments.
    """
    with connect(path) as connection:
        where, parameters = " WHERE archived=0", ()
        if category_id == "unassigned":
            where += " AND category_id IS NULL"
        elif category_id is not None:
            where, parameters = where + " AND category_id = ?", (category_id,)
        rows = connection.execute(
            "SELECT id, name, unit, created_at, category_id, precision FROM products" + where + " ORDER BY sort_order, created_at, id",
            parameters,
        ).fetchall()
    return [dict(row) for row in rows]


def find_product(path: Path, product_id: str) -> dict | None:
    """Return one product, or None when the UUID does not exist."""
    with connect(path) as connection:
        row = connection.execute(
            "SELECT id, name, unit, created_at, category_id, precision FROM products WHERE id = ?",
            (product_id,),
        ).fetchone()
    return dict(row) if row else None


def list_categories(path: Path) -> list[dict]:
    """Read the five initial category records in the agreed display order."""
    with connect(path) as connection:
        return [dict(row) for row in connection.execute(
            "SELECT id, name FROM categories WHERE archived=0 ORDER BY position, id"
        ).fetchall()]


def assign_category(path: Path, product_id: str, category_id: str) -> dict | None:
    """Assign an existing product without changing its identity or other fields.

    Returns None for a missing product; a nonexistent category fails the foreign
    key constraint. The API validates category existence for a readable error.
    """
    with connect(path) as connection:
        before = connection.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        cursor = connection.execute(
            "UPDATE products SET category_id = ? WHERE id = ?",
            (category_id, product_id),
        )
        if cursor.rowcount == 0:
            return None
        from app.audit import record
        record(connection, "product.category", product_id, dict(before), {"category_id": category_id})
        return dict(connection.execute(
            "SELECT id, name, unit, created_at, category_id, precision FROM products WHERE id = ?",
            (product_id,),
        ).fetchone())
