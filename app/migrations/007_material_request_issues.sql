CREATE TABLE IF NOT EXISTS material_request_issues (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES material_requests(id),
    issuer_id TEXT NOT NULL REFERENCES users(id),
    issuer_name TEXT NOT NULL,
    stock_date TEXT NOT NULL,
    work_date TEXT NOT NULL,
    mrf_date TEXT NOT NULL,
    excess_reason TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS material_issues_by_request ON material_request_issues(request_id);
CREATE TABLE IF NOT EXISTS material_request_issue_lines (
    issue_id TEXT NOT NULL REFERENCES material_request_issues(id),
    product_id TEXT NOT NULL,
    stock_entry_id TEXT NOT NULL UNIQUE,
    quantity_minor BIGINT NOT NULL CHECK (quantity_minor > 0),
    PRIMARY KEY (issue_id, product_id)
);
