-- An append-only movement ledger, not a mutable balance column on products.
-- quantity_minor stores exact integers in the product's chosen precision:
-- with precision 3, 1250 means 1.250 units. No floating-point stock arithmetic.
-- The UUID also serves as a retry/idempotency key, preventing duplicate saves.
CREATE TABLE stock_entries (
    id TEXT PRIMARY KEY NOT NULL,
    product_id TEXT NOT NULL REFERENCES products(id),
    kind TEXT NOT NULL CHECK(kind IN ('opening', 'receive', 'issue')),
    effective_date TEXT NOT NULL,
    quantity_minor INTEGER NOT NULL CHECK(quantity_minor >= 0),
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    CHECK(kind = 'opening' OR quantity_minor > 0)
)
