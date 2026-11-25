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


class MaterialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: UUID
    requester_name: str = Field(min_length=1, max_length=120)
    employee_id: str = Field(min_length=1, max_length=80)
    requester_position: str | None = Field(default=None, min_length=1, max_length=120)
    shift: Literal["morning", "afternoon", "night"]
    work_date: date
    notes: str = Field(default="", max_length=500)
    items: list[RequestLine] = Field(min_length=1, max_length=100)


class MaterialIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: UUID
    stock_date: date
    work_date: date
    excess_reason: str = Field(default="", max_length=500)
    items: list[RequestLine] = Field(min_length=1, max_length=100)


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
        setting = connection.execute("SELECT value_json FROM settings WHERE key='hub16_policy'").fetchone()
        policy = json.loads(setting["value_json"]) if setting else {}
        work_date = values.work_date.isoformat()
        mrf_date = request_row["work_date"] if policy.get("cross_date_issue") == "request_date" else work_date
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
                          COALESCE((SELECT SUM(i.quantity_minor) FROM material_request_issue_lines i
                                    JOIN material_request_issues h ON h.id=i.issue_id
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
        submitted_at = datetime.now(timezone.utc).isoformat()
        connection.execute(
            """INSERT INTO material_requests
               (id, account_id, requester_name, employee_id, requester_position, shift, work_date, notes, submitted_at, payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (identity, account_id, values.requester_name, values.employee_id, values.requester_position, values.shift,
             values.work_date.isoformat(), values.notes, submitted_at, canonical),
        )
        seen = set()
        for item in values.items:
            product_id = str(item.product_id)
            if product_id in seen:
                raise HTTPException(422, "List each product once and enter its total quantity.")
            seen.add(product_id)
            product = connection.execute(
                "SELECT name, unit, precision FROM products WHERE id=? AND archived=0", (product_id,)
            ).fetchone()
            if product is None:
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
                   (request_id, product_id, product_name, unit, quantity_minor, precision)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (identity, product_id, product["name"], product["unit"], amount, product["precision"]),
            )
        # No ledger call occurs here. Request, lines and audit save together.
        audit.record(connection, "material_request.submitted", identity,
                     after={"shift": values.shift, "work_date": values.work_date.isoformat(),
                            "line_count": len(values.items)})
    return {"id": identity, "status": "submitted"}


def register(application, path):
    @application.get("/api/mrf/requests")
    def submitted_requests(before: UUID | None = None, request_id: UUID | None = None):
        # Page by the last displayed record so new submissions do not shift
        # the older pages while a storekeeper is reviewing them.
        with database.connect(path) as connection:
            company_date = stock.company_today(connection)
            where, parameters = "", ()
            if request_id is not None:
                where, parameters = "WHERE r.id = ?", (str(request_id),)
            elif before is not None:
                cursor = connection.execute(
                    "SELECT id, submitted_at FROM material_requests WHERE id=?", (str(before),)
                ).fetchone()
                if cursor is None:
                    raise HTTPException(404, "This request page is unavailable. Refresh the list.")
                where = "WHERE r.submitted_at < ? OR (r.submitted_at = ? AND r.id < ?)"
                parameters = (cursor["submitted_at"], cursor["submitted_at"], cursor["id"])
            rows = connection.execute(
                """SELECT r.id, r.requester_name, r.employee_id, r.requester_position, r.shift,
                          r.work_date, r.notes, r.submitted_at, a.approver_name, a.approved_at
                   FROM material_requests r
                   LEFT JOIN material_request_approvals a ON a.request_id = r.id """ + where +
                " ORDER BY r.submitted_at DESC, r.id DESC LIMIT 26",
                parameters,
            ).fetchall()
            page = {row["id"]: {**dict(row), "items": [], "issues": []} for row in rows[:25]}
            if page:
                # The interpolated text contains only placeholders. Each ID
                # is bound separately, and one query loads the entire page.
                placeholders = ",".join("?" for _ in page)
                for line in connection.execute(
                    """SELECT l.request_id, l.product_id, l.product_name, l.unit, l.quantity_minor, l.precision,
                              COALESCE((SELECT SUM(i.quantity_minor) FROM material_request_issue_lines i
                                        JOIN material_request_issues h ON h.id=i.issue_id
                                        WHERE h.request_id=l.request_id AND i.product_id=l.product_id), 0) AS issued
                       FROM material_request_lines l WHERE l.request_id IN (""" + placeholders +
                    ") ORDER BY l.product_name, l.product_id",
                    tuple(page),
                ).fetchall():
                    page[line["request_id"]]["items"].append({
                        "product": line["product_name"], "unit": line["unit"],
                        "product_id": line["product_id"],
                        "quantity": stock.display(line["quantity_minor"], line["precision"]),
                        "issued": stock.display(line["issued"], line["precision"]),
                        "pending": stock.display(max(0, line["quantity_minor"] - line["issued"]), line["precision"]),
                    })
                for issued in connection.execute(
                    """SELECT request_id, issuer_name, stock_date, work_date, mrf_date, excess_reason
                       FROM material_request_issues WHERE request_id IN (""" + placeholders +
                    ") ORDER BY issued_at DESC, id DESC", tuple(page),
                ).fetchall():
                    page[issued["request_id"]]["issues"].append(dict(issued))
        return {"requests": list(page.values()), "next": rows[24]["id"] if len(rows) > 25 else None,
                "company_date": company_date}

    @application.post("/api/mrf/requests", status_code=201)
    def create_request(values: MaterialRequest, request: Request):
        return submit(path, request.state.user["id"], values)

    @application.post("/api/mrf/requests/{request_id}/issues", status_code=201)
    def issue_request(request_id: UUID, values: MaterialIssue, request: Request):
        return issue(path, str(request_id), request.state.user, values)

    @application.get("/api/mrf/requests/{request_id}/stock")
    def request_stock(request_id: UUID, stock_date: date):
        """Read the dated ledger balance for each product in this request."""
        identity = str(request_id)
        with database.connect(path) as connection:
            if not connection.execute("SELECT 1 FROM material_requests WHERE id=?", (identity,)).fetchone():
                raise HTTPException(404, "Material request not found.")
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
