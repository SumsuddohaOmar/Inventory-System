"""Bounded CSV/XLSX product import with explicit duplicate review and backup.

Only data cells are read; macros and formulas are never evaluated. A preview
cannot mutate inventory. Applying reviewed choices is one transaction after a
verified recovery copy. Linking never overwrites an existing product's stock.
"""

import base64
import csv
from datetime import datetime, timezone
from difflib import SequenceMatcher
from io import BytesIO, StringIO
import json
from pathlib import PurePosixPath
from uuid import uuid4
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from app import audit, backups, database, stock

NS = {"s":"http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def xml(data):
    """Reject document types/entities before parsing an untrusted Office part."""
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper(): raise stock.StockError("XML entities are not allowed in imports.")
    return ET.fromstring(data)


def read_file(filename, encoded, sheet=0):
    """Return worksheet names and rows from a bounded CSV/XLSX upload."""
    if len(encoded)>3_000_000: raise stock.StockError("Import file must be under 2 MB.")
    data=base64.b64decode(encoded,validate=True)
    if len(data)>2_000_000: raise stock.StockError("Import file must be under 2 MB.")
    if filename.lower().endswith(".csv"):
        rows=list(csv.reader(StringIO(data.decode("utf-8-sig"))))
        if len(rows)>10000: raise stock.StockError("Import has too many rows.")
        return ["CSV"],rows
    if not filename.lower().endswith(".xlsx"): raise stock.StockError("Use .xlsx or UTF-8 .csv files.")
    with ZipFile(BytesIO(data)) as archive:
        files=archive.infolist()
        if len(files)>2000 or sum(file.file_size for file in files)>20_000_000: raise stock.StockError("Expanded workbook is too large.")
        workbook=xml(archive.read("xl/workbook.xml"))
        sheets=workbook.findall("s:sheets/s:sheet",NS)
        if not 0<=sheet<len(sheets): raise stock.StockError("Worksheet selection is out of range.")
        relationship=sheets[sheet].attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
        links=xml(archive.read("xl/_rels/workbook.xml.rels"))
        link=next(row for row in links if row.attrib["Id"]==relationship)
        target=link.attrib["Target"]
        name=target.lstrip("/") if target.startswith("/") else str(PurePosixPath("xl")/target)
        if ".." in PurePosixPath(name).parts: raise stock.StockError("Invalid worksheet path.")
        strings=[]
        if "xl/sharedStrings.xml" in archive.namelist():
            strings=["".join(item.itertext()) for item in xml(archive.read("xl/sharedStrings.xml")).findall("s:si",NS)]
        rows=[]
        cells=0
        for row in xml(archive.read(name)).findall("s:sheetData/s:row",NS):
            values=[]
            for cell in row.findall("s:c",NS):
                cells+=1
                if cells>100000: raise stock.StockError("Workbook has too many cells.")
                letters="".join(character for character in cell.attrib.get("r","A1") if character.isalpha())
                column=0
                for character in letters:column=column*26+ord(character.upper())-64
                if column>200: raise stock.StockError("Worksheet has too many columns.")
                while len(values)<column:values.append("")
                raw=cell.find("s:v",NS)
                value=raw.text if raw is not None and raw.text else ""
                if cell.attrib.get("t")=="s":value=strings[int(value)]
                elif cell.attrib.get("t")=="inlineStr":value="".join(cell.find("s:is",NS).itertext())
                values[column-1]=value
            # Preserve physical row numbers so a chosen header row is stable.
            index=int(row.attrib.get("r",len(rows)+1))
            if index>10000:raise stock.StockError("Worksheet has too many rows.")
            while len(rows)<index:rows.append([])
            rows[index-1]=values
        return [row.attrib["name"] for row in sheets],rows


def preview(path, values):
    """Suggest matches; require a human choice before creating/linking products."""
    sheets,rows=read_file(values["filename"],values["data"],int(values.get("sheet",0)))
    header=int(values.get("header_row",1))-1
    if not 0<=header<len(rows):raise stock.StockError("Choose a valid header row.")
    headers=[str(value).strip() for value in rows[header]]
    aliases={"name":("item","item name","items","description","product","product name"),"unit":("unit","uom"),"opening":("opening","opening stock","open stock"),"code":("code","item code","items code")}
    mapping=values.get("mapping",{})
    for key,names in aliases.items():
        if key not in mapping:
            mapping[key]=next((index for index,name in enumerate(headers) if name.casefold() in names),None)
    if mapping.get("name") is None:raise stock.StockError("Choose the product-name column or a header named Item / Product / Description.")
    with database.connect(path) as connection:existing=[dict(row) for row in connection.execute("SELECT id,name,unit FROM products")]
    result=[]
    for number,cells in enumerate(rows[header+1:],header+2):
        def value(key):
            index=mapping.get(key)
            return str(cells[int(index)]).strip() if index is not None and 0<=int(index)<len(cells) else ""
        name=value("name")
        if not name:continue
        matches=[dict(product,match="Exact" if product["name"].casefold()==name.casefold() else "Possible") for product in existing if SequenceMatcher(None,product["name"].casefold(),name.casefold()).ratio()>=0.8]
        result.append(dict(row=number,name=name,unit=value("unit"),code=value("code"),opening=value("opening"),matches=matches))
        if len(result)>200:raise stock.StockError("Review imports in batches of at most 200 products.")
    return {"sheets":sheets,"headers":headers,"mapping":mapping,"rows":result}


def apply(path, values):
    """Apply reviewed product/opening rows, skipping links without overwriting data."""
    identity=str(values["id"])
    from app.workflows import cached,remember
    if not 1<=len(values["rows"])<=200:raise stock.StockError("Review 1–200 import rows.")
    with database.connect(path) as connection:
        previous=cached(connection,identity,{"type":"import",**values})
        if previous is not None:return previous
    backups.create(path,"protected-pre-import")
    with database.connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        prior=cached(connection,identity,{"type":"import",**values})
        if prior is not None:return prior
        result=[]
        for row in values["rows"]:
            if row["action"]=="skip":continue
            if row["action"]=="link":
                match=connection.execute("SELECT id FROM products WHERE id=?",(row.get("target_id"),)).fetchone()
                if not match:raise stock.StockError("A linked product no longer exists.")
                result.append({"id":match["id"],"action":"linked"});continue
            if row["action"]!="create":raise stock.StockError("Choose Create, Link or Skip for every row.")
            if not 1<=len(row["name"].strip())<=120 or not 1<=len(row["unit"].strip())<=30:raise stock.StockError("Every new product needs a name and unit.")
            category=values["category_id"]
            if not connection.execute("SELECT 1 FROM categories WHERE id=? AND archived=0",(category,)).fetchone():raise stock.StockError("Choose an active category.")
            product=str(uuid4())
            connection.execute("INSERT INTO products(id,name,unit,created_at,category_id,precision,code) VALUES (?,?,?,?,?,3,?)",
                               (product,row["name"].strip(),row["unit"].strip(),datetime.now(timezone.utc).isoformat(),category,str(row.get("code",""))[:80]))
            audit.record(connection,"product.imported",product,after={"name":row["name"],"source_row":row["row"]})
            if row.get("opening","")!="":
                stock.save_entry(path,product,dict(id=str(uuid4()),kind="opening",effective_date=values["date"],quantity=str(row["opening"]),notes="Reviewed Excel import"),connection)
            result.append({"id":product,"action":"created"})
        remember(connection,identity,{"type":"import",**values},result)
        return result
