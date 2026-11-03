"""Release checks that do not start a server or touch the real inventory."""

from html.parser import HTMLParser
import json
from pathlib import Path
import re
import ssl
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import configure_https
from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]


class Markup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids=[]
        self.targets=[]

    def handle_starttag(self, tag, attributes):
        values=dict(attributes)
        if "id" in values:self.ids.append(values["id"])
        if tag=="label" and "for" in values:self.targets.append(values["for"])
        self.targets.extend(values.get("aria-labelledby","").split())


class ReleaseTests(unittest.TestCase):
    def test_static_controls_have_unique_ids_and_valid_label_targets(self):
        markup=Markup()
        markup.feed((ROOT/"app/static/index.html").read_text(encoding="utf-8"))
        self.assertEqual(len(markup.ids),len(set(markup.ids)),"Duplicate IDs confuse labels and scripting")
        self.assertFalse(set(markup.targets)-set(markup.ids),"A label points to a missing control")
        # Literal ID selectors should resolve. Dynamic controls use templates
        # and therefore are not matched by this intentionally narrow check.
        missing=set()
        for script in (ROOT/"app/static").glob("*.js"):
            selectors=re.findall(r'querySelector\(["\']#([\w-]+)["\']\)',script.read_text(encoding="utf-8"))
            missing.update(set(selectors)-set(markup.ids))
        self.assertFalse(missing,f"Missing scripted controls: {missing}")

    def test_api_schema_contains_workspace_routes(self):
        # Building documentation exercises all request-model definitions but
        # never runs lifespan/migrations or opens a network socket.
        schema=create_app(ROOT/"unused-test.sqlite3").openapi()
        for route in ("/api/sync","/api/drafts","/api/reports","/api/catalog","/api/session"):
            self.assertIn(route,schema["paths"])

    def test_https_certificate_generation_does_not_enable_network_or_replace_ca(self):
        try:configure_https.openssl_path()
        except RuntimeError:self.skipTest("Optional installed OpenSSL is unavailable")
        with TemporaryDirectory(dir=ROOT) as directory, patch.object(configure_https,"ROOT",Path(directory)):
            config=configure_https.configure("192.168.1.10")
            folder=Path(directory)/"data"
            self.assertFalse(config["enabled"])
            ca=(folder/"tls/ca.crt").read_bytes()
            context=ssl.create_default_context(cafile=str(folder/"tls/ca.crt"))
            context.load_cert_chain(folder/"tls/server.crt",folder/"tls/server.key")
            configure_https.configure("192.168.1.11")
            self.assertEqual(ca,(folder/"tls/ca.crt").read_bytes())
            self.assertFalse(json.loads((folder/"network.json").read_text())["enabled"])


if __name__=="__main__":unittest.main()
