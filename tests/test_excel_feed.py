"""Disposable-DB checks for scoped credentials, product links and daily totals."""
import base64
from datetime import date
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4
from xml.etree import ElementTree as ET

from fastapi import HTTPException, Request
from app import accounts, database, excel_feed, stock, workflows
from app.main import create_app


class ExcelFeedTests(unittest.TestCase):
    def setUp(self):
        self.folder=TemporaryDirectory(dir=Path.cwd())
        self.path=Path(self.folder.name)/"inventory.sqlite3"
        database.initialize(self.path)
        self.clock=patch.object(stock,"today",return_value="2027-12-01");self.clock.start()
        self.rounds=patch.object(accounts,"ITERATIONS",1000);self.rounds.start()
        self.user=accounts.create_user(self.path,"report-user","long test passphrase",["read","reports"])
        self.product=database.insert_product(self.path,dict(id=str(uuid4()),name="Rice",unit="KG",category_id="dry-foods",precision=3,created_at="now"))
        self.app=create_app(self.path)
        self.app.state.postgres_pilot=True
        self.app.state.public_origin="https://inventory.example"
        self.manifest=dict(id=str(uuid4()),label="Test workbook",start_month="2026-11",months=12,
                           items=[dict(key="dry-foods:7",name="Rice",category_id="dry-foods",unit="KG"),
                                  dict(key="dry-foods:8",name="Not created",category_id="dry-foods",unit="KG")])
        self.result=self.endpoint("/api/excel/connections","POST")(self.manifest,self.request())

    def tearDown(self):
        self.clock.stop();self.rounds.stop();self.folder.cleanup()

    def endpoint(self,path,method="GET"):
        return next(route.endpoint for route in self.app.routes if getattr(route,"path",None)==path and method in route.methods)

    def request(self,password=None,query=""):
        headers=[]
        if password is not None:headers=[(b"authorization",b"Basic "+base64.b64encode(("excel:"+password).encode()))]
        request=Request({"type":"http","method":"GET","scheme":"https","path":"/api/excel-feed/x/odata/","root_path":"","headers":headers,"query_string":query.encode(),"server":("inventory.example",443)})
        request.state.user=self.user
        return request

    def feed(self,resource="Daily",password=None,query=""):
        response=self.endpoint("/api/excel-feed/{identity}/odata/{resource}")(
            UUID(self.manifest["id"]),self.request(self.result["password"] if password is None else password,query),resource)
        return response if resource=="$metadata" else json.loads(response.body)

    def entry(self,kind,quantity,day):
        return stock.save_entry(self.path,self.product["id"],dict(id=str(uuid4()),kind=kind,quantity=quantity,effective_date=day,notes=""))

    def test_only_exact_daily_receiving_and_issuing_are_sent(self):
        self.entry("opening","100","2026-11-01")
        self.entry("receive","2.5","2026-11-02")
        self.entry("receive","1.25","2026-11-02")
        self.entry("issue","1.2","2026-11-02")
        void=self.entry("receive","9","2026-11-03")
        workflows.correct(self.path,void["id"],dict(id=str(uuid4()),expected_revision=1,effective_date="2026-11-03",quantity="9",notes="test",voided=True))
        row=self.feed()["value"][0]
        self.assertEqual(row["R02"],"3.750")
        self.assertEqual(row["I02"],"1.200")
        self.assertEqual(row["R03"],"0.000")
        self.assertEqual(row["R01"],"0.000")  # The opening is NOT a receipt.
        self.assertIsNone(row["R31"])  # November has no day 31.
        self.assertEqual(set(row),set(excel_feed.COLUMNS))
        self.assertFalse(any("opening" in key.lower() or "closing" in key.lower() for key in row))

    def test_corrections_update_next_refresh_without_duplicate_movements(self):
        self.entry("opening","100","2026-11-01")
        entry=self.entry("receive","2","2026-11-02")
        self.assertEqual(self.feed()["value"][0]["R02"],"2.000")
        workflows.correct(self.path,entry["id"],dict(id=str(uuid4()),expected_revision=1,effective_date="2026-11-02",quantity="3.5",notes="test",voided=False))
        self.assertEqual(self.feed()["value"][0]["R02"],"3.500")
        self.assertEqual(self.feed()["value"][0]["R02"],"3.500")

    def test_unlinked_rows_remain_null_and_linked_zero_is_explicit(self):
        rows=self.feed()["value"]
        self.assertEqual(len(rows),24)
        self.assertEqual(rows[0]["Status"],"Linked")
        self.assertEqual(rows[0]["R01"],"0.000")
        self.assertEqual(rows[1]["Status"],"Not linked")
        self.assertIsNone(rows[1]["R01"])
        self.assertEqual(len({row["Key"] for row in rows}),24)

    def test_matching_is_conservative_and_uses_packing_to_disambiguate(self):
        products=[dict(id="a",name="Water",category_id="dry-foods",unit="BTL",code="",fields_json=json.dumps({"Packing":"500ml"})),
                  dict(id="b",name="Water",category_id="dry-foods",unit="BTL",code="",fields_json=json.dumps({"Packing":"1L"}))]
        item=dict(name="Water",category_id="dry-foods",unit="BTL")
        self.assertEqual(excel_feed.candidate(item,products),(None,"Ambiguous"))
        self.assertEqual(excel_feed.candidate({**item,"packing":"1L"},products),("b","Linked"))
        self.assertEqual(excel_feed.candidate({**item,"unit":"CTN"},products),(None,"Not linked"))
        self.assertEqual(excel_feed.candidate({**item,"name":"Waterr"},products),(None,"Not linked"))

    def test_existing_link_survives_rename_and_does_not_move_to_replacement_product(self):
        with database.connect(self.path) as connection:
            connection.execute("UPDATE products SET name=? WHERE id=?",("New label",self.product["id"]))
        self.assertEqual(self.feed()["value"][0]["ProductId"],self.product["id"])
        self.assertEqual(self.feed()["value"][0]["ProductName"],"New label")
        with database.connect(self.path) as connection:
            connection.execute("DELETE FROM products WHERE id=?",(self.product["id"],))
        database.insert_product(self.path,dict(id=str(uuid4()),name="Rice",unit="KG",category_id="dry-foods",precision=3,created_at="now"))
        self.endpoint("/api/excel/connections/{identity}/{action}","POST")(UUID(self.manifest["id"]),"match",{},self.request())
        row=self.feed()["value"][0]
        self.assertEqual(row["Status"],"Product removed")
        self.assertIsNone(row["R01"])

    def test_password_hash_only_is_stored_and_password_does_not_appear_in_listing(self):
        with database.connect(self.path) as connection:
            saved=connection.execute("SELECT value_json FROM settings WHERE key=?",(excel_feed.PREFIX+self.manifest["id"],)).fetchone()[0]
        self.assertNotIn(self.result["password"],saved)
        listed=self.endpoint("/api/excel/connections")(self.request())
        self.assertNotIn("password",json.dumps(listed))
        for password in ("wrong","long test passphrase"):
            with self.assertRaises(HTTPException) as failed:self.feed(password=password)
            self.assertEqual(failed.exception.status_code,401)
        with self.assertRaises(HTTPException):
            self.endpoint("/api/excel-feed/{identity}/odata/{resource}")(UUID(self.manifest["id"]),self.request(),"Daily")

    def test_password_rotation_revocation_and_current_account_rights(self):
        change=self.endpoint("/api/excel/connections/{identity}/{action}","POST")
        rotated=change(UUID(self.manifest["id"]),"password",{},self.request())
        with self.assertRaises(HTTPException):self.feed()
        self.result["password"]=rotated["password"]
        self.feed()
        accounts.update_user(self.path,self.user["id"],["read"],True)
        with self.assertRaises(HTTPException) as denied:self.feed()
        self.assertEqual(denied.exception.status_code,403)
        accounts.update_user(self.path,self.user["id"],["read","reports"],True)
        change(UUID(self.manifest["id"]),"revoke",{},self.request())
        with self.assertRaises(HTTPException):self.feed()

    def test_other_employee_cannot_manage_connection_and_duplicate_setup_is_rejected(self):
        request=self.request()
        request.state.user={**self.user,"id":"other"}
        with self.assertRaises(HTTPException) as denied:
            self.endpoint("/api/excel/connections/{identity}")(UUID(self.manifest["id"]),request)
        self.assertEqual(denied.exception.status_code,403)
        with self.assertRaises(HTTPException) as duplicate:
            self.endpoint("/api/excel/connections","POST")(self.manifest,self.request())
        self.assertEqual(duplicate.exception.status_code,409)

    def test_manual_review_links_unmatched_product_without_editing_stock(self):
        before=stock.get_stock(self.path,self.product["id"])
        other=database.insert_product(self.path,dict(id=str(uuid4()),name="Not created",unit="KG",category_id="dry-foods",precision=3,created_at="now"))
        result=self.endpoint("/api/excel/connections/{identity}/{action}","POST")(
            UUID(self.manifest["id"]),"link",{"key":"dry-foods:8","product_id":other["id"]},self.request())
        self.assertEqual(result["linked"],2)
        self.assertEqual(self.feed()["value"][1]["Status"],"Linked")
        self.assertEqual(stock.get_stock(self.path,self.product["id"]),before)

    def test_two_workbook_rows_cannot_silently_duplicate_one_products_movements(self):
        with self.assertRaises(HTTPException) as conflict:
            self.endpoint("/api/excel/connections/{identity}/{action}","POST")(
                UUID(self.manifest["id"]),"link",{"key":"dry-foods:8","product_id":self.product["id"]},self.request())
        self.assertEqual(conflict.exception.status_code,409)
        items=[dict(key="dry-foods:7",name="Rice",category_id="dry-foods",unit="KG"),dict(key="dry-foods:8",name="Rice",category_id="dry-foods",unit="KG")]
        with database.connect(self.path) as connection:excel_feed.match_unlinked(items,excel_feed.catalog(connection))
        self.assertEqual([item["status"] for item in items],["Ambiguous","Ambiguous"])

    def test_mistaken_link_can_be_removed_without_deleting_stock(self):
        self.entry("opening","0","2026-11-01")
        self.entry("receive","2.5","2026-11-02")
        before=stock.get_stock(self.path,self.product["id"])
        result=self.endpoint("/api/excel/connections/{identity}/{action}","POST")(
            UUID(self.manifest["id"]),"unlink",{"key":"dry-foods:7"},self.request())
        self.assertEqual(result["linked"],0)
        self.assertIsNone(self.feed()["value"][0]["R02"])
        self.assertEqual(stock.get_stock(self.path,self.product["id"]),before)

    def test_service_metadata_column_order_and_supported_query_options(self):
        self.assertEqual(self.feed("")["value"][0]["url"],"Daily")
        ET.fromstring(self.feed("$metadata").body)
        rows=self.feed(query="%24select=Key,R01&%24top=2&%24count=true")
        self.assertEqual(len(rows["value"]),2)
        self.assertEqual(set(rows["value"][0]),{"Key","R01"})
        self.assertEqual(rows["@odata.count"],24)
        self.assertNotIn("@odata.nextLink",self.feed())
        with self.assertRaises(HTTPException):self.feed(query="%24filter=Key%20eq%20%27x%27")

    def test_manifest_rejects_duplicate_row_ids_and_local_feed_is_disabled(self):
        invalid={**self.manifest,"items":[self.manifest["items"][0]]*2}
        with self.assertRaises(HTTPException):excel_feed.validate_manifest(invalid)
        self.app.state.postgres_pilot=False
        with self.assertRaises(HTTPException) as denied:self.feed()
        self.assertEqual(denied.exception.status_code,409)

    def test_general_settings_do_not_expose_profile_password_hashes(self):
        settings=self.endpoint("/api/settings")()
        self.assertFalse(any(key.startswith(excel_feed.PREFIX) for key in settings))

    def policy(self,method,path,token=None,authorization=None):
        from fastapi.responses import JSONResponse
        dispatch=next(item.kwargs["dispatch"] for item in self.app.user_middleware if item.kwargs.get("dispatch") and item.kwargs["dispatch"].__name__=="access_control")
        headers=[]
        if token:headers.append((b"cookie",f"inventory_session={token}".encode()))
        if authorization:headers.append((b"authorization",authorization.encode()))
        async def receive():return {"type":"http.request","body":b"{}","more_body":False}
        request=Request({"type":"http","method":method,"path":path,"root_path":"","scheme":"https","headers":headers,"server":("inventory.example",443),"query_string":b""},receive)
        async def handler(request):return JSONResponse({"allowed":True})
        coroutine=dispatch(request,handler)
        try:coroutine.send(None)
        except StopIteration as result:return result.value.status_code
        finally:coroutine.close()
        self.fail("Unexpected external I/O in policy test")

    def test_feed_credentials_cannot_authorize_writes_or_connection_management(self):
        auth="Basic "+base64.b64encode(("excel:"+self.result["password"]).encode()).decode()
        self.assertEqual(self.policy("POST","/api/excel-feed/x/odata/Daily",authorization=auth),405)
        self.assertEqual(self.policy("POST","/api/products",authorization=auth),401)
        self.assertEqual(self.policy("POST","/api/excel/connections",authorization=auth),401)
        token,_=accounts.login(self.path,"report-user","long test passphrase")
        self.assertEqual(self.policy("GET","/api/excel/connections",token),200)
        accounts.update_user(self.path,self.user["id"],["read"],True)
        self.assertEqual(self.policy("GET","/api/excel/connections",token),403)


if __name__=="__main__":unittest.main()
