"""Workflow regression tests: transactional safety, history, accounts and exports.

Use real disposable SQLite files. HTTP access policy is exercised with an
in-process Request/call-next stub, avoiding this runner's blocked socket pair.
"""

from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4
from xml.etree import ElementTree
from zipfile import ZipFile

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from app import accounts, audit, backups, database, reports, stock, workflows
from app.main import create_app


class WorkflowTests(unittest.TestCase):
    """Assert useful results rather than implementation-shaped SQL expectations."""

    def setUp(self):
        self.folder=TemporaryDirectory(dir=Path.cwd())
        self.path=Path(self.folder.name)/"inventory.sqlite3"
        self.clock=patch.object(stock,"today",return_value="2026-11-02");self.clock.start()
        # Keep test fixtures fast. Production still uses 600,000 PBKDF2 rounds.
        self.hash_rounds=patch.object(accounts,"ITERATIONS",1000);self.hash_rounds.start()
        database.initialize(self.path)
        self.product=database.insert_product(self.path,dict(id=str(uuid4()),name="Rice",unit="KG",category_id="dry-foods",precision=3,created_at="2026-11-02"))

    def tearDown(self):
        self.clock.stop();self.hash_rounds.stop();self.folder.cleanup()

    def entry(self,kind,quantity,day="2026-11-01"):
        return dict(id=str(uuid4()),kind=kind,quantity=quantity,effective_date=day,notes="")

    def opening(self,quantity="100"):
        return stock.save_entry(self.path,self.product["id"],self.entry("opening",quantity))

    def change(self,row,quantity,**extra):
        return dict(id=str(uuid4()),expected_revision=row["revision"],effective_date=row["effective_date"],quantity=quantity,notes="Count correction",voided=False,**extra)

    def test_only_administrator_can_permanently_delete_stock(self):
        self.opening()
        app = create_app(self.path)
        delete = next(route.endpoint for route in app.routes
                      if route.path == "/api/catalog/{identity}" and "DELETE" in route.methods)
        request = Request({"type": "http"})
        identity = UUID(self.product["id"])
        request.state.user = {"admin": False}
        with self.assertRaises(HTTPException) as denied:
            delete(identity, request, permanent=True)
        self.assertEqual(denied.exception.status_code, 403)
        with self.assertRaises(stock.StockError):
            delete(identity, request)
        request.state.user = {"admin": True}
        draft_id = str(uuid4())
        with database.connect(self.path) as connection:
            connection.execute(
                "INSERT INTO drafts VALUES (?, ?, ?, 1, 0, ?)",
                (draft_id, "test", json.dumps([{"product_id": str(identity)}]), "2026-11-02"),
            )
        with self.assertRaisesRegex(stock.StockError, "open draft"):
            delete(identity, request, permanent=True)
        with database.connect(self.path) as connection:
            connection.execute("UPDATE drafts SET finalized=1 WHERE id=?", (draft_id,))
        self.assertEqual(delete(identity, request, permanent=True), {"deleted": True})
        with database.connect(self.path) as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM products WHERE id=?", (str(identity),)).fetchone())
            self.assertIsNone(connection.execute("SELECT 1 FROM stock_entries WHERE product_id=?", (str(identity),)).fetchone())
            event = connection.execute("SELECT 1 FROM audit_events WHERE action='product.permanently_deleted'").fetchone()
            self.assertIsNotNone(event)
            self.assertIsNotNone(connection.execute("SELECT 1 FROM drafts WHERE id=?", (draft_id,)).fetchone())

    def test_correction_preserves_before_after_and_detects_stale_revision(self):
        opening=self.opening()
        corrected=workflows.correct(self.path,opening["id"],self.change(opening,"90"))
        self.assertEqual(corrected["revision"],2)
        self.assertEqual(stock.get_stock(self.path,self.product["id"])["balance"],"90.000")
        with self.assertRaises(stock.StockError):workflows.correct(self.path,opening["id"],self.change(opening,"80"))
        with database.connect(self.path) as connection:
            event=connection.execute("SELECT * FROM audit_events WHERE action='stock.corrected'").fetchone()
            self.assertEqual(json.loads(event["before_json"])["quantity"],"100.000")
            self.assertEqual(json.loads(event["after_json"])["quantity"],"90.000")

    def test_correction_failure_rolls_back_audit_and_stock(self):
        opening=self.opening("10")
        stock.save_entry(self.path,self.product["id"],self.entry("issue","8"))
        with self.assertRaises(stock.StockError):workflows.correct(self.path,opening["id"],self.change(opening,"5"))
        self.assertEqual(stock.get_stock(self.path,self.product["id"])["balance"],"2.000")
        with database.connect(self.path) as connection:self.assertEqual(connection.execute("SELECT COUNT(*) FROM audit_events WHERE action='stock.corrected'").fetchone()[0],0)

    def test_void_receipt_revalidates_stock(self):
        self.opening("0")
        received=stock.save_entry(self.path,self.product["id"],self.entry("receive","10"))
        stock.save_entry(self.path,self.product["id"],self.entry("issue","5"))
        change=self.change(received,"10");change["voided"]=True
        with self.assertRaises(stock.StockError):workflows.correct(self.path,received["id"],change)

    def test_retries_return_original_result_after_correction(self):
        payload=self.entry("opening","10")
        original=stock.save_entry(self.path,self.product["id"],payload)
        change=self.change(original,"20")
        corrected=workflows.correct(self.path,original["id"],change)
        self.assertEqual(stock.save_entry(self.path,self.product["id"],payload),original)
        self.assertEqual(workflows.correct(self.path,original["id"],change),corrected)
        self.assertEqual(stock.get_stock(self.path,self.product["id"])["balance"],"20.000")

    def test_bulk_all_or_nothing_and_retry(self):
        self.opening("10")
        rows=[dict(product_id=self.product["id"],**self.entry("receive","2")),dict(product_id=self.product["id"],**self.entry("issue","15"))]
        with self.assertRaises(stock.StockError):workflows.bulk(self.path,str(uuid4()),rows)
        self.assertEqual(stock.get_stock(self.path,self.product["id"])["received"],"0.000")
        rows[1]["quantity"]="5"
        identity=str(uuid4());first=workflows.bulk(self.path,identity,rows)
        self.assertEqual(workflows.bulk(self.path,identity,rows),first)
        self.assertEqual(stock.get_stock(self.path,self.product["id"])["balance"],"7.000")

    def test_future_draft_never_auto_finalizes(self):
        self.opening()
        rows=[dict(product_id=self.product["id"],**self.entry("receive","2","2026-11-05"))]
        identity=str(uuid4());saved=workflows.save_draft(self.path,identity,rows,0)
        with self.assertRaises(stock.StockError):workflows.finalize_draft(self.path,identity)
        self.assertEqual(stock.get_stock(self.path,self.product["id"])["received"],"0.000")
        self.assertEqual(saved["revision"],1)
        with self.assertRaises(stock.StockError):workflows.save_draft(self.path,identity,rows,0)

    def test_finalizing_draft_twice_posts_once(self):
        self.opening()
        rows=[dict(product_id=self.product["id"],**self.entry("receive","2"))]
        identity=str(uuid4());workflows.save_draft(self.path,identity,rows,0)
        first=workflows.finalize_draft(self.path,identity)
        self.assertEqual(workflows.finalize_draft(self.path,identity),first)
        self.assertEqual(stock.get_stock(self.path,self.product["id"])["balance"],"102.000")

    def test_month_closing_blocks_writes(self):
        self.opening()
        with database.connect(self.path) as connection:connection.execute("INSERT INTO closed_months VALUES ('2026-11','now')")
        with self.assertRaises(stock.StockError):stock.save_entry(self.path,self.product["id"],self.entry("receive","1"))

    def test_accounts_no_default_password_and_revoked_sessions(self):
        self.assertFalse(accounts.configured(self.path))
        owner=accounts.create_user(self.path,"Owner","long test passphrase",first=True)
        with self.assertRaises(accounts.AccountError):accounts.create_user(self.path,"Other","long test passphrase",first=True)
        token,user=accounts.login(self.path,"OWNER","long test passphrase")
        self.assertTrue(user["admin"])
        self.assertNotIn("password_hash",user)
        self.assertEqual(accounts.session_user(self.path,token)["id"],owner["id"])
        accounts.logout(self.path,token);self.assertIsNone(accounts.session_user(self.path,token))
        with self.assertRaises(accounts.AccountError):accounts.update_user(self.path,owner["id"],[],False)

    def test_login_failures_are_committed_and_lockout_applies(self):
        accounts.create_user(self.path,"Owner","long test passphrase",first=True)
        for _ in range(5):
            with self.assertRaises(accounts.AccountError):accounts.login(self.path,"owner","incorrect")
        with self.assertRaisesRegex(accounts.AccountError,"Too many"):accounts.login(self.path,"owner","long test passphrase")

    def test_permissions_are_read_fresh_from_account(self):
        employee=accounts.create_user(self.path,"Staff","long test passphrase",["read","receive"])
        token,_=accounts.login(self.path,"staff","long test passphrase")
        accounts.update_user(self.path,employee["id"],["read"],True)
        self.assertNotIn("receive",accounts.session_user(self.path,token)["permissions"])

    def policy(self,method,route,token=None,body=None,origin=None):
        """Drive only immediate in-memory awaits, without creating a socket/event loop."""
        app=create_app(self.path)
        dispatch=next(item.kwargs["dispatch"] for item in app.user_middleware if item.kwargs.get("dispatch",None) and item.kwargs["dispatch"].__name__=="access_control")
        headers=[]
        if token:headers.append((b"cookie",f"inventory_session={token}".encode()))
        if origin:headers.append((b"origin",origin.encode()))
        async def receive():return {"type":"http.request","body":json.dumps(body or {}).encode(),"more_body":False}
        request=Request({"type":"http","method":method,"path":route,"root_path":"","scheme":"http","headers":headers,"server":("127.0.0.1",8000),"query_string":b""},receive)
        async def next_handler(request):return JSONResponse({"allowed":True})
        coroutine=dispatch(request,next_handler)
        try:coroutine.send(None)
        except StopIteration as result:return result.value
        finally:coroutine.close()
        self.fail("Policy unexpectedly required external async I/O")

    def test_access_policy_blocks_anonymous_and_unauthorized_writes(self):
        self.assertEqual(self.policy("GET","/api/products").status_code,401)
        accounts.create_user(self.path,"Staff","long test passphrase",["read"])
        token,_=accounts.login(self.path,"staff","long test passphrase")
        self.assertEqual(self.policy("GET","/api/products",token).status_code,200)
        self.assertEqual(self.policy("POST",f'/api/products/{self.product["id"]}/stock',token,{"kind":"receive"}).status_code,403)
        self.assertEqual(self.policy("POST","/api/setup",origin="https://other.example").status_code,403)

    def test_report_periods_snapshots_and_workbook_xml(self):
        self.opening("100");stock.save_entry(self.path,self.product["id"],self.entry("receive","20"));stock.save_entry(self.path,self.product["id"],self.entry("issue","15"))
        report=reports.create(self.path,"2026-11-01","2026-11-02")
        self.assertEqual(report["rows"][0]["closing"],"105.000")
        self.assertEqual(reports.period("friday","2026-11-13"),("2026-11-01","2026-11-13"))
        with self.assertRaises(stock.StockError):reports.period("friday","2026-11-02")
        with ZipFile(BytesIO(reports.workbook(report))) as archive:
            for name in archive.namelist():ElementTree.fromstring(archive.read(name))
            self.assertIn(b"105.000",archive.read("xl/worksheets/sheet1.xml"))
        stock.save_entry(self.path,self.product["id"],self.entry("receive","1"))
        self.assertEqual(report["rows"][0]["closing"],"105.000")

    def test_verified_backup_and_protected_restore(self):
        self.opening("10");saved=backups.create(self.path)
        stock.save_entry(self.path,self.product["id"],self.entry("receive","1"))
        protected=backups.restore_offline(self.path,self.path.parent/"backups"/saved["name"])
        self.assertTrue(protected["name"].startswith("protected-pre-restore"))
        self.assertEqual(stock.get_stock(self.path,self.product["id"])["balance"],"10.000")
        blank=self.path.parent/"empty.sqlite3";blank.touch()
        with self.assertRaises(ValueError):backups.restore_offline(self.path,blank)


if __name__=="__main__":unittest.main()
