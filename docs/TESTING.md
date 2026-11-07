# Verification record — November 2, 2026

## Mobile bulk draft retry follow-up — November 7, 2026

Draft autosave retries now compare parsed entries rather than JSON key order.
An identical retry returns the existing revision without changing stock or
incrementing the draft revision. Different content still requires the current
revision. Browser recovery remembers its attempted payloads, including restored
legacy local drafts, and retries changed input only if the server still contains
one of those known attempts. The recovered revision is checked again on write.
Unknown edits from another device/tab remain held with local quantities intact.

Two additional workflow tests verify identical/reordered retries and genuine
cross-device conflict rejection. A phone-sized Chromium regression exercises
lost responses with and without subsequent edits, restored legacy drafts, slow
autosaves, independent device edits and shared-storage tab edits through the
actual bulk form submit handler. The fixture does not contact the live app.
Real-phone acceptance remains required after an approved deployment.

## Excel daily connection follow-up — November 6, 2026

The prepared copy passed native Windows Excel refresh/calculation checks using
synthetic quantities: opening 10, received 2.5, issued 1, closing 11.5 and next
month opening 11.5. Test inputs were cleared before saving the production query.
The source workbook is unchanged. A read-only audit confirmed preservation of
79,111 original calculation formulas and 338,720 new daily links, blank first
opening cells, no cached formula errors, a native Power Query connection and
a macro-free output. The saved Overview and App Data views were rendered and
reviewed. These checks do not establish authenticated refresh from Render.

The selected categories, stock, workflow, import/template, monthly report,
Excel feed, history import and release suites passed 88 tests, including 15
new feed tests. They cover exact daily aggregation, corrections, voids, blank
unmatched rows, duplicate mapping, unlinking, scoped passwords, revocation,
current account permissions and cookie/Basic isolation at endpoint/policy level.

Chromium checks passed for Excel setup/review at 320, 375 and 1280px and monthly
report layouts at five widths. Real IndexedDB tests passed for reload, shared
tabs and independent devices. All 13 socket-free offline tests and browser
script syntax checks passed. No live inventory was modified.

The additional complete HTTP/ASGI check could not run in this sandbox: Windows
asyncio stalls creating its socketpair before the event loop starts. The
optional `tests/excel-feed-http.py` is retained for a normal environment with
httpx installed. First live authenticated refresh after deployment, and the
first Mac desktop refresh, remain acceptance checks. No Mac, browser-Excel or
mobile-Excel refresh result is claimed.

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
