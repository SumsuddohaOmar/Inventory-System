-- Schema 2 introduces real category identities, independent of display names.
-- database.initialize seeds the initial records and adds the nullable product
-- foreign key in the same transaction. NULL preserves old unassigned products.
CREATE TABLE categories (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL UNIQUE CHECK(length(trim(name)) BETWEEN 1 AND 120),
    position INTEGER NOT NULL
)
