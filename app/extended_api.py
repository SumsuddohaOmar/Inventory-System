"""Application workflows and centralized server-side account enforcement.

Keep this separate from the introductory routes so the educational first steps
remain readable. Every API request except setup/session requires authentication;
each write also checks its permission, including every offline-sync operation.
"""

from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from app import accounts, audit, backups, database, imports, reports, stock, workflows


class Credentials(BaseModel):
    """Credentials only enter password hashing/session code, never the audit trail."""
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)
    remember: bool = False


class Employee(Credentials):
    """An administrator may create employees with explicit custom capabilities."""
    permissions: list[str] = Field(default_factory=lambda: ["read"])
    admin: bool = False


class EmployeeChange(BaseModel):
    """Only permissions/status are editable here; passwords never echo back."""
    permissions: list[str]
    active: bool


class Correction(BaseModel):
    """A versioned operation; the original entry ID remains stable."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: UUID
    expected_revision: int = Field(ge=1)
    effective_date: date
    quantity: str = Field(min_length=1, max_length=40, strict=True)
    notes: str = Field(default="", max_length=500)
    voided: bool = False


class ReportInput(BaseModel):
    """Custom, cumulative-Friday or full-month reporting with optional item selection."""
    start: date
    end: date
    kind: Literal["custom", "friday", "monthly"] = "custom"
    product_ids: list[UUID] = Field(default_factory=list)
    exclude_zero: bool = True
    name: str = Field(default="Inventory report", min_length=1, max_length=120)


class ProductChange(BaseModel):
    """Descriptive/ordering changes never reinterpret existing units or quantities."""
    name: str = Field(min_length=1, max_length=120)
    code: str = Field(default="", max_length=80)
    notes: str = Field(default="", max_length=1000)
    archived: bool = False
    low_stock: str | None = None
    sort_order: int = 0
    custom_fields: dict[str,str] = Field(default_factory=dict)


class CategoryChange(BaseModel):
    """Category names/order may change while their identities/history stay stable."""
    name: str = Field(min_length=1,max_length=120)
    position: int = Field(default=0,ge=0,le=10000)
    archived: bool = False


class DailyChange(BaseModel):
    """A daily grid edit carries the revisions of all underlying active entries."""
    id: UUID
    kind: Literal["receive","issue"]
    effective_date: date
    quantity: str = Field(min_length=1,max_length=40,strict=True)
    expected_entries: dict[str,int]
    notes: str = Field(default="Daily total correction",max_length=500)


class ImportRow(BaseModel):
    """Only reviewed, bounded product fields may enter the import transaction."""
    row:int=Field(ge=1)
    name:str=Field(max_length=120)
    unit:str=Field(max_length=30)
    code:str=Field(default="",max_length=80)
    opening:str=Field(default="",max_length=40,strict=True)
    action:Literal["skip","create","link"]
    target_id:UUID|None=None


class ImportApply(BaseModel):
    id:UUID
    category_id:str=Field(min_length=1,max_length=80)
    date:date
    rows:list[ImportRow]=Field(min_length=1,max_length=200)


class HistoryRow(BaseModel):
    """Every imported movement targets an explicitly chosen existing product."""
    model_config=ConfigDict(extra="forbid",str_strip_whitespace=True)
    row:int=Field(ge=1)
    id:UUID
    product_id:UUID|None=None
    kind:Literal["opening","receive","issue"]|None=None
    effective_date:date|None=None
    quantity:str=Field(max_length=40,strict=True)
    notes:str=Field(default="",max_length=500)
    action:Literal["skip","import"]
    acknowledge_duplicate:bool=False


class HistoryApply(BaseModel):
    id:UUID
    filename:str=Field(min_length=1,max_length=120)
    rows:list[HistoryRow]=Field(min_length=1,max_length=200)


def register(application, path):
    """Attach authenticated workflows to one application/database factory instance."""
    # Local import avoids a module cycle while sharing the exact stock model.
    from app.main import StockInput
    from app import excel_feed
    excel_feed.register(application,path)

    def require(user, permission):
        if permission not in user["permissions"]: raise HTTPException(403, f"Permission required: {permission}")

    @application.post("/api/history-import/inspect")
    def inspect_history(values:dict):
        from app import history_imports
        try:return history_imports.inspect(values)
        except stock.StockError:raise
        except Exception:raise HTTPException(422,"Could not read history headers. Check the file, worksheet and header row.")

    @application.post("/api/history-import/preview")
    def preview_history(values:dict):
        from app import history_imports
        try:return history_imports.preview(path,values)
        except stock.StockError:raise
        except Exception:raise HTTPException(422,"Could not read this history file. Check its columns and date format.")

    @application.post("/api/history-import/apply")
    def apply_history(values:HistoryApply,request:Request):
        from app import history_imports
        require(request.state.user,"products")
        # Authorize the complete batch before backup or any ledger mutation.
        # Product-management rights alone never grant stock-writing rights.
        for row in values.rows:
            if row.action=="skip":continue
            if not row.product_id or not row.kind or not row.effective_date:
                raise HTTPException(422,f"Row {row.row} needs a product, type and valid date.")
            require(request.state.user,"issue" if row.kind=="issue" else "receive")
        return history_imports.apply(path,values.model_dump(mode="json"))

    @application.middleware("http")
    async def access_control(request: Request, call_next):
        """Authenticate before serving data and reject unexpected browser origins."""
        route = request.url.path
        if application.state.postgres_pilot and route != "/api/import/preview" and route.startswith(
            ("/api/backups", "/api/import", "/api/history-import")
        ):
            return JSONResponse(status_code=501, content={"detail": "Unavailable on the hosted pilot until cloud backup and restore are verified. No data was changed."})
        if route.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            expected_origin = application.state.public_origin or str(request.base_url).rstrip("/")
            if origin and origin != expected_origin:
                return JSONResponse(status_code=403, content={"detail": "Cross-origin writes are blocked."})
            if len(await request.body()) > 4 * 1024 * 1024:
                return JSONResponse(status_code=413, content={"detail": "Request is too large."})
        if not route.startswith("/api/") or route in ("/api/session", "/api/setup", "/api/logout"):
            return await call_next(request)
        if route.startswith("/api/excel-feed/"):
            # This narrow GET-only service authenticates its scoped Basic
            # password itself. It never grants a browser session or write access.
            if request.method not in ("GET","HEAD"):
                return JSONResponse(status_code=405,content={"detail":"The Excel feed is read-only."})
            return await call_next(request)
        user = accounts.session_user(path, request.cookies.get("inventory_session"))
        if not user: return JSONResponse(status_code=401, content={"detail": "Sign in to use your inventory."})
        # A tab can retain an offline profile after another tab switches the
        # shared session cookie. Never post its queue under the new account.
        owner = request.headers.get("x-inventory-owner")
        if route in ("/api/sync", "/api/sync/status") and not owner:
            return JSONResponse(status_code=409, content={"detail": "Reload this app before synchronizing. The queue owner must be confirmed; existing pending entries are kept."})
        if owner and owner != user["id"]:
            return JSONResponse(status_code=409, content={"detail": "The signed-in account changed. Pending entries remain with their original account. Sign back in to synchronize."})
        if route.startswith("/api/sync") and not application.state.postgres_pilot:
            return JSONResponse(status_code=409, content={"detail": "Pending queues synchronize only with the cloud inventory. Local server data is not a cloud sync target."})
        request.state.user = user
        permissions = {"read"}
        if route.startswith("/api/excel/"): permissions = {"read","reports"}
        elif route.startswith("/api/users"): permissions = {"users"}
        elif route.startswith("/api/audit"): permissions = {"audit"}
        elif route.startswith("/api/backups"): permissions = {"backups"}
        elif route.startswith(("/api/reports","/api/templates")): permissions = {"reports"}
        elif route.startswith(("/api/settings", "/api/months")): permissions = {"settings"}
        elif request.method not in ("GET", "HEAD", "OPTIONS"):
            if route.startswith("/api/sync"): permissions = {"read"}  # Each operation is checked separately below.
            elif route.endswith("/stock") or route == "/api/bulk":
                try:
                    body = await request.json()
                    rows = body.get("entries", []) if route == "/api/bulk" else [body]
                    permissions = {"receive" if row.get("kind") in ("receive", "opening") else "issue" for row in rows}
                except (ValueError, AttributeError): permissions = {"receive", "issue"}
            elif route.startswith("/api/entries/") or route.endswith("/daily-total"): permissions = {"correct"}
            elif route.startswith("/api/drafts"): permissions = {"read"}  # Finalization checks current rights explicitly.
            else: permissions = {"products"}
        if not permissions.issubset(user["permissions"]):
            return JSONResponse(status_code=403, content={"detail": "Your current permissions do not allow this action. Pending entries are kept for review."})
        token = audit.actor.set(user["id"])
        try: return await call_next(request)
        finally: audit.actor.reset(token)

    @application.exception_handler(accounts.AccountError)
    async def account_error(request, error):
        return JSONResponse(status_code=400, content={"detail": str(error)})

    @application.exception_handler(ValueError)
    @application.exception_handler(KeyError)
    async def invalid_values(request, error):
        return JSONResponse(status_code=422, content={"detail": "Invalid input. Check IDs, dates, quantities and required fields."})

    def sign_in(response, token, remember, secure):
        """HttpOnly prevents scripts reading the session; SameSite restricts CSRF."""
        response.set_cookie("inventory_session", token, httponly=True, samesite="strict", secure=secure,
                            max_age=30 * 86400 if remember else None)

    @application.get("/api/session")
    def session(request: Request):
        return {"configured": accounts.configured(path), "operator_setup": request.app.state.operator_setup, "user": accounts.session_user(path, request.cookies.get("inventory_session")), "permissions": accounts.PERMISSIONS}

    @application.get("/api/connection/qr.svg")
    def connection_qr(request:Request):
        from app.connection_qr import svg
        return Response(svg(application.state.public_origin or str(request.base_url).rstrip("/")),media_type="image/svg+xml")

    @application.post("/api/setup")
    def setup(values: Credentials, request: Request):
        # Hosted databases are provisioned by their operator before public use.
        if request.app.state.operator_setup:
            raise HTTPException(403, "Ask the inventory operator to create the first account.")
        accounts.create_user(path, values.username, values.password, admin=True, first=True)
        token, user = accounts.login(path, values.username, values.password, values.remember)
        response = JSONResponse(user)
        sign_in(response, token, values.remember, request.url.scheme == "https" or bool(application.state.public_origin))
        return response

    @application.post("/api/session")
    def login(values: Credentials, request: Request):
        token, user = accounts.login(path, values.username, values.password, values.remember)
        response = JSONResponse(user)
        sign_in(response, token, values.remember, request.url.scheme == "https" or bool(application.state.public_origin))
        return response

    @application.post("/api/logout")
    def logout(request: Request):
        accounts.logout(path, request.cookies.get("inventory_session"))
        response = JSONResponse({"signed_out": True})
        response.delete_cookie("inventory_session")
        return response

    @application.get("/api/users")
    def users():
        with database.connect(path) as connection: return [accounts.public_user(row) for row in connection.execute("SELECT * FROM users ORDER BY username")]

    @application.post("/api/users")
    def add_user(values: Employee):
        return accounts.create_user(path, values.username, values.password, values.permissions, values.admin)

    @application.patch("/api/users/{identity}")
    def change_user(identity: UUID, values: EmployeeChange):
        return accounts.update_user(path, str(identity), values.permissions, values.active)

    @application.get("/api/audit")
    def history(limit: int = 100, entity_id: str | None = None):
        limit = min(max(limit, 1), 500)
        with database.connect(path) as connection:
            # PostgreSQL cannot infer the type of a parameter used only in IS NULL.
            if entity_id is None:
                rows = connection.execute(
                    "SELECT a.*, u.username AS actor_name FROM audit_events a LEFT JOIN users u ON u.id=a.actor ORDER BY a.created_at DESC LIMIT ?", (limit,)
                )
            else:
                rows = connection.execute(
                    "SELECT a.*, u.username AS actor_name FROM audit_events a LEFT JOIN users u ON u.id=a.actor WHERE a.entity_id=? ORDER BY a.created_at DESC LIMIT ?",
                    (entity_id, limit),
                )
            return [dict(row) for row in rows]

    @application.get("/api/catalog")
    def catalog():
        with database.connect(path) as connection:
            connection.execute("BEGIN")
            today = stock.company_today(connection)
            entries = {}
            for entry in connection.execute(
                "SELECT * FROM stock_entries ORDER BY product_id, effective_date DESC, created_at DESC, id"
            ):
                entries.setdefault(entry["product_id"], []).append(entry)
            result = []
            for row in connection.execute("SELECT * FROM products ORDER BY sort_order,name").fetchall():
                product = dict(row)
                product["stock"] = stock.summarize(product, entries.get(product["id"], ()), today)
                result.append(product)
            return result

    @application.patch("/api/catalog/{identity}")
    def edit_product(identity: UUID, values: ProductChange):
        with database.connect(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM products WHERE id=?", (str(identity),)).fetchone()
            if not row: raise HTTPException(404, "Product not found.")
            low = stock.to_minor(values.low_stock, row["precision"]) if values.low_stock not in (None, "") else None
            if len(values.custom_fields)>30 or any(not 1<=len(key)<=60 or len(value)>1000 for key,value in values.custom_fields.items()):raise HTTPException(422,"Use at most 30 custom fields, with short labels and values.")
            connection.execute("UPDATE products SET name=?,code=?,notes=?,archived=?,low_stock_minor=?,sort_order=? WHERE id=?",
                               (values.name, values.code, values.notes, int(values.archived), low, values.sort_order, str(identity)))
            connection.execute("UPDATE products SET fields_json=? WHERE id=?",(json.dumps(values.custom_fields),str(identity)))
            after = dict(connection.execute("SELECT * FROM products WHERE id=?", (str(identity),)).fetchone())
            audit.record(connection, "product.edited", str(identity), dict(row), after)
            return after

    @application.delete("/api/catalog/{identity}")
    def safe_delete_product(identity: UUID, request: Request, permanent: bool = False):
        if permanent and not request.state.user["admin"]:
            raise HTTPException(403, "Only an administrator can delete a product with stock history.")
        product_id = str(identity)
        with database.connect(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
            if not row:
                raise HTTPException(404, "Product not found.")
            reference = f"%{product_id}%"
            # Finalized drafts and report versions are historical snapshots.
            # Keep them intact while allowing administrators to remove live stock.
            if permanent:
                drafts = connection.execute(
                    "SELECT id,payload_json FROM drafts WHERE finalized=0 AND payload_json LIKE ?", (reference,)
                ).fetchall()
                for draft in drafts:
                    entries = json.loads(draft["payload_json"])
                    remaining = [entry for entry in entries if entry.get("product_id") != product_id]
                    if len(remaining) == len(entries):
                        continue
                    # Bump the revision so a tab with the old draft cannot overwrite this edit.
                    connection.execute(
                        "UPDATE drafts SET payload_json=?,revision=revision+1 WHERE id=?",
                        (json.dumps(remaining), draft["id"]),
                    )
            else:
                used = connection.execute(
                    "SELECT 1 FROM drafts WHERE payload_json LIKE ? "
                    "UNION ALL SELECT 1 FROM report_versions WHERE payload_json LIKE ? LIMIT 1",
                    (reference, reference),
                ).fetchone()
                if used:
                    raise stock.StockError("This product is referenced by a saved draft or report. Archive it instead.")
            if permanent:
                # Remove movements before the product to satisfy the ledger's foreign key.
                # The deletion and its audit record commit together.
                connection.execute("DELETE FROM stock_entries WHERE product_id=?", (product_id,))
            elif connection.execute("SELECT 1 FROM stock_entries WHERE product_id=?", (product_id,)).fetchone():
                raise stock.StockError("This product has stock history. Archive it or ask an administrator to delete it.")
            action = "product.permanently_deleted" if permanent else "product.safely_deleted"
            audit.record(connection, action, product_id, before=dict(row))
            connection.execute("DELETE FROM products WHERE id=?", (product_id,))
            return {"deleted": True}

    @application.get("/api/category-management")
    def all_categories():
        with database.connect(path) as connection:return [dict(row) for row in connection.execute("SELECT * FROM categories ORDER BY position,name")]

    @application.post("/api/category-management")
    def create_category(values: CategoryChange):
        identity=str(uuid4())
        with database.connect(path) as connection:
            if any(row[0].casefold()==values.name.strip().casefold() for row in connection.execute("SELECT name FROM categories")):raise stock.StockError("That category name already exists.")
            connection.execute("INSERT INTO categories VALUES (?,?,?,?)",(identity,values.name.strip(),values.position,int(values.archived)))
            audit.record(connection,"category.created",identity,after=values.model_dump())
        return {"id":identity,**values.model_dump()}

    @application.patch("/api/category-management/{identity}")
    def edit_category(identity: str, values: CategoryChange):
        with database.connect(path) as connection:
            before=connection.execute("SELECT * FROM categories WHERE id=?",(identity,)).fetchone()
            if not before:raise HTTPException(404,"Category not found.")
            if any(row[0].casefold()==values.name.strip().casefold() for row in connection.execute("SELECT name FROM categories WHERE id<>?",(identity,))):raise stock.StockError("That category name already exists.")
            connection.execute("UPDATE categories SET name=?,position=?,archived=? WHERE id=?",(values.name.strip(),values.position,int(values.archived),identity))
            audit.record(connection,"category.edited",identity,dict(before),values.model_dump())
        return {"id":identity,**values.model_dump()}

    @application.post("/api/import/preview")
    def preview_import(values: dict):
        try:return imports.preview(path,values)
        except stock.StockError:raise
        except Exception:raise HTTPException(422,"Could not read that CSV/XLSX worksheet. Check file type, sheet and header row.")

    @application.post("/api/import/apply")
    def apply_import(values: ImportApply, request: Request):
        # A product-management permission alone cannot introduce opening stock.
        if any(row.opening!="" and row.action=="create" for row in values.rows):require(request.state.user,"receive")
        return imports.apply(path,values.model_dump(mode="json"))

    @application.post("/api/bulk")
    def save_bulk(values: dict):
        identity = str(UUID(values["id"]))
        rows = []
        for row in values.get("entries", []):
            product = str(UUID(row["product_id"]))
            parsed = StockInput(**{key: value for key, value in row.items() if key != "product_id"})
            rows.append({"product_id": product, **parsed.model_dump(mode="json")})
        return workflows.bulk(path, identity, rows)

    @application.patch("/api/entries/{identity}")
    def correction(identity: UUID, values: Correction):
        return workflows.correct(path, str(identity), values.model_dump(mode="json"))

    @application.put("/api/products/{identity}/daily-total")
    def daily_total(identity:UUID,values:DailyChange):
        return workflows.set_daily_total(path,str(identity),values.model_dump(mode="json"))

    @application.get("/api/drafts")
    def drafts():
        with database.connect(path) as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM drafts WHERE owner=? AND finalized=0 ORDER BY created_at DESC", (audit.actor.get(),))]

    @application.put("/api/drafts/{identity}")
    def draft(identity: UUID, values: dict):
        rows = []
        for row in values.get("entries", []):
            rows.append({"product_id": str(UUID(row["product_id"])), **StockInput(**{key: value for key, value in row.items() if key != "product_id"}).model_dump(mode="json")})
        if len(rows) > 200: raise HTTPException(422, "Too many draft rows.")
        return workflows.save_draft(path, str(identity), rows, int(values.get("revision", 0)))

    @application.post("/api/drafts/{identity}/finalize")
    def finalize(identity: UUID, request: Request):
        with database.connect(path) as connection:
            row = connection.execute("SELECT * FROM drafts WHERE id=? AND owner=?", (str(identity), audit.actor.get())).fetchone()
            if not row: raise HTTPException(404, "Draft not found.")
            for entry in json.loads(row["payload_json"]): require(request.state.user, "issue" if entry["kind"] == "issue" else "receive")
        return workflows.finalize_draft(path, str(identity))

    @application.post("/api/sync")
    def synchronize(values: dict, request: Request):
        """Hold conflicts/dependencies, sync independent entries; never discard them."""
        outcomes, blocked, failed = [], set(), set()
        operations = values.get("operations", [])
        if not isinstance(operations, list) or len(operations) > 200: raise HTTPException(422, "Sync accepts at most 200 operations.")
        if any(not isinstance(operation,dict) or not isinstance(operation.get("depends_on",[]),list) or len(operation.get("depends_on",[]))>200 for operation in operations):
            raise HTTPException(422,"Each sync operation must be an object with at most 200 dependencies.")
        # Validate envelope identities before executing ANY row. Malformed JSON
        # types must not reach SQLite bindings or create unusable held records.
        for operation in operations:
            identities=[operation.get("id"),operation.get("product_id"),*operation.get("depends_on",[])]
            if operation.get("resolves_id") is not None:identities.append(operation["resolves_id"])
            try:
                for identity in identities:
                    if not isinstance(identity,str):raise ValueError("Identity must be text")
                    UUID(identity)
            except ValueError:
                raise HTTPException(422,"Sync operation, product, review and dependency IDs must be UUID strings.")
        for operation in operations:
            identity = operation.get("id", "")
            product = operation.get("product_id", "")
            try:
                with database.connect(path) as connection:
                    # Claim/review the conflict and apply its replacement under
                    # one write lock. Two reviewers cannot both resolve it.
                    connection.execute("BEGIN IMMEDIATE")
                    committed=connection.execute("SELECT 1 FROM operations WHERE id=?",(identity,)).fetchone()
                    if committed:
                        # This is a receipt lookup, not a new stock write. A
                        # lost response followed by permission revocation must
                        # never turn an already-saved movement into a reviewable
                        # replacement (which could duplicate the original).
                        try:
                            values=StockInput(**{key:value for key,value in operation.items() if key not in ("product_id","depends_on","resolves_id")})
                            result=stock.save_entry(path,str(UUID(product)),values.model_dump(mode="json"),connection)
                        except (stock.StockError, ValueError):
                            failed.add(identity);blocked.add(product)
                            outcomes.append({"id":identity,"status":"held","reviewable":False,"reason":"This save ID is already committed with different data. Check stock history; it cannot be replaced through conflict review."})
                            continue
                        outcomes.append({"id":identity,"status":"saved","result":result})
                        # Reconcile any hold left by an older client/server
                        # version for a save that is already in the ledger.
                        connection.execute("UPDATE sync_holds SET resolved=1 WHERE id=?",(identity,))
                        continue
                    held=connection.execute("SELECT * FROM sync_holds WHERE id=?",(identity,)).fetchone()
                    if held:
                        if held["owner"] != request.state.user["id"]:
                            require(request.state.user, "correct")
                        if held["resolved"]:
                            outcomes.append({"id":identity,"status":"resolved","reason":"This held operation was explicitly reviewed."});continue
                        failed.add(identity);blocked.add(product)
                        outcomes.append({"id":identity,"status":"held","reason":"Manual authorized review is required: "+held["reason"]});continue
                    unresolved=any(dependency in failed or not(connection.execute("SELECT 1 FROM operations WHERE id=?",(dependency,)).fetchone() or connection.execute("SELECT 1 FROM sync_holds WHERE id=? AND resolved=1",(dependency,)).fetchone()) for dependency in operation.get("depends_on",[]))
                    if product in blocked or unresolved:
                        outcomes.append({"id":identity,"status":"waiting","reason":"A dependent entry awaits authorized review."});failed.add(identity);continue
                    resolution=None
                    if operation.get("resolves_id"):
                        require(request.state.user,"correct")
                        resolution=connection.execute("SELECT * FROM sync_holds WHERE id=?",(operation["resolves_id"],)).fetchone()
                        already=connection.execute("SELECT 1 FROM operations WHERE id=?",(identity,)).fetchone()
                        if not resolution:raise stock.StockError("The held operation no longer exists.")
                        original=json.loads(resolution["payload_json"])
                        if original.get("product_id")!=product or original.get("kind")!=operation.get("kind"):raise stock.StockError("A reviewed replacement must keep the original product and transaction type.")
                        original_saved=connection.execute("SELECT 1 FROM operations WHERE id=?",(operation["resolves_id"],)).fetchone()
                        if (resolution["resolved"] or original_saved) and not already:
                            # A second reviewer must not create another stock
                            # entry or another held conflict for the same review.
                            # Keep a resolved alias: another device may already
                            # have queued dependents against this review UUID.
                            connection.execute("INSERT INTO sync_holds VALUES (?,?,?,?,1) ON CONFLICT (id) DO NOTHING",
                                               (identity,request.state.user["id"],json.dumps(operation),"Another authorized review resolved the original conflict."))
                            connection.execute("UPDATE sync_holds SET resolved=1 WHERE id=?",(operation["resolves_id"],))
                            outcomes.append({"id":identity,"status":"resolved","reason":"Another review already resolved this conflict; this replacement was not posted."})
                            continue
                    values=StockInput(**{key:value for key,value in operation.items() if key not in ("product_id","depends_on","resolves_id")})
                    require(request.state.user,"issue" if values.kind=="issue" else "receive")
                    result=stock.save_entry(path,str(UUID(product)),values.model_dump(mode="json"),connection)
                    if resolution:
                        # A reviewed replacement can itself have been held. A
                        # successful later review resolves that entire ancestry,
                        # so devices waiting on the original can move forward.
                        ancestor=operation["resolves_id"]
                        visited=set()
                        while ancestor and ancestor not in visited:
                            visited.add(ancestor)
                            previous=connection.execute("SELECT * FROM sync_holds WHERE id=?",(ancestor,)).fetchone()
                            if not previous:break
                            payload=json.loads(previous["payload_json"])
                            if payload.get("product_id")!=product or payload.get("kind")!=values.kind:break
                            connection.execute("UPDATE sync_holds SET resolved=1 WHERE id=?",(ancestor,))
                            ancestor=payload.get("resolves_id")
                        audit.record(connection, "sync.resolved", str(identity), before={"held_id":operation["resolves_id"],"original_owner":resolution["owner"]}, after=result)
                outcomes.append({"id": identity, "status": "saved", "result": result})
            except (stock.StockError, stock.MissingProduct, ValueError, HTTPException) as error:
                blocked.add(product)
                failed.add(identity)
                with database.connect(path) as connection:
                    reason=str(getattr(error,"detail",str(error)))
                    connection.execute("INSERT INTO sync_holds VALUES (?,?,?,?,0) ON CONFLICT (id) DO NOTHING",(str(identity),audit.actor.get(),json.dumps(operation),reason))
                    audit.record(connection, "sync.held", str(identity), after={"reason":reason})
                outcomes.append({"id": identity, "status": "held", "reason": getattr(error, "detail", str(error))})
        return outcomes

    @application.get("/api/sync/holds")
    def sync_holds(request:Request):
        require(request.state.user,"correct")
        with database.connect(path) as connection:return [dict(row) for row in connection.execute("SELECT * FROM sync_holds WHERE resolved=0")]

    @application.post("/api/sync/status")
    def sync_status(values:dict,request:Request):
        ids=values.get("ids",[])
        if not isinstance(ids,list) or len(ids)>200:raise HTTPException(422,"Check at most 200 save IDs.")
        result=[]
        with database.connect(path) as connection:
            for identity in ids:
                row=connection.execute("SELECT * FROM sync_holds WHERE id=? AND owner=?",(str(identity),request.state.user["id"])).fetchone()
                if row:result.append({"id":row["id"],"resolved":bool(row["resolved"])})
        return result

    @application.post("/api/reports")
    def make_report(values: ReportInput):
        start, end = values.start.isoformat(), values.end.isoformat()
        if values.kind != "custom": start, end = reports.period(values.kind, end)
        return reports.create(path, start, end, [str(identity) for identity in values.product_ids], values.exclude_zero, values.name,
                              layout="monthly-stock-v1" if values.kind == "monthly" else None)

    @application.get("/api/reports")
    def list_reports():
        with database.connect(path) as connection:
            # Conservative review flag: later stock or catalog changes can
            # affect a saved report. Its original snapshot remains immutable.
            return [dict(row) for row in connection.execute("SELECT id,name,from_date,to_date,created_at,EXISTS(SELECT 1 FROM audit_events a WHERE a.action IN ('stock.created','stock.corrected','stock.voided','product.created','product.edited','product.category','category.edited') AND a.created_at>r.created_at) AS needs_review FROM report_versions r ORDER BY created_at DESC")]

    @application.get("/api/reports/{identity}/excel")
    def excel(identity: UUID, template_id: UUID | None = None):
        with database.connect(path) as connection:
            row = connection.execute("SELECT payload_json FROM report_versions WHERE id=?", (str(identity),)).fetchone()
        if not row: raise HTTPException(404, "Saved report not found.")
        data=json.loads(row[0])
        if template_id:
            from app import templates
            with database.connect(path) as connection:template=connection.execute("SELECT * FROM templates WHERE id=?",(str(template_id),)).fetchone()
            if not template:raise HTTPException(404,"Template not found.")
            content=templates.fill(data,template["workbook_base64"],json.loads(template["mapping_json"]))
        else:content=reports.workbook(data)
        return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="inventory-{identity}.xlsx"'})

    @application.get("/api/templates")
    def list_templates():
        with database.connect(path) as connection:return [dict(row) for row in connection.execute("SELECT id,name,mapping_json FROM templates")]

    @application.post("/api/templates")
    def add_template(values:dict):
        from app import templates
        templates.validate(values["data"],values["mapping"])
        identity=str(uuid4())
        with database.connect(path) as connection:
            connection.execute("INSERT INTO templates VALUES (?,?,?,?)",(identity,str(values["name"])[:120],values["data"],json.dumps(values["mapping"])))
            audit.record(connection,"template.saved",identity,after={"name":values["name"]})
        return {"id":identity,"name":values["name"]}

    @application.post("/api/templates/inspect")
    def inspect_template(values:dict):
        try:
            from app import templates
            sheets,rows=imports.read_file("template.xlsx",values["data"],int(values.get("sheet",0)))
            return {"sheets":sheets,"sample":rows[:15],"suggested_mapping":templates.suggest(values["data"],database.list_categories(path))}
        except stock.StockError:raise
        except Exception:raise HTTPException(422,"Could not read this XLSX template.")

    @application.get("/api/backups")
    def list_backups():
        folder = path.parent / "backups"
        return [{"name": file.name, "size": file.stat().st_size} for file in sorted(folder.glob("*.sqlite3"), reverse=True)]

    @application.post("/api/backups")
    def make_backup():
        return backups.create(path, destinations=settings().get("backup_locations", []))

    @application.get("/api/settings")
    def settings():
        with database.connect(path) as connection:
            return {row["key"]: json.loads(row["value_json"]) for row in connection.execute("SELECT * FROM settings")
                    if not row["key"].startswith(excel_feed.PREFIX)}

    @application.put("/api/settings")
    def change_settings(values: dict):
        allowed = {"company_name", "backup_locations", "date_format","time_format","utc_offset_minutes"}
        if set(values) - allowed: raise HTTPException(422, "Unsupported company setting.")
        if "backup_locations" in values and (not isinstance(values["backup_locations"], list) or any(not isinstance(value, str) for value in values["backup_locations"])):
            raise HTTPException(422, "Backup locations must be a list of folder paths.")
        if "utc_offset_minutes" in values and (type(values["utc_offset_minutes"]) is not int or not -720<=values["utc_offset_minutes"]<=840):raise HTTPException(422,"UTC offset must be -720 to +840 minutes.")
        if "date_format" in values and values["date_format"] not in ("YYYY-MM-DD","DD/MM/YYYY","MM/DD/YYYY"):raise HTTPException(422,"Unsupported date format.")
        if "time_format" in values and values["time_format"] not in ("12h","24h"):raise HTTPException(422,"Choose 12h or 24h time.")
        if "company_name" in values and (not isinstance(values["company_name"],str) or len(values["company_name"])>120):raise HTTPException(422,"Company name must be at most 120 characters.")
        with database.connect(path) as connection:
            for key, value in values.items():
                connection.execute("INSERT INTO settings VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value_json=excluded.value_json", (key, json.dumps(value)))
            audit.record(connection, "settings.changed", "company", after=values)
        return values

    @application.get("/api/company")
    def company():
        values=settings()
        return {"company_name":values.get("company_name","Local Inventory"),"date_format":values.get("date_format","YYYY-MM-DD"),"time_format":values.get("time_format","24h"),"utc_offset_minutes":values.get("utc_offset_minutes",180)}

    @application.get("/api/months")
    def months():
        with database.connect(path) as connection: return [dict(row) for row in connection.execute("SELECT * FROM closed_months ORDER BY month DESC")]

    @application.put("/api/months/{month}")
    def month_status(month: str, values: dict):
        try: first = date.fromisoformat(month + "-01")
        except ValueError: raise HTTPException(422, "Use a YYYY-MM month.")
        if first.isoformat()[:7] != month: raise HTTPException(422, "Use a YYYY-MM month.")
        with database.connect(path) as connection:
            if values.get("closed"):
                connection.execute("INSERT INTO closed_months VALUES (?, ?) ON CONFLICT (month) DO NOTHING", (month, datetime.now(timezone.utc).isoformat()))
            else: connection.execute("DELETE FROM closed_months WHERE month=?", (month,))
            audit.record(connection, "month.closed" if values.get("closed") else "month.reopened", month)
        return {"month": month, "closed": bool(values.get("closed"))}
