"""Monthly stock sheets use app history and the user's receipts-inclusive opening.

No source workbook or live inventory is read by these regression tests.
"""
from datetime import date
from decimal import Decimal
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from app import database, monthly_report, reports, stock
from app.extended_api import ReportInput
from app.main import create_app


class MonthlyReportTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory(dir=Path.cwd())
        self.path = Path(self.folder.name) / "inventory.sqlite3"
        database.initialize(self.path)
        self.product = database.insert_product(self.path, dict(id=str(uuid4()), name="Rice", unit="KG",
                                                category_id="dry-foods", precision=3, created_at="2026-10-01"))
        self.clock = patch.object(stock,"today",return_value="2026-12-05")
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        self.folder.cleanup()

    def entry(self, kind, quantity, day):
        return stock.save_entry(self.path,self.product["id"],dict(id=str(uuid4()),kind=kind,
                                quantity=quantity,effective_date=day,notes=""))

    def make(self):
        app = create_app(self.path)
        endpoint = next(route.endpoint for route in app.routes if getattr(route,"path",None)=="/api/reports" and "POST" in route.methods)
        return endpoint(ReportInput(start=date(2026,11,1),end=date(2026,11,15),kind="monthly"))

    def test_previous_closing_plus_current_receipts_without_double_counting(self):
        self.entry("opening","100.1","2026-10-01")
        self.entry("receive","20.2","2026-10-31")
        self.entry("issue","30.1","2026-10-31")  # Previous closing = 90.2.
        self.entry("receive","10.2","2026-11-01")
        self.entry("receive","5.3","2026-11-30")  # Whole selected month.
        self.entry("issue","3.4","2026-11-20")
        self.entry("receive","500","2026-12-01")  # Not this month's stock.
        report = self.make()
        row = report["rows"][0]
        self.assertEqual((report["from_date"],report["to_date"]),("2026-11-01","2026-11-30"))
        self.assertEqual(row["opening"],"90.200")
        self.assertEqual(row["received"],"15.500")
        self.assertEqual(row["stock_sheet_opening"],"105.700")
        self.assertEqual(row["issued"],"3.400")
        self.assertEqual(row["closing"],"102.300")
        self.assertEqual(Decimal(row["stock_sheet_opening"])-Decimal(row["issued"]),Decimal(row["closing"]))

    def test_new_products_and_empty_reports(self):
        self.entry("opening","5.5","2026-11-12")
        self.entry("receive","1.2","2026-11-13")
        self.assertEqual(self.make()["rows"][0]["stock_sheet_opening"],"6.700")
        blank=reports.create(self.path,"2026-09-01","2026-09-30",layout="monthly-stock-v1")
        self.assertEqual(blank["rows"],[])
        with ZipFile(BytesIO(reports.workbook(blank))) as archive:
            for name in archive.namelist():
                ET.fromstring(archive.read(name))

    def test_saved_company_and_figures_do_not_change_after_new_stock(self):
        self.entry("opening","10","2026-10-01")
        with database.connect(self.path) as connection:
            connection.execute("INSERT INTO settings VALUES (?,?)",("company_name",json.dumps("Test Store")))
        report=self.make()
        self.entry("receive","2","2026-11-15")
        with database.connect(self.path) as connection:
            saved=json.loads(connection.execute("SELECT payload_json FROM report_versions WHERE id=?",(report["id"],)).fetchone()[0])
        self.assertEqual(saved,report)
        self.assertEqual(saved["company_name"],"Test Store")
        self.assertEqual(saved["rows"][0]["stock_sheet_opening"],"10.000")
        self.assertEqual(self.make()["rows"][0]["stock_sheet_opening"],"12.000")

    def test_export_has_reference_columns_numeric_cells_and_no_received_column(self):
        self.entry("opening","10.1","2026-10-01")
        self.entry("receive","2.2","2026-11-01")
        self.entry("issue","1.3","2026-11-02")
        with database.connect(self.path) as connection:
            connection.execute("UPDATE products SET fields_json=? WHERE id=?",(json.dumps({"Packing":"1 x 5KG"}),self.product["id"]))
        with ZipFile(BytesIO(reports.workbook(self.make()))) as archive:
            for name in archive.namelist():
                ET.fromstring(archive.read(name))
            sheet=ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            headers=["".join(cell.itertext()) for cell in sheet.findall(".//{*}row[@r='5']/{*}c")]
            self.assertEqual(headers,["S.no","Item name","Packing","Unit","Open stock","Consumed","Available stock"])
            self.assertEqual(sheet.find(".//{*}c[@r='E6']/{*}v").text,"12.300")
            self.assertEqual(sheet.find(".//{*}c[@r='G6']/{*}v").text,"11.000")
            self.assertNotIn("t",sheet.find(".//{*}c[@r='E6']").attrib)
            self.assertEqual(sheet.find(".//{*}c[@r='C6']/{*}is/{*}t").text,"1 x 5KG")
            self.assertEqual(sheet.find(".//{*}pane").get("topLeftCell"),"A6")

    def test_cutlery_fields_are_literal_and_legacy_exports_keep_their_format(self):
        self.entry("opening","0","2026-10-01")
        with database.connect(self.path) as connection:
            connection.execute("UPDATE products SET name=?,category_id=?,fields_json=? WHERE id=?",
                               ("=1+1","cutlery-cookeries",json.dumps({"Specification":"<steel> & safe"}),self.product["id"]))
        report=reports.create(self.path,"2026-11-01","2026-11-30",exclude_zero=False,layout="monthly-stock-v1")
        with ZipFile(BytesIO(reports.workbook(report))) as archive:
            sheet=ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            self.assertEqual(sheet.find(".//{*}c[@r='C6']/{*}is/{*}t").text,"=1+1")
            self.assertEqual(sheet.find(".//{*}c[@r='D6']/{*}is/{*}t").text,"<steel> & safe")
            self.assertIsNone(sheet.find(".//{*}f"))
        legacy=reports.create(self.path,"2026-11-01","2026-11-30",exclude_zero=False)
        self.assertNotIn("layout",legacy)
        with ZipFile(BytesIO(reports.workbook(legacy))) as archive:
            sheet=ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            self.assertEqual(sheet.find(".//{*}c[@r='E3']/{*}is/{*}t").text,"Received")


if __name__ == "__main__":
    unittest.main()
