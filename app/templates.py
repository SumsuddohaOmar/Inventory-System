"""Fill mapped company XLSX cells while preserving other workbook ZIP parts.

No formulas/macros are executed. Layouts, styles, merges and logos remain in their
original parts. The user maps every worksheet and its data range before export;
unmapped worksheets are rejected to avoid silently distributing stale stock.
"""

import base64
from io import BytesIO
import re
from datetime import date
from xml.etree import ElementTree as ET
from zipfile import ZipFile,ZIP_DEFLATED

from app import imports,stock
from app.reports import xml_text

NS="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
ET.register_namespace("",NS)


def validate(encoded,mapping):
    """Bound the input and require one explicit mapping for every worksheet."""
    sheets,_=imports.read_file("template.xlsx",encoded,0)
    if sorted(row["sheet"] for row in mapping)!=sorted(sheets):raise stock.StockError("Map every worksheet exactly once so no old stock sheet is left unchanged.")
    categories=[row["category_id"] for row in mapping]
    if len(set(categories))!=len(categories):raise stock.StockError("Each category must map to only one worksheet.")
    for row in mapping:
        if not 1<=int(row["start_row"])<=int(row["end_row"])<=10000:raise stock.StockError("Choose a valid template data-row range.")
        if not {"name","opening","issued","closing"}.issubset(row["columns"]):raise stock.StockError("Map Item, Opening, Issued and Available columns.")
        if any(not re.fullmatch(r"[A-Z]{1,3}",column) for column in row["columns"].values()):raise stock.StockError("Use Excel column letters, e.g. A or AB.")
        if len(set(row["columns"].values()))!=len(row["columns"]):raise stock.StockError("Different report fields must not overwrite the same column.")
        if row.get("opening_mode") not in ("period_start","with_receipts"):raise stock.StockError("Choose what the company's Opening column means.")
        if "received" in row["columns"] and row["opening_mode"]=="with_receipts":raise stock.StockError("Do not count receipts in both Opening and a separate Received column.")
    return sheets


def fill(report,encoded,mapping):
    """Replace only mapped cells and titles; reject capacity/receipt ambiguities."""
    validate(encoded,mapping)
    source=BytesIO(base64.b64decode(encoded,validate=True));output=BytesIO()
    with ZipFile(source) as archive,ZipFile(output,"w",ZIP_DEFLATED) as result:
        workbook=imports.xml(archive.read("xl/workbook.xml"))
        relationships=imports.xml(archive.read("xl/_rels/workbook.xml.rels"))
        targets={row.attrib["Id"]:row.attrib["Target"] for row in relationships}
        parts={}
        mapped_ids={row["category_id"] for row in mapping}
        if any(row.get("category_id") not in mapped_ids for row in report["rows"]):raise stock.StockError("The report contains a category that is not mapped in this template.")
        for config in mapping:
            sheet=next(row for row in workbook.find(f"{{{NS}}}sheets") if row.attrib["name"]==config["sheet"])
            target=targets[sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]]
            filename=target.lstrip("/") if target.startswith("/") else "xl/"+target
            original_xml=archive.read(filename)
            declarations=list(ET.iterparse(BytesIO(original_xml),events=("start-ns",)))
            for _,(prefix,uri) in declarations:
                try:ET.register_namespace(prefix,uri)
                except ValueError:pass
            tree=imports.xml(original_xml);data=tree.find(f"{{{NS}}}sheetData")
            # Office's mc:Ignorable contains PREFIX STRINGS, not namespace-aware
            # names. Preserve even unused original declarations so serialization
            # cannot leave those prefix references dangling in the saved file.
            for _,(prefix,uri) in declarations:
                used=any(str(node.tag).startswith("{"+uri+"}") or any(key.startswith("{"+uri+"}") for key in node.attrib) for node in tree.iter())
                if prefix and not used:tree.set("xmlns:"+prefix,uri)
            rows=[row for row in report["rows"] if row.get("category_id")==config["category_id"]]
            start,end=int(config["start_row"]),int(config["end_row"])
            if len(rows)>end-start+1:raise stock.StockError("Template data range is too small. Extend it before exporting.")
            def put(reference,value,numeric=False):
                match=re.fullmatch(r"([A-Z]{1,3})([1-9]\d{0,4})",reference)
                if not match:raise stock.StockError("Invalid mapped template cell.")
                number=int(match[2]);row=next((row for row in data if int(row.attrib["r"])==number),None)
                if row is None:row=ET.SubElement(data,f"{{{NS}}}row",{"r":str(number)})
                cell=next((cell for cell in row if cell.attrib.get("r")==reference),None)
                if cell is None:cell=ET.SubElement(row,f"{{{NS}}}c",{"r":reference})
                # Retain s (style) and other attributes, but remove old values or
                # formulas only in the cells explicitly mapped as data.
                for child in list(cell):cell.remove(child)
                if numeric:
                    cell.attrib.pop("t",None);ET.SubElement(cell,f"{{{NS}}}v").text=str(value)
                else:
                    cell.set("t","inlineStr");ET.SubElement(ET.SubElement(cell,f"{{{NS}}}is"),f"{{{NS}}}t").text=xml_text(value)
            for number in range(start,end+1):
                record=rows[number-start] if number-start<len(rows) else None
                for key,column in config["columns"].items():
                    value=record.get(key,"") if record else ""
                    if record and key=="serial":value=number-start+1
                    if record and key.startswith("field:"):value=record.get("custom_fields",{}).get(key[6:],"")
                    if record and key=="opening":
                        if config["opening_mode"]=="with_receipts":
                            from decimal import Decimal
                            value=str(Decimal(record["opening"])+Decimal(record["received"]))
                        elif "received" not in config["columns"] and any(character not in "0." for character in record["received"]):
                            raise stock.StockError("Receipts would be hidden. Map Received or choose Opening includes receipts.")
                    put(f"{column}{number}",value,bool(record and key in ("serial","opening","received","issued","closing")))
                target_row=next(row for row in data if int(row.attrib["r"])==number)
                if record:target_row.attrib.pop("hidden",None)
                else:target_row.set("hidden","1")
            if config.get("title_cell"):put(config["title_cell"],report["name"])
            start_date=date.fromisoformat(report["from_date"]);end_date=date.fromisoformat(report["to_date"])
            period_text=f'{report["from_date"]} to {report["to_date"]}'
            if config.get("period_style")=="company" and start_date.month==end_date.month and start_date.year==end_date.year:
                period_text=f'{start_date:%b}-{start_date.day} TO {end_date.day}-{end_date.year}'.upper()
            if config.get("period_cell"):put(config["period_cell"],period_text)
            if config.get("issue_period_cell"):put(config["issue_period_cell"],f'{start_date.day} TO {end_date.day} ISSUE')
            # Excel expects rows/cells in coordinate order.
            data[:]=sorted(data,key=lambda row:int(row.attrib["r"]))
            for row in data:row[:]=sorted(row,key=lambda cell:(len(re.sub(r"\d","",cell.attrib["r"])),re.sub(r"\d","",cell.attrib["r"])))
            parts[filename]=ET.tostring(tree,encoding="utf-8",xml_declaration=True)
        for member in archive.infolist():result.writestr(member,parts.get(member.filename,archive.read(member.filename)))
    return output.getvalue()


def suggest(encoded,categories):
    """Suggest mappings from actual header labels/serial rows, never from stock values."""
    sheets,_=imports.read_file("template.xlsx",encoded,0)
    result=[]
    aliases={"name":("item name","items","description"),"code":("items code","item code"),"unit":("unit",),"field:Packing":("packing",),"field:Specification":("specification","specitication"),"serial":("s.no","no"),"opening":("open stock","opening stock"),"received":("received",),"issued":("consumed","issued"),"closing":("available stock",)}
    for index,sheet in enumerate(sheets):
        _,rows=imports.read_file("template.xlsx",encoded,index)
        header=next((number for number,row in enumerate(rows[:30]) if any(str(value).strip().casefold() in aliases["name"] for value in row)),None)
        if header is None:continue
        columns={}
        for number,value in enumerate(rows[header]):
            for key,names in aliases.items():
                if str(value).strip().casefold() in names:columns[key]=chr(65+number)
        category=next((row for row in categories if row["id"] == ("dry-foods" if "FOOD" in sheet.upper() else "disposables" if "DISPOS" in sheet.upper() else "chemicals" if "CHEMI" in sheet.upper() else "stationery" if "STATION" in sheet.upper() else "cutlery-cookeries" if "CUT" in sheet.upper() else "")),None)
        if not category:continue
        name_index=ord(columns["name"])-65
        serial_index=ord(columns.get("serial","A"))-65
        data_rows=[number+1 for number,row in enumerate(rows[header+1:],header+1) if len(row)>max(name_index,serial_index) and row[name_index] and re.fullmatch(r"\d+(?:\.0)?",str(row[serial_index]))]
        if not data_rows:continue
        config=dict(sheet=sheet,category_id=category["id"],start_row=min(data_rows),end_row=max(data_rows),columns=columns,
                    opening_mode="period_start" if "received" in columns else "with_receipts",period_style="company")
        for number,row in enumerate(rows[:header],1):
            for column,value in enumerate(row):
                text=str(value).upper()
                if "STOCK REPORT" in text:config["title_cell"]=f"{chr(65+column)}{number}"
                elif "ISSUE" in text and "TO" in text:config["issue_period_cell"]=f"{chr(65+column)}{number}"
                elif "TO" in text and re.search(r"\d",text):config["period_cell"]=f"{chr(65+column)}{number}"
        result.append(config)
    return result
