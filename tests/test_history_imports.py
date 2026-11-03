"""Real temporary-database checks for reviewed historical transaction imports.

The UI review is not trusted by these tests: changed permissions, duplicates,
invalid stock and competing retries must all remain safe at the server boundary.
"""
import base64
from datetime import date
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED

from fastapi import HTTPException, Request
from app import database, history_imports, reports, stock
from app.extended_api import HistoryApply
from app.main import create_app


class HistoryImportTests(unittest.TestCase):
    def setUp(self):
        self.folder=TemporaryDirectory(dir=Path.cwd())
        self.path=Path(self.folder.name)/"inventory.sqlite3"
        self.clock=patch.object(stock,"today",return_value="2026-11-02")
        self.clock.start()
        database.initialize(self.path)
        self.product=database.insert_product(self.path,dict(id=str(uuid4()),name="Rice",unit="KG",
            precision=3,category_id="dry-foods",created_at="2026-11-02"))
        self.movement("opening","100","2026-10-01")

    def tearDown(self):
        self.clock.stop()
        self.folder.cleanup()

    def movement(self,kind,quantity,day):
        return stock.save_entry(self.path,self.product["id"],dict(id=str(uuid4()),kind=kind,
            quantity=quantity,effective_date=day,notes=""))

    def source(self,text="Item,Date,Type,Quantity,Notes\nRice,2026-11-01,receive,20,delivery\nRice,2026-11-02,issue,15,kitchen\n"):
        return dict(filename="history.csv",data=base64.b64encode(text.encode()).decode(),date_format="iso")

    def reviewed(self,source=None):
        source=source or self.source()
        preview=history_imports.preview(self.path,source)
        rows=[dict(row=row["row"],id=row["id"],product_id=self.product["id"],
              kind=row["kind"],effective_date=row["effective_date"],quantity=row["quantity"],
              notes=row["notes"],action="import",acknowledge_duplicate=False) for row in preview["rows"]]
        return dict(id=str(uuid4()),filename=source["filename"],rows=rows)

    def test_preview_is_read_only_and_identifies_stable_source_rows(self):
        before=stock.get_stock(self.path,self.product["id"])
        first=history_imports.preview(self.path,self.source())
        second=history_imports.preview(self.path,self.source())
        self.assertEqual(first,second)
        self.assertEqual(first["rows"][0]["matches"][0]["id"],self.product["id"])
        self.assertEqual(before,stock.get_stock(self.path,self.product["id"]))
        self.assertFalse((self.path.parent/"backups").exists())

    def test_reviewed_apply_backup_audit_and_retry(self):
        values=self.reviewed()
        result=history_imports.apply(self.path,values)
        self.assertEqual(history_imports.apply(self.path,values),result)
        summary=stock.get_stock(self.path,self.product["id"])
        self.assertEqual(summary["balance"],"105.000")
        self.assertEqual(len(summary["entries"]),3)
        self.assertEqual(len(list((self.path.parent/"backups").glob("protected-pre-history-import-*.sqlite3"))),1)
        with database.connect(self.path) as connection:
            event=connection.execute("SELECT after_json FROM audit_events WHERE action='history.imported'").fetchone()
            self.assertEqual(json.loads(event[0])["source_rows"],[2,3])

    def test_same_file_with_new_batch_id_cannot_duplicate_rows(self):
        values=self.reviewed()
        history_imports.apply(self.path,values)
        preview=history_imports.preview(self.path,self.source())
        self.assertTrue(all(row["already_imported"] for row in preview["rows"]))
        history_imports.apply(self.path,{**values,"id":str(uuid4())})
        self.assertEqual(len(stock.get_stock(self.path,self.product["id"])["entries"]),3)

    def test_concurrent_imports_of_identical_file_post_once(self):
        first=self.reviewed()
        second={**first,"id":str(uuid4())}
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(lambda values:history_imports.apply(self.path,values),[first,second]))
        self.assertEqual(stock.get_stock(self.path,self.product["id"])["balance"],"105.000")

    def test_overspending_rolls_back_every_selected_row_and_audit(self):
        values=self.reviewed()
        values["rows"][1]["quantity"]="200"
        with self.assertRaises(stock.StockError):history_imports.apply(self.path,values)
        summary=stock.get_stock(self.path,self.product["id"])
        self.assertEqual(summary["balance"],"100.000")
        self.assertEqual(len(summary["entries"]),1)
        with database.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM audit_events WHERE action='history.imported'").fetchone()[0],0)
        self.assertTrue(list((self.path.parent/"backups").glob("protected-pre-history-import-*.sqlite3")))

    def test_existing_duplicate_requires_explicit_acknowledgement(self):
        self.movement("receive","20","2026-11-01")
        preview=history_imports.preview(self.path,self.source())
        self.assertTrue(preview["rows"][0]["possible_duplicates"])
        values=self.reviewed()
        with self.assertRaisesRegex(stock.StockError,"acknowledge"):history_imports.apply(self.path,values)
        values["rows"][0]["acknowledge_duplicate"]=True
        history_imports.apply(self.path,values)
        self.assertEqual(stock.get_stock(self.path,self.product["id"])["balance"],"125.000")

    def test_same_quantity_in_different_formats_still_checks_batch_duplicates(self):
        source=self.source("Item,Date,Type,Quantity\nRice,2026-11-01,receive,2.0\nRice,2026-11-01,receive,2.000\n")
        values=self.reviewed(source)
        with self.assertRaisesRegex(stock.StockError,"acknowledge"):history_imports.apply(self.path,values)
        self.assertEqual(stock.get_stock(self.path,self.product["id"])["balance"],"100.000")
        values["rows"][1]["acknowledge_duplicate"]=True
        history_imports.apply(self.path,values)
        self.assertEqual(stock.get_stock(self.path,self.product["id"])["balance"],"104.000")

    def test_custom_columns_dates_and_invalid_rows_stay_visible(self):
        source=self.source("Thing,When,Flow,Value\nRice,02/11/2026,Received,1\nUnknown,not a date,other,3\n")
        layout=history_imports.inspect(source)
        self.assertEqual(layout["headers"],["Thing","When","Flow","Value"])
        result=history_imports.preview(self.path,{**source,"date_format":"dmy",
            "mapping":dict(name=0,date=1,kind=2,quantity=3)})
        self.assertEqual(result["rows"][0]["effective_date"],"2026-11-02")
        self.assertEqual(result["rows"][0]["kind"],"receive")
        self.assertEqual(len(result["rows"]),2)
        self.assertEqual(len(result["rows"][1]["errors"]),2)

    def test_explicit_excel_calendars_and_ambiguous_dates(self):
        self.assertEqual(history_imports.parse_date("61","excel1900"),"1900-03-01")
        self.assertEqual(history_imports.parse_date("1","excel1900"),"1900-01-01")
        self.assertEqual(history_imports.parse_date("0","excel1904"),"1904-01-01")
        self.assertEqual(history_imports.parse_date("02/11/2026","dmy"),"2026-11-02")
        self.assertEqual(history_imports.parse_date("02/11/2026","mdy"),"2026-02-11")
        for value in ("60","1.5","NaN","-1"):
            with self.assertRaises(stock.StockError):history_imports.parse_date(value,"excel1900")

    def test_xlsx_serial_dates_custom_header_and_exact_quantities(self):
        # Create a tiny real XLSX around the standard report container, replacing
        # only its worksheet. The test has no optional spreadsheet dependency.
        container=reports.workbook(dict(name="Fixture",from_date="2026-11-01",to_date="2026-11-02",rows=[]))
        serial=(date(2026,11,1)-date(1899,12,30)).days
        def text(reference,value):return f'<c r="{reference}" t="inlineStr"><is><t>{value}</t></is></c>'
        sheet='<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        sheet+='<row r="3">'+''.join(text(f'{column}3',value) for column,value in zip("ABCD",("Item","Date","Type","Quantity")))+'</row>'
        sheet+='<row r="4">'+text("A4","Rice")+f'<c r="B4"><v>{serial}</v></c>'+text("C4","received")+'<c r="D4"><v>0.100</v></c></row></sheetData></worksheet>'
        output=BytesIO()
        with ZipFile(BytesIO(container)) as original,ZipFile(output,"w",ZIP_DEFLATED) as target:
            for part in original.infolist():target.writestr(part,sheet.encode() if part.filename=="xl/worksheets/sheet1.xml" else original.read(part.filename))
        source=dict(filename="history.xlsx",data=base64.b64encode(output.getvalue()).decode(),header_row=3,date_format="excel1900")
        preview=history_imports.preview(self.path,source)
        self.assertEqual(preview["rows"][0]["effective_date"],"2026-11-01")
        self.assertEqual(preview["rows"][0]["row"],4)
        history_imports.apply(self.path,self.reviewed(source))
        self.assertEqual(stock.get_stock(self.path,self.product["id"])["balance"],"100.100")

    def test_current_permissions_checked_before_backup_or_any_write(self):
        endpoint=next(route.endpoint for route in create_app(self.path).routes if getattr(route,"path",None)=="/api/history-import/apply")
        request=Request({"type":"http","headers":[]})
        request.state.user={"id":"local-owner","permissions":["read","products","receive"]}
        with self.assertRaises(HTTPException) as failure:endpoint(HistoryApply(**self.reviewed()),request)
        self.assertEqual(failure.exception.status_code,403)
        self.assertFalse((self.path.parent/"backups").exists())
        self.assertEqual(stock.get_stock(self.path,self.product["id"])["balance"],"100.000")

    def test_skipped_invalid_row_is_not_imported_and_future_row_is_rejected(self):
        values=self.reviewed()
        values["rows"][0]["effective_date"]="2026-11-03"
        with self.assertRaises(stock.StockError):history_imports.apply(self.path,values)
        values["rows"][0]["action"]="skip"
        history_imports.apply(self.path,values)
        self.assertEqual(stock.get_stock(self.path,self.product["id"])["balance"],"85.000")


if __name__=="__main__":unittest.main()
