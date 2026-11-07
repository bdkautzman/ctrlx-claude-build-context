"""Web API, auth and node registry tests (no ctrlX needed)."""

import base64
import http.client
import io
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
from app.web_server import (RequestBody, UploadError, WebServer, content_disposition,
                            receive_file)

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULTS_FILE = os.path.join(HERE, "..", "defaults", "settings.json")


def make_token(scopes, exp_offset=3600):
    def b64(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    return "{}.{}.sig".format(b64({"alg": "none"}), b64({"scope": scopes, "exp": time.time() + exp_offset}))


def bearer(scopes):
    return {"Authorization": "Bearer " + make_token(list(scopes))}


class AuthTest(unittest.TestCase):
    def test_roles(self):
        """view < operate < configure; each scope gives its role and the ones below, nothing above."""
        cases = [((config.SCOPE_VIEW,), auth.VIEW), ((config.SCOPE_OPERATE,), auth.OPERATE),
                 ((config.SCOPE_CONFIGURE,), auth.CONFIGURE), ((config.SCOPE_ADMIN,), auth.CONFIGURE),
                 ((config.SCOPE_VIEW, config.SCOPE_OPERATE), auth.OPERATE), (("other.app.r",), None)]
        for scopes, role in cases:
            self.assertEqual(auth.role_of(set(scopes)), role, scopes)
            for needed in auth.ROLES:
                allowed = role is not None and auth.ROLES.index(role) >= auth.ROLES.index(needed)
                self.assertEqual(auth.allows(set(scopes), needed), allowed, (scopes, needed))
                if allowed:
                    auth.require(bearer(scopes), needed)
                else:
                    with self.assertRaises(auth.AuthError) as ctx:
                        auth.require(bearer(scopes), needed)
                    self.assertEqual(ctx.exception.status, 403)

    def test_role_names_match_the_manifest(self):
        with open(os.path.join(HERE, "..", "configs", "package-assets",
                               config.SNAP_NAME + ".package-manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        declared = {s["identifier"] for d in manifest["scopes-declaration"] for s in d["scopes"]}
        self.assertEqual(declared, set(auth.ROLE_SCOPES.values()))
        for menu in manifest["menus"].values():       # every role must see the menu entries
            for entry in menu:
                self.assertEqual(set(entry["permissions"]), declared, entry["id"])

    def test_unknown_role_is_a_bug(self):
        with self.assertRaises(RuntimeError):
            auth.require(bearer([config.SCOPE_ADMIN]), True)    # the old require(headers, write=True)

    def test_missing_expired_malformed(self):
        for headers in ({}, {"Authorization": "Bearer x.y"},
                        {"Authorization": "Bearer " + make_token([config.SCOPE_ADMIN], -10)}):
            with self.assertRaises(auth.AuthError) as ctx:
                auth.require(headers)
            self.assertEqual(ctx.exception.status, 401)

    def test_user_name(self):
        token = base64.urlsafe_b64encode(json.dumps({"name": "anna", "scope": []}).encode()).decode().rstrip("=")
        self.assertEqual(auth.user_name({"Authorization": "Bearer a." + token + ".s"}), "anna")
        self.assertEqual(auth.user_name({}), "")


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

    def test_json_node(self):
        seen = []
        reg = NodeRegistry("root", [
            NodeSpec("j/summary", "string", [], json=True),
            NodeSpec("j/input", "string", {"a": 1}, json=True, writable=True, max_length=50,
                     on_write=lambda doc: seen.append(doc)),
        ])
        self.assertEqual(reg.get("j/summary"), "[]")
        reg.set_internal("j/summary", [{"name": "ä", "n": 2}])
        self.assertEqual(reg.get("j/summary"), '[{"name":"ä","n":2}]')      # compact, UTF-8
        self.assertEqual(reg.get_json("j/summary"), [{"name": "ä", "n": 2}])
        reg.write("j/input", '{"b": [1, 2]}')
        self.assertEqual(seen, [{"b": [1, 2]}])                              # on_write gets the parsed object
        for text, reason in (("{nope", "invalid"), ("x" * 51, "range"), (5, "type")):
            with self.assertRaises(NodeWriteError) as ctx:
                reg.write("j/input", text)
            self.assertEqual(ctx.exception.reason, reason, text)
        self.assertEqual(reg.get_json("j/input"), {"b": [1, 2]})           # rejected writes change nothing
        with self.assertRaises(ValueError):
            NodeRegistry("root", [NodeSpec("j/x", "uint64", 0, json=True)])
        with self.assertRaises(ValueError):
            NodeRegistry("root", [NodeSpec("j/x", "string", "", view_role="admin")])

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

    def req(self, method, path, body=None, scopes=(config.SCOPE_CONFIGURE,)):
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

    def test_roles_on_routes(self):
        """view: status + values; operate: + writable values; configure: + settings and app data."""
        api = config.API_PREFIX
        routes = [("GET", "/status", None, auth.VIEW), ("GET", "/values", None, auth.VIEW),
                  ("PUT", "/values/values/enabled", {"value": True}, auth.OPERATE),
                  ("GET", "/settings", None, auth.CONFIGURE), ("GET", "/settings/meta", None, auth.CONFIGURE),
                  ("PUT", "/settings", self.store.get(), auth.CONFIGURE),
                  ("POST", "/appdata/save", {"phase": "save", "id": "t"}, auth.CONFIGURE)]
        for scope, role in ((config.SCOPE_VIEW, auth.VIEW), (config.SCOPE_OPERATE, auth.OPERATE),
                            (config.SCOPE_CONFIGURE, auth.CONFIGURE)):
            for method, path, body, needed in routes:
                status, _ = self.req(method, api + path, body, scopes=(scope,))
                allowed = auth.ROLES.index(role) >= auth.ROLES.index(needed)
                self.assertEqual(status in (200, 204), allowed, (role, method, path, status))
                if not allowed:
                    self.assertEqual(status, 403, (role, method, path))

    def test_status_tells_the_role(self):
        for scope, role, operate, configure in ((config.SCOPE_VIEW, "view", False, False),
                                                (config.SCOPE_OPERATE, "operate", True, False),
                                                (config.SCOPE_CONFIGURE, "configure", True, True)):
            status, body = self.req("GET", config.API_PREFIX + "/status", scopes=(scope,))
            self.assertEqual((status, body["role"], body["canOperate"], body["canConfigure"]),
                             (200, role, operate, configure))

    def test_settings_roundtrip(self):
        status, body = self.req("PUT", config.API_PREFIX + "/settings", {"example": {"label": "via api"}})
        self.assertEqual(status, 200)
        self.assertEqual(body["example"]["label"], "via api")
        self.assertEqual(self.dl.registry.get("values/label"), "via api")
        status, body = self.req("PUT", config.API_PREFIX + "/settings", {"general": {"logLevel": 5}})
        self.assertEqual(status, 400)
        self.assertTrue(body["errors"])

    def test_values(self):
        status, body = self.req("GET", config.API_PREFIX + "/values", scopes=(config.SCOPE_VIEW,))
        self.assertEqual(status, 200)
        self.assertIn("values/setpoint", body)
        self.assertFalse(body["values/setpoint"]["canWrite"])          # view: no Edit button
        self.assertNotIn("settings/active", body)                      # view_role="configure"
        self.assertNotIn("viewRole", body["values/setpoint"])
        status, body = self.req("GET", config.API_PREFIX + "/values", scopes=(config.SCOPE_OPERATE,))
        self.assertTrue(body["values/setpoint"]["canWrite"])
        self.assertFalse(body["status/heartbeat"]["canWrite"])          # read-only node
        status, body = self.req("GET", config.API_PREFIX + "/values")
        self.assertIn("settings/active", body)
        status, _ = self.req("PUT", config.API_PREFIX + "/values/values/setpoint", {"value": 1.5},
                             scopes=(config.SCOPE_OPERATE,))
        self.assertEqual(status, 200)
        status, _ = self.req("PUT", config.API_PREFIX + "/values/status/heartbeat", {"value": 1})
        self.assertEqual(status, 400)
        status, _ = self.req("PUT", config.API_PREFIX + "/values/nope", {"value": 1})
        self.assertEqual(status, 404)

    def test_appdata_endpoints(self):
        status, _ = self.req("POST", config.API_PREFIX + "/appdata/save", {"phase": "save", "id": "t"})
        self.assertEqual(status, 204)
        status, _ = self.req("POST", config.API_PREFIX + "/appdata/load", {"phase": "query", "id": "t"},
                             scopes=(config.SCOPE_OPERATE,))
        self.assertEqual(status, 403)
        status, _ = self.req("POST", config.API_PREFIX + "/appdata/load", {"phase": "abort", "id": "t"})
        self.assertEqual(status, 204)



class UploadDownloadTest(unittest.TestCase):
    """Helpers for app routes: receive_file (streamed upload, .tmp + rename), RequestBody.drain,
    content_disposition (download file names)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "sub", "file.txt")

    def tearDown(self):
        self.tmp.cleanup()

    def test_receive_file(self):
        data = b"x" * 600000                                   # more than one chunk
        size = receive_file(RequestBody(io.BytesIO(data + b"next request"), len(data)), self.path, 10 ** 6)
        self.assertEqual(size, len(data))
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), data)                   # exactly Content-Length, not more
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["file.txt"])

    def test_rejected_uploads_leave_nothing(self):
        def check(tmp_path):
            raise UploadError(400, "not a valid file")
        for body, limit, chk, status in ((b"12345", 4, None, 413), (b"12", 10, None, 400),
                                         (b"12345", 10, check, 400)):
            length = 5                                         # (b"12": the client went away)
            with self.assertRaises(UploadError) as ctx:
                receive_file(RequestBody(io.BytesIO(body), length), self.path, limit, chk)
            self.assertEqual(ctx.exception.status, status)
            self.assertFalse(os.path.exists(self.path))
            self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_drain(self):
        body = RequestBody(io.BytesIO(b"a" * 1000), 1000)
        self.assertFalse(body.drain(limit=999))                # too much left: close the connection instead
        self.assertTrue(body.drain(limit=1000))
        self.assertEqual(body.left, 0)

    def test_content_disposition(self):
        self.assertEqual(content_disposition("scan.pdf"), 'attachment; filename="scan.pdf"')
        self.assertEqual(content_disposition('Prüf "1".csv'),
                         "attachment; filename=\"Pr_f _1_.csv\"; filename*=UTF-8''Pr%C3%BCf%20%221%22.csv")


if __name__ == "__main__":
    unittest.main()
