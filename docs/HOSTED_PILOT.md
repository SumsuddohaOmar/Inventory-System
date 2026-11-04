# Hosted inventory pilot

## Zero-cost PostgreSQL pilot

Use a fresh Neon Free PostgreSQL database and the Render Free service defined
in `render.yaml`. The service hosts both the existing browser app and its Python
API on one HTTPS origin. Render can sleep when idle. Use sample data and leave
paid upgrades off.

1. Install `cloud/requirements.txt` in a separate Python environment.
2. Run `python cloud_init.py` against the empty pilot database. The connection
   URL and administrator password are prompted without echo. Use the provider's
   TLS-enabled URL. If schema installation succeeds but account creation fails,
   use `create_owner.py` with the pilot URL supplied via the environment.
3. Deploy the source-only project root to Render Free and add the pilot URL as
   the secret environment variable `INVENTORY_POSTGRES_URL`. Use a private
   source repository. Keep local data, credentials and backups outside it.
4. Verify sign-in, product creation, receive and issue, and idempotent sync on
   the public HTTPS link. Check persistence across a service restart. Repeat
   the phone test on Wi-Fi and mobile data.

PostgreSQL integration and the live service have not yet been verified.
Product import preview is read-only and can run in PostgreSQL mode. Import
apply, historical imports and in-app backups remain unavailable in cloud mode. Separate deployments are required for separate businesses.

## Container alternative

The existing FastAPI backend can run as a single container with a persistent
disk. This is a transitional pilot path that keeps the existing SQLite behavior.
Netlify Functions cannot run this container or hold its SQLite file persistently.
The Netlify and PostgreSQL migration remains a separate implementation step.

Deploy one replica behind a host-managed HTTPS endpoint. Mount a fresh,
writable persistent volume at `/inventory-data` for UID 10001. Set
`INVENTORY_ALLOWED_HOSTS` to the exact public hostname and configure Uvicorn
to trust forwarded headers only from the hosting provider's reverse proxy.
Verify the sign-in cookie is Secure and cross-origin writes are rejected.

In a terminal attached to the same persistent volume, run
`python create_owner.py`. The container blocks public first-account creation.
Sign in and enter fictional products. Test a receive and issue, restart the
container, and confirm the balance persists. On Android, test the same HTTPS
link on Wi-Fi and mobile data, then test an offline entry and reconnect.

Each pilot deployment has its own database. Shared business workspaces,
PostgreSQL, deployment, and live inventory migration are pending. Keep real
inventory out of pilot volumes and deploy artifacts.


## Cloud recovery drill before real data

Use only fictional pilot records and a separate empty PostgreSQL target. If
you cannot provision a disposable target within the free allowance, stop the
drill without changing the live database.

1. Record expected products, balances, ledger rows, employees and permissions,
   reports, and settings. Save a custom-format dump with PostgreSQL client tools
   using the direct TLS connection: `pg_dump --format=custom --file=pilot.dump`.
   Set the source connection through PostgreSQL client configuration. Keep the
   dump and connection secrets outside Git and any shared folder.
2. Inspect the archive with `pg_restore --list pilot.dump`. Configure the client
   for the empty disposable target and confirm its host and database name before
   running `pg_restore --exit-on-error --single-transaction --no-owner
   --no-privileges --dbname=RESTORE_DATABASE pilot.dump`. Use a client version
   compatible with both servers. Never use the production database as the target.
3. Point a separate test app at the restored target. Check owner and employee
   sign-in, permissions, balances, ledger history, reports and settings. Test a
   receive, an issue and an identical sync retry. Compare the expected records
   before the test writes. Keep the dump outside Render's runtime filesystem.
4. Record the dump time, recovery time, and comparison results. Do not migrate
   real inventory until the drill passes and there is a documented repeatable
   backup schedule. The Excel reports are exports, not recovery copies.

This is a drill procedure; it does not indicate that cloud restore has passed.
