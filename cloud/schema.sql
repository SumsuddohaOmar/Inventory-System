BEGIN;

-- Fresh PostgreSQL pilot schema. Use a separate database for each pilot.


-- Schema version 1: product identity and one fixed unit, without stock balances.
-- Stock will be derived from dated transactions in Phase 2, never guessed here.
-- No unique name constraint: distinct products may legitimately share a name.
CREATE TABLE products (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 120),
    unit TEXT NOT NULL CHECK(length(trim(unit)) BETWEEN 1 AND 30),
    created_at TEXT NOT NULL
);

-- Schema 2 introduces real category identities, independent of display names.
-- database.initialize seeds the initial records and adds the nullable product
-- foreign key in the same transaction. NULL preserves old unassigned products.
CREATE TABLE categories (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL UNIQUE CHECK(length(trim(name)) BETWEEN 1 AND 120),
    position INTEGER NOT NULL
);

INSERT INTO categories (id, name, position) VALUES
    ('dry-foods', 'Dry Foods', 0),
    ('disposables', 'Disposables', 1),
    ('chemicals', 'Chemicals', 2),
    ('stationery', 'Stationery', 3),
    ('cutlery-cookeries', 'Cutlery & Cookeries', 4);
ALTER TABLE products ADD COLUMN category_id TEXT REFERENCES categories(id);
ALTER TABLE products ADD COLUMN precision INTEGER NOT NULL DEFAULT 3 CHECK (precision BETWEEN 0 AND 6);
CREATE INDEX products_category ON products(category_id);

-- An append-only movement ledger, not a mutable balance column on products.
-- quantity_minor stores exact integers in the product's chosen precision:
-- with precision 3, 1250 means 1.250 units. No floating-point stock arithmetic.
-- The UUID also serves as a retry/idempotency key, preventing duplicate saves.
CREATE TABLE stock_entries (
    id TEXT PRIMARY KEY NOT NULL,
    product_id TEXT NOT NULL REFERENCES products(id),
    kind TEXT NOT NULL CHECK(kind IN ('opening', 'receive', 'issue')),
    effective_date TEXT NOT NULL,
    quantity_minor BIGINT NOT NULL CHECK(quantity_minor >= 0),
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    CHECK(kind = 'opening' OR quantity_minor > 0)
);

-- Current ledger rows remain fast to query; every correction preserves a full
-- before/after snapshot in audit_events. Version checks prevent lost updates.
ALTER TABLE stock_entries ADD COLUMN revision INTEGER NOT NULL DEFAULT 1;
ALTER TABLE stock_entries ADD COLUMN voided INTEGER NOT NULL DEFAULT 0;
CREATE TABLE audit_events (id TEXT PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL, entity_id TEXT NOT NULL, before_json TEXT, after_json TEXT, created_at TEXT NOT NULL);
CREATE TABLE operations (id TEXT PRIMARY KEY, request_json TEXT NOT NULL, result_json TEXT NOT NULL);
CREATE TABLE drafts (id TEXT PRIMARY KEY, owner TEXT NOT NULL, payload_json TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, finalized INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, permissions_json TEXT NOT NULL, admin INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires_at BIGINT NOT NULL);
CREATE TABLE login_attempts (username TEXT PRIMARY KEY, failures INTEGER NOT NULL DEFAULT 0, blocked_until BIGINT NOT NULL DEFAULT 0);
CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
CREATE TABLE report_versions (id TEXT PRIMARY KEY, name TEXT NOT NULL, from_date TEXT NOT NULL, to_date TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE closed_months (month TEXT PRIMARY KEY, created_at TEXT NOT NULL);
ALTER TABLE products ADD COLUMN archived INTEGER NOT NULL DEFAULT 0;
ALTER TABLE products ADD COLUMN notes TEXT NOT NULL DEFAULT '';
ALTER TABLE products ADD COLUMN code TEXT NOT NULL DEFAULT '';
ALTER TABLE products ADD COLUMN low_stock_minor BIGINT;
ALTER TABLE products ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0;
ALTER TABLE categories ADD COLUMN archived INTEGER NOT NULL DEFAULT 0;
ALTER TABLE products ADD COLUMN fields_json TEXT NOT NULL DEFAULT '{}';
CREATE TABLE templates (id TEXT PRIMARY KEY, name TEXT NOT NULL, workbook_base64 TEXT NOT NULL, mapping_json TEXT NOT NULL);
CREATE TABLE sync_holds (id TEXT PRIMARY KEY, owner TEXT NOT NULL, payload_json TEXT NOT NULL, reason TEXT NOT NULL, resolved INTEGER NOT NULL DEFAULT 0);

CREATE UNIQUE INDEX one_opening_per_product ON stock_entries(product_id) WHERE kind = 'opening';
CREATE INDEX stock_product_date ON stock_entries(product_id, effective_date);
COMMIT;
