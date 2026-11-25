CREATE TABLE IF NOT EXISTS material_requests (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES users(id),
    requester_name TEXT NOT NULL,
    employee_id TEXT NOT NULL,
    shift TEXT NOT NULL CHECK (shift IN ('morning', 'afternoon', 'night')),
    work_date TEXT NOT NULL,
    notes TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS material_request_lines (
    request_id TEXT NOT NULL REFERENCES material_requests(id),
    product_id TEXT NOT NULL,
    product_name TEXT NOT NULL,
    unit TEXT NOT NULL,
    quantity_minor BIGINT NOT NULL CHECK (quantity_minor > 0),
    precision INTEGER NOT NULL,
    PRIMARY KEY (request_id, product_id)
);
