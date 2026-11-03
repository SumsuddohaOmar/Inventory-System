"""Reviewed CSV/XLSX history import using the ordinary exact stock ledger.

Import never bypasses stock rules. Each physical source row has a stable UUID,
so replaying a file cannot silently post it twice. Similar existing transactions
are warnings, not proof of a duplicate: two legitimate receipts can be equal.
The reviewer must explicitly acknowledge those cases. Products are never created
or linked automatically by this workflow.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import base64
from uuid import UUID, uuid5

from app import audit, backups, database, imports, stock, workflows

# A constant namespace makes row identities stable across devices/restarts.
SOURCE_NAMESPACE = UUID("a8c6c8ed-6e21-4d66-a1f4-820f760b5d8e")
FIELDS = {
    "name": ("name", "item", "product", "product name", "item name", "description"),
    "code": ("code", "item code", "product code"),
    "date": ("date", "stock date", "transaction date", "effective date"),
    "kind": ("type", "kind", "entry type", "transaction type"),
    "quantity": ("quantity", "qty", "amount"),
    "notes": ("notes", "note", "remarks"),
}
KINDS = {"opening": "opening", "opening stock": "opening", "receive": "receive",
         "received": "receive", "receipt": "receive", "issue": "issue", "issued": "issue"}


def inspect(values):
    """Expose headers/sample first so unfamiliar column names can be mapped."""
    sheets, rows = imports.read_file(values["filename"], values["data"], int(values.get("sheet", 0)))
    header = int(values.get("header_row", 1)) - 1
    if not 0 <= header < len(rows): raise stock.StockError("Choose a valid header row.")
    headers = [str(cell).strip() for cell in rows[header]]
    return dict(sheets=sheets, headers=headers, sample=rows[header + 1:header + 6],
                mapping={key: next((i for i, value in enumerate(headers) if value.casefold() in aliases), None)
                         for key, aliases in FIELDS.items()})


def parse_date(value, mode):
    """Require an explicit date convention instead of guessing ambiguous dates.

    Excel's 1900 and 1904 calendars are separate choices. The nonexistent
    1900-02-29 serial is rejected. Fractions/time-of-day are not silently lost.
    """
    text = str(value).strip()
    if mode in ("excel1900", "excel1904"):
        try:
            serial = Decimal(text)
            if not serial.is_finite() or serial != serial.to_integral_value() or not 0 <= serial <= 2958465:
                raise ValueError
            number = int(serial)
            if mode == "excel1900":
                if number < 1 or number == 60: raise ValueError
                return (date(1899, 12, 31) + timedelta(days=number - (number > 60))).isoformat()
            return (date(1904, 1, 1) + timedelta(days=number)).isoformat()
        except (InvalidOperation, ValueError, OverflowError):
            raise stock.StockError("Use a valid whole-day Excel serial for the selected calendar.")
    formats = {"iso": "%Y-%m-%d", "dmy": "%d/%m/%Y", "mdy": "%m/%d/%Y"}
    if mode not in formats: raise stock.StockError("Choose an explicit import date format.")
    try: return datetime.strptime(text, formats[mode]).date().isoformat()
    except ValueError: raise stock.StockError("Date does not match the selected format.")


def preview(path, values):
    """Read/match rows without writes; retain invalid rows for visible review."""
    sheet = int(values.get("sheet", 0))
    sheets, rows = imports.read_file(values["filename"], values["data"], sheet)
    header = int(values.get("header_row", 1)) - 1
    if not 0 <= header < len(rows): raise stock.StockError("Choose a valid header row.")
    headers = [str(cell).strip() for cell in rows[header]]
    provided = values.get("mapping", {})
    if not isinstance(provided, dict): raise stock.StockError("Column mapping must be an object.")
    mapping = {}
    for key, aliases in FIELDS.items():
        index = provided.get(key, next((i for i, name in enumerate(headers) if name.casefold() in aliases), None))
        if index is not None and (type(index) is not int or not 0 <= index < len(headers)):
            raise stock.StockError("Choose valid source columns.")
        mapping[key] = index
    if any(mapping[key] is None for key in ("date", "kind", "quantity")):
        raise stock.StockError("Map Date, Type and Quantity before reviewing history.")
    if mapping["name"] is None and mapping["code"] is None:
        raise stock.StockError("Map a product name or code column.")
    source = sha256(base64.b64decode(values["data"], validate=True)).hexdigest()
    with database.connect(path) as connection:
        products = [dict(row) for row in connection.execute("SELECT id,name,code,unit,precision,archived FROM products ORDER BY name")]
        existing = [dict(row) for row in connection.execute("SELECT * FROM stock_entries WHERE voided=0")]
        known = {row[0] for row in connection.execute("SELECT id FROM operations")}
    result = []
    signatures = set()
    for number, cells in enumerate(rows[header + 1:], header + 2):
        if not any(str(cell).strip() for cell in cells): continue
        def cell(key):
            index = mapping[key]
            return str(cells[index]).strip() if index is not None and index < len(cells) else ""
        name, code = cell("name"), cell("code")
        matches = [product for product in products if (code and product["code"].casefold() == code.casefold()) or
                   (not code and name and product["name"].casefold() == name.casefold())]
        identity = str(uuid5(SOURCE_NAMESPACE, f"{source}:{sheet}:{number}"))
        errors = []
        try: day = parse_date(cell("date"), values.get("date_format", "iso"))
        except stock.StockError as error: day = ""; errors.append(str(error))
        kind = KINDS.get(cell("kind").casefold(), "")
        if not kind: errors.append("Type must be opening, receive or issue.")
        possible = []
        for product in matches:
            try: amount = stock.to_minor(cell("quantity"), product["precision"])
            except stock.StockError as error: errors.append(f"{product['name']}: {error}"); continue
            possible.extend(row["id"] for row in existing if (row["product_id"], row["kind"], row["effective_date"], row["quantity_minor"]) == (product["id"], kind, day, amount))
        signature = (code.casefold() or name.casefold(), kind, day, cell("quantity"))
        repeated = signature in signatures
        signatures.add(signature)
        result.append(dict(row=number, id=identity, name=name, code=code, raw_date=cell("date"),
                           effective_date=day, kind=kind, quantity=cell("quantity"), notes=cell("notes"),
                           matches=matches, errors=list(dict.fromkeys(errors)), possible_duplicates=possible,
                           repeated_in_file=repeated, already_imported=identity in known))
        if len(result) > 200: raise stock.StockError("Review history in batches of at most 200 rows.")
    return dict(sheets=sheets, headers=headers, mapping=mapping, source_hash=source, rows=result)


def apply(path, values):
    """Back up, then import all selected rows atomically with retry protection.

    Skipped rows never enter the ledger. Existing matching entries require an
    acknowledgement, checked under the write lock so concurrent changes cannot
    slip between preview and apply. Quantity/date/permissions are revalidated.
    """
    identity = str(values["id"])
    request = {"type": "history-import", **values}
    if not 1 <= len(values["rows"]) <= 200: raise stock.StockError("Review 1–200 history rows.")
    with database.connect(path) as connection:
        prior = workflows.cached(connection, identity, request)
        if prior is not None: return prior
    backups.create(path, "protected-pre-history-import")
    with database.connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        prior = workflows.cached(connection, identity, request)
        if prior is not None: return prior
        entries = []
        selected = set()
        for row in values["rows"]:
            if row["action"] == "skip": continue
            if row["action"] != "import": raise stock.StockError("Choose Import or Skip for every row.")
            product = connection.execute("SELECT * FROM products WHERE id=?", (row["product_id"],)).fetchone()
            if not product: raise stock.MissingProduct("A matched product no longer exists.")
            amount = stock.to_minor(row["quantity"], product["precision"])
            seen = connection.execute("SELECT 1 FROM operations WHERE id=?", (row["id"],)).fetchone()
            duplicate = connection.execute("SELECT 1 FROM stock_entries WHERE product_id=? AND kind=? AND effective_date=? AND quantity_minor=? AND voided=0",
                                           (row["product_id"], row["kind"], row["effective_date"], amount)).fetchone()
            signature = (row["product_id"], row["kind"], row["effective_date"], amount)
            if (duplicate or signature in selected) and not seen and not row.get("acknowledge_duplicate", False):
                raise stock.StockError(f"Row {row['row']} matches an existing transaction. Explicitly acknowledge the possible duplicate or skip it.")
            entries.append({key: row[key] for key in ("id", "product_id", "kind", "effective_date", "quantity", "notes")})
            selected.add(signature)
        if not entries: raise stock.StockError("Select at least one history row to import.")
        result = workflows.bulk(path, str(uuid5(UUID(identity), "history-rows")), entries, connection)
        audit.record(connection, "history.imported", identity, after={"filename": values["filename"], "source_rows": [row["row"] for row in values["rows"] if row["action"] == "import"]})
        workflows.remember(connection, identity, request, result)
        return result
