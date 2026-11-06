"""Read the supplied template and prepare a credential-free connection manifest.

This script never edits a workbook. Spreadsheet authoring is performed by the
Artifact Tool builder; final native Power Query registration uses Excel itself.
"""
import argparse
import calendar
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from uuid import NAMESPACE_URL, uuid5
from zipfile import ZipFile
import xml.etree.ElementTree as ET

NS={"m":"http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CATEGORIES={"Disposables":"disposables","Dry Foods":"dry-foods","Chemicals":"chemicals",
            "Stationery":"stationery","Cutlery & Crockery":"cutlery-cookeries"}
COLUMNS=["Key","Status","ProductId","ProductName","Unit","RefreshedAt"]+[f"R{day:02d}" for day in range(1,32)]+[f"I{day:02d}" for day in range(1,32)]


def col(number):
    result=""
    while number:
        number,remainder=divmod(number-1,26)
        result=chr(65+remainder)+result
    return result


def prepare(source,output,origin):
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    identity=str(uuid5(NAMESPACE_URL,"inventory-daily-workbook:"+digest))
    definitions=[]
    with ZipFile(source) as archive:
        strings=["".join(node.itertext()) for node in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("m:si",NS)]
        relationships={node.get("Id"):node.get("Target") for node in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))}
        for sheet in ET.fromstring(archive.read("xl/workbook.xml")).findall("m:sheets/m:sheet",NS):
            match=re.fullmatch(r"([A-Za-z]{3} \d{4}) (.+)",sheet.get("name"))
            if not match:continue
            category=CATEGORIES.get(match[2])
            if not category:raise ValueError("Unsupported inventory category: "+match[2])
            month=datetime.strptime(match[1],"%b %Y").strftime("%Y-%m")
            target=relationships[sheet.get("{"+REL+"}id")]
            target=target.lstrip("/") if target.startswith("/") else "xl/"+target
            root=ET.fromstring(archive.read(target))
            cells={}
            for node in root.findall("m:sheetData/m:row/m:c",NS):
                value=node.findtext("m:v",default="",namespaces=NS)
                value=strings[int(value)] if node.get("t")=="s" else "".join(node.find("m:is",NS).itertext()) if node.get("t")=="inlineStr" else value
                cells[node.get("r")]=dict(value=value,formula=node.find("m:f",NS) is not None)
            definitions.append(dict(name=sheet.get("name"),month=month,category_id=category,path=target,cells=cells))
    periods=sorted({sheet["month"] for sheet in definitions})
    if not periods:raise ValueError("No supported monthly inventory worksheets found.")
    items=[]
    for sheet in definitions:
        if sheet["month"]!=periods[0]:continue
        cells=sheet["cells"]
        for row in range(7,10000):
            item=cells.get(f"B{row}")
            if not item or not item["value"] or item["formula"]:continue
            def text(column):return cells.get(f"{column}{row}",{}).get("value","").strip()
            cutlery=sheet["category_id"]=="cutlery-cookeries"
            items.append(dict(key=f'{sheet["category_id"]}:{row}',name=item["value"].strip(),category_id=sheet["category_id"],
                              unit="" if cutlery else text("C"),code=text("C") if cutlery else "",
                              packing=text("D") if sheet["category_id"]=="dry-foods" else "",
                              specification=text("D") if cutlery else ""))
    keys=sorted(item["key"]+":"+month for month in periods for item in items)
    index={key:row+2 for row,key in enumerate(keys)}
    sheets=[]
    for sheet in definitions:
        rows=[dict(row=int(item["key"].split(":")[1]),data_row=index[item["key"]+":"+sheet["month"]]) for item in items if item["category_id"]==sheet["category_id"]]
        wide=sheet["category_id"] in ("dry-foods","cutlery-cookeries")
        days=calendar.monthrange(*map(int,sheet["month"].split("-")))[1]
        # Confirm the actual template headers before touching any yellow cells.
        for start in (6 if wide else 5,45 if wide else 43):
            for day in range(days):
                if not sheet["cells"].get(f"{col(start+day)}6",{}).get("formula"):
                    raise ValueError("Missing date header on "+sheet["name"])
        sheets.append(dict(name=sheet["name"],path=sheet["path"],rows=rows,days=days,receive_start=6 if wide else 5,issue_start=45 if wide else 43))
    manifest=dict(id=identity,label="Inventory daily quantities November 2026–October 2027",start_month=periods[0],months=len(periods),items=items)
    output.mkdir(parents=True,exist_ok=True)
    setup=output/"Excel connection setup.json"
    setup.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    plan=dict(source=str(source.resolve()),source_sha256=digest,output=str((output/"Inventory System - App Daily Link.xlsx").resolve()),
              profile=manifest,columns=COLUMNS,keys=keys,sheets=sheets,feed_url=origin.rstrip("/")+f"/api/excel-feed/{identity}/odata/")
    (output/"workbook-build-plan.json").write_text(json.dumps(plan,ensure_ascii=False),encoding="utf-8")
    print(f"Prepared {len(items)} product mappings across {len(sheets)} worksheets. No workbook or app data changed.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("source",type=Path)
    parser.add_argument("output",type=Path)
    parser.add_argument("--origin",default="https://inventory-sample-pilot.onrender.com")
    args=parser.parse_args()
    prepare(args.source,args.output,args.origin)
