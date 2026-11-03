-- Current ledger rows remain fast to query; every correction preserves a full
-- before/after snapshot in audit_events. Version checks prevent lost updates.
ALTER TABLE stock_entries ADD COLUMN revision INTEGER NOT NULL DEFAULT 1;
ALTER TABLE stock_entries ADD COLUMN voided INTEGER NOT NULL DEFAULT 0;
CREATE TABLE audit_events (id TEXT PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL, entity_id TEXT NOT NULL, before_json TEXT, after_json TEXT, created_at TEXT NOT NULL);
CREATE TABLE operations (id TEXT PRIMARY KEY, request_json TEXT NOT NULL, result_json TEXT NOT NULL);
CREATE TABLE drafts (id TEXT PRIMARY KEY, owner TEXT NOT NULL, payload_json TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, finalized INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, permissions_json TEXT NOT NULL, admin INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires_at INTEGER NOT NULL);
CREATE TABLE login_attempts (username TEXT PRIMARY KEY, failures INTEGER NOT NULL DEFAULT 0, blocked_until INTEGER NOT NULL DEFAULT 0);
CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
CREATE TABLE report_versions (id TEXT PRIMARY KEY, name TEXT NOT NULL, from_date TEXT NOT NULL, to_date TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE closed_months (month TEXT PRIMARY KEY, created_at TEXT NOT NULL);
ALTER TABLE products ADD COLUMN archived INTEGER NOT NULL DEFAULT 0;
ALTER TABLE products ADD COLUMN notes TEXT NOT NULL DEFAULT '';
ALTER TABLE products ADD COLUMN code TEXT NOT NULL DEFAULT '';
ALTER TABLE products ADD COLUMN low_stock_minor INTEGER;
ALTER TABLE products ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0;
ALTER TABLE categories ADD COLUMN archived INTEGER NOT NULL DEFAULT 0;
ALTER TABLE products ADD COLUMN fields_json TEXT NOT NULL DEFAULT '{}';
CREATE TABLE templates (id TEXT PRIMARY KEY, name TEXT NOT NULL, workbook_base64 TEXT NOT NULL, mapping_json TEXT NOT NULL);
CREATE TABLE sync_holds (id TEXT PRIMARY KEY, owner TEXT NOT NULL, payload_json TEXT NOT NULL, reason TEXT NOT NULL, resolved INTEGER NOT NULL DEFAULT 0);
