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

Administrators can open **Requests** in the inventory navigation to
review each submission and its complete item list. Storekeepers can use the
same list with **View inventory** and **Review submitted material requests**.
The list displays requests newest first and can load older submissions.
Give designated managers **Approve material requests** in addition to those
permissions. Approval records the manager and time without changing inventory.
Earlier requests remain visible with their position marked **Not recorded**.
Storekeepers with **Issue stock** and **Review submitted material requests**
can record quantities handed out from an approved request. The actual stock
date is recorded separately from the issuance work date used for MRF
assignment. Quantities left blank remain pending. Excess issuance requires a
reason, and all entered quantities must pass normal stock checks before any
stock changes are saved. **Issue history** shows receipt confirmation and
original, adjusted, returned and net quantities. Stock history can correct
linked movements within the original issue and return limits.

In **Requests → Daily Material Request Form**, select a date to see its
numbered form grouped by shift. Requests use their work date and issues use
their assigned MRF date. Staff with settings permission can close or reopen a
form. An approved request can be marked prepared, or its remaining quantities
can be closed or transferred to a later date for a new approval.

The portal shows account-scoped request history. Requesters can copy items,
save named item lists, and edit or cancel requests awaiting approval. An edit
saves a replacement and keeps the superseded request in history. Settings can
hide specific products from the requester catalog. Requests can carry an
urgent flag, a required date, and individual item remarks.

Once the form is open and signed in, submissions and selected storekeeper
issues can survive a connection drop. The browser stores the payload until
the server confirms it or reports a validation error. Sign-in and loading
the request list still require a connection.

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
catalog uses its own permission. It displays names, units and categories for
active inventory products that administrators allow in the requester catalog.
Product IDs link request lines to the shared inventory.

The inventory ledger stores exact quantities as integers scaled to each
product's precision. Its write service can accept an existing database
connection, so an issuance can write the ledger, request allocation and audit
event together. The existing retry key and closing rules provide the starting
point for handling connection loss and concurrent submissions.

## Remaining rollout work

- Link standalone Inventory issues selectively to a daily MRF. Add combined
  preparation across requests and employee self-registration with admin approval.
- Verify offline recovery, sign-in expiry, account switching and installation on
  iPhone, iPad, macOS, Android and Windows. Verify hosted PostgreSQL migration,
  backup and restore in an isolated environment.
- Add the Excel and printable PDF layouts when the templates arrive. Review free
  tier capacity before the controlled pilot. Production requires separate approval.
