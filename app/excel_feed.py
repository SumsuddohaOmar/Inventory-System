"""Revocable Excel connections exposing ONLY daily received/issued quantities.

The workbook's opening stock and calculations remain in Excel. Browser sessions
manage connections; a separate random password authenticates read-only OData
requests. That password is stored only as a hash and cannot call write APIs.
Profile/mapping records use the existing transactional settings store, avoiding
an invasive database migration on the pilot. No product or ledger row is edited.
"""
import base64
import calendar
from datetime import date, datetime, timezone
import hashlib
import hmac
import json
import re
import secrets
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response

from app import accounts, audit, database, stock

PREFIX = "excel-profile:"
COLUMNS = ["Key", "Status", "ProductId", "ProductName", "Unit", "RefreshedAt"] + [f"R{day:02d}" for day in range(1,32)] + [f"I{day:02d}" for day in range(1,32)]
QUANTITIES = set(COLUMNS[6:])
MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def normalize(value):
    return " ".join(str(value or "").casefold().split())


def months(start, count):
    year, month = map(int,start.split("-"))
    for offset in range(count):
        value = year*12 + month-1 + offset
        yield f"{value//12:04d}-{value%12+1:02d}"


def allowed(user):
    if not user or not {"read","reports"}.issubset(user["permissions"]):
        raise HTTPException(403,"Read and Reports permissions are required for an Excel connection.")


def load(connection, identity):
    row = connection.execute("SELECT value_json FROM settings WHERE key=?",(PREFIX+str(identity),)).fetchone()
    if not row: raise HTTPException(404,"Excel connection not found.")
    return json.loads(row[0])


def save(connection, profile):
    connection.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT (key) DO UPDATE SET value_json=excluded.value_json",
                       (PREFIX+profile["id"],json.dumps(profile)))


def manage(user, profile):
    allowed(user)
    if profile["owner"] != user["id"] and not user.get("admin"):
        raise HTTPException(403,"Only the connection owner or administrator can change it.")


def catalog(connection):
    return [dict(row) for row in connection.execute("SELECT id,name,unit,code,category_id,fields_json,precision FROM products")]


def candidate(item, products):
    """Never guess among duplicate names. Packing/specification disambiguate.

    Imported app names sometimes include '(packing)'. Accept that suffix only
    when it exactly matches the workbook's packing; never fuzzy-match names.
    Existing confirmed IDs remain stable if a product is subsequently renamed.
    """
    pool = [p for p in products if p["category_id"] == item["category_id"]]
    if item.get("code"):
        coded = [p for p in pool if normalize(p["code"]) == normalize(item["code"])]
        if coded: pool = coded
        else: pool = [p for p in pool if normalize(p["name"]) == normalize(item["name"])]
    else:
        names = {normalize(item["name"])}
        if item.get("packing"): names.add(normalize(item["name"]+" ("+item["packing"]+")"))
        pool = [p for p in pool if normalize(p["name"]) in names]
    for field in ("unit","packing","specification"):
        expected = normalize(item.get(field))
        if not expected: continue
        def actual(product):
            if field == "unit": return normalize(product["unit"])
            return next((normalize(value) for key,value in json.loads(product["fields_json"]).items() if normalize(key)==field),"")
        exact = [p for p in pool if actual(p)==expected]
        # A conflicting nonempty unit/description must not link automatically.
        pool = exact if exact else [p for p in pool if not actual(p)]
    if len(pool)==1: return pool[0]["id"],"Linked"
    return None,"Ambiguous" if pool else "Not linked"


def public(profile, include_items=False):
    result = {key:profile[key] for key in ("id","label","start_month","months","active","created_at")}
    result["linked"] = sum(bool(item.get("product_id")) for item in profile["items"])
    result["total"] = len(profile["items"])
    if include_items: result["items"] = profile["items"]
    return result


def match_unlinked(items,products):
    """One app product may fill only one workbook product row per profile."""
    proposed = {}
    used = {item["product_id"] for item in items if item.get("product_id")}
    for item in items:
        if item.get("product_id"): continue
        identity,status = candidate(item,products)
        if identity in used:identity,status=None,"Ambiguous"
        item.update(product_id=identity,status=status)
        if identity:proposed.setdefault(identity,[]).append(item)
    for matches in proposed.values():
        if len(matches)>1:
            for item in matches:item.update(product_id=None,status="Ambiguous")


def validate_manifest(values):
    try:
        identity = str(UUID(values["id"]))
        start = values["start_month"]
        count = values["months"]
        items = values["items"]
        label = values["label"]
    except (KeyError,TypeError,ValueError): raise HTTPException(422,"Choose the prepared Excel connection setup JSON file.")
    if not isinstance(start,str) or not MONTH.fullmatch(start) or not 2000 <= int(start[:4]) <= 2098 or type(count) is not int or not 1 <= count <= 24:
        raise HTTPException(422,"Use a valid starting month and 1–24 months.")
    if not isinstance(label,str) or not 1 <= len(label) <= 120 or not isinstance(items,list) or not 1 <= len(items) <= 2000:
        raise HTTPException(422,"The connection needs a short label and 1–2000 workbook product rows.")
    cleaned, keys = [], set()
    for row in items:
        if not isinstance(row,dict): raise HTTPException(422,"Each workbook product must be an object.")
        item = {key:row.get(key,"") for key in ("key","name","category_id","unit","packing","specification","code")}
        if any(not isinstance(value,str) or len(value)>1000 for value in item.values()) or not item["name"] or not re.fullmatch(r"[a-z0-9-]+:\d+",item["key"]) or item["key"] in keys:
            raise HTTPException(422,"Check workbook row keys and product labels; each row key must be unique.")
        keys.add(item["key"])
        cleaned.append(item)
    return dict(id=identity,label=label,start_month=start,months=count,items=cleaned)


def issue_password(profile):
    password = secrets.token_urlsafe(32)
    profile["password_hash"] = hashlib.sha256(password.encode()).hexdigest()
    profile["active"] = True
    return password


def authenticate(connection, request, identity):
    """Only this dedicated endpoint accepts these credentials. Never a URL key."""
    denied = HTTPException(401,"Use the Excel connection username and password.",headers={"WWW-Authenticate":'Basic realm="Inventory Excel", charset="UTF-8"'})
    authorization = request.headers.get("authorization","")
    try:
        scheme, value = authorization.split(" ",1)
        if scheme.casefold() != "basic": raise ValueError()
        username, password = base64.b64decode(value,validate=True).decode().split(":",1)
    except (ValueError,UnicodeError): raise denied
    try: profile = load(connection,identity)
    except HTTPException: raise denied
    digest = hashlib.sha256(password.encode()).hexdigest()
    if username != "excel" or not profile["active"] or not hmac.compare_digest(digest,profile["password_hash"]): raise denied
    row = connection.execute("SELECT * FROM users WHERE id=? AND active=1",(profile["owner"],)).fetchone()
    if not row: raise denied
    allowed(accounts.public_user(row))  # Current rights, not cached creation rights.
    return profile


def feed_rows(connection, profile):
    """Aggregate exact stored minor units; exclude openings and voided entries."""
    periods = list(months(profile["start_month"],profile["months"]))
    after = list(months(periods[-1],2))[1]+"-01"
    totals = {(row["product_id"],row["effective_date"],row["kind"]):row["amount"] for row in connection.execute(
        "SELECT product_id,effective_date,kind,SUM(quantity_minor) AS amount FROM stock_entries WHERE voided=0 AND kind IN ('receive','issue') AND effective_date>=? AND effective_date<? GROUP BY product_id,effective_date,kind",
        (periods[0]+"-01",after))}
    products = {p["id"]:p for p in catalog(connection)}
    refreshed = datetime.now(timezone.utc).isoformat()
    rows = []
    for month in periods:
        days = calendar.monthrange(*map(int,month.split("-")))[1]
        for item in profile["items"]:
            product = products.get(item.get("product_id"))
            status = "Linked" if product else "Product removed" if item.get("product_id") else item.get("status","Not linked")
            row = dict(Key=item["key"]+":"+month,Status=status,ProductId=product["id"] if product else "",
                       ProductName=product["name"] if product else item["name"],Unit=product["unit"] if product else item.get("unit",""),RefreshedAt=refreshed)
            for prefix,kind in (("R","receive"),("I","issue")):
                for day in range(1,32):
                    amount = totals.get((product["id"],f"{month}-{day:02d}",kind),0) if product else 0
                    row[f"{prefix}{day:02d}"] = stock.display(amount,product["precision"]) if product and day <= days else None
            rows.append(row)
    return rows


def metadata():
    properties = ''.join(f'<Property Name="{name}" Type="Edm.Decimal" Precision="28" Scale="6" Nullable="true"/>' if name in QUANTITIES else f'<Property Name="{name}" Type="Edm.String" Nullable="false"/>' for name in COLUMNS)
    return ('<?xml version="1.0" encoding="utf-8"?><edmx:Edmx Version="4.0" xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx"><edmx:DataServices><Schema Namespace="Inventory" xmlns="http://docs.oasis-open.org/odata/ns/edm"><EntityType Name="DailyRow"><Key><PropertyRef Name="Key"/></Key>'+properties+'</EntityType><EntityContainer Name="Container"><EntitySet Name="Daily" EntityType="Inventory.DailyRow"/></EntityContainer></Schema></edmx:DataServices></edmx:Edmx>')


def register(application,path):
    @application.post("/api/excel/connections")
    def create(values:dict,request:Request):
        allowed(request.state.user)
        profile = validate_manifest(values)
        with database.connect(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM settings WHERE key=?",(PREFIX+profile["id"],)).fetchone():
                raise HTTPException(409,"This workbook connection already exists. Open it below to review links or reset its password.")
            products = catalog(connection)
            match_unlinked(profile["items"],products)
            profile.update(owner=request.state.user["id"],created_at=datetime.now(timezone.utc).isoformat())
            password = issue_password(profile)
            save(connection,profile)
            audit.record(connection,"excel.connection_created",profile["id"],after={"label":profile["label"],"rows":len(profile["items"])})
        return {**public(profile,True),"username":"excel","password":password}

    @application.get("/api/excel/connections")
    def list_connections(request:Request):
        allowed(request.state.user)
        with database.connect(path) as connection:
            profiles = [json.loads(row[0]) for row in connection.execute("SELECT value_json FROM settings WHERE key LIKE ?",(PREFIX+"%",))]
        return [public(p) for p in profiles if p["owner"]==request.state.user["id"] or request.state.user.get("admin")]

    @application.get("/api/excel/connections/{identity}")
    def detail(identity:UUID,request:Request):
        with database.connect(path) as connection: profile=load(connection,identity)
        manage(request.state.user,profile)
        return public(profile,True)

    @application.post("/api/excel/connections/{identity}/{action}")
    def change(identity:UUID,action:str,values:dict,request:Request):
        password = None
        with database.connect(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            profile=load(connection,identity)
            manage(request.state.user,profile)
            if action=="password": password=issue_password(profile)
            elif action=="revoke": profile["active"]=False
            elif action=="match":
                products=catalog(connection)
                match_unlinked(profile["items"],products)
            elif action=="link":
                item=next((item for item in profile["items"] if item["key"]==values.get("key")),None)
                product=connection.execute("SELECT id FROM products WHERE id=?",(str(values.get("product_id","")),)).fetchone()
                if not item or not product: raise HTTPException(422,"Choose a workbook row and an existing app product.")
                if any(other["key"]!=item["key"] and other.get("product_id")==product["id"] for other in profile["items"]):
                    raise HTTPException(409,"This app product is already linked to another workbook row. Choose the correct distinct product.")
                item.update(product_id=product["id"],status="Linked")
            elif action=="unlink":
                item=next((item for item in profile["items"] if item["key"]==values.get("key")),None)
                if not item: raise HTTPException(422,"Choose an existing workbook product row.")
                # Removing a mistaken mapping changes only this workbook's
                # source selection. The app product and its history are kept.
                item.update(product_id=None,status="Not linked")
            else: raise HTTPException(404,"Unknown Excel connection action.")
            save(connection,profile)
            audit.record(connection,"excel.connection_"+action,profile["id"],after={"label":profile["label"]})
        return {**public(profile,True),**({"username":"excel","password":password} if password else {})}

    @application.get("/api/excel-feed/{identity}/odata/")
    @application.get("/api/excel-feed/{identity}/odata/{resource}")
    def odata(identity:UUID,request:Request,resource:str=""):
        # The cloud feed never accepts cookie authentication. A workbook can
        # be shared without sharing the user's app password/session/DB URL.
        if not application.state.postgres_pilot:
            raise HTTPException(409,"Excel refresh is available on the cloud inventory.")
        root=(application.state.public_origin or str(request.base_url).rstrip("/"))+f"/api/excel-feed/{identity}/odata/"
        headers={"OData-Version":"4.0","Cache-Control":"no-store"}
        with database.connect(path) as connection:
            profile=authenticate(connection,request,identity)
            if resource=="$metadata":return Response(metadata(),media_type="application/xml",headers=headers)
            if not resource:return JSONResponse({"@odata.context":root+"$metadata","value":[{"name":"Daily","kind":"EntitySet","url":"Daily"}]},headers=headers)
            if resource!="Daily":raise HTTPException(404,"Only daily receiving and issuing are available.")
            unexpected=set(request.query_params)-{"$select","$top","$skip","$count","$format"}
            if unexpected:raise HTTPException(400,"Unsupported OData option. Use the prepared InventoryDaily query.")
            selected=request.query_params.get("$select",",").split(",") if "$select" in request.query_params else COLUMNS
            if not set(selected).issubset(COLUMNS):raise HTTPException(400,"Unknown daily feed column.")
            try:
                skip=int(request.query_params.get("$skip",0))
                limit=int(request.query_params.get("$top",50000))
                if skip<0 or not 0<=limit<=50000:raise ValueError()
            except ValueError:raise HTTPException(400,"Invalid OData row limit.")
            rows=feed_rows(connection,profile)
        # One response is one database snapshot, so concurrent stock changes
        # cannot mix old/new balances across separately fetched pages. The
        # prepared query buffers this complete result before populating Excel.
        output={"@odata.context":root+"$metadata#Daily","value":[{key:row[key] for key in selected} for row in rows[skip:skip+limit]]}
        if request.query_params.get("$count")=="true":output["@odata.count"]=len(rows)
        return JSONResponse(output,headers=headers,media_type="application/json;odata.metadata=minimal;IEEE754Compatible=true")
