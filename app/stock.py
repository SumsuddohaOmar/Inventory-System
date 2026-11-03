"""Exact stock arithmetic and atomic ledger writes for the local stock milestone.

An opening entry establishes a product's starting quantity/date. Receipts add,
issues subtract. Never update a stored balance or silently rewrite history.
Transactions are serialized before reading stock so two simultaneous issues
cannot both spend the same units. workflows.py builds corrections and drafts
on these same ledger checks; audit changes commit with the business operation.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
from contextlib import nullcontext
import json
from pathlib import Path
import re

from app.database import connect
from app import audit

MAX_MINOR = 10**15  # Bound each quantity and each balance well below SQLite int64.
COMPANY_ZONE = timezone(timedelta(hours=3), "Asia/Riyadh")


class StockError(ValueError):
    """A readable business-rule rejection; the route returns HTTP 409."""


class MissingProduct(LookupError):
    """Differentiate a missing product (404) from a stock conflict (409)."""


def today(offset_minutes=180) -> str:
    """Return the stock business date in Riyadh, the current company default.

    The company can configure a fixed UTC offset; full IANA/DST zones remain
    future work. UTC timestamps remain
    separate from this effective business date and never change its meaning.
    """
    return datetime.now(timezone(timedelta(minutes=offset_minutes))).date().isoformat()


def company_today(connection):
    """Read the configured fixed UTC offset inside the current transaction."""
    row=connection.execute("SELECT value_json FROM settings WHERE key='utc_offset_minutes'").fetchone()
    return today(json.loads(row[0]) if row else 180)


def to_minor(value: str, precision: int) -> int:
    """Convert an ordinary decimal string to exact product-scaled integer units.

    Reject signs, exponents, NaN, infinity, rounding and unsupported precision.
    Decimal parsing happens before arithmetic; binary floats are never used.
    """
    if not isinstance(value, str) or len(value) > 40 or not re.fullmatch(r"\d+(?:\.\d+)?", value, flags=re.ASCII):
        raise StockError("Enter a non-negative quantity using digits and a decimal point.")
    try:
        # Prevent the default Decimal context from rounding a very long fraction.
        with localcontext() as context:
            context.prec = len(value) + 10
            scaled = Decimal(value) * (10**precision)
    except InvalidOperation as error:
        raise StockError("Invalid quantity.") from error
    if scaled > MAX_MINOR:
        raise StockError("Quantity is too large.")
    if scaled != scaled.to_integral_value():
        raise StockError(f"This product allows at most {precision} decimal places; quantities are never rounded.")
    return int(scaled)


def display(minor: int, precision: int) -> str:
    """Format an exact integer quantity as a fixed-point decimal string."""
    return format(Decimal(minor).scaleb(-precision), f".{precision}f")


def public_entry(row, precision: int) -> dict:
    """Convert an internal ledger row to JSON-ready quantities without floats."""
    result = dict(row)
    result["quantity"] = display(result.pop("quantity_minor"), precision)
    return result


def _summary(connection, product: dict) -> dict:
    """Read one consistent ledger snapshot and derive totals and history."""
    rows = connection.execute(
        "SELECT * FROM stock_entries WHERE product_id = ? ORDER BY effective_date DESC, created_at DESC, id",
        (product["id"],),
    ).fetchall()
    totals = {kind: sum(row["quantity_minor"] for row in rows if row["kind"] == kind and not row["voided"])
              for kind in ("opening", "receive", "issue")}
    precision = product["precision"]
    return {
        "product_id": product["id"], "precision": precision,
        "initialized": any(row["kind"] == "opening" for row in rows),
        "opening": display(totals["opening"], precision),
        "received": display(totals["receive"], precision),
        "issued": display(totals["issue"], precision),
        "balance": display(totals["opening"] + totals["receive"] - totals["issue"], precision),
        "today": company_today(connection), "entries": [public_entry(row, precision) for row in rows],
    }


def get_stock(path: Path, product_id: str) -> dict:
    """Return opening, receipt, issue, balance and all entries, or raise 404 marker."""
    with connect(path) as connection:
        product = connection.execute("SELECT id, precision FROM products WHERE id = ?", (product_id,)).fetchone()
        if product is None:
            raise MissingProduct("Product not found.")
        return _summary(connection, dict(product))


def save_entry(path: Path, product_id: str, entry: dict, connection=None) -> dict:
    """Commit a single dated movement exactly once, or reject it without changes.

    entry contains UUID id, kind, YYYY-MM-DD effective_date, decimal quantity
    and notes. The API validates their types. The UUID is a global retry key:
    an identical retry returns the first record, a changed payload is rejected.
    We check every dated balance, not just today's total, when adding history.
    Same-day receipts are available to same-day issues (daily stock model).
    """
    # An outer bulk transaction can supply its connection. In that case only
    # the caller owns commit/rollback, keeping all products atomic together.
    with (nullcontext(connection) if connection is not None else connect(path)) as connection:
        if not connection.in_transaction: connection.execute("BEGIN IMMEDIATE")
        request_json = json.dumps({"product_id": product_id, **entry}, sort_keys=True)
        operation = connection.execute("SELECT * FROM operations WHERE id=?", (entry["id"],)).fetchone()
        if operation:
            if operation["request_json"] != request_json: raise StockError("Save ID already belongs to different data.")
            return json.loads(operation["result_json"])
        product = connection.execute("SELECT id, precision FROM products WHERE id = ?", (product_id,)).fetchone()
        if product is None:
            raise MissingProduct("Product not found.")
        precision = product["precision"]
        amount = to_minor(entry["quantity"], precision)
        existing = connection.execute("SELECT * FROM stock_entries WHERE id = ?", (entry["id"],)).fetchone()
        if existing:
            if (existing["product_id"], existing["kind"], existing["effective_date"], existing["quantity_minor"], existing["notes"]) != (
                product_id, entry["kind"], entry["effective_date"], amount, entry["notes"]
            ):
                raise StockError("This save ID was already used for different data. Refresh before starting another entry.")
            return public_entry(existing, precision)
        if entry["effective_date"] > company_today(connection):
            raise StockError("Future entries cannot affect stock. Keep them as drafts until their date.")
        if connection.execute("SELECT 1 FROM closed_months WHERE month=?", (entry["effective_date"][:7],)).fetchone():
            raise StockError("That month is closed. An administrator must reopen it before changes.")
        if entry["kind"] != "opening" and amount == 0:
            raise StockError("Receiving and issuing quantities must be greater than zero.")
        opening = connection.execute(
            "SELECT effective_date FROM stock_entries WHERE product_id = ? AND kind = 'opening'", (product_id,)
        ).fetchone()
        if entry["kind"] == "opening" and opening:
            raise StockError("Opening stock is already set. Use Receive/Issue, or correct the existing opening entry with permission.")
        if entry["kind"] != "opening" and not opening:
            raise StockError("Set this product's opening stock first, even if it is zero.")
        if opening and entry["effective_date"] < opening["effective_date"]:
            raise StockError("An entry cannot be dated before this product's opening stock.")
        connection.execute(
            "INSERT INTO stock_entries (id, product_id, kind, effective_date, quantity_minor, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (entry["id"], product_id, entry["kind"], entry["effective_date"], amount, entry["notes"], datetime.now(timezone.utc).isoformat()),
        )
        validate_timeline(connection, product_id)
        # Raising above rolls back the insertion as part of connect's transaction.
        row = connection.execute("SELECT * FROM stock_entries WHERE id = ?", (entry["id"],)).fetchone()
        result = public_entry(row, precision)
        audit.record(connection, "stock.created", entry["id"], after=result)
        connection.execute("INSERT INTO operations VALUES (?, ?, ?)", (entry["id"], request_json, json.dumps(result)))
        return result


def validate_timeline(connection, product_id):
    """Validate the entire active daily timeline after a proposed write/correction."""
    rows = connection.execute("SELECT * FROM stock_entries WHERE product_id=? AND voided=0 ORDER BY effective_date", (product_id,)).fetchall()
    opening = next((row for row in rows if row["kind"] == "opening"), None)
    if rows and not opening: raise StockError("An opening stock entry is required.")
    days = {}
    for row in rows:
        if row["effective_date"] < opening["effective_date"]: raise StockError("Entries cannot precede opening stock.")
        signed = -row["quantity_minor"] if row["kind"] == "issue" else row["quantity_minor"]
        days[row["effective_date"]] = days.get(row["effective_date"], 0) + signed
    balance = 0
    for day, change in days.items():
        balance += change
        if balance < 0: raise StockError(f"Not enough stock on {day}. This change would make the balance negative.")
        if balance > MAX_MINOR: raise StockError("This change exceeds the supported stock balance.")
