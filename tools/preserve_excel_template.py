"""Merge Artifact-authored cells while preserving unsupported native features.

Only new daily-input formulas, five Overview notes and one supporting worksheet
are copied from the Artifact export. Existing style IDs, original calculation
formulas, conditional formatting, hyperlinks, panes and all other ZIP parts
remain from the original workbook. No formulas are authored by this merger.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from zipfile import ZipFile, ZIP_DEFLATED
from lxml import etree as ET

MAIN="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL="http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT="http://schemas.openxmlformats.org/package/2006/content-types"
NS={"m":MAIN}


def column(number):
    result=""
    while number:
        number,remainder=divmod(number-1,26)
        result=chr(65+remainder)+result
    return result


def sheet_paths(archive):
    relationships={row.get("Id"):row.get("Target") for row in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))}
    return {sheet.get("name"):(relationships[sheet.get("{"+REL+"}id")].lstrip("/") if relationships[sheet.get("{"+REL+"}id")].startswith("/") else "xl/"+relationships[sheet.get("{"+REL+"}id")])
            for sheet in ET.fromstring(archive.read("xl/workbook.xml")).findall("m:sheets/m:sheet",NS)}


def serialize(root):return ET.tostring(root,encoding="UTF-8",xml_declaration=True,standalone=True)


def main(plan_path):
    plan=json.loads(plan_path.read_text(encoding="utf-8"))
    source=Path(plan["source"])
    if hashlib.sha256(source.read_bytes()).hexdigest()!=plan["source_sha256"]:
        raise RuntimeError("The original workbook changed after inspection. Reinspect before rebuilding.")
    staged=plan_path.parent/"artifact-authored-cells.xlsx"
    changes={}
    copied=0
    with ZipFile(source) as original,ZipFile(staged) as authored:
        paths=sheet_paths(authored)
        original_paths=sheet_paths(original)
        strings=["".join(node.itertext()) for node in ET.fromstring(authored.read("xl/sharedStrings.xml")).findall("m:si",NS)] if "xl/sharedStrings.xml" in authored.namelist() else []

        def portable(cell):
            result=deepcopy(cell)
            if result.get("t")=="s":
                value=result.find("m:v",NS)
                text=strings[int(value.text)]
                result.remove(value)
                if result.find("m:f",NS) is not None:
                    result.set("t","str")
                    ET.SubElement(result,"{"+MAIN+"}v").text=text
                else:
                    result.set("t","inlineStr")
                    ET.SubElement(ET.SubElement(result,"{"+MAIN+"}is"),"{"+MAIN+"}t").text=text
            return result

        definitions=plan["sheets"]+[dict(name="Overview",overview=True)]
        for definition in definitions:
            name=definition["name"]
            root=ET.fromstring(original.read(original_paths[name]))
            data=root.find("m:sheetData",NS)
            row_map={int(row.get("r")):row for row in data}
            old={cell.get("r"):cell for cell in data.findall("m:row/m:c",NS)}
            new_root=ET.fromstring(authored.read(paths[name]))
            new={cell.get("r"):cell for cell in new_root.findall("m:sheetData/m:row/m:c",NS)}
            if definition.get("overview"):
                references=["B9","B31","B32","B33","B34"]
            else:
                references=[f"{column(start+day)}{item['row']}" for start in (definition["receive_start"],definition["issue_start"])
                            for item in definition["rows"] for day in range(definition["days"])]
            for reference in references:
                generated=portable(new[reference])
                if not definition.get("overview") and generated.find("m:f",NS) is None:
                    raise RuntimeError("The Artifact export is missing a daily formula: "+name+"!"+reference)
                target=old.get(reference)
                if target is None:
                    row_number=int(''.join(char for char in reference if char.isdigit()))
                    row=row_map.get(row_number)
                    if row is None:
                        row=ET.SubElement(data,"{"+MAIN+"}row",r=str(row_number));row_map[row_number]=row
                    target=ET.SubElement(row,"{"+MAIN+"}c",r=reference,s="43")
                # Keep the original cell's native style and other attributes.
                for child in list(target):target.remove(child)
                target.attrib.pop("t",None)
                if generated.get("t"):target.set("t",generated.get("t"))
                for child in generated:target.append(deepcopy(child))
                copied+=1
            if definition.get("overview"):
                dimension=root.find("m:dimension",NS)
                if dimension is not None:dimension.set("ref","B2:G34")
            changes[original_paths[name]]=serialize(root)

        # New support-sheet strings are converted to inline strings so the
        # original sharedStrings table can remain completely untouched.
        support=ET.fromstring(authored.read(paths["App Data"]))
        for row in support.findall("m:sheetData/m:row",NS):
            for cell in list(row):
                if cell.tag!="{"+MAIN+"}c":continue
                replacement=portable(cell)
                replacement.set("s","18" if row.get("r")=="1" else "0")
                row.replace(cell,replacement)
        for col in support.findall("m:cols/m:col",NS):col.attrib.pop("style",None)
        new_part="xl/worksheets/appDailyData.xml"
        changes[new_part]=serialize(support)
        workbook=ET.fromstring(original.read("xl/workbook.xml"))
        sheets=workbook.find("m:sheets",NS)
        sheet_id=max(int(sheet.get("sheetId")) for sheet in sheets)+1
        ET.SubElement(sheets,"{"+MAIN+"}sheet",name="App Data",sheetId=str(sheet_id),attrib={"{"+REL+"}id":"rIdAppDailyData"})
        calculation=workbook.find("m:calcPr",NS)
        if calculation is None:calculation=ET.SubElement(workbook,"{"+MAIN+"}calcPr")
        calculation.attrib.update(dict(calcMode="auto",fullCalcOnLoad="1",forceFullCalc="1"))
        changes["xl/workbook.xml"]=serialize(workbook)
        relationships=ET.fromstring(original.read("xl/_rels/workbook.xml.rels"))
        ET.SubElement(relationships,"{"+PACKAGE_REL+"}Relationship",Id="rIdAppDailyData",Type=REL+"/worksheet",Target="worksheets/appDailyData.xml")
        changes["xl/_rels/workbook.xml.rels"]=serialize(relationships)
        content=ET.fromstring(original.read("[Content_Types].xml"))
        ET.SubElement(content,"{"+CONTENT+"}Override",PartName="/"+new_part,ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml")
        changes["[Content_Types].xml"]=serialize(content)
        output=Path(plan["output"])
        with ZipFile(output,"w",ZIP_DEFLATED) as target:
            for part in original.infolist():target.writestr(part,changes.pop(part.filename,original.read(part.filename)))
            for name,contents in changes.items():target.writestr(name,contents)
    if hashlib.sha256(source.read_bytes()).hexdigest()!=plan["source_sha256"]:
        raise RuntimeError("Source preservation check failed.")
    print(f"Preserved native template features; copied {copied} Artifact-authored input/note cells. Original source unchanged.")


if __name__=="__main__":main(Path(sys.argv[1]).resolve())
