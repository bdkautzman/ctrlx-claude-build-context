"""Web API, auth and node registry tests (no ctrlX needed)."""

import base64
import http.client
import json
import os
import tempfile
import time
import unittest

from app import auth, config, grouping
from app.datalayer_nodes import NodeRegistry, NodeSpec, NodeWriteError, build_node_specs
from app.datalayer_service import DataLayerService
from app.main import Services
from app.settings_rules import RULES
from app.settings_store import SettingsStore
from app.web_server import WebServer
from tests.helpers import FakeProc

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULTS_FILE = os.path.join(HERE, "..", "defaults", "settings.json")


def make_token(scopes, exp_offset=3600):
    def b64(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    return "{}.{}.sig".format(b64({"alg": "none"}), b64({"scope": scopes, "exp": time.time() + exp_offset}))


class AuthTest(unittest.TestCase):
    def test_scopes(self):
        headers = {"Authorization": "Bearer " + make_token([config.SCOPE_READ])}
        self.assertTrue(auth.can_read(auth.scopes_from_headers(headers)))
        self.assertFalse(auth.can_write(auth.scopes_from_headers(headers)))
        with self.assertRaises(auth.AuthError) as ctx:
            auth.require(headers, write=True)
        self.assertEqual(ctx.exception.status, 403)

    def test_admin_scope(self):
        headers = {"Authorization": "Bearer " + make_token([config.SCOPE_ADMIN])}
        self.assertTrue(auth.can_write(auth.require(headers, write=True)))

    def test_missing_expired_malformed(self):
        for headers in ({}, {"Authorization": "Bearer x.y"},
                        {"Authorization": "Bearer " + make_token([config.SCOPE_ADMIN], -10)}):
            with self.assertRaises(auth.AuthError) as ctx:
                auth.require(headers, write=False)
            self.assertEqual(ctx.exception.status, 401)


class NodeRegistryTest(unittest.TestCase):
    def setUp(self):
        self.reg = NodeRegistry("root", [
            NodeSpec("a/ro", "string", "x"),
            NodeSpec("a/f", "float64", 0, writable=True, minimum=-1, maximum=1),
            NodeSpec("a/u", "uint64", 0, writable=True),
            NodeSpec("a/b", "bool8", False, writable=True),
        ])

    def test_write_validation(self):
        self.assertEqual(self.reg.write("a/f", 0.5), 0.5)
        for name, value, reason in [("a/ro", "y", "readonly"), ("a/f", 2, "range"), ("a/f", "1", "type"),
                                    ("a/f", float("nan"), "invalid"), ("a/u", -1, "range"),
                                    ("a/b", 1, "type"), ("nope", 1, "unknown")]:
            with self.assertRaises(NodeWriteError) as ctx:
                self.reg.write(name, value)
            self.assertEqual(ctx.exception.reason, reason, (name, value))

    def test_on_write_can_reject(self):
        def reject_odd(value):
            if value % 2:
                raise NodeWriteError("invalid", "odd")
        reg = NodeRegistry("root", [NodeSpec("a/even", "uint64", 0, writable=True, on_write=reject_odd)])
        self.assertEqual(reg.write("a/even", 4), 4)
        with self.assertRaises(NodeWriteError):
            reg.write("a/even", 5)
        self.assertEqual(reg.get("a/even"), 4)  # a rejected value is not stored

    def test_template_specs_are_valid(self):
        with open(DEFAULTS_FILE, encoding="utf-8") as f:
            settings = json.load(f)
        reg = NodeRegistry(config.DATALAYER_ROOT, build_node_specs(settings, "1.0.0"))
        for name in reg.snapshot():
            self.assertRegex(name, r"^[a-z0-9-]+(/[a-z0-9-]+)*$", "use lowercase-dash node names")


class WebApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        active = os.path.join(cls.tmp.name, "activeConfiguration")
        os.makedirs(active)
        cls.store = SettingsStore(os.path.join(active, config.SNAP_NAME, "settings.json"),
                                  DEFAULTS_FILE, active, RULES)
        cls.store.load_from_disk(create_if_missing=True)
        cls.fake = FakeProc(os.path.join(cls.tmp.name, "proc"))
        cls.fake.add(100, "frame", "/system.slice/snap.alpha.control.service", "/snap/alpha/1/frame",
                     threads={101: {"comm": "worker"}}, pss=1500)
        os.makedirs(os.path.join(cls.tmp.name, "media", "USB1"))
        cls.services = Services(cls.store, proc_root=cls.fake.root, media_root=os.path.join(cls.tmp.name, "media"))
        cls.store.add_listener(cls.services.apply)
        now = time.time()
        for i in range(4):                      # four samples in the last minute
            cls.fake.update(100, utime=100 * i, anon=1000 + 10 * i)
            cls.fake.busy(100)
            cls.services.collector.sample(now - 40 + 10 * i)
        cls.dl = DataLayerService(cls.store.get(), "test", commands=cls.services.queries)
        cls.store.add_listener(lambda _s: cls.dl.on_settings_changed(cls.store.get_redacted()))
        cls._port = config.DEV_HTTP_PORT
        config.DEV_HTTP_PORT = 0  # let the OS pick a free port
        cls.web = WebServer(cls.store, cls.dl, cls.services)
        cls.web.start()
        cls.port = cls.web._server.server_address[1]
        cls.api = config.API_PREFIX

    @classmethod
    def tearDownClass(cls):
        cls.web.stop()
        config.DEV_HTTP_PORT = cls._port
        cls.tmp.cleanup()

    def req(self, method, path, body=None, scopes=(config.SCOPE_WRITE,), raw=False):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Authorization": "Bearer " + make_token(list(scopes))} if scopes is not None else {}
        data = json.dumps(body) if body is not None else None
        conn.request(method, path, body=data, headers=headers)
        res = conn.getresponse()
        payload = res.read()
        conn.close()
        if raw:
            return res.status, payload, res
        return res.status, (json.loads(payload) if payload and res.getheader("Content-Type", "").startswith("application/json") else payload)

    def test_static_index_without_token(self):
        status, body = self.req("GET", config.URL_PREFIX + "/", scopes=None)
        self.assertEqual(status, 200)
        self.assertIn(b"<html", body)
        status, body = self.req("GET", config.URL_PREFIX + "/vendor/uPlot.iife.min.js", scopes=None)
        self.assertEqual(status, 200)

    def test_static_traversal_blocked(self):
        status, _ = self.req("GET", config.URL_PREFIX + "/../app/config.py", scopes=None)
        self.assertEqual(status, 404)

    def test_api_requires_token(self):
        for path in ("/settings", "/overview", "/series?ids=sys.cpu", "/export"):
            status, _ = self.req("GET", self.api + path, scopes=None)
            self.assertEqual(status, 401, path)

    def test_read_only_user_cannot_write(self):
        status, _ = self.req("PUT", self.api + "/settings", {"general": {"logLevel": "DEBUG"}},
                             scopes=(config.SCOPE_READ,))
        self.assertEqual(status, 403)
        status, _ = self.req("POST", self.api + "/events", {"text": "x"}, scopes=(config.SCOPE_READ,))
        self.assertEqual(status, 403)

    def test_settings_roundtrip(self):
        status, body = self.req("PUT", self.api + "/settings", {"leak": {"minMbPerHour": 2.5}})
        self.assertEqual(status, 200)
        self.assertEqual(body["leak"]["minMbPerHour"], 2.5)
        self.assertEqual(json.loads(self.dl.registry.get("settings/active"))["leak"]["minMbPerHour"], 2.5)
        status, body = self.req("PUT", self.api + "/settings", {"collector": {"intervalSeconds": 0}})
        self.assertEqual(status, 400)
        self.assertTrue(body["errors"])
        self.req("POST", self.api + "/settings/reset")

    def test_status_overview_group(self):
        status, body = self.req("GET", self.api + "/status", scopes=(config.SCOPE_READ,))
        self.assertEqual(status, 200)
        self.assertEqual(body["collector"]["samples"], 4)
        self.assertAlmostEqual(body["serverTime"], time.time(), delta=5)
        self.assertIn("tiers", body["history"])
        self.assertEqual(body["recording"]["state"], "off")
        status, body = self.req("GET", self.api + "/overview", scopes=(config.SCOPE_READ,))
        self.assertEqual(status, 200)
        alpha = [a for a in body["apps"] if a["key"] == "snap:alpha"][0]
        self.assertNotIn("processList", alpha)
        self.assertEqual(alpha["threads"], 2)
        status, body = self.req("GET", self.api + "/group?key=snap:alpha", scopes=(config.SCOPE_READ,))
        self.assertEqual(status, 200)
        self.assertEqual(body["processList"][0]["pid"], 100)
        self.assertEqual(len(body["processList"][0]["threadList"]), 2)
        status, _ = self.req("GET", self.api + "/group?key=snap:nope", scopes=(config.SCOPE_READ,))
        self.assertEqual(status, 404)

    def test_series(self):
        status, body = self.req("GET", self.api + "/series?ids=app.snap:alpha.anon,sys.cpu,app.snap:nope.anon&points=100",
                                scopes=(config.SCOPE_READ,))
        self.assertEqual(status, 200)
        self.assertEqual(body["tier"], "raw")
        self.assertEqual(len(body["times"]), 4)
        self.assertAlmostEqual(body["series"]["app.snap:alpha.anon"]["avg"][-1], 1030 / 1024, places=2)
        self.assertNotIn("app.snap:nope.anon", body["series"])
        for bad in ("/series", "/series?ids=sys.cpu&from=10&to=5", "/series?ids=sys.cpu&from=x",
                    "/series?ids=sys.cpu&points=y"):
            status, _ = self.req("GET", self.api + bad, scopes=(config.SCOPE_READ,))
            self.assertEqual(status, 400, bad)

    def test_analysis(self):
        status, body = self.req("GET", self.api + "/analysis?level=apps", scopes=(config.SCOPE_READ,))
        self.assertEqual(status, 200)
        alpha = [r for r in body["results"] if r["entity"] == "app.snap:alpha"][0]
        self.assertEqual(alpha["verdict"], "insufficient")      # 30 s of data
        status, body = self.req("GET", self.api + "/analysis?level=processes&app=snap:alpha&metric=rss",
                                scopes=(config.SCOPE_READ,))
        self.assertEqual(status, 200)
        self.assertEqual(body["results"][0]["process"], "frame")
        status, _ = self.req("GET", self.api + "/analysis?level=threads", scopes=(config.SCOPE_READ,))
        self.assertEqual(status, 400)

    def test_markers_by_api_and_datalayer(self):
        status, body = self.req("POST", self.api + "/events", {"text": "  test started "})
        self.assertEqual((status, body["type"], body["text"]), (200, "marker", "test started"))
        status, _ = self.req("PUT", self.api + "/values/events/marker", {"value": "from the PLC"})
        self.assertEqual(status, 200)
        status, body = self.req("GET", self.api + "/events", scopes=(config.SCOPE_READ,))
        texts = [e["text"] for e in body["events"]]
        self.assertIn("test started", texts)
        self.assertIn("from the PLC", texts)
        status, _ = self.req("POST", self.api + "/events", {"text": "  "})
        self.assertEqual(status, 400)
        status, _ = self.req("PUT", self.api + "/values/status/heartbeat", {"value": 1})
        self.assertEqual(status, 400)

    def test_analyzer_start_counts_as_restart_of_its_app(self):
        ev = self.services.events.add(time.time() - 20, "analyzer", "started",
                                      grouping.Group("snap:alpha", "alpha", "snap", "control", "alpha", True))
        try:
            status, body = self.req("GET", self.api + "/analysis?level=apps", scopes=(config.SCOPE_READ,))
            alpha = [r for r in body["results"] if r["entity"] == "app.snap:alpha"][0]
            self.assertEqual(alpha["restarts"], 1)
        finally:
            self.services.events._events.remove(ev)

    def test_media(self):
        status, body = self.req("GET", self.api + "/media", scopes=(config.SCOPE_READ,))
        self.assertEqual(status, 200)
        self.assertEqual([m["name"] for m in body["media"]], ["USB1"])

    def test_export(self):
        status, data, res = self.req("GET", self.api + "/export?level=apps&format=csv", scopes=(config.SCOPE_READ,), raw=True)
        self.assertEqual(status, 200)
        self.assertIn("attachment", res.getheader("Content-Disposition"))
        lines = data.decode().splitlines()
        self.assertTrue(lines[0].startswith("epoch,time,app,name,procs_min"))
        self.assertTrue(any(",snap:alpha,alpha," in line for line in lines[1:]))
        for level in ("processes", "threads", "system"):
            status, data, _ = self.req("GET", self.api + "/export?format=json&level=" + level,
                                       scopes=(config.SCOPE_READ,), raw=True)
            self.assertEqual(status, 200, level)
            self.assertIn("series", json.loads(data))
        status, _ = self.req("GET", self.api + "/export?level=nope", scopes=(config.SCOPE_READ,))
        self.assertEqual(status, 400)

    def test_appdata_endpoints(self):
        status, _ = self.req("POST", self.api + "/appdata/save", {"phase": "save", "id": "t"})
        self.assertEqual(status, 204)
        status, _ = self.req("POST", self.api + "/appdata/load", {"phase": "query", "id": "t"},
                             scopes=(config.SCOPE_READ,))
        self.assertEqual(status, 403)
        status, _ = self.req("POST", self.api + "/appdata/load", {"phase": "abort", "id": "t"})
        self.assertEqual(status, 204)


if __name__ == "__main__":
    unittest.main()
