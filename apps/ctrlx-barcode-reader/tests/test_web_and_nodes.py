"""Web API, auth and node registry tests (no ctrlX needed)."""

import base64
import http.client
import json
import os
import tempfile
import time
import unittest

from app import auth, config
from app.datalayer_nodes import NodeRegistry, NodeSpec, NodeWriteError, build_node_specs
from app.datalayer_service import DataLayerService
from app.reader import Reader
from app.settings_rules import RULES, validate_settings
from app.settings_store import SettingsStore
from app.web_server import WebServer
from tests.helpers import HAVE_IMAGING, FakeCamera, merge

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
                                  DEFAULTS_FILE, active, RULES, validator=validate_settings)
        cls.store.load_from_disk(create_if_missing=True)
        FakeCamera.reset()
        cls.reader = Reader(cls.store, camera_factory=FakeCamera)
        cls.dl = DataLayerService(cls.store.get(), "test", cls.reader)
        cls.reader.registry = cls.dl.registry
        cls.store.add_listener(cls.dl.on_settings_changed)
        cls.store.add_listener(cls.reader.on_settings)
        cls.reader.start()
        cls._port = config.DEV_HTTP_PORT
        config.DEV_HTTP_PORT = 0  # let the OS pick a free port
        cls.web = WebServer(cls.store, cls.dl, cls.reader)
        cls.web.start()
        cls.port = cls.web._server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.web.stop()
        cls.reader.stop()
        config.DEV_HTTP_PORT = cls._port
        cls.tmp.cleanup()

    def req(self, method, path, body=None, scopes=(config.SCOPE_WRITE,)):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Authorization": "Bearer " + make_token(list(scopes))} if scopes is not None else {}
        data = json.dumps(body) if body is not None else None
        conn.request(method, path, body=data, headers=headers)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        return res.status, (json.loads(raw) if raw and res.getheader("Content-Type", "").startswith("application/json") else raw)

    def test_static_index_without_token(self):
        status, body = self.req("GET", config.URL_PREFIX + "/", scopes=None)
        self.assertEqual(status, 200)
        self.assertIn(b"<html", body)

    def test_static_traversal_blocked(self):
        status, _ = self.req("GET", config.URL_PREFIX + "/../app/config.py", scopes=None)
        self.assertEqual(status, 404)

    def test_api_requires_token(self):
        status, _ = self.req("GET", config.API_PREFIX + "/settings", scopes=None)
        self.assertEqual(status, 401)

    def test_read_only_user_cannot_write(self):
        status, _ = self.req("PUT", config.API_PREFIX + "/settings", {"general": {"logLevel": "DEBUG"}},
                             scopes=(config.SCOPE_READ,))
        self.assertEqual(status, 403)

    def test_settings_roundtrip(self):
        status, body = self.req("PUT", config.API_PREFIX + "/settings",
                                merge(self.store.get(), {"camera": {"address": "192.0.2.9"}}))
        self.assertEqual(status, 200)
        self.assertEqual(body["camera"]["address"], "192.0.2.9")
        self.assertIn('"address":"192.0.2.9"', self.dl.registry.get("settings/active"))
        status, body = self.req("PUT", config.API_PREFIX + "/settings", {"general": {"logLevel": 5}})
        self.assertEqual(status, 400)
        self.assertTrue(body["errors"])

    def test_values(self):
        status, body = self.req("GET", config.API_PREFIX + "/values", scopes=(config.SCOPE_READ,))
        self.assertEqual(status, 200)
        self.assertIn("result/sequence", body)
        status, _ = self.req("PUT", config.API_PREFIX + "/values/request-id", {"value": "x"})
        self.assertEqual(status, 400)
        status, _ = self.req("PUT", config.API_PREFIX + "/values/status/heartbeat", {"value": 1})
        self.assertEqual(status, 400)

    def test_reader_routes_need_scopes(self):
        for method, path in (("POST", "/read"), ("POST", "/continuous"), ("GET", "/live"),
                             ("POST", "/cameras/discover"), ("POST", "/camera/reconnect")):
            status, _ = self.req(method, config.API_PREFIX + path, {}, scopes=(config.SCOPE_READ,))
            self.assertEqual(status, 403, path)
        for path in ("/reader", "/history", "/formats", "/media"):
            status, _ = self.req("GET", config.API_PREFIX + path, scopes=(config.SCOPE_READ,))
            self.assertEqual(status, 200, path)
        status, body = self.req("GET", config.API_PREFIX + "/formats", scopes=(config.SCOPE_READ,))
        self.assertIn({"name": "DataMatrix", "label": "Data Matrix", "kind": "2D"}, body)
        status, _ = self.req("POST", config.API_PREFIX + "/continuous", {"enabled": "yes"})
        self.assertEqual(status, 400)

    @unittest.skipUnless(HAVE_IMAGING, "OpenCV/numpy/zxing-cpp not available")
    def test_read_images_live(self):
        status, result = self.req("POST", config.API_PREFIX + "/read", {"wait": True})
        self.assertEqual(status, 200, result)
        seq = result["sequence"]
        self.assertEqual(result["source"], "ui")
        status, body = self.req("GET", config.API_PREFIX + "/reader", scopes=(config.SCOPE_READ,))
        self.assertEqual(body["last"]["sequence"], seq)
        self.assertEqual(body["camera"]["state"], "connected")
        status, hist = self.req("GET", config.API_PREFIX + "/history", scopes=(config.SCOPE_READ,))
        self.assertEqual(hist[0]["sequence"], seq)
        status, data = self.req("GET", config.API_PREFIX + "/images/{}/display".format(seq), scopes=(config.SCOPE_READ,))
        self.assertEqual(status, 200)
        self.assertTrue(data.startswith(b"\xff\xd8"))
        status, data = self.req("GET", config.API_PREFIX + "/images/{}/full".format(seq), scopes=(config.SCOPE_READ,))
        self.assertTrue(data.startswith(b"\x89PNG"))
        status, _ = self.req("GET", config.API_PREFIX + "/images/99999/thumb", scopes=(config.SCOPE_READ,))
        self.assertEqual(status, 404)
        deadline = time.time() + 20
        status = 204
        while status == 204 and time.time() < deadline:
            status, data = self.req("GET", config.API_PREFIX + "/live?after=0")
            time.sleep(0.1)
        self.assertEqual(status, 200)
        self.assertTrue(data.startswith(b"\xff\xd8"))
        status, body = self.req("POST", config.API_PREFIX + "/continuous", {"enabled": False})
        self.assertEqual((status, body), (200, {"continuous": False}))

    def test_appdata_endpoints(self):
        status, _ = self.req("POST", config.API_PREFIX + "/appdata/save", {"phase": "save", "id": "t"})
        self.assertEqual(status, 204)
        status, _ = self.req("POST", config.API_PREFIX + "/appdata/load", {"phase": "query", "id": "t"},
                             scopes=(config.SCOPE_READ,))
        self.assertEqual(status, 403)
        status, _ = self.req("POST", config.API_PREFIX + "/appdata/load", {"phase": "abort", "id": "t"})
        self.assertEqual(status, 204)


if __name__ == "__main__":
    unittest.main()
