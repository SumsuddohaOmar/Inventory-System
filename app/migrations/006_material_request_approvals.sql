CREATE TABLE IF NOT EXISTS material_request_approvals (
    request_id TEXT PRIMARY KEY REFERENCES material_requests(id),
    approver_id TEXT NOT NULL REFERENCES users(id),
    approver_name TEXT NOT NULL,
    approved_at TEXT NOT NULL
);
