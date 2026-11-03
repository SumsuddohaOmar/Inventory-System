# Local Inventory — 0.4.1 development preview

Editable inventory software using Python, FastAPI, SQLite and ordinary browser
files. All application source is included. No subscription, cloud account,
Node build or external database is needed to run it.

This expands the working product/category/stock milestone. It is **not the
finished Windows/Android release**. See [STATUS.md](docs/STATUS.md) for the
remaining requirements and limits of verification.

## Open it on this Windows laptop

In File Explorer, open this folder and double-click **Start Inventory.cmd**.
It uses the Python environment already prepared in this workspace and opens
the current app. Keep its server window open; Ctrl+C stops it.

If the command window disappears, use **Open Inventory**, the Windows shortcut
prepared in this local folder. It explicitly keeps Command Prompt open after
the launcher returns. `create_shortcut.ps1` contains its editable source and can
recreate it on another Windows computer. The machine-specific `.lnk` itself is
excluded from the source ZIP. Startup attempts are recorded privately in
`data/launcher-status.log`; unexpected Python failures use `data/launcher-error.log`.

On the first account-enabled launch, choose an administrator username and a password of at
least 12 characters. There is no default password. Existing products and stock
remain; the schema upgrade first makes a verified recovery copy.

Stop old inventory server windows before working with the upgraded database.
The launcher refuses detected older inventory servers on known local ports,
because their old endpoints could bypass new accounts. It recognizes this
version and uses ports 8000–8009 when another application occupies a port.
Use the page it opens. A new address/port is a new browser storage location:
synchronize pending entries at their original address before changing it.

Use **Add a product**, then expand its **Stock details** to set opening stock,
receive or issue. Opening 100 + receiving 20 − issuing 15 gives 105. **Daily
bulk entry** handles several products. Drafts save automatically; only explicit
finalization changes stock. Future dates remain drafts and never auto-finalize.
Other screens provide activity, monthly totals, reports, employee permissions,
audit, reviewed imports and backups. Use sample data during device acceptance.

## Fresh Windows setup

Install Python 3.12 or newer (verified here with 3.14.3). Open PowerShell in this
folder. Initial dependency downloads need internet; normal local use does not.
Virtual environments cannot be copied between computers.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe launch.py
```

The Windows launcher also works once this folder has its own `.venv`. If `py`
fails or opens the Microsoft Store, use the actual installed Python path.
In PowerShell, put `&` before a quoted executable path.

For macOS, use `python3 -m venv .venv`,
`.venv/bin/python -m pip install -r requirements-lock.txt` and
`.venv/bin/python launch.py`. The Python source is portable; Windows shortcuts
and the Git-for-Windows certificate lookup are platform-specific. macOS has
not been device-tested.

## Data, backups and updates

The live database is `data/inventory.sqlite3`. The entire `data/` folder is
private: it can contain backups, company workbooks, settings and HTTPS keys.
It is excluded from Git and the source ZIP. The ZIP contains source, not your
inventory. Keep the database on a local disk, not a shared network drive.

Startup upgrades older databases to schema 4 after a verified backup. Automatic
backups run on startup and check hourly, making at most one copy per UTC day.
Administration supports manual copies and additional destinations. Retention
keeps 30 daily, 12 weekly and 12 monthly checkpoints; manual/protected copies
remain. Backups run only while the laptop/server is running.

```powershell
.\.venv\Scripts\python.exe maintenance.py backup
.\.venv\Scripts\python.exe maintenance.py check
```

For restore, stop **all** inventory servers first and supply the real copy:

```powershell
.\.venv\Scripts\python.exe maintenance.py restore --servers-stopped --from-file "D:\Backups\your-backup.sqlite3"
```

Restore verifies the file, makes a protected recovery copy and signs out old
sessions. The flag is your assertion that all servers stopped; known server
ports are also checked, but arbitrary custom processes cannot all be detected.

For manual updates: synchronize phone outboxes, stop servers, make a verified
backup and keep the old source. Replace source while preserving `data/`, your
environment and the server address. Restart the new version. Never open an
upgraded database with old code; restore a compatible pre-upgrade backup for
rollback. A guided updater remains future work.

## Android/offline use

See [ANDROID.md](docs/ANDROID.md). Local HTTPS preparation, an offline shell and
stock-create outbox are implemented; real Android installation, trust and
reconnection acceptance remain pending. Phone access uses the laptop's reachable
local address, not the phone's `127.0.0.1`.

Offline entries remain account-specific and conflicts wait for authorized
review. Corrections, administration and report generation currently require
the server. The optional PIN locks the screen; it does not encrypt storage.
Clearing browser storage can erase unsynchronized entries. Server backups do
not include entries still waiting on a phone.

## Tests and learning

```powershell
.\.venv\Scripts\python.exe verify.py
```

This runs isolated calculation, migration, workflow and release checks. With
optional Node.js installed it also checks browser scripts and offline requests.
Full HTTP integration tests, in a normal Windows session, use:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Read [TESTING.md](docs/TESTING.md) for results, [ARCHITECTURE.md](docs/ARCHITECTURE.md)
to learn the source, [REQUIREMENTS.md](docs/REQUIREMENTS.md) for the full agreed
plan and [DEPENDENCIES.md](docs/DEPENDENCIES.md) for free/open-source components.
For an educational walkthrough of a complete save, read
[LEARNING_GUIDE.md](docs/LEARNING_GUIDE.md).

Historical transactions can now be imported through **Administration → Import
dated stock history**. See [HISTORY_IMPORT.md](docs/HISTORY_IMPORT.md) for supported
columns, explicit date formats, duplicate review and recovery behavior.
