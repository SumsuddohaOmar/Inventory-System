"""HTTP entry point: validate input, call persistence, and serve the local UI.

Run with: python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
The factory lets tests use temporary databases without touching your products.
"""

from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
import logging
import os
import asyncio
import json
from contextlib import suppress
from pathlib import Path
import sqlite3
from uuid import UUID, uuid4
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, Field

from app import database
from app import stock
from app.version import VERSION

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"
logger = logging.getLogger(__name__)


class ProductInput(BaseModel):
    """Product fields accepted from the browser; trim before checking lengths."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    unit: str = Field(min_length=1, max_length=30)
    category_id: str = Field(min_length=1, max_length=80)
    precision: int = Field(default=3, ge=0, le=6, strict=True)


class Product(ProductInput):
    """Public stored record; ID and UTC creation time are server generated."""

    id: UUID
    created_at: datetime
    # Only old migrated records may be unassigned; new products require a category.
    category_id: str | None


class Category(BaseModel):
    """A stable category identity and its user-facing name."""

    id: str
    name: str


class CategoryAssignment(BaseModel):
    """A narrowly scoped edit: assign a product to one existing category."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    category_id: str = Field(min_length=1, max_length=80)


class StockInput(BaseModel):
    """A single explicit stock movement with a client-created retry UUID.

    quantity is a string to preserve decimals exactly across JSON. The stock
    module applies the product's precision and checks dated availability.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    id: UUID
    kind: Literal["opening", "receive", "issue"]
    effective_date: date
    quantity: str = Field(min_length=1, max_length=40, strict=True)
    notes: str = Field(default="", max_length=500)


def create_app(database_path: Path | None = None) -> FastAPI:
    """Build an independent app, optionally pointing to a test database.

    INVENTORY_DB accepts an absolute path for moving data outside the source
    folder. The default remains stable even if you run from another directory.
    """
    from app.postgres import PostgresTarget

    dsn = os.environ.get("INVENTORY_POSTGRES_URL") if database_path is None else None
    path = (PostgresTarget(dsn) if dsn else
            Path(database_path or os.environ.get("INVENTORY_DB", ROOT / "data" / "inventory.sqlite3")).resolve())

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        """Initialize storage before requests are accepted; no import-time writes."""
        if dsn:
            # Cloud schema is installed explicitly by the database operator.
            with database.connect(path) as connection:
                connection.execute("SELECT 1 FROM users LIMIT 1")
            yield
            return
        database.initialize(path)
        async def automatic_backups():
            """Keep daily copies while Windows is running; surface failures."""
            from app import backups
            while True:
                try:
                    def run_backup():
                        with database.connect(path) as connection:
                            row = connection.execute("SELECT value_json FROM settings WHERE key='backup_locations'").fetchone()
                        backups.daily(path, json.loads(row[0]) if row else [])
                    await asyncio.to_thread(run_backup)
                    message = "Automatic backup checked successfully."
                except Exception:
                    logger.exception("Automatic inventory backup failed")
                    message = "Automatic backup failed. Check backup folders and create a manual copy."
                with database.connect(path) as connection:
                    connection.execute("INSERT INTO settings VALUES ('backup_status', ?) ON CONFLICT (key) DO UPDATE SET value_json=excluded.value_json", (json.dumps(message),))
                await asyncio.sleep(3600)
        task = asyncio.create_task(automatic_backups())
        try: yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError): await task

    application = FastAPI(title="Local Inventory · Phase 1", version=VERSION, lifespan=lifespan)
    application.state.operator_setup = bool(dsn) or os.environ.get("INVENTORY_OPERATOR_SETUP") == "1"
    application.state.postgres_pilot = bool(dsn)
    application.state.public_origin = os.environ.get("INVENTORY_PUBLIC_ORIGIN") if dsn else None
    # A local Host allowlist also limits browser DNS-rebinding attacks. LAN
    # access uses the explicit authenticated HTTPS launcher configuration.
    allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    # LAN hosts are added only by the explicit HTTPS launcher configuration.
    for host in os.environ.get("INVENTORY_ALLOWED_HOSTS", "").split(","):
        if host.strip() and "*" not in host:allowed_hosts.append(host.strip())
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    @application.middleware("http")
    async def browser_headers(request: Request, call_next):
        """Serve only our own scripts/styles and prevent stale inventory caching."""
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(sqlite3.Error)
    async def storage_error(request: Request, error: sqlite3.Error):
        """Give the user a useful failure without exposing SQL or local paths."""
        logger.error("SQLite operation failed", exc_info=error)
        return JSONResponse(status_code=503, content={"detail": "Storage unavailable. Please try again; check the server if it continues."})

    @application.exception_handler(stock.StockError)
    async def stock_conflict(request: Request, error: stock.StockError):
        """Return safe business-rule failures without committing a partial write."""
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @application.exception_handler(stock.MissingProduct)
    async def missing_stock_product(request: Request, error: stock.MissingProduct):
        """Use the same missing-product meaning for ledger and product routes."""
        return JSONResponse(status_code=404, content={"detail": str(error)})

    @application.get("/", include_in_schema=False)
    def home():
        """Return the accessible browser page; assets are all served locally."""
        # Render the storage label from the server configuration, even offline
        # when the browser uses its cached copy of this app shell.
        page = (STATIC / "index.html").read_text(encoding="utf-8")
        storage = "Cloud storage: PostgreSQL" if dsn else "Local storage: SQLite"
        return HTMLResponse(page.replace("Inventory workspace · No subscription", f"{storage} · No subscription"))

    @application.get("/api/products", response_model=list[Product])
    def products(category_id: str | None = None):
        """Read all, one category, or old unassigned products; search comes later."""
        if category_id is not None and category_id != "unassigned":
            require_category(category_id)
        return database.list_products(path, category_id)

    def require_category(category_id: str):
        """Reject unknown category IDs before a write reaches SQLite."""
        if not any(category["id"] == category_id for category in database.list_categories(path)):
            raise HTTPException(status_code=422, detail="Choose an existing category.")

    @application.get("/api/categories", response_model=list[Category])
    def categories():
        """Expose the seeded category records so the browser never invents IDs."""
        return database.list_categories(path)

    @application.post("/api/products", response_model=Product, status_code=201)
    def add_product(product: ProductInput):
        """Validate then save one product; malformed input receives HTTP 422.

        JSON is required, and cross-origin access is not enabled. There is no
        authentication yet: keep this learning milestone on the loopback address.
        """
        require_category(product.category_id)
        record = product.model_dump()
        record.update(id=str(uuid4()), created_at=datetime.now(timezone.utc).isoformat())
        return database.insert_product(path, record)

    @application.get("/api/products/{product_id}", response_model=Product)
    def product_by_id(product_id: UUID):
        """Look up a UUID; distinguish missing records (404) from bad IDs (422)."""
        product = database.find_product(path, str(product_id))
        if product is None:
            raise HTTPException(status_code=404, detail="Product not found.")
        return product

    @application.get("/api/products/{product_id}/stock")
    def product_stock(product_id: UUID):
        """Read exact totals and dated history; no balance is guessed or cached."""
        return stock.get_stock(path, str(product_id))

    @application.post("/api/products/{product_id}/stock", status_code=201)
    def add_stock(product_id: UUID, entry: StockInput):
        """Save a dated opening, receipt or issue, safely handling identical retries."""
        return stock.save_entry(path, str(product_id), entry.model_dump(mode="json"))

    @application.patch("/api/products/{product_id}/category", response_model=Product)
    def change_category(product_id: UUID, assignment: CategoryAssignment):
        """Categorize existing saved products while preserving their IDs and details.

        Stock entries refer to the product UUID, so changing its category does
        not lose its ledger. Permission checks, audit and report review will be
        added in their planned phases; this remains a localhost prototype.
        """
        require_category(assignment.category_id)
        product = database.assign_category(path, str(product_id), assignment.category_id)
        if product is None:
            raise HTTPException(status_code=404, detail="Product not found.")
        return product

    from app.extended_api import register
    register(application, path)

    @application.get("/sw.js", include_in_schema=False)
    def service_worker():
        """Root scope allows caching the local app shell, never API secrets."""
        return FileResponse(STATIC / "sw.js", media_type="application/javascript")

    application.mount("/static", StaticFiles(directory=STATIC), name="static")
    return application


app = create_app()
