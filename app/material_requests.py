"""Create material requests independently of inventory transactions."""

from datetime import date, datetime, timezone
import json
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app import audit, database, stock


class RequestLine(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    product_id: UUID
    quantity: str = Field(min_length=1, max_length=40)


class RequestedItem(RequestLine):
    remarks: str = Field(default="", max_length=300)


class MaterialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: UUID
    requester_name: str = Field(min_length=1, max_length=120)
    employee_id: str = Field(min_length=1, max_length=80)
    requester_position: str | None = Field(default=None, min_length=1, max_length=120)
    shift: Literal["morning", "afternoon", "night"]
    work_date: date
    notes: str = Field(default="", max_length=500)
    priority: Literal["normal", "urgent"] = "normal"
    required_date: date | None = None
    allow_duplicate: bool = False
    replaces: UUID | None = None
    items: list[RequestedItem] = Field(min_length=1, max_length=100)


class MaterialIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: UUID
    stock_date: date
    work_date: date
    excess_reason: str = Field(default="", max_length=500)
    items: list[RequestLine] = Field(min_length=1, max_length=100)


class MaterialReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    receiver_name: str = Field(min_length=1, max_length=120)
    employee_id: str = Field(min_length=1, max_length=80)


class DailyFormStatus(BaseModel):
    closed: bool = Field(strict=True)


class MaterialReturn(RequestLine):
    id: UUID
    stock_date: date
    reason: str = Field(min_length=1, max_length=400)


class MaterialTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)
    items: list[RequestLine] = Field(min_length=1, max_length=100)


class PendingAction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: Literal["close", "transfer"]
    reason: str = Field(min_length=1, max_length=400)
    work_date: date | None = None


def initialize(connection):
    """Apply the same additive schema to SQLite and hosted PostgreSQL."""
    sql = (database.MIGRATIONS / "005_material_requests.sql").read_text(encoding="utf-8")
    for statement in sql.split(";"):
        if statement.strip():
            connection.execute(statement)


def initialize_approvals(connection, *, postgres=False):
    """Preserve old requests with an empty position until new ones arrive."""
    # SQLite calls this once through its schema version. PostgreSQL calls it
    # on startup, so its ALTER must also be safe on repeated deploys.
    guard = " IF NOT EXISTS" if postgres else ""
    connection.execute(f"ALTER TABLE material_requests ADD COLUMN{guard} requester_position TEXT")
    connection.execute((database.MIGRATIONS / "006_material_request_approvals.sql").read_text(encoding="utf-8"))


def initialize_issues(connection):
    sql = (database.MIGRATIONS / "007_material_request_issues.sql").read_text(encoding="utf-8")
    for statement in sql.split(";"):
        if statement.strip():
            connection.execute(statement)


def initialize_handover(connection, *, postgres=False):
    sql = (database.MIGRATIONS / "008_material_request_handover.sql").read_text(encoding="utf-8")
    for statement in sql.split(";"):
        if statement.strip():
            connection.execute(statement)
    guard = " IF NOT EXISTS" if postgres else ""
    connection.execute(f"ALTER TABLE material_requests ADD COLUMN{guard} priority TEXT NOT NULL DEFAULT 'normal'")
    connection.execute(f"ALTER TABLE material_requests ADD COLUMN{guard} required_date TEXT")
    connection.execute(f"ALTER TABLE material_request_lines ADD COLUMN{guard} remarks TEXT NOT NULL DEFAULT ''")
    # Allocate numbers for historical dates once, in work-date order. Future
    # transactions allocate the next number without renumbering old forms.
    existing_dates = connection.execute(
        """SELECT work_date FROM (
               SELECT work_date FROM material_requests
               UNION SELECT mrf_date AS work_date FROM material_request_issues
           ) dates WHERE work_date NOT IN (SELECT work_date FROM daily_material_forms)
           ORDER BY work_date"""
    ).fetchall()
    for row in existing_dates:
        ensure_daily_form(connection, row["work_date"])


def ensure_daily_form(connection, work_date):
    """Allocate one immutable number using the enclosing serialized write."""
    result = connection.execute("SELECT * FROM daily_material_forms WHERE work_date=?", (work_date,)).fetchone()
    if result:
        return dict(result)
    month = work_date[:7]
    sequence = connection.execute(
        "SELECT COALESCE(MAX(sequence), 0) + 1 FROM daily_material_forms WHERE month=?", (month,),
    ).fetchone()[0]
    connection.execute("INSERT INTO daily_material_forms VALUES (?, ?, ?, 0)", (work_date, month, sequence))
    return {"work_date": work_date, "month": month, "sequence": sequence, "closed": 0}


def require_open_form(connection, work_date):
    if ensure_daily_form(connection, work_date)["closed"]:
        raise HTTPException(409, "That daily MRF is closed. Ask an administrator to reopen it.")


def hidden_products(connection):
    row = connection.execute("SELECT value_json FROM settings WHERE key='hub16_hidden_products'").fetchone()
    return set(json.loads(row["value_json"])) if row else set()


def validate_ledger_correction(connection, entry_id, amount, effective_date):
    """Keep linked returns, corrections and MRF totals consistent."""
    issued = connection.execute(
        """SELECT h.mrf_date, h.request_id, il.issue_id, il.product_id, il.quantity_minor AS original,
                  COALESCE(SUM(rs.quantity_minor),0) AS returned, MIN(rs.effective_date) AS first_return
           FROM material_request_issue_lines il JOIN material_request_issues h ON h.id=il.issue_id
           LEFT JOIN material_request_returns rt ON rt.issue_id=il.issue_id AND rt.product_id=il.product_id
           LEFT JOIN stock_entries rs ON rs.id=rt.stock_entry_id AND rs.voided=0
           WHERE il.stock_entry_id=? GROUP BY h.mrf_date, h.request_id, il.issue_id, il.product_id, il.quantity_minor""", (entry_id,),
    ).fetchone()
    if issued:
        form = connection.execute("SELECT closed FROM daily_material_forms WHERE work_date=?", (issued["mrf_date"],)).fetchone()
        if form and form["closed"]:
            raise stock.StockError("Reopen the daily MRF before correcting its issuance.")
        if connection.execute("SELECT 1 FROM material_request_pending_actions WHERE request_id=?", (issued["request_id"],)).fetchone():
            raise stock.StockError("The request’s pending quantities have already been closed or transferred.")
        if amount > issued["original"]:
            raise stock.StockError("Record additional stock through the request’s issuance form.")
        if amount < issued["returned"] or (issued["first_return"] and effective_date > issued["first_return"]):
            raise stock.StockError("This correction conflicts with an existing return.")
        return
    returned = connection.execute(
        """SELECT source.quantity_minor AS issued, source.effective_date AS issued_date,
                  COALESCE(SUM(other_stock.quantity_minor),0) AS other_returns
           FROM material_request_returns rt JOIN material_request_issue_lines il
                ON il.issue_id=rt.issue_id AND il.product_id=rt.product_id
           JOIN stock_entries source ON source.id=il.stock_entry_id AND source.voided=0
           LEFT JOIN material_request_returns other ON other.issue_id=rt.issue_id
                     AND other.product_id=rt.product_id AND other.id<>rt.id
           LEFT JOIN stock_entries other_stock ON other_stock.id=other.stock_entry_id AND other_stock.voided=0
           WHERE rt.stock_entry_id=? GROUP BY source.quantity_minor, source.effective_date""", (entry_id,),
    ).fetchone()
    if returned and (amount + returned["other_returns"] > returned["issued"] or effective_date < returned["issued_date"]):
        raise stock.StockError("The corrected return must fit the issued quantity and come on or after the issue date.")


def issue(path, request_id: str, user: dict, values: MaterialIssue):
    identity = str(values.id)
    canonical = json.dumps(values.model_dump(mode="json"), sort_keys=True)
    with database.connect(path) as connection:
        # Use the ledger's write lock and connection so every line, the issue
        # record and its audit event commit together or all roll back.
        connection.execute("BEGIN IMMEDIATE")
        previous = connection.execute(
            "SELECT request_id, issuer_id, payload_json FROM material_request_issues WHERE id=?", (identity,),
        ).fetchone()
        if previous:
            if (previous["request_id"], previous["issuer_id"], previous["payload_json"]) != (request_id, user["id"], canonical):
                raise HTTPException(409, "That issue ID is already in use.")
            return {"id": identity}
        request_row = connection.execute(
            """SELECT r.work_date, a.approved_at FROM material_requests r
               LEFT JOIN material_request_approvals a ON a.request_id=r.id WHERE r.id=?""", (request_id,),
        ).fetchone()
        if request_row is None:
            raise HTTPException(404, "Material request not found.")
        if not request_row["approved_at"]:
            raise HTTPException(409, "Approve this request before issuing materials.")
        if connection.execute("SELECT 1 FROM material_request_pending_actions WHERE request_id=?", (request_id,)).fetchone():
            raise HTTPException(409, "The remaining quantities on this request were already closed or transferred.")
        setting = connection.execute("SELECT value_json FROM settings WHERE key='hub16_policy'").fetchone()
        policy = json.loads(setting["value_json"]) if setting else {}
        work_date = values.work_date.isoformat()
        mrf_date = request_row["work_date"] if policy.get("cross_date_issue") == "request_date" else work_date
        require_open_form(connection, mrf_date)
        stock_date = values.stock_date.isoformat()
        issued_at = datetime.now(timezone.utc).isoformat()
        connection.execute(
            """INSERT INTO material_request_issues
               (id, request_id, issuer_id, issuer_name, stock_date, work_date, mrf_date, excess_reason, issued_at, payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (identity, request_id, user["id"], user["username"], stock_date, work_date, mrf_date,
             values.excess_reason, issued_at, canonical),
        )
        seen = set()
        for item in values.items:
            product_id = str(item.product_id)
            if product_id in seen:
                raise HTTPException(422, "Enter one issue quantity per product.")
            seen.add(product_id)
            line = connection.execute(
                """SELECT l.quantity_minor, l.precision,
                          COALESCE((SELECT SUM(s.quantity_minor) FROM material_request_issue_lines i
                                    JOIN material_request_issues h ON h.id=i.issue_id
                                    JOIN stock_entries s ON s.id=i.stock_entry_id AND s.voided=0
                                    WHERE h.request_id=l.request_id AND i.product_id=l.product_id), 0) AS issued
                   FROM material_request_lines l JOIN products p ON p.id=l.product_id AND p.archived=0
                   WHERE l.request_id=? AND l.product_id=?""", (request_id, product_id),
            ).fetchone()
            if line is None:
                raise HTTPException(422, "Select an available product from this request.")
            try:
                amount = stock.to_minor(item.quantity, line["precision"])
                if amount <= 0:
                    raise stock.StockError("Enter an issue quantity greater than zero.")
                if amount + line["issued"] > line["quantity_minor"] and not values.excess_reason:
                    raise stock.StockError("Enter an excess issue reason to issue more than the remaining quantity.")
                entry_id = str(uuid4())
                stock.save_entry(path, product_id, {
                    "id": entry_id, "kind": "issue", "effective_date": stock_date,
                    "quantity": item.quantity, "notes": f"Hub-16 request {request_id}",
                }, connection)
            except stock.StockError as error:
                raise HTTPException(422, {"product_id": product_id, "message": str(error)}) from error
            connection.execute(
                """INSERT INTO material_request_issue_lines (issue_id, product_id, stock_entry_id, quantity_minor)
                   VALUES (?, ?, ?, ?)""", (identity, product_id, entry_id, amount),
            )
        audit.record(connection, "material_request.issued", request_id, after={
            "issue_id": identity, "stock_date": stock_date, "work_date": work_date, "mrf_date": mrf_date,
            "line_count": len(values.items), "excess_reason": values.excess_reason,
        })
    return {"id": identity}

def submit(path, account_id: str, values: MaterialRequest):
    payload = values.model_dump(mode="json")
    del payload["allow_duplicate"]
    # Omit defaults added after launch so old idempotency records stay valid.
    if values.priority == "normal":
        del payload["priority"]
    if values.required_date is None:
        del payload["required_date"]
    if values.replaces is None:
        del payload["replaces"]
    for item in payload["items"]:
        if not item["remarks"]:
            del item["remarks"]
    # An older browser may retry a pre-upgrade request without a position.
    # Preserve its original payload for that retry, but require new entries.
    if values.requester_position is None:
        del payload["requester_position"]
    identity = payload["id"]
    canonical = json.dumps(payload, sort_keys=True)
    with database.connect(path) as connection:
        # A retry after a dropped response must return the first receipt even
        # if a product has since been renamed or removed from the catalog.
        connection.execute("BEGIN IMMEDIATE")
        previous = connection.execute(
            "SELECT account_id, payload_json FROM material_requests WHERE id=?", (identity,)
        ).fetchone()
        if previous:
            if (previous["account_id"], previous["payload_json"]) != (account_id, canonical):
                raise HTTPException(409, "That submission ID is already in use.")
            return {"id": identity, "status": "submitted"}

        if values.requester_position is None:
            raise HTTPException(422, "Enter the requester’s position.")
        require_open_form(connection, values.work_date.isoformat())
        replaced_id = str(values.replaces) if values.replaces else ""
        if replaced_id:
            old = connection.execute(
                """SELECT r.work_date FROM material_requests r WHERE r.id=? AND r.account_id=?
                   AND NOT EXISTS (SELECT 1 FROM material_request_approvals a WHERE a.request_id=r.id)
                   AND NOT EXISTS (SELECT 1 FROM material_request_cancellations c WHERE c.request_id=r.id)""",
                (replaced_id, account_id),
            ).fetchone()
            if old is None:
                raise HTTPException(409, "Only your own active requests awaiting approval can be edited.")
            require_open_form(connection, old["work_date"])
        if not values.allow_duplicate:
            product_ids = tuple(str(item.product_id) for item in values.items)
            placeholders = ",".join("?" for _ in product_ids)
            duplicate = connection.execute(
                """SELECT 1 FROM material_requests r JOIN material_request_lines l ON l.request_id=r.id
                   LEFT JOIN material_request_pending_actions a ON a.request_id=r.id
                   LEFT JOIN material_request_cancellations c ON c.request_id=r.id
                   WHERE r.account_id=? AND r.work_date=? AND r.shift=? AND r.id<>?
                     AND a.request_id IS NULL AND c.request_id IS NULL
                     AND l.product_id IN (""" + placeholders + ") LIMIT 1",
                (account_id, values.work_date.isoformat(), values.shift, replaced_id, *product_ids),
            ).fetchone()
            if duplicate:
                raise HTTPException(409, {"code": "duplicate_items",
                                          "message": "This account already requested some of these items for the same work date and shift."})
        submitted_at = datetime.now(timezone.utc).isoformat()
        connection.execute(
            """INSERT INTO material_requests
               (id, account_id, requester_name, employee_id, requester_position, shift, work_date, notes, submitted_at,
                payload_json, priority, required_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (identity, account_id, values.requester_name, values.employee_id, values.requester_position, values.shift,
             values.work_date.isoformat(), values.notes, submitted_at, canonical, values.priority,
             values.required_date.isoformat() if values.required_date else None),
        )
        seen = set()
        hidden = hidden_products(connection)
        for item in values.items:
            product_id = str(item.product_id)
            if product_id in seen:
                raise HTTPException(422, "List each product once and enter its total quantity.")
            seen.add(product_id)
            product = connection.execute(
                "SELECT name, unit, precision FROM products WHERE id=? AND archived=0", (product_id,)
            ).fetchone()
            if product is None or product_id in hidden:
                raise HTTPException(422, {"product_id": product_id, "message": "This product is unavailable. Clear its quantity or reload the catalog."})
            try:
                amount = stock.to_minor(item.quantity, product["precision"])
            except stock.StockError as error:
                raise HTTPException(422, {"product_id": product_id, "message": str(error)}) from error
            if amount <= 0:
                raise HTTPException(422, {"product_id": product_id, "message": "Enter a quantity greater than zero."})
            # Save the visible name and unit at submission for future review.
            connection.execute(
                """INSERT INTO material_request_lines
                   (request_id, product_id, product_name, unit, quantity_minor, precision, remarks)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (identity, product_id, product["name"], product["unit"], amount, product["precision"], item.remarks),
            )
        # No ledger call occurs here. Request, lines and audit save together.
        audit.record(connection, "material_request.submitted", identity,
                     after={"shift": values.shift, "work_date": values.work_date.isoformat(),
                            "line_count": len(values.items)})
        if replaced_id:
            connection.execute("INSERT INTO material_request_cancellations VALUES (?, ?, ?)",
                               (replaced_id, account_id, submitted_at))
            audit.record(connection, "material_request.replaced", replaced_id, after={"replacement_id": identity})
    return {"id": identity, "status": "submitted"}


def register(application, path):
    def list_requests(before, request_id=None, account_id=None):
        # Page by the last displayed record so new submissions do not shift
        # the older pages while a storekeeper is reviewing them.
        with database.connect(path) as connection:
            company_date = stock.company_today(connection)
            where, parameters = "", ()
            if request_id is not None:
                where, parameters = "WHERE r.id = ?", (str(request_id),)
            elif before is not None:
                cursor = connection.execute(
                    "SELECT id, submitted_at, account_id FROM material_requests WHERE id=?", (str(before),)
                ).fetchone()
                if cursor is None or (account_id is not None and cursor["account_id"] != account_id):
                    raise HTTPException(404, "This request page is unavailable. Refresh the list.")
                where = "WHERE (r.submitted_at < ? OR (r.submitted_at = ? AND r.id < ?))"
                parameters = (cursor["submitted_at"], cursor["submitted_at"], cursor["id"])
            if account_id is not None:
                where += " AND r.account_id=?" if where else "WHERE r.account_id=?"
                parameters += (account_id,)
            rows = connection.execute(
                """SELECT r.id, r.requester_name, r.employee_id, r.requester_position, r.shift,
                          r.work_date, r.notes, r.submitted_at, r.priority, r.required_date, a.approver_name, a.approved_at,
                          pa.action AS pending_action, pa.reason AS pending_reason, pa.successor_id,
                          prep.prepared_at, c.cancelled_at
                   FROM material_requests r
                   LEFT JOIN material_request_approvals a ON a.request_id = r.id
                   LEFT JOIN material_request_pending_actions pa ON pa.request_id = r.id
                   LEFT JOIN material_request_preparations prep ON prep.request_id=r.id
                   LEFT JOIN material_request_cancellations c ON c.request_id=r.id """ + where +
                " ORDER BY r.submitted_at DESC, r.id DESC LIMIT 26",
                parameters,
            ).fetchall()
            page = {row["id"]: {**dict(row), "items": [], "issues": []} for row in rows[:25]}
            if page:
                # The interpolated text contains only placeholders. Each ID
                # is bound separately, and one query loads the entire page.
                placeholders = ",".join("?" for _ in page)
                for line in connection.execute(
                    """SELECT l.request_id, l.product_id, l.product_name, l.unit, l.quantity_minor, l.precision, l.remarks,
                              COALESCE((SELECT SUM(s.quantity_minor) FROM material_request_issue_lines i
                                        JOIN material_request_issues h ON h.id=i.issue_id
                                        JOIN stock_entries s ON s.id=i.stock_entry_id AND s.voided=0
                                        WHERE h.request_id=l.request_id AND i.product_id=l.product_id), 0) AS issued
                       FROM material_request_lines l WHERE l.request_id IN (""" + placeholders +
                    ") ORDER BY l.product_name, l.product_id",
                    tuple(page),
                ).fetchall():
                    page[line["request_id"]]["items"].append({
                        "product": line["product_name"], "unit": line["unit"],
                        "product_id": line["product_id"],
                        "remarks": line["remarks"],
                        "quantity": stock.display(line["quantity_minor"], line["precision"]),
                        "issued": stock.display(line["issued"], line["precision"]),
                        "pending": stock.display(max(0, line["quantity_minor"] - line["issued"]), line["precision"]),
                    })
                for issued in connection.execute(
                    """SELECT i.id, i.request_id, i.issuer_name, i.stock_date, i.work_date, i.mrf_date,
                              i.excess_reason, r.receiver_name, r.employee_id AS receiver_employee_id, r.received_at
                       FROM material_request_issues i LEFT JOIN material_request_receipts r ON r.issue_id=i.id
                       WHERE i.request_id IN (""" + placeholders +
                    ") ORDER BY i.issued_at DESC, i.id DESC", tuple(page),
                ).fetchall():
                    page[issued["request_id"]]["issues"].append({**dict(issued), "items": []})
                issues = {issue["id"]: issue for record in page.values() for issue in record["issues"]}
                for line in connection.execute(
                    """SELECT il.issue_id, il.product_id, l.product_name, l.unit, l.precision,
                              il.quantity_minor AS original, s.quantity_minor AS issued,
                              COALESCE((SELECT SUM(rs.quantity_minor) FROM material_request_returns rt
                                        JOIN stock_entries rs ON rs.id=rt.stock_entry_id AND rs.voided=0
                                        WHERE rt.issue_id=il.issue_id AND rt.product_id=il.product_id), 0) AS returned
                       FROM material_request_issue_lines il
                       JOIN material_request_issues i ON i.id=il.issue_id
                       JOIN material_request_lines l ON l.request_id=i.request_id AND l.product_id=il.product_id
                       LEFT JOIN stock_entries s ON s.id=il.stock_entry_id AND s.voided=0
                       WHERE i.request_id IN (""" + placeholders + ") ORDER BY l.product_name, l.product_id", tuple(page),
                ).fetchall():
                    issued = line["issued"] or 0
                    issues[line["issue_id"]]["items"].append({
                        "product_id": line["product_id"], "product": line["product_name"], "unit": line["unit"],
                        "original": stock.display(line["original"], line["precision"]),
                        "issued": stock.display(issued, line["precision"]),
                        "returned": stock.display(line["returned"], line["precision"]),
                        "net": stock.display(issued - line["returned"], line["precision"]),
                    })
        return {"requests": list(page.values()), "next": rows[24]["id"] if len(rows) > 25 else None,
                "company_date": company_date}

    @application.get("/api/mrf/requests")
    def submitted_requests(before: UUID | None = None, request_id: UUID | None = None):
        return list_requests(before, request_id)

    @application.get("/api/mrf/my-requests")
    def my_requests(request: Request, before: UUID | None = None):
        page = list_requests(before, account_id=request.state.user["id"])
        for record in page["requests"]:
            # Show handover status to the requester without the collector's ID.
            record["issues"] = [{"received": bool(issue["received_at"])} for issue in record["issues"]]
        return page

    @application.post("/api/mrf/my-requests/{request_id}/cancel")
    def cancel_request(request_id: UUID, request: Request):
        identity, actor = str(request_id), request.state.user["id"]
        with database.connect(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT r.work_date, a.approved_at, c.cancelled_at FROM material_requests r
                   LEFT JOIN material_request_approvals a ON a.request_id=r.id
                   LEFT JOIN material_request_cancellations c ON c.request_id=r.id
                   WHERE r.id=? AND r.account_id=?""", (identity, actor),
            ).fetchone()
            if row is None:
                raise HTTPException(404, "Material request not found.")
            if row["cancelled_at"]:
                return {"cancelled_at": row["cancelled_at"]}
            if row["approved_at"]:
                raise HTTPException(409, "An approved request must be handled by the storekeeper.")
            require_open_form(connection, row["work_date"])
            cancelled_at = datetime.now(timezone.utc).isoformat()
            connection.execute("INSERT INTO material_request_cancellations VALUES (?, ?, ?)", (identity, actor, cancelled_at))
            audit.record(connection, "material_request.cancelled", identity)
        return {"cancelled_at": cancelled_at}

    @application.get("/api/mrf/my-templates")
    def my_templates(request: Request):
        with database.connect(path) as connection:
            rows = connection.execute(
                "SELECT name, items_json FROM material_request_templates WHERE account_id=? ORDER BY name",
                (request.state.user["id"],),
            ).fetchall()
        return [{"name": row["name"], "items": json.loads(row["items_json"])} for row in rows]

    @application.put("/api/mrf/my-templates")
    def save_template(values: MaterialTemplate, request: Request):
        account_id = request.state.user["id"]
        with database.connect(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            seen = set()
            hidden = hidden_products(connection)
            for item in values.items:
                product_id = str(item.product_id)
                product = connection.execute("SELECT precision FROM products WHERE id=? AND archived=0", (product_id,)).fetchone()
                if product is None or product_id in seen or product_id in hidden:
                    raise HTTPException(422, "Use available products and list each item once.")
                seen.add(product_id)
                if stock.to_minor(item.quantity, product["precision"]) <= 0:
                    raise HTTPException(422, "Template quantities must be greater than zero.")
            connection.execute(
                """INSERT INTO material_request_templates VALUES (?, ?, ?, ?)
                   ON CONFLICT (account_id, name) DO UPDATE SET items_json=excluded.items_json""",
                (str(uuid4()), account_id, values.name, json.dumps(values.model_dump(mode="json")["items"])),
            )
        return {"name": values.name}

    @application.post("/api/mrf/requests", status_code=201)
    def create_request(values: MaterialRequest, request: Request):
        return submit(path, request.state.user["id"], values)

    @application.post("/api/mrf/requests/{request_id}/issues", status_code=201)
    def issue_request(request_id: UUID, values: MaterialIssue, request: Request):
        return issue(path, str(request_id), request.state.user, values)

    @application.post("/api/mrf/requests/{request_id}/prepare")
    def prepare_request(request_id: UUID, request: Request):
        identity = str(request_id)
        with database.connect(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not connection.execute("SELECT 1 FROM material_request_approvals WHERE request_id=?", (identity,)).fetchone():
                raise HTTPException(409, "Approve the request before marking it prepared.")
            existing = connection.execute(
                "SELECT prepared_at FROM material_request_preparations WHERE request_id=?", (identity,),
            ).fetchone()
            if existing:
                return dict(existing)
            if connection.execute("SELECT 1 FROM material_request_pending_actions WHERE request_id=?", (identity,)).fetchone():
                raise HTTPException(409, "The remaining quantities on this request were already handled.")
            prepared_at = datetime.now(timezone.utc).isoformat()
            connection.execute("INSERT INTO material_request_preparations VALUES (?, ?, ?)",
                               (identity, request.state.user["id"], prepared_at))
            audit.record(connection, "material_request.prepared", identity)
        return {"prepared_at": prepared_at}

    @application.post("/api/mrf/requests/{request_id}/pending-action")
    def change_pending(request_id: UUID, values: PendingAction, request: Request):
        identity = str(request_id)
        account_id = request.state.user["id"]
        target_date = values.work_date.isoformat() if values.work_date else None
        if (values.action == "transfer") != bool(target_date):
            raise HTTPException(422, "Choose a new work date for a transfer.")
        with database.connect(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                """SELECT a.action, a.reason, a.successor_id, r.work_date FROM material_request_pending_actions a
                   LEFT JOIN material_requests r ON r.id=a.successor_id WHERE a.request_id=?""", (identity,),
            ).fetchone()
            if previous:
                if (previous["action"], previous["reason"], previous["work_date"]) != (values.action, values.reason, target_date):
                    raise HTTPException(409, "The remaining quantities were already handled.")
                return {"successor_id": previous["successor_id"]}
            source = connection.execute(
                """SELECT r.* FROM material_requests r
                   JOIN material_request_approvals a ON a.request_id=r.id WHERE r.id=?""", (identity,),
            ).fetchone()
            if not source:
                raise HTTPException(409, "Approve the request before handling its remaining quantities.")
            lines = connection.execute(
                """SELECT l.*, l.quantity_minor - COALESCE(
                       (SELECT SUM(s.quantity_minor) FROM material_request_issue_lines il
                        JOIN material_request_issues i ON i.id=il.issue_id
                        JOIN stock_entries s ON s.id=il.stock_entry_id AND s.voided=0
                        WHERE i.request_id=l.request_id AND il.product_id=l.product_id), 0
                   ) AS remaining
                   FROM material_request_lines l WHERE l.request_id=?""", (identity,),
            ).fetchall()
            pending = [line for line in lines if line["remaining"] > 0]
            if not pending:
                raise HTTPException(422, "There are no remaining quantities to close or transfer.")
            successor = None
            recorded_at = datetime.now(timezone.utc).isoformat()
            if target_date:
                if target_date <= source["work_date"]:
                    raise HTTPException(422, "Choose a work date after the original request date.")
                require_open_form(connection, target_date)
                successor = str(uuid4())
                connection.execute(
                    """INSERT INTO material_requests
                       (id, account_id, requester_name, employee_id, requester_position, shift, work_date,
                        notes, submitted_at, payload_json, priority, required_date)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (successor, source["account_id"], source["requester_name"], source["employee_id"],
                     source["requester_position"], source["shift"], target_date, source["notes"], recorded_at,
                     json.dumps({"transferred_from": identity}), source["priority"], source["required_date"]),
                )
                for line in pending:
                    connection.execute(
                        """INSERT INTO material_request_lines
                           (request_id, product_id, product_name, unit, quantity_minor, precision, remarks)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (successor, line["product_id"], line["product_name"], line["unit"], line["remaining"],
                         line["precision"], line["remarks"]),
                    )
            connection.execute(
                "INSERT INTO material_request_pending_actions VALUES (?, ?, ?, ?, ?, ?)",
                (identity, account_id, values.action, values.reason, successor, recorded_at),
            )
            audit.record(connection, f"material_request.pending_{values.action}", identity,
                         after={"reason": values.reason, "successor_id": successor})
        return {"successor_id": successor}

    @application.post("/api/mrf/issues/{issue_id}/receipt")
    def receive_issue(issue_id: UUID, values: MaterialReceipt, request: Request):
        identity = str(issue_id)
        with database.connect(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not connection.execute("SELECT 1 FROM material_request_issues WHERE id=?", (identity,)).fetchone():
                raise HTTPException(404, "Issuance not found.")
            previous = connection.execute(
                "SELECT receiver_name, employee_id AS receiver_employee_id, received_at FROM material_request_receipts WHERE issue_id=?",
                (identity,),
            ).fetchone()
            if previous:
                if (previous["receiver_name"], previous["receiver_employee_id"]) != (values.receiver_name, values.employee_id):
                    raise HTTPException(409, "This issuance already has a different receipt. Refresh the request.")
                return dict(previous)
            received_at = datetime.now(timezone.utc).isoformat()
            connection.execute(
                "INSERT INTO material_request_receipts VALUES (?, ?, ?, ?, ?)",
                (identity, request.state.user["id"], values.receiver_name, values.employee_id, received_at),
            )
            audit.record(connection, "material_request.received", identity, after={"received_at": received_at})
        return {"receiver_name": values.receiver_name, "receiver_employee_id": values.employee_id, "received_at": received_at}

    @application.post("/api/mrf/issues/{issue_id}/returns", status_code=201)
    def return_material(issue_id: UUID, values: MaterialReturn):
        issue_key, product_id, identity = str(issue_id), str(values.product_id), str(values.id)
        with database.connect(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            entry = {"id": identity, "kind": "receive", "effective_date": values.stock_date.isoformat(),
                     "quantity": values.quantity, "notes": f"Hub-16 return for {issue_key}: {values.reason}"}
            previous = connection.execute(
                "SELECT issue_id, product_id FROM material_request_returns WHERE id=?", (identity,),
            ).fetchone()
            if previous:
                if (previous["issue_id"], previous["product_id"]) != (issue_key, product_id):
                    raise HTTPException(409, "That return ID is already in use.")
                stock.save_entry(path, product_id, entry, connection)
                return {"id": identity}
            source = connection.execute(
                """SELECT s.effective_date AS stock_date, s.quantity_minor, p.precision,
                          COALESCE((SELECT SUM(rs.quantity_minor) FROM material_request_returns rt
                                    JOIN stock_entries rs ON rs.id=rt.stock_entry_id AND rs.voided=0
                                    WHERE rt.issue_id=il.issue_id AND rt.product_id=il.product_id), 0) AS returned
                   FROM material_request_issue_lines il JOIN material_request_issues i ON i.id=il.issue_id
                   JOIN stock_entries s ON s.id=il.stock_entry_id AND s.voided=0
                   JOIN products p ON p.id=il.product_id AND p.archived=0
                   WHERE il.issue_id=? AND il.product_id=?""", (issue_key, product_id),
            ).fetchone()
            if source is None:
                raise HTTPException(404, "No active issue line was found for this product.")
            if entry["effective_date"] < source["stock_date"]:
                raise HTTPException(422, "The return date must be on or after the stock issue date.")
            amount = stock.to_minor(values.quantity, source["precision"])
            if not 0 < amount <= source["quantity_minor"] - source["returned"]:
                raise HTTPException(422, "Enter a positive quantity within the remaining returnable amount.")
            if connection.execute("SELECT 1 FROM operations WHERE id=?", (identity,)).fetchone():
                raise HTTPException(409, "That return ID is already in use.")
            stock.save_entry(path, product_id, entry, connection)
            connection.execute(
                "INSERT INTO material_request_returns VALUES (?, ?, ?, ?, ?)",
                (identity, issue_key, product_id, identity, amount),
            )
            audit.record(connection, "material_request.returned", issue_key, after={"return_id": identity, "product_id": product_id})
        return {"id": identity}

    @application.get("/api/mrf/daily/{work_date}")
    def daily_form(work_date: date, include_all: bool = False):
        day = work_date.isoformat()
        with database.connect(path) as connection:
            form = connection.execute("SELECT * FROM daily_material_forms WHERE work_date=?", (day,)).fetchone()
            rows = connection.execute(
                """WITH movements AS (
                       SELECT r.shift, l.product_id, l.product_name, l.unit, l.precision,
                              l.quantity_minor AS requested, 0 AS issued
                       FROM material_requests r JOIN material_request_lines l ON l.request_id=r.id
                       WHERE r.work_date=? AND NOT EXISTS
                             (SELECT 1 FROM material_request_cancellations c WHERE c.request_id=r.id)
                       UNION ALL
                       SELECT r.shift, l.product_id, l.product_name, l.unit, l.precision, 0, s.quantity_minor
                       FROM material_request_issues i JOIN material_requests r ON r.id=i.request_id
                       JOIN material_request_issue_lines il ON il.issue_id=i.id
                       JOIN stock_entries s ON s.id=il.stock_entry_id AND s.voided=0
                       JOIN material_request_lines l ON l.request_id=r.id AND l.product_id=il.product_id
                       WHERE i.mrf_date=?
                   )
                   SELECT shift, product_id, MAX(product_name) AS product, unit, precision,
                          SUM(requested) AS requested, SUM(issued) AS issued
                   FROM movements GROUP BY shift, product_id, unit, precision
                   ORDER BY CASE shift WHEN 'morning' THEN 1 WHEN 'afternoon' THEN 2 ELSE 3 END, MAX(product_name), product_id""",
                (day, day),
            ).fetchall()
            if include_all:
                active_products = connection.execute(
                    "SELECT id, name, unit, precision FROM products WHERE archived=0 ORDER BY name, id"
                ).fetchall()
                present = {(row["shift"], row["product_id"]) for row in rows}
                for shift in ("morning", "afternoon", "night"):
                    rows.extend({
                        "shift": shift, "product": product["name"], "unit": product["unit"],
                        "precision": product["precision"], "requested": 0, "issued": 0,
                    } for product in active_products if (shift, product["id"]) not in present)
        number = f"H16-MRF-{form['month'].replace('-', '')}-{form['sequence']:04d}" if form else None
        return {"work_date": day, "number": number, "closed": bool(form and form["closed"]), "items": [
            {key: row[key] for key in ("shift", "product", "unit")} | {
                key: stock.display(row[key], row["precision"]) for key in ("requested", "issued")
            } for row in rows
        ]}

    @application.put("/api/mrf/daily/{work_date}")
    def change_daily_form_status(work_date: date, values: DailyFormStatus):
        day = work_date.isoformat()
        with database.connect(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            form = connection.execute("SELECT closed FROM daily_material_forms WHERE work_date=?", (day,)).fetchone()
            if not form:
                raise HTTPException(404, "There is no MRF for that work date.")
            if bool(form["closed"]) != values.closed:
                connection.execute("UPDATE daily_material_forms SET closed=? WHERE work_date=?", (int(values.closed), day))
                audit.record(connection, "material_form.closed" if values.closed else "material_form.reopened", day)
        return {"closed": values.closed}

    @application.get("/api/mrf/requests/{request_id}/stock")
    def request_stock(request_id: UUID, stock_date: date):
        """Read the dated ledger balance for each product in this request."""
        identity = str(request_id)
        with database.connect(path) as connection:
            if not connection.execute("SELECT 1 FROM material_requests WHERE id=?", (identity,)).fetchone():
                raise HTTPException(404, "Material request not found.")
            if connection.execute("SELECT 1 FROM material_request_cancellations WHERE request_id=?", (identity,)).fetchone():
                raise HTTPException(409, "This request was cancelled.")
            rows = connection.execute(
                """SELECT l.product_id, l.precision, p.archived,
                          MAX(CASE WHEN s.kind='opening' THEN 1 ELSE 0 END) AS opened,
                          SUM(CASE WHEN s.kind IN ('opening','receive') THEN s.quantity_minor
                                   WHEN s.kind='issue' THEN -s.quantity_minor ELSE 0 END) AS balance
                   FROM material_request_lines l
                   LEFT JOIN products p ON p.id=l.product_id
                   LEFT JOIN stock_entries s ON s.product_id=p.id AND s.effective_date<=? AND s.voided=0
                   WHERE l.request_id=?
                   GROUP BY l.product_id, l.precision, p.archived""", (stock_date.isoformat(), identity),
            ).fetchall()
        return {row["product_id"]: {
            "balance": stock.display(row["balance"], row["precision"]) if row["opened"] and row["archived"] == 0 else None,
            "available": row["archived"] == 0,
        } for row in rows}

    @application.post("/api/mrf/requests/{request_id}/approval")
    def approve_request(request_id: UUID, request: Request):
        identity = str(request_id)
        user = request.state.user
        with database.connect(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not connection.execute("SELECT 1 FROM material_requests WHERE id=?", (identity,)).fetchone():
                raise HTTPException(404, "Material request not found.")
            if connection.execute("SELECT 1 FROM material_request_cancellations WHERE request_id=?", (identity,)).fetchone():
                raise HTTPException(409, "This request was cancelled.")
            previous = connection.execute(
                "SELECT approver_name, approved_at FROM material_request_approvals WHERE request_id=?", (identity,)
            ).fetchone()
            if previous:
                # A retry or another manager sees the original approval.
                return dict(previous)
            approval = {"approver_name": user["username"], "approved_at": datetime.now(timezone.utc).isoformat()}
            connection.execute(
                """INSERT INTO material_request_approvals
                   (request_id, approver_id, approver_name, approved_at) VALUES (?, ?, ?, ?)""",
                (identity, user["id"], approval["approver_name"], approval["approved_at"]),
            )
            audit.record(connection, "material_request.approved", identity, after=approval)
        return approval
