-- Schema version 1: product identity and one fixed unit, without stock balances.
-- Stock will be derived from dated transactions in Phase 2, never guessed here.
-- No unique name constraint: distinct products may legitimately share a name.
CREATE TABLE products (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 120),
    unit TEXT NOT NULL CHECK(length(trim(unit)) BETWEEN 1 AND 30),
    created_at TEXT NOT NULL
)
