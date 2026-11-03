# Agreed direction and phased backlog

Recovered from the referenced **App Coding Help** conversation on November 2,
2026. These are future requirements, not claims that Phase 1 implements them.
The original workbook attachments have not been re-inspected for this milestone;
template fidelity must be verified against the actual workbooks in Phase 6.

## Permanent constraints

- $0 mandatory spending; free/open-source tools, local Windows server and SQLite.
- Complete editable application source, schemas, build/setup scripts and docs.
- Educational file explanations, function docs, decision comments, database,
  security and synchronization explanations; maintain this standard as we grow.
- Local Wi-Fi only initially; Android works offline outside the network. Laptop
  must be powered on and reachable to synchronize. Optional cloud migration later.
- Preserve historical data and saved reports. No silent overwrites or deletion.

## Phase 1 — this milestone

Organized project, local server, browser page, schema migration, validation,
product save/list/read, restart persistence, setup instructions and behavior tests.
The product has a stable UUID, name, fixed unit and UTC creation timestamp.

## Phase 2 — products, categories and stock model

First slice implemented: five persistent initial categories, product category
selection, filtering and assignment of existing products. Schema-1 migration
preserves old products as unassigned with a verified pre-upgrade copy. Stock,
category management, permissions/audit and reporting remain future work.

Stock slice implemented in 0.3.0: fixed per-product precision, a dated opening/
receive/issue ledger, single-product forms, exact balances, history, safe retries
and atomic stock validation including backdated entries. Opening stock may be
zero and can be set once. No corrections or adjustments yet; do not mark the
broader bulk-entry, editing, permissions/audit or offline requirements complete.

- Five initial categories: **Dry Foods, Disposables, Chemicals, Stationery,
  Cutlery & Cookeries**. The report workbook uses “Cutlery & Crockery”; preserve
  that template label independently from the app label.
- Add, rename, reorder and archive categories/products; safe deletion only where
  history is not endangered. Category count is initially five, not permanently
  limited to five.
- Optional product codes; custom product fields and notes; category-specific
  fields such as Packing, Item Code and Specification.
- One fixed unit per product; custom quantity precision per product. Use exact
  decimal arithmetic or scaled integers, not floating-point stock calculations.
- Product starting date and opening stock, including mid-month activation and
  zero opening stock. Custom product order plus temporary sorting.
- Move product between categories while retaining its ID, stock, transactions,
  notes and audit history; validate destination fields and flag affected reports.
- Stock = opening + received − issued + recorded adjustments. Receiving-side
  month opening stays fixed during the month; issuing-side availability includes
  receipts. Do not treat those display values as unrelated stock balances.
- Automatic month carry-forward with review; optional month closing; authorized
  historical adjustments with downstream recalculation and report review.

## Phase 3 — entry and inventory screens

- Single and bulk receiving/issuing in **every category**; 20–40 products per
  submission. Existing dated forms reload saved entries; editing must update
  records rather than post the same stock movement again. Blank is not deletion.
- Separate same-day transactions plus daily totals. When editing a daily total,
  let the user choose how to edit underlying transactions; preserve history.
- General transaction notes plus individual item notes. No visible transaction
  reference numbers required; internal unique IDs remain essential.
- Auto-saved drafts plus explicit final save. Future dates are drafts only;
  authorized manual finalization on/after the date revalidates stock and rights.
- Both direct table editing and dedicated forms. Historical corrections must
  recompute affected stock and future periods without duplicating entries.
- Monthly Excel-style received/issued sections and daily views; one day, date
  range or full month in one activity page. Category + date + selected-item filters.
- Advanced search/filtering; frozen product names, resizing, column renaming,
  reordering, visibility and other agreed Excel-style customization.
- Windows balanced dashboard with visible categories and month/year selectors;
  custom low-stock thresholds, alerts and dashboard summaries.
- Smart validation and warnings; transaction validation must preserve stock
  consistency, including historical effects. Company timezone, date format and
  time format are configurable; timestamps retain unambiguous UTC storage.

## Phase 4 — people, permissions and history

- Multiple employees with custom permissions per person; remembered trusted
  devices. Enforce rights at the server, not just by hiding buttons.
- Permission-based stock adjustments; complete audit trail with actor, time,
  before/after values, corrections, category moves and conflict resolutions.
- Offline entries use last synchronized rights; entries made under revoked
  permissions are preserved for authorized review when reconnecting.
- Guided first setup: administrator, company settings, categories, optional
  import of products/opening stock, or manual entry instead.

## Phase 5 — Android PWA and safe synchronization

- Installable PWA, local HTTPS with trust configured and tested on Android;
  initialize installation/offline data while connected. HTTP over a LAN address
  is not assumed to support service workers.
- QR-code server address plus manual fallback, status detection and QR refresh
  when the laptop address changes. Normal employee authentication still applies.
- Full synchronized inventory history offline; offline receiving, issuing and
  authorized editing; pending entries and failures clearly shown.
- Request persistent browser storage where available; protect drafts and warn
  before detectable risky actions. No manual recovery export selected initially.
  Clearing Android/browser storage can still destroy unsynchronized records.
- Automatic sync when reachable; idempotent operations, revision checks, atomic
  stock validation, retry handling and dependency tracking are required.
- **Manual conflict review: hold conflicts, sync safe independent entries.**
  Dependent entries wait. Never silently overwrite or discard either version.
- Mobile dashboard prioritizes Receive, Issue, Inventory, Reports and sync status.
  Bulk entry uses **Compact List + Expandable Details**, search, filters, notes,
  drafts and stock validation.
- Optional PIN lock with retry limits, inactivity locking and recovery through
  normal authentication. It does not replace transport or device security.

## Phase 6 — Excel, reports, backups and updates

- Flexible Excel imports: products, opening stock and historical transactions.
  Preview/map/validate before applying; verified pre-import backup.
- Review exact and possible duplicates; link existing, skip or create. Avoid
  double imports and never overwrite stock/history without explicit review.
- Import and manage company report templates, preserving category-specific
  columns/formatting where technically feasible. Recheck the supplied workbooks.
- Friday reports are **cumulative from month start to selected Friday**, not
  independent seven-day reports; the first short Friday period may be skipped.
  Monthly reports cover the full month. Support manual and guided generation.
- Preview, select items, and exclude only products with zero opening, receiving,
  issuing and closing during the period. Items with activity stay eligible.
- Report opening/available figures must include receipts correctly even if the
  company template has no Received column. The earlier example of 150 and the
  issuing-side explanation need concrete fixture-based reconciliation before
  implementing template calculations; do not silently choose a misleading label.
- Saved reports plus version history. Corrections flag reports for review;
  preserve previous exports. Report generation does not mutate inventory.
- Automatic and manual backups to multiple configurable locations; integrity
  checks, restore procedure and protected pre-restore copy.
- Smart retention: daily 30 days, weekly 12 weeks, monthly 12 months; manual
  and protected pre-restore backups until explicitly released/deleted. Only
  clean old automatic backups after verifying usable recovery copies remain.
  Backup failures alert; retention never deletes inventory history or reports.
- Manual backup-protected app updates, schema compatibility checks and recovery;
  coordinate Android PWA/server versions. No forced automatic updates.

## Phase 7 — installation and release checks

Exercise workflows, accessibility, authorization, historical stock correctness,
concurrent writes, offline failure/recovery, conflicts, restore and Excel fidelity.
Test Windows/Android on real devices. Add installation instructions and server
start/stop/status controls. Automatic Windows startup was recommended in the
prior discussion but no explicit selection was recovered; confirm before adding
startup registration. Do not silently install a background service.

## Implementation tracking

The phase list preserves the full agreed scope. Development preview 0.4.1 now
contains building blocks across several phases; this does not mark those phases
complete. See STATUS.md for the feature-by-feature implementation and remaining
work, and TESTING.md for verification limits. Dedicated adjustments, wide-grid
history conversion, offline corrections, advanced report-column choices,
guided updates and real Windows/Android acceptance remain outstanding.
