"""Higher-level inventory operations: atomic bulk, preserved corrections, drafts.

All write workflows acquire SQLite's write lock BEFORE validating revisions or
stock. An operation UUID records the original result so retries remain safe even
after the corresponding transaction has subsequently been corrected.
"""

from datetime import datetime, timezone
import json
from uuid import uuid4

from app import audit, stock
from app.database import connect


def cached(connection, identity, request):
    """Return a prior result for an identical operation; reject UUID reuse."""
    row = connection.execute("SELECT * FROM operations WHERE id=?", (identity,)).fetchone()
    if row:
        if row["request_json"] != json.dumps(request, sort_keys=True): raise stock.StockError("Operation ID already used for other data.")
        return json.loads(row["result_json"])


def remember(connection, identity, request, result):
    """Persist the operation's result inside its business transaction."""
    connection.execute("INSERT INTO operations VALUES (?, ?, ?)", (identity, json.dumps(request, sort_keys=True), json.dumps(result)))


def bulk(path, identity, entries, connection=None):
    """Save 1–200 entries atomically; retrying a batch never duplicates stock."""
    from contextlib import nullcontext
    if not 1 <= len(entries) <= 200: raise stock.StockError("A batch needs 1–200 entries.")
    ids = [entry["id"] for entry in entries]
    if len(set(ids)) != len(ids) or identity in ids: raise stock.StockError("Every batch and row needs a distinct save ID.")
    request = {"type": "bulk", "entries": entries}
    with (nullcontext(connection) if connection is not None else connect(path)) as connection:
        if not connection.in_transaction: connection.execute("BEGIN IMMEDIATE")
        result = cached(connection, identity, request)
        if result is not None: return result
        # Apply openings/receipts before issues within the same batch. This
        # allows a valid same-day receiving+issuing batch regardless of row order.
        ordered = sorted(entries, key=lambda row: (row["effective_date"], {"opening": 0, "receive": 1, "issue": 2}[row["kind"]]))
        result = []
        for row in ordered:
            values = dict(row)
            product = values.pop("product_id")
            result.append(stock.save_entry(path, product, values, connection))
        remember(connection, identity, request, result)
        return result


def correct(path, entry_id, change, connection=None):
    """Correct or void an entry, preserving snapshots and checking its revision.

    expected_revision prevents stale offline/browser edits from overwriting a
    newer correction. Original operation results remain in operations forever.
    """
    request = {"type": "correction", "entry_id": entry_id, **change}
    from contextlib import nullcontext
    with (nullcontext(connection) if connection is not None else connect(path)) as connection:
        if not connection.in_transaction:connection.execute("BEGIN IMMEDIATE")
        prior = cached(connection, change["id"], request)
        if prior is not None: return prior
        row = connection.execute("SELECT * FROM stock_entries WHERE id=?", (entry_id,)).fetchone()
        if not row: raise stock.MissingProduct("Stock entry not found.")
        if row["revision"] != change["expected_revision"]: raise stock.StockError("This entry changed since you loaded it. Refresh and review both versions.")
        if row["voided"]: raise stock.StockError("A voided entry cannot be silently restored.")
        for day in (row["effective_date"], change["effective_date"]):
            if connection.execute("SELECT 1 FROM closed_months WHERE month=?", (day[:7],)).fetchone(): raise stock.StockError("Reopen the closed month before correcting it.")
        if change["effective_date"] > stock.company_today(connection): raise stock.StockError("Future entries must remain drafts.")
        if row["kind"] == "opening" and change["voided"]: raise stock.StockError("Correct opening stock instead of voiding it.")
        precision = connection.execute("SELECT precision FROM products WHERE id=?", (row["product_id"],)).fetchone()[0]
        amount = stock.to_minor(change["quantity"], precision)
        if row["kind"] != "opening" and amount == 0: raise stock.StockError("Use Void entry to cancel a receipt or issue; quantity must be positive.")
        before = stock.public_entry(row, precision)
        connection.execute("UPDATE stock_entries SET quantity_minor=?,effective_date=?,notes=?,voided=?,revision=revision+1 WHERE id=?",
                           (amount, change["effective_date"], change["notes"], int(change["voided"]), entry_id))
        stock.validate_timeline(connection, row["product_id"])
        after = stock.public_entry(connection.execute("SELECT * FROM stock_entries WHERE id=?", (entry_id,)).fetchone(), precision)
        audit.record(connection, "stock.voided" if change["voided"] else "stock.corrected", entry_id, before, after)
        remember(connection, change["id"], request, after)
        return after


def save_draft(path, identity, entries, expected_revision):
    """Autosave a private draft without touching stock; optimistic revision check."""
    with connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM drafts WHERE id=?", (identity,)).fetchone()
        if row:
            if row["owner"] != audit.actor.get(): raise stock.StockError("This draft belongs to another account.")
            if row["finalized"]: raise stock.StockError("This draft was already finalized.")
            if row["revision"] != expected_revision: raise stock.StockError("Draft changed elsewhere. Reload it before editing.")
            connection.execute("UPDATE drafts SET payload_json=?,revision=revision+1 WHERE id=?", (json.dumps(entries), identity))
            revision = row["revision"] + 1
        else:
            if expected_revision != 0: raise stock.StockError("Draft no longer exists.")
            connection.execute("INSERT INTO drafts VALUES (?, ?, ?, 1, 0, ?)", (identity, audit.actor.get(), json.dumps(entries), datetime.now(timezone.utc).isoformat()))
            revision = 1
        return {"id": identity, "revision": revision}


def set_daily_total(path, product_id, change):
    """Apply an explicitly reviewed daily total through preserved ledger changes.

    Increasing a total creates an additional transaction. Decreasing corrects
    newest entries first, voiding rows reduced to zero. All original snapshots
    remain in audit, and the caller supplies ALL expected row revisions so a
    newly added/edited transaction cannot be overwritten by a stale grid.
    """
    if change["kind"] not in ("receive","issue"):raise stock.StockError("Choose a receiving or issuing daily total.")
    request={"type":"daily-total","product_id":product_id,**change}
    with connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        previous=cached(connection,change["id"],request)
        if previous is not None:return previous
        product=connection.execute("SELECT * FROM products WHERE id=?",(product_id,)).fetchone()
        if not product:raise stock.MissingProduct("Product not found.")
        rows=connection.execute("SELECT * FROM stock_entries WHERE product_id=? AND kind=? AND effective_date=? AND voided=0 ORDER BY created_at DESC,id",(product_id,change["kind"],change["effective_date"])).fetchall()
        expected={row["id"]:row["revision"] for row in rows}
        if expected!=change["expected_entries"]:raise stock.StockError("Daily transactions changed. Reload the month and review before editing.")
        target=stock.to_minor(change["quantity"],product["precision"])
        original=sum(row["quantity_minor"] for row in rows)
        result=[]
        if target>original:
            result.append(stock.save_entry(path,product_id,dict(id=str(uuid4()),kind=change["kind"],effective_date=change["effective_date"],quantity=stock.display(target-original,product["precision"]),notes=change["notes"]),connection))
        else:
            reduction=original-target
            for row in rows:
                if reduction==0:break
                removed=min(reduction,row["quantity_minor"]);remaining=row["quantity_minor"]-removed
                result.append(correct(path,row["id"],dict(id=str(uuid4()),expected_revision=row["revision"],effective_date=row["effective_date"],quantity=stock.display(remaining or row["quantity_minor"],product["precision"]),voided=remaining==0,notes=change["notes"]),connection))
                reduction-=removed
        audit.record(connection,"stock.daily_total",product_id,before={"total":stock.display(original,product["precision"]),"rows":expected},after={"total":change["quantity"],"kind":change["kind"],"date":change["effective_date"]})
        remember(connection,change["id"],request,result)
        return result


def finalize_draft(path, identity):
    """Finalize only explicitly; future dates, permissions and stock are rechecked."""
    with connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM drafts WHERE id=?", (identity,)).fetchone()
        if not row or row["owner"] != audit.actor.get(): raise stock.StockError("Draft not found for this account.")
        entries = json.loads(row["payload_json"])
        # Stable derived batch UUID means a repeated Finalize click is safe.
        from uuid import UUID, uuid5
        operation = str(uuid5(UUID(identity), "finalize"))
        result = bulk(path, operation, entries, connection)
        connection.execute("UPDATE drafts SET finalized=1 WHERE id=?", (identity,))
        audit.record(connection, "draft.finalized", identity)
        return result
