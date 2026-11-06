"""Optional complete ASGI/HTTP check; uses httpx and a disposable test database.

Run separately from the socket-free suite because some Windows sandboxes do
not permit the event-loop handles needed by asyncio/AnyIO.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_excel_feed import ExcelFeedTests
import httpx


async def check():
    fixture = ExcelFeedTests()
    fixture.setUp()
    try:
        fixture.entry("opening", "0", "2026-11-01")
        fixture.entry("receive", "2.5", "2026-11-02")
        root = f"/api/excel-feed/{fixture.manifest['id']}/odata/"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fixture.app), base_url="https://inventory.example") as client:
            denied = await client.get(root)
            assert denied.status_code == 401 and denied.headers["www-authenticate"].startswith("Basic")
            auth = ("excel", fixture.result["password"])
            service = await client.get(root, auth=auth)
            assert service.status_code == 200 and service.json()["value"][0]["url"] == "Daily"
            metadata = await client.get(root + "$metadata", auth=auth)
            assert metadata.status_code == 200 and "Edm.Decimal" in metadata.text
            values = await client.get(root + "Daily", auth=auth)
            assert values.status_code == 200 and values.json()["value"][0]["R02"] == "2.500"
            assert values.headers["cache-control"] == "no-store"
            assert (await client.post(root + "Daily", auth=auth, json={})).status_code == 405
            assert (await client.get("/api/excel/connections", auth=auth)).status_code == 401
            signed_in = await client.post("/api/session", json={"username":"report-user", "password":"long test passphrase"})
            assert signed_in.status_code == 200
            assert (await client.get("/api/excel/connections")).status_code == 200
            assert (await client.get(root)).status_code == 401, "Cookies must not authorize Excel refresh"
            revoked = await client.post(f"/api/excel/connections/{fixture.manifest['id']}/revoke", json={})
            assert revoked.status_code == 200
            assert (await client.get(root + "Daily", auth=auth)).status_code == 401
        print("PASS: complete HTTP middleware, Basic challenge, OData discovery/metadata/daily JSON, read-only scope, cookie isolation and revocation.")
    finally:
        fixture.tearDown()


if __name__ == "__main__":
    asyncio.run(check())
