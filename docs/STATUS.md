# Development status — November 2, 2026

Version 0.4.1 is a development preview. The original local product/category
milestone worked in the browser. Expanded workflows have isolated automated
checks and still need Windows/Android acceptance testing.

Final release run: **66 Python tests and 3 offline-request JavaScript tests
passed**, with all browser scripts passing syntax checks. Seven additional HTTP
integration tests subsequently passed with network permission. Live HTTPS also
responded successfully. Full browser and Android acceptance are still pending;
see TESTING.md.

| Area | Implemented in source | Remaining |
|---|---|---|
| Catalog | Five categories, metadata/custom fields, ordering, archive, restricted safe deletion | Broader accessibility/usability review |
| Stock | Exact precision, dated opening/receive/issue, historical balance checks, concurrent writes, revision-checked corrections/voids | Dedicated permission-controlled adjustments |
| Entry | Bulk rows, autosaved drafts, explicit finalization, activity, daily totals, monthly receive/issue table, month closing | Real-device recovery scenarios |
| Accounts | First-owner setup, permissions, remembered sessions, fresh authorization, before/after audit | Guided account/password recovery and broader security review |
| Offline | Account-specific catalog/history, durable stock-create queue, dependency waiting, preserved conflicts, authorized review, optional PIN | Offline corrections/other edits; Android installation/storage/reconnect tests |
| Reports | Monthly, cumulative month-to-Friday, custom ranges, selected products, all-zero exclusion, saved snapshots, review flags, XLSX | Native Excel visual QA, advanced report-column options |
| Company template | Five-sheet mapping prepared locally; styles/non-worksheet parts preserved in checks | Excel print/layout comparison; explicit capacity extensions where needed |
| Import | Reviewed CSV/XLSX products/opening; duplicate link/skip/create; mapped dated history import, explicit date calendars, duplicate acknowledgement, atomic apply and protected backups | Wide monthly-matrix conversion and broader layouts |
| Recovery | Verified online copies, extra folders, retention, offline restore, upgrade backups | Restore drills; guided updater |
| Connection | QR/manual address, private CA generation, opt-in LAN HTTPS | Actual device trust/network/firewall setup; QR scanning test |
| Settings | Date/time preferences and fixed UTC offset | Full IANA timezone/daylight-saving support |

Report review flags are conservative: later stock/catalog changes flag old
snapshots even when outside their date range. Saved values never change silently.

The full requirements remain in REQUIREMENTS.md. No cloud deployment, automatic
Windows startup registration, system certificate trust or firewall changes were
performed automatically. After the user installed the phone CA, LAN HTTPS was
explicitly enabled and the live server started successfully, applying schema 4
with its pre-upgrade recovery copy. An existing administrator was confirmed.
Tests used disposable databases; no test stock was added to the live inventory.
The local template seed does not import products or stock automatically.

The 0.4.1 update adds the history-import workflow without a database schema
change. It accepts one transaction per source row; it does not guess transactions
from a wide company workbook. The new browser review screen still needs a real
browser acceptance check alongside the rest of the preview.
