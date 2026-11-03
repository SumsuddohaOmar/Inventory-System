# Learn the source

The browser sends JSON to FastAPI. The server validates the account and input,
opens a short-lived SQLite connection, commits a business transaction and
returns JSON. The browser never opens the SQLite file. Everything used by the
page is served locally; there is no frontend build step or CDN.

## Suggested reading order

1. `app/static/index.html`: native forms, labels, sections and status regions.
2. `app/static/app.js` and `stock.js`: product requests and single-item stock.
3. `app/main.py`: app factory, validation, startup and basic endpoints.
4. `app/database.py` and `app/migrations/`: connections, upgrades and schema.
5. `app/stock.py`: exact arithmetic and historical stock invariants.
6. `app/workflows.py`: revisions, corrections, atomic bulk entry and drafts.
7. `app/extended_api.py`: account policy and additional workflow endpoints.
8. `app/static/workspace.js`: catalog, entry, reporting and administration UI.
9. `app/static/offline.js`, `auth.js`, `sw.js`: storage, outbox, sign-in and shell.
10. `app/reports.py`, `imports.py`, `templates.py`, `backups.py`: file workflows.
11. `tests/`: small examples of expected behavior and failure handling.

`app/history_imports.py` and `app/static/history-import.js` add a separate,
commented inspect/map/review/apply workflow for dated historical transactions.
It reuses the existing exact ledger and atomic bulk operation rather than
creating an alternative balance calculation. Read HISTORY_IMPORT.md for scope.

Each module explains its purpose. Comments emphasize why transactions, integer
quantities, stable IDs and explicit review matter. Source is fully editable;
third-party components retain their own licenses.

## Quantities and history

A product has a permanent UUID, fixed unit and precision from 0–6 decimals.
Quantities are stored as scaled integers, never binary floats. Precision 3 means
1.250 is stored as 1250. Balances come from opening + receipts − issues; there
is no independently editable balance column that can drift from history.

Stock writes use SQLite write transactions and revalidate affected daily balances.
A receipt today cannot justify overspending yesterday. Same-day receipts can
fund same-day issues. Future final entries and dates before opening are rejected.
Corrections require the expected revision, retain audit before/after values and
revalidate history. Voiding preserves the original entry rather than deleting it.

Each submitted operation has a UUID. Repeating the same ID/payload returns its
original result; reusing it for different data conflicts. Bulk rows commit
together or roll back together. Future drafts never auto-finalize.

## Permissions and offline boundaries

Passwords use salted PBKDF2. Server-side sessions store hashed opaque tokens;
HttpOnly cookies identify sessions. Each request reads current account rights.
Origin/host checks restrict browser requests. Audit changes share the transaction
with the business write. Application audit is not tamper-proof against someone
with direct write access to the laptop's SQLite file.

IndexedDB partitions cached data and queued stock creates by account. The service
worker caches only public shell files. Sync rechecks permissions and stock rules,
keeps conflicts for manual review and waits for dependencies. A successful
review and replacement stock write share one transaction; competing reviewers
cannot post two replacements. Offline corrections remain future work.

## Migration and recovery

SQLite `user_version` tracks schema 1–4. Startup backs up an existing older
database, then applies missing SQL in one transaction. Unknown newer schemas
are rejected. `executescript` is avoided during upgrades because it can commit
unexpectedly. All business modules use the same connection helper, bound SQL
parameters and explicit transaction ownership.

Reports save immutable data snapshots; export does not change stock. Template
export changes only mapped worksheet content and preserves other workbook ZIP
parts. Imports preview first and apply reviewed choices atomically, after backup.
Backups use SQLite's online backup API and integrity checks; restore is an offline
maintenance action. See STATUS.md for incomplete areas and TESTING.md for evidence.
