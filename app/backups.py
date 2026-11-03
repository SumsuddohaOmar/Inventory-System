"""Verified SQLite copies, protected recovery points and conservative retention.

Never delete inventory history to retain backups. Manual/pre-upgrade/pre-restore
copies are protected; automatic cleanup runs only after a new verified copy.
Restore is an OFFLINE utility so active servers cannot write during replacement.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4

from app.database import connect


def create(path: Path, kind="manual", destinations=()) -> dict:
    """Use SQLite's online backup API, verify integrity, then copy to extra locations."""
    folder = path.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{kind}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid4()}.sqlite3"
    target = folder / name
    with connect(path) as source, connect(target) as backup:
        source.backup(backup)
        if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok": raise RuntimeError("Backup integrity verification failed.")
    copies = [str(target)]
    for location in destinations:
        destination = Path(location).expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, destination / name)
        # Verify copied bytes as well; cleanup must not proceed after any failure.
        if (destination / name).read_bytes() != target.read_bytes(): raise RuntimeError("Extra backup copy verification failed.")
        copies.append(str(destination / name))
    if kind == "auto": retain(folder)
    return {"name": name, "copies": copies, "size": target.stat().st_size}


def retain(folder):
    """Keep 30 daily, 12 weekly and 12 monthly checkpoints; protect other files."""
    files = sorted(folder.glob("auto-*.sqlite3"), reverse=True)
    daily, weekly, monthly, keep = set(), set(), set(), set()
    for file in files:
        try: day = datetime.strptime(file.name.split("-")[1], "%Y%m%dT%H%M%S").date()
        except ValueError: continue
        keys = (day.isoformat(), day.isocalendar()[:2], (day.year, day.month))
        for seen, limit, key in zip((daily, weekly, monthly), (30, 12, 12), keys):
            if key not in seen and len(seen) < limit:
                seen.add(key)
                keep.add(file)
    for file in files:
        if file not in keep: file.unlink()


def daily(path, destinations=()):
    """Make at most one automatic backup per UTC day on startup/maintenance."""
    prefix = f"auto-{datetime.now(timezone.utc):%Y%m%d}"
    folder = path.parent / "backups"
    if not any(folder.glob(prefix + "*.sqlite3")): return create(path, "auto", destinations)


def restore_offline(path, backup):
    """Restore a verified copy only after ALL inventory servers have been stopped.

    This function is exposed by maintenance.py, not an online web endpoint. The
    operator must stop processes first. Preserve a protected pre-restore copy,
    verify the source, then replace using SQLite backup rather than raw copying.
    """
    backup = Path(backup).resolve()
    if backup == path.resolve(): raise ValueError("Source and destination must differ.")
    with connect(backup) as source:
        if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok": raise ValueError("Recovery copy failed verification.")
        version = source.execute("PRAGMA user_version").fetchone()[0]
        if not 1 <= version <= 4 or not source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='products'").fetchone():
            raise ValueError("This is not a supported inventory recovery copy.")
    protected = create(path, "protected-pre-restore")
    with connect(backup) as source, connect(path) as destination: source.backup(destination)
    # Restoring an old backup must not resurrect previously revoked sessions.
    with connect(path) as connection:
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name='sessions'").fetchone(): connection.execute("DELETE FROM sessions")
    return protected
