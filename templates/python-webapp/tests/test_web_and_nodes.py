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
from app.settings_rules import RULES
from app.settings_store import SettingsStore
from app.web_server import WebServer

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
        cls.dl = DataLayerService(cls.store.get(), "test")
        cls.store.add_listener(cls.dl.on_settings_changed)
        cls._port = config.DEV_HTTP_PORT
        config.DEV_HTTP_PORT = 0  # let the OS pick a free port
        cls.web = WebServer(cls.store, cls.dl)
        cls.web.start()
        cls.port = cls.web._server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.web.stop()
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
        status, body = self.req("PUT", config.API_PREFIX + "/settings", {"example": {"label": "via api"}})
        self.assertEqual(status, 200)
        self.assertEqual(body["example"]["label"], "via api")
        self.assertEqual(self.dl.registry.get("values/label"), "via api")
        status, body = self.req("PUT", config.API_PREFIX + "/settings", {"general": {"logLevel": 5}})
        self.assertEqual(status, 400)
        self.assertTrue(body["errors"])

    def test_values(self):
        status, body = self.req("GET", config.API_PREFIX + "/values", scopes=(config.SCOPE_READ,))
        self.assertEqual(status, 200)
        self.assertIn("values/setpoint", body)
        status, _ = self.req("PUT", config.API_PREFIX + "/values/values/setpoint", {"value": 1.5})
        self.assertEqual(status, 200)
        status, _ = self.req("PUT", config.API_PREFIX + "/values/status/heartbeat", {"value": 1})
        self.assertEqual(status, 400)

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
