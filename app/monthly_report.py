"""Workbook-inspired monthly stock sheets built only from saved app snapshots.

The reference workbook supplies the presentation, never products or quantities.
Keep this small exporter dependency-free like the existing built-in exporter.
The browser uses the same category-specific columns and labels.
"""
from datetime import date
from decimal import Decimal
from io import BytesIO
from math import ceil
import re
from zipfile import ZipFile, ZIP_DEFLATED

from app.reports import escape


def columns(category_id):
    """Food needs packing; cutlery needs codes/specifications; all keep units."""
    result = [("number", "S.no", 6)]
    if category_id == "cutlery-cookeries":
        result.append(("code", "Item code", 16))
    result.append(("name", "Item name", 38))
    if category_id == "dry-foods":
        result.append(("packing", "Packing", 22))
    if category_id == "cutlery-cookeries":
        result.append(("specification", "Specification", 36))
    return result + [("unit", "Unit", 12), ("stock_sheet_opening", "Open stock", 16),
                     ("issued", "Consumed", 16),
                     ("closing", "Available stock", 20)]


def stock_sheet_opening(row):
    """The user's stock-sheet convention includes this month's receipts.

    Keep the true period opening and received amounts untouched in the saved
    report. Decimal adds their exact strings without binary float rounding.
    """
    return format(Decimal(row["opening"]) + Decimal(row["received"]), "f")


def custom_field(row, label):
    return next((str(value) for key, value in row.get("custom_fields", {}).items()
                 if key.strip().casefold() == label), "")


def workbook(report):
    """Create one colored, printable stock sheet per category with numeric cells.

    Values are literal snapshot data, not formulas or links to the user's file.
    Decimal display uses one digit while the exact underlying value is retained.
    Open stock includes receipts: previous closing + received - consumed = available.
    """
    groups = {}
    for row in report["rows"]:
        groups.setdefault((row.get("category_id"), row["category"]), []).append(row)
    if not groups:
        groups[(None, "Inventory")] = []
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    numeric = {"stock_sheet_opening", "issued", "closing"}
    styles = {"stock_sheet_opening": 3, "issued": 4, "closing": 5}
    start, end = date.fromisoformat(report["from_date"]), date.fromisoformat(report["to_date"])
    period = f'{start.strftime("%B").upper()} {start.day:02d} TO {end.day:02d}, {start.year}'

    def cell(reference, value, style=0, number=False):
        text = escape(str(value))
        payload = f"<v>{text}</v>" if number else f'<is><t xml:space="preserve">{text}</t></is>'
        return f'<c r="{reference}" s="{style}"' + ('>' if number else ' t="inlineStr">') + payload + '</c>'

    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        sheets, relations, overrides, names = [], [], [], set()
        for index, ((category_id, category), rows) in enumerate(groups.items(), 1):
            name = re.sub(r"[\[\]:*?/\\\x00-\x1f]", "_", category)[:31].strip("'") or "Inventory"
            if name.casefold() in names:
                name = name[:26] + f" {index}"
            names.add(name.casefold())
            sheets.append(f'<sheet name="{escape(name, {chr(34): "&quot;"})}" sheetId="{index}" r:id="rId{index}"/>')
            relations.append(f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>')
            overrides.append(f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
            headers = columns(category_id)
            last = chr(64 + len(headers))
            xml_rows = []
            title = report.get("name", "Inventory report")
            titles = ["MONTHLY STOCK REPORT" if title == "Inventory report" else title, f'{report.get("company_name", "Local Inventory")} STOCK SHEET',
                      f"{category} · {period}", "Open stock = previous closing + received this month. Available stock = open stock - consumed."]
            for line, title in enumerate(titles, 1):
                xml_rows.append(f'<row r="{line}" ht="24" customHeight="1">{cell(f"A{line}",title,1 if line<=3 else 7)}</row>')
            # The reference's cutlery sheet uses yellow item headers and a
            # white serial header; the other categories use green item headers.
            header_cells = [cell(f'{chr(65+col)}5', label,
                                 0 if category_id == "cutlery-cookeries" and key == "number" else
                                 styles.get(key, 3 if category_id == "cutlery-cookeries" else 2))
                            for col, (key, label, _) in enumerate(headers)]
            xml_rows.append('<row r="5" ht="32" customHeight="1">' + "".join(header_cells) + '</row>')
            for number, original in enumerate(rows, 1):
                row = {**original, "number": number, "stock_sheet_opening": stock_sheet_opening(original), "packing": custom_field(original,"packing"),
                       "specification": custom_field(original,"specification")}
                cells = [cell(f'{chr(65+col)}{number+5}', row.get(key,""),
                              6 if key in numeric else 0, key in numeric or key == "number")
                         for col, (key, _, _) in enumerate(headers)]
                lines = max(ceil(len(str(row.get(key,""))) / max(width-2,1)) for key, _, width in headers)
                height = min(409, max(30, lines*15+8))
                xml_rows.append(f'<row r="{number+5}" ht="{height}" customHeight="1">' + "".join(cells) + '</row>')
            widths = "".join(f'<col min="{col}" max="{col}" width="{width}" customWidth="1"/>'
                             for col, (_, _, width) in enumerate(headers, 1))
            merges = "".join(f'<mergeCell ref="A{line}:{last}{line}"/>' for line in range(1,5))
            sheet = (f'<worksheet xmlns="{ns}"><sheetPr><pageSetUpPr fitToPage="1"/></sheetPr><sheetViews><sheetView workbookViewId="0" showGridLines="0">'
                     '<pane ySplit="5" topLeftCell="A6" state="frozen"/></sheetView></sheetViews>'
                     f'<cols>{widths}</cols><sheetData>{"".join(xml_rows)}</sheetData>'
                     f'<autoFilter ref="A5:{last}{len(rows)+5}"/><mergeCells count="4">{merges}</mergeCells>'
                     '<pageMargins left="0.25" right="0.25" top="0.4" bottom="0.4" header="0.2" footer="0.2"/>'
                     '<pageSetup orientation="landscape" paperSize="9" fitToWidth="1" fitToHeight="0"/></worksheet>')
            archive.writestr(f"xl/worksheets/sheet{index}.xml", sheet)
        archive.writestr("xl/workbook.xml", f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{"".join(sheets)}</sheets></workbook>')
        relations.append('<Relationship Id="styles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + "".join(relations) + '</Relationships>')
        archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' + "".join(overrides) + '</Types>')
        fills = ['none','gray125','FFFFFF00','FF92D050','FFFFFF00','FF00B0F0','FFFF0000']
        fill_xml = ''.join(f'<fill><patternFill patternType="{color}"/></fill>' if color in ('none','gray125')
                           else f'<fill><patternFill patternType="solid"><fgColor rgb="{color}"/><bgColor indexed="64"/></patternFill></fill>' for color in fills)
        # Style indices: body, title, identity header, three balance headers,
        # numeric body, equation note. Labels accompany every color.
        xfs = ['<xf fontId="0" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>']
        xfs += ['<xf fontId="1" fillId="2" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>']
        xfs += [f'<xf fontId="1" fillId="{fill}" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>' for fill in (3,4,5,6)]
        xfs += ['<xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>',
                '<xf fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>']
        archive.writestr('xl/styles.xml', f'<styleSheet xmlns="{ns}"><numFmts count="1"><numFmt numFmtId="164" formatCode="#,##0.0"/></numFmts><fonts count="2"><font><sz val="11"/><name val="Arial"/></font><font><b/><sz val="11"/><name val="Arial"/></font></fonts><fills count="7">{fill_xml}</fills><borders count="2"><border/><border><left style="thin"/><right style="thin"/><top style="thin"/><bottom style="thin"/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="8">{"".join(xfs)}</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
    return output.getvalue()
