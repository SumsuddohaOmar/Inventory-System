# Follow one stock save

This guide connects the files so you can change the project yourself.

## From the form to disk

Open `app/static/stock.js`. A native form gathers a quantity as **text**, such
as `1.250`. JavaScript creates a UUID and sends JSON to the product's stock URL.
The ID stays the same for a retry of unchanged values. This matters because a
server may commit successfully even when its reply is lost.

In `app/main.py`, `StockInput` checks the UUID, date, entry type and field lengths.
In `app/extended_api.py`, middleware checks the signed-in user and required
permission. Hiding a button is convenient, but the server is the authority.

`app/stock.py` converts quantity text into an integer using the product's fixed
precision. With three decimal places, `1.250` becomes `1250`. Adding integers
avoids the small errors that binary floating-point numbers can introduce.
Extra decimals are rejected rather than silently rounded.

The write starts a SQLite transaction. Think of this as a sealed group of
changes: either all changes commit, or an error rolls all of them back. The
stock module checks the product, opening entry, date, closed month and every
affected daily balance. SQLite's write lock prevents two competing issues from
both spending the same remaining stock.

The entry, audit event and operation result are recorded together. The route
returns after commit. The browser refreshes the history. A later refresh failing
does not undo the already committed save.

## Retry IDs and revisions solve different problems

An **operation UUID** answers: “Did we already process this request?” Reusing it
with identical data returns the original result. Changing its data is rejected.

A **revision number** answers: “Has this existing entry changed since I loaded
it?” Corrections carry the revision seen by the user. If another person edited
first, the stale correction is rejected for review. A new UUID alone cannot
prevent overwriting someone else's edit; that is why corrections need both.

## What changes while offline

`offline.js` stores a stock create in IndexedDB only after a network failure.
A server rejection is kept as a rejection, not disguised as a successful save.
The UI says **pending** because the server has not confirmed the entry.

On reconnect the server rechecks current rights and stock. Dependencies wait
for earlier entries. A conflict is preserved for authorized review. A reviewed
replacement has a new UUID and points to the held original. Resolving the hold
and posting the replacement share one transaction, so two reviewers cannot both
post a replacement.

## A safe way to learn by editing

Use a copied source folder with its own empty data folder. Read the relevant
test first, change one behavior, run `verify.py`, and inspect the result with a
sample product. Tests explain expected behavior through names and comments.
Use a normal Windows session for the full HTTP suite. Keep production inventory
and experimental databases separate; do not edit live SQLite rows to fix a UI.

The module map is in ARCHITECTURE.md. Outstanding features and device testing
are listed in STATUS.md. Reading a limitation there helps distinguish an
unfinished feature from a bug in code that is intended to work already.
