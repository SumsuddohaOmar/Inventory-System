"""Date-range inventory reports and portable Excel workbooks without paid tools.

Every report saves its data snapshot in SQLite. Re-exporting an old version
never quietly substitutes new stock values. Cells use literal strings/numbers,
not formulas built from product names, preventing spreadsheet formula injection.
"""

from calendar import monthrange
from datetime import date, datetime, timezone
from io import BytesIO
import json
import re
from uuid import uuid4
from xml.sax.saxutils import escape as _escape
from zipfile import ZipFile, ZIP_DEFLATED

from app.database import connect
from app import audit, stock


def xml_text(value):
    """Keep spreadsheet XML valid even when imported text has control bytes.

    The original stored text is preserved. Only the exported representation
    replaces characters that XML 1.0 cannot contain, including lone surrogates.
    """
    return "".join(character if character in "\t\n\r" or
                   0x20 <= ord(character) <= 0xD7FF or
                   0xE000 <= ord(character) <= 0xFFFD or
                   0x10000 <= ord(character) <= 0x10FFFF else "\ufffd"
                   for character in str(value))


def escape(value, entities=None):
    """Sanitize before applying ordinary XML escaping to text/attributes."""
    return _escape(xml_text(value), entities or {})


def create(path, start, end, product_ids=(), exclude_zero=True, name="Inventory report"):
    """Snapshot period opening, received, issued and closing for selected products.

    Period opening is the ledger balance strictly BEFORE start. An opening entry
    dated inside the range is shown as additional opening allocation so no stock
    disappears for mid-month products. opening + received - issued = closing.
    """
    if start > end: raise stock.StockError("Report start must not follow its end.")
    with connect(path) as connection:
        connection.execute("BEGIN")
        products = connection.execute("SELECT p.*,c.name AS category FROM products p LEFT JOIN categories c ON p.category_id=c.id ORDER BY c.position,p.name").fetchall()
        entries = connection.execute("SELECT * FROM stock_entries WHERE voided=0 AND effective_date<=?", (end,)).fetchall()
        selected = set(product_ids)
        rows = []
        for product in products:
            if selected and product["id"] not in selected: continue
            opening = received = issued = 0
            for entry in entries:
                if entry["product_id"] != product["id"]: continue
                amount = entry["quantity_minor"]
                if entry["effective_date"] < start or entry["kind"] == "opening":
                    opening += -amount if entry["kind"] == "issue" else amount
                elif entry["kind"] == "receive": received += amount
                else: issued += amount
            closing = opening + received - issued
            if exclude_zero and not any((opening, received, issued, closing)): continue
            rows.append(dict(product_id=product["id"], name=product["name"], category=product["category"] or "Unassigned",
                             category_id=product["category_id"], unit=product["unit"], code=product["code"], custom_fields=json.loads(product["fields_json"]),
                             opening=stock.display(opening, product["precision"]), received=stock.display(received, product["precision"]),
                             issued=stock.display(issued, product["precision"]), closing=stock.display(closing, product["precision"])))
        identity = str(uuid4())
        result = dict(id=identity, name=name, from_date=start, to_date=end, rows=rows,
                      created_at=datetime.now(timezone.utc).isoformat())
        connection.execute("INSERT INTO report_versions VALUES (?, ?, ?, ?, ?, ?)",
                           (identity, name, start, end, json.dumps(result), result["created_at"]))
        audit.record(connection, "report.saved", identity, after={"name": name, "from": start, "to": end})
        return result


def period(kind, end):
    """Cumulative Friday reports start on month day 1; monthly covers the month."""
    value = date.fromisoformat(end)
    if kind == "friday" and value.weekday() != 4: raise stock.StockError("Choose a Friday for a Friday report.")
    first = value.replace(day=1)
    last = value.replace(day=monthrange(value.year, value.month)[1]) if kind == "monthly" else value
    return first.isoformat(), last.isoformat()


def workbook(report) -> bytes:
    """Create a standards-based XLSX with one worksheet per category.

    Standard library ZIP/XML keeps this release dependency-free. Category-specific
    company templates require separate mapping; this is the built-in report.
    """
    groups = {}
    for row in report["rows"]: groups.setdefault(row["category"], []).append(row)
    if not groups: groups["Inventory"] = []
    output = BytesIO()
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        sheets, relations, overrides = [], [], []
        used_names=set()
        for number, (category, rows) in enumerate(groups.items(), 1):
            safe_name=re.sub(r"[\[\]:*?/\\\x00-\x1f]","_",category)[:31].strip("'") or "Inventory"
            if safe_name.casefold() in used_names:safe_name=safe_name[:26]+f" {number}"
            used_names.add(safe_name.casefold())
            sheets.append(f'<sheet name="{escape(safe_name, {chr(34): "&quot;"})}" sheetId="{number}" r:id="rId{number}"/>')
            relations.append(f'<Relationship Id="rId{number}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{number}.xml"/>')
            overrides.append(f'<Override PartName="/xl/worksheets/sheet{number}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
            values = [[report["name"]], [f'{report["from_date"]} to {report["to_date"]}'],
                      ["Item", "Code", "Unit", "Opening", "Received", "Issued", "Available"]]
            values.extend([[row[key] for key in ("name", "code", "unit", "opening", "received", "issued", "closing")] for row in rows])
            xml_rows = []
            for index, cells in enumerate(values, 1):
                cells_xml = []
                for column, value in enumerate(cells):
                    reference = f"{chr(65 + column)}{index}"
                    numeric = index > 3 and column >= 3
                    cells_xml.append(f'<c r="{reference}"><v>{escape(str(value))}</v></c>' if numeric else f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>')
                xml_rows.append(f'<row r="{index}">' + "".join(cells_xml) + '</row>')
            archive.writestr(f"xl/worksheets/sheet{number}.xml", f'<worksheet xmlns="{ns}"><sheetViews><sheetView workbookViewId="0"><pane ySplit="3" topLeftCell="A4" state="frozen"/></sheetView></sheetViews><cols><col min="1" max="1" width="32" customWidth="1"/><col min="2" max="7" width="16" customWidth="1"/></cols><sheetData>{"".join(xml_rows)}</sheetData></worksheet>')
        archive.writestr("xl/workbook.xml", f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{"".join(sheets)}</sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + "".join(relations) + '</Relationships>')
        archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' + "".join(overrides) + '</Types>')
    return output.getvalue()
