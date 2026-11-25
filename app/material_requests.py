"""Create material requests independently of inventory transactions."""

from datetime import date, datetime, timezone
import json
from typing import Literal
from uuid import UUID

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
    shift: Literal["morning", "afternoon", "night"]
    work_date: date
    notes: str = Field(default="", max_length=500)
    items: list[RequestLine] = Field(min_length=1, max_length=100)


def initialize(connection):
    """Apply the same additive schema to SQLite and hosted PostgreSQL."""
    sql = (database.MIGRATIONS / "005_material_requests.sql").read_text(encoding="utf-8")
    for statement in sql.split(";"):
        if statement.strip():
            connection.execute(statement)


def submit(path, account_id: str, values: MaterialRequest):
    payload = values.model_dump(mode="json")
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

        submitted_at = datetime.now(timezone.utc).isoformat()
        connection.execute(
            """INSERT INTO material_requests
               (id, account_id, requester_name, employee_id, shift, work_date, notes, submitted_at, payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (identity, account_id, values.requester_name, values.employee_id, values.shift,
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
                raise HTTPException(422, "A selected product is unavailable. Refresh the catalog.")
            try:
                amount = stock.to_minor(item.quantity, product["precision"])
            except stock.StockError as error:
                raise HTTPException(422, f"{product['name']}: {error}") from error
            if amount <= 0:
                raise HTTPException(422, "Requested quantities must be greater than zero.")
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
    @application.post("/api/mrf/requests", status_code=201)
    def create_request(values: MaterialRequest, request: Request):
        return submit(path, request.state.user["id"], values)
