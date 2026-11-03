"""Excel/CSV review, template preservation and connection-code regression tests."""

import base64
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4
from zipfile import ZipFile,ZIP_DEFLATED
from xml.etree import ElementTree as ET

from app import connection_qr,database,imports,reports,stock,templates,workflows
from app.main import create_app
from fastapi import Request
from fastapi import HTTPException


class ImportTemplateTests(unittest.TestCase):
    def setUp(self):
        self.folder=TemporaryDirectory(dir=Path.cwd());self.path=Path(self.folder.name)/"inventory.sqlite3"
        self.clock=patch.object(stock,"today",return_value="2026-11-02");self.clock.start()
        database.initialize(self.path)
        self.product=database.insert_product(self.path,dict(id=str(uuid4()),name="Rice",unit="KG",category_id="dry-foods",precision=3,created_at="2026-11-02"))

    def tearDown(self):self.clock.stop();self.folder.cleanup()

    def movement(self,kind,quantity,product=None):
        return stock.save_entry(self.path,product or self.product["id"],dict(id=str(uuid4()),kind=kind,quantity=quantity,effective_date="2026-11-01",notes=""))

    def test_csv_preview_does_not_write_and_reviews_duplicates(self):
        encoded=base64.b64encode(b"Item,Unit,Opening\nRice,KG,10\nNew item,Piece,5\n").decode()
        preview=imports.preview(self.path,{"filename":"items.csv","data":encoded})
        self.assertEqual(preview["rows"][0]["matches"][0]["id"],self.product["id"])
        self.assertEqual(len(database.list_products(self.path)),1)
        rows=[{**preview["rows"][0],"action":"link","target_id":self.product["id"]}, {**preview["rows"][1],"action":"create"}]
        values=dict(id=str(uuid4()),rows=rows,category_id="dry-foods",date="2026-11-01")
        result=imports.apply(self.path,values)
        self.assertEqual(imports.apply(self.path,values),result)
        self.assertEqual(len(database.list_products(self.path)),2)
        self.assertFalse(stock.get_stock(self.path,self.product["id"])["initialized"])
        self.assertEqual(stock.get_stock(self.path,result[1]["id"])["balance"],"5.000")

    def test_import_rolls_back_all_rows_on_invalid_stock(self):
        values=dict(id=str(uuid4()),category_id="dry-foods",date="2026-11-01",rows=[dict(row=2,name="Good",unit="KG",opening="10",action="create"),dict(row=3,name="Bad",unit="KG",opening="1.0001",action="create")])
        with self.assertRaises(stock.StockError):imports.apply(self.path,values)
        self.assertEqual(len(database.list_products(self.path)),1)
        self.assertTrue(list((self.path.parent/"backups").glob("protected-pre-import-*.sqlite3")))

    def report(self):
        self.movement("opening","100");self.movement("receive","20");self.movement("issue","15")
        return reports.create(self.path,"2026-11-01","2026-11-02")

    def test_xlsx_reader_and_template_preserve_other_parts(self):
        report=self.report();original=reports.workbook(report)
        # A non-worksheet ZIP part must survive without byte changes.
        file=BytesIO()
        with ZipFile(BytesIO(original)) as source,ZipFile(file,"w",ZIP_DEFLATED) as target:
            for row in source.infolist():target.writestr(row,source.read(row.filename))
            target.writestr("customXml/preserve.xml",b"<unchanged>original</unchanged>")
        encoded=base64.b64encode(file.getvalue()).decode()
        sheets,rows=imports.read_file("template.xlsx",encoded)
        self.assertEqual(rows[3][0],"Rice")
        mapping=[dict(sheet=sheets[0],category_id="dry-foods",start_row=4,end_row=6,opening_mode="period_start",columns=dict(name="A",code="B",unit="C",opening="D",received="E",issued="F",closing="G"))]
        result=templates.fill(report,encoded,mapping)
        with ZipFile(BytesIO(result)) as archive:
            self.assertEqual(archive.read("customXml/preserve.xml"),b"<unchanged>original</unchanged>")
            root=ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            self.assertEqual(root.find(".//{*}c[@r='G4']/{*}v").text,"105.000")
            self.assertEqual(root.find(".//{*}row[@r='5']").attrib["hidden"],"1")

    def test_hidden_receipts_and_duplicate_columns_are_rejected(self):
        report=self.report();encoded=base64.b64encode(reports.workbook(report)).decode()
        mapping=[dict(sheet="Dry Foods",category_id="dry-foods",start_row=4,end_row=6,opening_mode="period_start",columns=dict(name="A",opening="D",issued="F",closing="G"))]
        with self.assertRaises(stock.StockError):templates.fill(report,encoded,mapping)
        mapping[0]["opening_mode"]="with_receipts"
        filled=templates.fill(report,encoded,mapping)
        with ZipFile(BytesIO(filled)) as archive:
            root=ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            self.assertEqual(root.find(".//{*}c[@r='D4']/{*}v").text,"120.000")
        mapping[0]["columns"]["opening"]="A"
        with self.assertRaises(stock.StockError):templates.validate(encoded,mapping)

    def test_export_keeps_formula_like_names_literal_and_xml_valid(self):
        report=self.report()
        report["name"]="Report\x00name"
        report["rows"][0]["name"]="=1+1\x01"
        with ZipFile(BytesIO(reports.workbook(report))) as archive:
            root=ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            self.assertEqual(root.find(".//{*}c[@r='A4']/{*}is/{*}t").text,"=1+1\ufffd")
            self.assertIsNone(root.find(".//{*}f"))

    def test_daily_total_uses_revisions_and_preserves_history(self):
        self.movement("opening","10");self.movement("receive","2");self.movement("receive","3")
        entries=[row for row in stock.get_stock(self.path,self.product["id"])["entries"] if row["kind"]=="receive"]
        change=dict(id=str(uuid4()),kind="receive",effective_date="2026-11-01",quantity="1",notes="Reviewed total",expected_entries={row["id"]:row["revision"] for row in entries})
        workflows.set_daily_total(self.path,self.product["id"],change)
        self.assertEqual(stock.get_stock(self.path,self.product["id"])["balance"],"11.000")
        self.assertEqual(len(stock.get_stock(self.path,self.product["id"])["entries"]),3)
        with self.assertRaises(stock.StockError):workflows.set_daily_total(self.path,self.product["id"],{**change,"id":str(uuid4())})

    def sync(self,operations,permissions=None):
        app=create_app(self.path)
        endpoint=next(route.endpoint for route in app.routes if getattr(route,"path",None)=="/api/sync")
        request=Request({"type":"http","headers":[]});request.state.user={"id":"local-owner","permissions":permissions or ["read","receive","issue","correct"]}
        return endpoint({"operations":operations},request)

    def test_sync_holds_conflicts_and_continues_independent_entries(self):
        self.movement("opening","10")
        other=database.insert_product(self.path,dict(id=str(uuid4()),name="Other",unit="KG",category_id="dry-foods",created_at="now"))
        self.movement("opening","0",other["id"])
        bad=dict(id=str(uuid4()),product_id=self.product["id"],kind="issue",quantity="11",effective_date="2026-11-01",notes="")
        safe={**bad,"id":str(uuid4()),"product_id":other["id"],"kind":"receive","quantity":"2"}
        dependent={**bad,"id":str(uuid4()),"quantity":"1","depends_on":[bad["id"]]}
        result=self.sync([bad,safe,dependent])
        self.assertEqual([row["status"] for row in result],["held","saved","waiting"])
        self.movement("receive","100")
        self.assertEqual(self.sync([bad])[0]["status"],"held")
        reviewed={**bad,"id":str(uuid4()),"quantity":"5","resolves_id":bad["id"]}
        self.assertEqual(self.sync([reviewed])[0]["status"],"saved")
        self.assertEqual(self.sync([bad])[0]["status"],"resolved")
        self.assertEqual(self.sync([dependent])[0]["status"],"saved")

    def test_revoked_sync_permission_is_held_without_stock_change(self):
        self.movement("opening","10")
        operation=dict(id=str(uuid4()),product_id=self.product["id"],kind="issue",quantity="1",effective_date="2026-11-01",notes="")
        self.assertEqual(self.sync([operation],["read"])[0]["status"],"held")
        self.assertEqual(stock.get_stock(self.path,self.product["id"])["balance"],"10.000")

    def test_two_reviewers_only_post_one_replacement(self):
        self.movement("opening","10")
        bad=dict(id=str(uuid4()),product_id=self.product["id"],kind="issue",quantity="11",effective_date="2026-11-01",notes="")
        self.assertEqual(self.sync([bad])[0]["status"],"held")
        reviews=[{**bad,"id":str(uuid4()),"quantity":"2","resolves_id":bad["id"]} for _ in range(2)]
        with ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(lambda value:self.sync([value])[0]["status"],reviews))
        self.assertCountEqual(results,["saved","resolved"])
        self.assertEqual(stock.get_stock(self.path,self.product["id"])["balance"],"8.000")
        with database.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sync_holds WHERE resolved=0").fetchone()[0],0)

    def test_malformed_sync_envelope_rejects_whole_batch_before_writes(self):
        self.movement("opening","10")
        valid=dict(id=str(uuid4()),product_id=self.product["id"],kind="receive",quantity="2",effective_date="2026-11-01",notes="")
        with self.assertRaises(HTTPException) as failure:
            self.sync([valid,{**valid,"id":[]}])
        self.assertEqual(failure.exception.status_code,422)
        self.assertEqual(stock.get_stock(self.path,self.product["id"])["balance"],"10.000")

    def test_successful_review_resolves_failed_review_ancestry(self):
        self.movement("opening","10")
        bad=dict(id=str(uuid4()),product_id=self.product["id"],kind="issue",quantity="11",effective_date="2026-11-01",notes="")
        self.sync([bad])
        review={**bad,"id":str(uuid4()),"quantity":"12","resolves_id":bad["id"]}
        self.assertEqual(self.sync([review])[0]["status"],"held")
        final={**review,"id":str(uuid4()),"quantity":"3","resolves_id":review["id"]}
        self.assertEqual(self.sync([final])[0]["status"],"saved")
        self.assertEqual(self.sync([bad,review])[0]["status"],"resolved")
        self.assertEqual(stock.get_stock(self.path,self.product["id"])["balance"],"7.000")

    def test_qr_format_and_error_correction_syndromes(self):
        text="https://192.168.1.10:8443";values=connection_qr.codewords(text)
        self.assertEqual(len(values),70)
        # Independent polynomial check: all 15 Reed–Solomon roots must vanish.
        root=1
        for _ in range(15):
            remainder=0
            for value in values:remainder=connection_qr.multiply(remainder,root)^value
            self.assertEqual(remainder,0);root=connection_qr.multiply(root,2)
        grid=connection_qr.matrix(text)
        self.assertEqual([int(value) for value in grid[0][:8]],[1,1,1,1,1,1,1,0])
        format_bits=sum(int(grid[index][8])<<index for index in range(6))
        format_bits|=int(grid[7][8])<<6|int(grid[8][8])<<7|int(grid[8][7])<<8
        for index in range(9,15):format_bits|=int(grid[8][14-index])<<index
        self.assertEqual(format_bits,0x77C4)
        ET.fromstring(connection_qr.svg(text))
        with self.assertRaises(ValueError):connection_qr.matrix("x"*54)


if __name__=="__main__":unittest.main()
