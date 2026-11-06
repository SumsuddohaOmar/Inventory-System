"""Read-only preservation audit of a prepared copy; never edits the source."""
import hashlib
import base64
import io
import json
from pathlib import Path
import sys
import struct
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from openpyxl import load_workbook
from preserve_excel_template import sheet_paths


def main(plan_path):
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    source, output = Path(plan["source"]), Path(plan["output"])
    assert hashlib.sha256(source.read_bytes()).hexdigest() == plan["source_sha256"], "Source changed"
    original = load_workbook(source, read_only=True, data_only=False)
    linked = load_workbook(output, read_only=True, data_only=False)
    cached = load_workbook(output, read_only=True, data_only=True)
    assert linked.sheetnames == original.sheetnames + ["App Data"]
    definitions = {row["name"]: row for row in plan["sheets"]}
    formulas = daily = 0
    checked_styles=set()
    for name in original.sheetnames:
        definition = definitions.get(name)
        changed = set()
        if definition:
            for item in definition["rows"]:
                for start in (definition["receive_start"], definition["issue_start"]):
                    for day in range(definition["days"]):
                        changed.add((item["row"], start + day))
        opening = definition["receive_start"] - 1 if definition else None
        product_rows = {item["row"] for item in definition["rows"]} if definition else set()
        for row_number, (old_row, new_row, cached_row) in enumerate(zip(original[name].iter_rows(), linked[name].iter_rows(), cached[name].iter_rows()), 1):
            for col_number, (old, new, value) in enumerate(zip(old_row, new_row, cached_row), 1):
                if getattr(old,"has_style",False):
                    pair=(old._style_id,new._style_id)
                    if pair not in checked_styles:
                        for attribute in ("font","fill","border","alignment","number_format","protection"):
                            assert getattr(old,attribute)==getattr(new,attribute), f"Unexpected {attribute} change {name}!{old.coordinate}"
                        checked_styles.add(pair)
                if definition and name.startswith("Nov ") and row_number in product_rows and col_number == opening:
                    assert new.value is None, "Opening must stay blank"
                if old.value is None and new.value is None:
                    continue
                if value.data_type == "e":
                    raise AssertionError(f"Formula error {name}!{value.coordinate}: {value.value}")
                if name == "Overview" and old.coordinate in {"B9", "B31", "B32", "B33", "B34"}:
                    continue
                if (row_number, col_number) in changed:
                    assert new.data_type == "f" and "'App Data'!$BQ" in new.value, f"Missing daily link {name}!{new.coordinate}"
                    daily += 1
                else:
                    assert old.value == new.value, f"Unexpected change {name}!{old.coordinate}: {old.value!r} => {new.value!r}"
                    formulas += old.data_type == "f"
                if old.has_style:
                    assert old.number_format == new.number_format, f"Number format {name}!{old.coordinate}"
                    assert str(old.font) == str(new.font) and str(old.fill) == str(new.fill), f"Style {name}!{old.coordinate}"
    for row in linked["App Data"].iter_rows(min_row=2, max_row=len(plan["keys"])+1, max_col=68):
        assert all(cell.value is None for cell in row), "Fixture data remains"
    with ZipFile(source) as before, ZipFile(output) as archive:
        ns={"m":"http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        old_paths, new_paths=sheet_paths(before), sheet_paths(archive)
        for name in original.sheetnames:
            old_xml=ET.fromstring(before.read(old_paths[name]))
            new_xml=ET.fromstring(archive.read(new_paths[name]))
            for expression in ("m:mergeCells/m:mergeCell", "m:sheetViews/m:sheetView/m:pane", "m:hyperlinks/m:hyperlink", "m:dataValidations/m:dataValidation", "m:conditionalFormatting"):
                def signature(root):
                    return sorted([(node.tag, sorted(node.attrib.items()), node.text or "", [(child.tag,sorted(child.attrib.items()),child.text or "") for child in node]) for node in root.findall(expression,ns)],key=repr)
                assert signature(old_xml)==signature(new_xml), f"Native feature changed: {name} {expression}"
        calculation=ET.fromstring(archive.read("xl/workbook.xml")).find("m:calcPr",ns)
        assert calculation.get("calcMode","auto")=="auto", "Workbook must calculate automatically"
        assert "xl/connections.xml" in archive.namelist()
        assert any(name.startswith("xl/queryTables/queryTable") for name in archive.namelist())
        assert not any("vbaProject" in name for name in archive.namelist()), "Delivered workbook must remain macro-free"
        assert b"InventoryDaily" in archive.read("xl/connections.xml")
        mashup=next(ET.fromstring(archive.read(name)) for name in archive.namelist()
                    if name.startswith("customXml/item") and name.endswith(".xml") and ET.fromstring(archive.read(name)).tag.endswith("DataMashup"))
        blob=base64.b64decode(mashup.text)
        _,size=struct.unpack_from("<II",blob)
        with ZipFile(io.BytesIO(blob[8:8+size])) as package:
            query=package.read("Formulas/Section1.m").decode("utf-8-sig")
            assert plan["feed_url"] in query and "OData.Feed" in query and "LOCAL TEST DATA" not in query
            assert "password" not in query.casefold() and "postgresql://" not in query.casefold()
    result = dict(source_unchanged=True, original_calculation_formulas_preserved=formulas,
                  daily_links=daily, opening_cells_blank=True, fixture_cleared=True,
                  cached_formula_errors=0, native_connection_present=True, macro_free=True)
    (plan_path.parent / "workbook-audit.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    original.close(); linked.close(); cached.close()


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
