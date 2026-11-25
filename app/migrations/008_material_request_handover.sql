CREATE TABLE IF NOT EXISTS material_request_receipts (
    issue_id TEXT PRIMARY KEY REFERENCES material_request_issues(id),
    recorded_by TEXT NOT NULL REFERENCES users(id),
    receiver_name TEXT NOT NULL,
    employee_id TEXT NOT NULL,
    received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_material_forms (
    work_date TEXT PRIMARY KEY,
    month TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    closed INTEGER NOT NULL DEFAULT 0 CHECK (closed IN (0, 1)),
    UNIQUE (month, sequence)
);
CREATE INDEX IF NOT EXISTS material_requests_by_work_date ON material_requests(work_date);
CREATE INDEX IF NOT EXISTS material_issues_by_mrf_date ON material_request_issues(mrf_date);
CREATE TABLE IF NOT EXISTS material_request_returns (
    id TEXT PRIMARY KEY,
    issue_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    stock_entry_id TEXT NOT NULL UNIQUE,
    quantity_minor BIGINT NOT NULL CHECK (quantity_minor > 0),
    FOREIGN KEY (issue_id, product_id) REFERENCES material_request_issue_lines(issue_id, product_id)
);
CREATE INDEX IF NOT EXISTS material_returns_by_issue ON material_request_returns(issue_id, product_id);
CREATE TABLE IF NOT EXISTS material_request_templates (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    items_json TEXT NOT NULL,
    UNIQUE (account_id, name)
);
CREATE TABLE IF NOT EXISTS material_request_pending_actions (
    request_id TEXT PRIMARY KEY REFERENCES material_requests(id),
    account_id TEXT NOT NULL REFERENCES users(id),
    action TEXT NOT NULL CHECK (action IN ('close', 'transfer')),
    reason TEXT NOT NULL,
    successor_id TEXT REFERENCES material_requests(id),
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS material_request_preparations (
    request_id TEXT PRIMARY KEY REFERENCES material_requests(id),
    account_id TEXT NOT NULL REFERENCES users(id),
    prepared_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS material_request_cancellations (
    request_id TEXT PRIMARY KEY REFERENCES material_requests(id),
    account_id TEXT NOT NULL REFERENCES users(id),
    cancelled_at TEXT NOT NULL
);
