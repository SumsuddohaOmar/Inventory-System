# Hub-16 implementation

## Start a sample run

From this checkout, install the existing development requirements with
`python -m pip install -r requirements-dev.txt`, then run
`python hub16_sample.py` and open `http://127.0.0.1:8765`. The launcher prints
two randomly generated sets of sample credentials. The administrator can use
the existing inventory screens. The employee signs in at
`http://127.0.0.1:8765/material-requests` to submit sample requests.

In **Administration → Employees & permissions**, create shift accounts with
**View material request catalog** and **Submit material requests**. Clear
**View inventory** for request-only accounts. Each submission records both the
account, requester name, employee ID and position entered on the form. Choose the
work date explicitly for all shifts in this first stage. An evening collection
for the following day should use that following day's date.

Administrators can open **Administration → Submitted material requests** to
review each submission and its complete item list. Storekeepers can use the
same list with **View inventory** and **Review submitted material requests**.
The list displays requests newest first and can load older submissions.
Give designated managers **Approve material requests** in addition to those
permissions. Approval records the manager and time without changing inventory.
Earlier requests remain visible with their position marked **Not recorded**.

Every launch creates its own ignored SQLite file under `data/hub16-samples`.
The sample inventory has two fictional products with opening stock. The server
accepts connections from this computer only, and backups generated during a
sample run stay under that run's folder. Use only fictional data here.

Run `python verify.py` for the existing inventory checks. Run
`python -m unittest discover -s tests -p test_mrf_catalog.py` for the catalog
and request submission integration test.

Open **Hub-16 material request settings** in the inventory administration
section with an account that can edit company settings. Select a policy for
each of the three cases, or choose the manual option to make the decision for
each record once the workflows are available. Save to store the choices in
the shared settings table and record the update in audit history.

## How Hub-16 will fit into inventory

The current app serves plain HTML, CSS and JavaScript from FastAPI. It has a
responsive stylesheet and a service worker that caches the public shell.
Browser data in IndexedDB is scoped by account, and sync submissions are
checked against the current session. The existing outbox handles inventory
operations with a PostgreSQL-only sync endpoint. Local SQLite exercises business
logic and permissions. An isolated PostgreSQL target is needed to verify real
sync, conflict handling and the hosted backup path.

The existing session cookie identifies employees and store staff. The employee
catalog uses its own permission. Today it displays the active inventory catalog
with names, units and category information. Product IDs are included for linking
request lines. Configurable employee product visibility still needs to be added.

The inventory ledger stores exact quantities as integers scaled to each
product's precision. Its write service can accept an existing database
connection, so an issuance can write the ledger, request allocation and audit
event together. The existing retry key and closing rules provide the starting
point for handling connection loss and concurrent submissions.

## Build and verification order

1. Add SQLite and PostgreSQL migrations for request records, lines and employee
   visibility. Build the employee portal with its own permissions, request cart
   and history. Verify that submission and preparation leave balances intact.
2. After the work-date rules are confirmed, add daily MRF numbering, shift
   grouping, issuance, pending transfers, corrections and returns. Link existing
   requests during manual issuance and support selective MRF inclusion.
   Verify concurrent saves, retry keys and a single stock deduction per issuance.
3. After the receiving policy is confirmed, add collection confirmation and
   audit the signed-in actor and recorded receiver. Add account-scoped offline
   queues with explicit pending and conflict states.
4. Test browser mode and optional installation on iPhone and iPad Safari,
   macOS Safari and Chrome, Android Chrome, and Windows Edge and Chrome.
   On each, test a cold offline launch after setup, a queued write through a
   reload, reconnect with the app open, reconnect after closing it, retry after
   an interrupted response, expired sessions, account switching and unavailable
   browser storage. Record results separately for browser and installed modes.
5. Add Excel and PDF layouts when templates arrive. Verify unified backup and
   restore in an isolated environment. Review free-tier capacity before the
   controlled sample-data pilot. Production requires separate approval.
