"""Run a fresh, fictional Hub-16 workspace on this computer."""

from datetime import datetime, timezone
from pathlib import Path
from secrets import token_urlsafe
from tempfile import mkdtemp
from uuid import uuid4

import uvicorn

from app import accounts, database, stock
from app.main import create_app


def main():
    # An explicit new SQLite file prevents an inherited database URL or path
    # from redirecting any of the fixture writes to an existing inventory.
    sample_root = Path(__file__).resolve().parent / "data" / "hub16-samples"
    sample_root.mkdir(parents=True, exist_ok=True)
    path = Path(mkdtemp(dir=sample_root, prefix="run-")) / "inventory.sqlite3"
    database.initialize(path)

    admin_password, employee_password = token_urlsafe(24), token_urlsafe(24)
    accounts.create_user(path, "sample-admin", admin_password, first=True)
    accounts.create_user(path, "sample-employee", employee_password, permissions=("mrf_catalog",))
    for name, unit, category, quantity in (
        ("Sample Rice", "kg", "dry-foods", "100"),
        ("Sample Cups", "box", "disposables", "25"),
    ):
        product = database.insert_product(path, {
            "id": str(uuid4()), "name": name, "unit": unit, "category_id": category,
            "created_at": datetime.now(timezone.utc).isoformat(), "precision": 0,
        })
        stock.save_entry(path, product["id"], {
            "id": str(uuid4()), "kind": "opening", "quantity": quantity,
            "effective_date": stock.today(), "notes": "Fictional opening stock",
        })

    # Bind only to this machine. A separate port also gives browser caches and
    # service workers a different origin from the usual inventory workspace.
    print(f"Sample database: {path}", flush=True)
    print(f"Sample administrator: sample-admin / {admin_password}", flush=True)
    print(f"Sample employee: sample-employee / {employee_password}", flush=True)
    uvicorn.run(create_app(path), host="127.0.0.1", port=8765)


if __name__ == "__main__":
    main()
