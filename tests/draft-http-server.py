"""Disposable local HTTP fixture for the full browser bulk-entry regression."""
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import accounts,database,stock
from app.main import create_app
import uvicorn

if __name__=="__main__":
    with TemporaryDirectory(prefix="draft-http-") as folder:
        path=Path(folder)/"inventory.sqlite3"
        database.initialize(path)
        accounts.ITERATIONS=1000  # Disposable test account only.
        accounts.create_user(path,"draft-test","local test passphrase",first=True)
        product=database.insert_product(path,dict(id=str(uuid4()),name="Draft test rice",unit="KG",category_id="dry-foods",precision=1,created_at="2026-11-01"))
        stock.save_entry(path,product["id"],dict(id=str(uuid4()),kind="opening",effective_date="2026-11-01",quantity="100",notes="Disposable test"))
        app=create_app(path)
        app.state.postgres_pilot=True
        app.state.public_origin="http://127.0.0.1:8129"
        uvicorn.run(app,host="127.0.0.1",port=8129,log_level="warning")
