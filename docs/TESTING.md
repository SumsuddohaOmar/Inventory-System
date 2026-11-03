# Verification record — November 2, 2026

Environment: Windows, Python 3.14.3; versions in requirements-lock.txt.
Run `python verify.py` using the project environment for socket-free checks.
Tests use disposable SQLite databases and do not write to the live inventory.

## Current preview

Version 0.4.1: **66 Python tests and 3 JavaScript offline-request tests passed**;
all browser scripts passed syntax checks. Twelve history-import checks cover
read-only previews, stable row IDs, atomic rollback, backups/audit, repeated and
concurrent imports, existing/in-file duplicate acknowledgement, current rights,
custom columns, explicit date calendars, skipped/future rows, and real XLSX
numeric dates/exact quantities. UI acceptance remains pending.

The release suite covers categories/migrations, exact stock and concurrent
overspending, launcher version selection, correction revisions/rollback,
bulk atomicity, future drafts, month closing, accounts/lockout/current rights,
access-policy decisions, saved reports, verified backup/restore, reviewed imports,
template preservation, daily totals, dependency-aware sync and QR algebra.

New conflict tests run two reviewers concurrently and prove only one replacement
posts. Another proves a successful later review resolves earlier failed reviews.
Spreadsheet tests keep formula-looking product names literal and parse exported
XML even when source text contains forbidden XML control characters.

Release checks validate unique UI IDs, label targets, literal script selectors,
API schema generation and real OpenSSL certificate generation in a temporary
folder. They verify network mode stays disabled and renewal retains the CA.
No system trust or firewall changes occur during those tests.

Node checks parse every browser JavaScript file. Three isolated offline tests
exercise the actual request function: uncached category filtering from the
account catalog, graceful cache-write failure, and no queueing of rejected
online writes. They use a small browser substitute, not an Android browser.

The final numeric results are recorded in STATUS.md after the release run.

## Limits

With network permission granted, all **7 HTTP integration tests passed** for
signed-in create/read/restart, category routes, invalid inputs, missing records,
special characters, assets/host restrictions and safe schema refusal. Their
temporary directories now stay inside the writable workspace. Earlier event-loop
socket restrictions no longer blocked this run. A live HTTPS check also returned
200 with certificate verification enabled and confirmed version 0.4.1 and an
existing administrator. Phone reachability/browser acceptance remain pending.

The original milestone previously passed six HTTP tests and a real browser
save/reload of Sugar; the user also confirmed categories worked. Those results
do not establish correctness of the expanded 0.4.1 browser interface.

The actual company report was filled in an isolated check: five mappings were
accepted, worksheet XML parsed and every non-worksheet ZIP part (including
styles) remained byte-identical. No native Excel rendering/print QA is claimed.
The original company file was not changed.

Still required: Windows browser end-to-end acceptance, keyboard/zoom/screen-reader
review, real Android installation/offline recovery, trusted local HTTPS, camera
QR decoding, storage pressure, revoked permissions on reconnect, app updates
with pending work, and a restore drill. ANDROID.md describes the device sequence.
No accessibility certification or complete production-readiness claim is made.

Launcher follow-up: both Windows shortcuts now use the shared HTTPS-aware
launcher and keep their window open after exit. Unexpected startup exceptions
are saved to the private data/launcher-error.log. The five launcher regression
tests pass; the PowerShell shortcut also passes syntax parsing. The user's
reported File Explorer auto-close still needs a retry of the corrected shortcut
to identify any remaining device-specific startup error.

Further auto-close investigation: the actual command file passed an isolated
Windows execution test with a no-server Python stub. A new Open Inventory
shortcut uses cmd /D /K call with the exact project command-file path. That
invocation was also tested: the shell accepted a further command after the
launcher returned, before explicit exit. The shortcut has been created locally;
File Explorer retry remains the next observation needed for the reported issue.
This does not establish that the HTTPS server started from a user double-click.
