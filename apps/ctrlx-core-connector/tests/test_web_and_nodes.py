"""Web API, auth and node registry tests (no ctrlX needed)."""

import base64
import http.client
import json
import os
import tempfile
import time
import unittest

from app import auth, config
from app.browser import Browser
from app.datalayer_nodes import NodeRegistry, NodeSpec, NodeWriteError, build_node_specs
from app.datalayer_service import DataLayerService
from app.kvd import KvdService
from app.monitor import CoreMonitor
from app.settings_rules import RULES
from app.settings_store import SettingsStore
from app.web_server import WebServer
from tests.fake_adapter import FakeAdapter

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
        cls.monitor = CoreMonitor(cls.store.get())
        cls.dl = DataLayerService(cls.store.get_redacted(), "test")
        cls.store.add_listener(cls.monitor.on_settings_changed)
        cls.store.add_listener(lambda _s: cls.dl.on_settings_changed(cls.store.get_redacted()))
        # this device's Data Layer: a few nodes + a simulated KVD with its nodes.json in the app data
        cls.kvd_file = os.path.join(active, "kvd", "nodes.json")
        os.makedirs(os.path.dirname(cls.kvd_file))
        with open(cls.kvd_file, "w", encoding="utf-8") as f:
            json.dump({"types": [], "nodes": [{"address": "line/count", "type": "uint32", "value": 4,
                                               "metadata": {"nodeClass": "Variable"}}]}, f)
        cls.fake = FakeAdapter({"kvd/value-count": {"nodeClass": "Variable", "type": "uint32", "value": 1},
                                "plant/speed": {"nodeClass": "Variable", "type": "float64", "value": 1.5, "write": True},
                                "plant/temp": {"nodeClass": "Variable", "type": "float64", "value": 20.0}},
                               kvd_file=cls.kvd_file)
        cls.monitor.attach(cls.fake)   # the monitor thread is not started: nothing polls
        cls._port = config.DEV_HTTP_PORT
        config.DEV_HTTP_PORT = 0  # let the OS pick a free port
        cls.web = WebServer(cls.store, cls.dl, cls.monitor, Browser(cls.monitor.client),
                            KvdService(cls.monitor.client, cls.kvd_file, apply_timeout_s=0.5))
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


    def test_role_split(self):
        """The user's split 2026-11-10: a route refuses (403) the role just below the one it needs."""
        below = {auth.OPERATE: (config.SCOPE_VIEW,), auth.CONFIGURE: (config.SCOPE_OPERATE,)}
        routes = [("PUT", "/cores/_local/node?path=x", {"value": 1}, auth.OPERATE),
              ("POST", "/cores", {}, auth.CONFIGURE), ("DELETE", "/cores/x", None, auth.CONFIGURE),
              ("POST", "/cores/_local/kvd/nodes", {}, auth.CONFIGURE),
              ("DELETE", "/cores/_local/kvd/nodes?address=x", None, auth.CONFIGURE),
              ("PUT", "/settings", {}, auth.CONFIGURE)]
        for method, path, body, needed in routes:
            res = self.req(method, config.API_PREFIX + path, body, scopes=below[needed])
            self.assertEqual(res[0], 403, (method, path, needed))

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
                             scopes=(config.SCOPE_VIEW,))
        self.assertEqual(status, 403)

    def test_settings_roundtrip(self):
        status, body = self.req("PUT", config.API_PREFIX + "/settings", {"general": {"logLevel": "DEBUG"}})
        self.assertEqual(status, 200)
        self.assertEqual(body["general"]["logLevel"], "DEBUG")
        self.assertIn('"logLevel":"DEBUG"', self.dl.registry.get("settings/active"))
        status, body = self.req("PUT", config.API_PREFIX + "/settings", {"general": {"logLevel": 5}})
        self.assertEqual(status, 400)
        self.assertTrue(body["errors"])

    def test_values(self):
        status, body = self.req("GET", config.API_PREFIX + "/values", scopes=(config.SCOPE_VIEW,))
        self.assertEqual(status, 200)
        self.assertIn("cores/summary", body)
        status, _ = self.req("PUT", config.API_PREFIX + "/values/status/heartbeat", {"value": 1})
        self.assertEqual(status, 400)

    def test_appdata_endpoints(self):
        status, _ = self.req("POST", config.API_PREFIX + "/appdata/save", {"phase": "save", "id": "t"})
        self.assertEqual(status, 204)
        status, _ = self.req("POST", config.API_PREFIX + "/appdata/load", {"phase": "query", "id": "t"},
                             scopes=(config.SCOPE_VIEW,))
        self.assertEqual(status, 403)
        status, _ = self.req("POST", config.API_PREFIX + "/appdata/load", {"phase": "abort", "id": "t"})
        self.assertEqual(status, 204)

    def test_cores_add_list_delete(self):
        core = {"name": "web-1", "host": "192.168.1.7", "user": "boschrexroth", "password": "Secret-123",
                "sslPort": 443, "skipCertificateCheck": True}
        status, body = self.req("POST", config.API_PREFIX + "/cores", core)
        self.assertEqual(status, 201, body)
        self.assertEqual(body["password"], "********")
        status, body = self.req("POST", config.API_PREFIX + "/cores", core)
        self.assertEqual(status, 409)
        status, cores = self.req("GET", config.API_PREFIX + "/cores", scopes=(config.SCOPE_VIEW,))
        self.assertEqual([c["name"] for c in cores], [config.LOCAL_ID, "web-1"])   # this device first
        self.assertTrue(cores[0]["local"])
        self.assertEqual(cores[1]["remote"], "devices/remotes/cm-web-1")
        self.assertEqual(cores[1]["webUrl"], "https://192.168.1.7/")
        # passwords never leave the app: not in /cores, /settings or the Data Layer
        for path in ("/cores", "/settings"):
            _, raw = self.req("GET", config.API_PREFIX + path)
            self.assertNotIn("Secret-123", json.dumps(raw))
        self.assertNotIn("Secret-123", self.dl.registry.get("settings/active"))
        with open(self.store.status()["file"], encoding="utf-8") as f:
            self.assertEqual(json.load(f)["cores"][0]["password"], "Secret-123")  # stored in app data
        # the generic settings form sends the masked value back: the password must survive
        _, current = self.req("GET", config.API_PREFIX + "/settings")
        current["general"]["logLevel"] = "WARNING"
        status, _ = self.req("PUT", config.API_PREFIX + "/settings", current)
        self.assertEqual(status, 200)
        self.assertEqual(self.store.get()["cores"][0]["password"], "Secret-123")
        status, _ = self.req("DELETE", config.API_PREFIX + "/cores/web-1")
        self.assertEqual(status, 200)
        status, _ = self.req("DELETE", config.API_PREFIX + "/cores/web-1")
        self.assertEqual(status, 404)

    def test_cores_name_case_and_host_port(self):
        """Names keep their capitals; the port comes with the host field ("host:port", or a pasted URL)."""
        base = {"user": "boschrexroth", "password": "p", "skipCertificateCheck": True}
        add = lambda **kw: self.req("POST", config.API_PREFIX + "/cores", dict(base, **kw))
        try:
            status, body = add(name="Line-8-Core", host="192.168.1.10:8443")
            self.assertEqual(status, 201, body)
            self.assertEqual((body["name"], body["host"], body["sslPort"]), ("Line-8-Core", "192.168.1.10", 8443))
            _, cores = self.req("GET", config.API_PREFIX + "/cores")
            self.assertEqual(cores[-1]["remote"], "devices/remotes/cm-Line-8-Core")
            self.assertEqual(cores[-1]["webUrl"], "https://192.168.1.10:8443/")
            # cores/configured counts this device too
            self.assertEqual(self.dl.registry.get("cores/configured"), len(self.store.get()["cores"]) + 1)
            status, body = add(name="line-8-CORE", host="10.0.0.2")       # differs only in case
            self.assertEqual((status, body["errors"]), (409, ["name: already used"]))
            status, body = add(name="Pasted", host=" https://core-2.local/ ")
            self.assertEqual((status, body["host"], body["sslPort"]), (201, "core-2.local", 443))
            for host in ("10.0.0.3:0", "10.0.0.3:70000"):
                status, body = add(name="Port", host=host)
                self.assertEqual(status, 400, host)
                self.assertEqual(body["errors"], ["host: port must be 1-65535"])
            status, body = add(name="Port", host="10.0.0.3:https")
            self.assertEqual((status, body["errors"]), (400, ["host: invalid format"]))
        finally:
            for name in ("Line-8-Core", "Pasted"):
                self.req("DELETE", config.API_PREFIX + "/cores/" + name)
        self.assertEqual(self.store.get()["cores"], [])

    def test_connected_count_includes_this_device(self):
        dl = DataLayerService(self.store.get_redacted(), "test")
        self.assertEqual(dl.registry.get("cores/configured"), 1)          # only this device
        dl.publish_cores([{"name": "a", "state": "connected"}, {"name": "b", "state": "error"}],
                         {"name": config.LOCAL_ID, "state": "connected"})
        self.assertEqual(dl.registry.get("cores/connected"), 2)
        self.assertEqual([c["name"] for c in json.loads(dl.registry.get("cores/summary"))], ["a", "b"])
        dl.publish_cores([{"name": "a", "state": "connected"}], {"name": config.LOCAL_ID, "state": "offline"})
        self.assertEqual(dl.registry.get("cores/connected"), 1)

    def test_cores_validation_and_permissions(self):
        bad = {"name": "Bad Name", "host": "192.168.1.7;rm", "user": "boschrexroth", "password": ""}
        status, body = self.req("POST", config.API_PREFIX + "/cores", bad)
        self.assertEqual(status, 400)
        self.assertIn("name: invalid format", body["errors"])
        self.assertIn("host: invalid format", body["errors"])
        self.assertTrue(any(e.startswith("password:") for e in body["errors"]))
        self.assertEqual(self.store.get()["cores"], [])
        good = {"name": "ro-test", "host": "10.0.0.1", "user": "u", "password": "p"}
        status, _ = self.req("POST", config.API_PREFIX + "/cores", good, scopes=(config.SCOPE_VIEW,))
        self.assertEqual(status, 403)
        status, _ = self.req("DELETE", config.API_PREFIX + "/cores/x", scopes=(config.SCOPE_VIEW,))
        self.assertEqual(status, 403)

    def test_this_device_cannot_be_removed(self):
        status, _ = self.req("DELETE", config.API_PREFIX + "/cores/" + config.LOCAL_ID)
        self.assertEqual(status, 400)

    def test_browser_endpoints(self):
        base = config.API_PREFIX + "/cores/" + config.LOCAL_ID
        status, body = self.req("GET", base + "/children?path=", scopes=(config.SCOPE_VIEW,))
        self.assertEqual(status, 200, body)
        self.assertEqual([c["name"] for c in body["children"]], ["kvd", "line", "plant"])
        status, body = self.req("GET", base + "/children?path=plant", scopes=(config.SCOPE_VIEW,))
        self.assertEqual([(c["name"], c["value"]) for c in body["children"]], [("speed", 1.5), ("temp", 20.0)])
        status, body = self.req("GET", base + "/node?path=plant%2Fspeed", scopes=(config.SCOPE_VIEW,))
        self.assertEqual((status, body["value"], body["metadata"]["operations"]["write"]), (200, 1.5, True))
        status, body = self.req("POST", base + "/values", {"paths": ["plant/temp"]}, scopes=(config.SCOPE_VIEW,))
        self.assertEqual((status, body[0]["value"]), (200, 20.0))   # a read, allowed for readers
        status, _ = self.req("PUT", base + "/node?path=plant/speed", {"value": "2.5"}, scopes=(config.SCOPE_VIEW,))
        self.assertEqual(status, 403)
        status, body = self.req("PUT", base + "/node?path=plant/speed", {"value": "2.5"})
        self.assertEqual((status, body["value"]), (200, 2.5))
        status, body = self.req("PUT", base + "/node?path=plant/temp", {"value": "1"})
        self.assertEqual((status, body["title"]), (403, "Data Layer: PERMISSION_DENIED"))
        status, body = self.req("PUT", base + "/node?path=plant/speed", {"value": "fast"})
        self.assertEqual(status, 400)
        status, body = self.req("GET", base + "/node?path=nope")
        self.assertEqual((status, body["title"]), (404, "Data Layer: INVALID_ADDRESS"))
        status, _ = self.req("GET", base + "/children?path=a//b")
        self.assertEqual(status, 400)
        status, _ = self.req("GET", config.API_PREFIX + "/cores/unknown/children?path=")
        self.assertEqual(status, 404)
        self.monitor.attach(None)
        try:
            status, body = self.req("GET", base + "/children?path=")
            self.assertEqual((status, body["title"]), (503, "Data Layer: CLIENT_NOT_CONNECTED"))
        finally:
            self.monitor.attach(self.fake)

    def test_kvd_endpoints(self):
        base = config.API_PREFIX + "/cores/" + config.LOCAL_ID
        status, body = self.req("GET", base + "/kvd", scopes=(config.SCOPE_VIEW,))
        self.assertEqual((status, body["available"], body["nodes"]), (200, True, ["line/count"]))
        node = {"address": "line/setpoint", "nodeClass": "Variable", "type": "int16", "value": "-3", "unit": "mm"}
        status, _ = self.req("POST", base + "/kvd/nodes", node, scopes=(config.SCOPE_VIEW,))
        self.assertEqual(status, 403)
        status, body = self.req("POST", base + "/kvd/nodes", dict(node, value="x"))
        self.assertEqual((status, body["errors"][0].split(":")[0]), (400, "value"))
        status, body = self.req("POST", base + "/kvd/nodes", node)
        self.assertEqual((status, body["value"]), (201, -3))
        status, body = self.req("GET", base + "/children?path=line")
        self.assertEqual([c["name"] for c in body["children"]], ["count", "setpoint"])
        status, body = self.req("POST", base + "/kvd/nodes", node)
        self.assertEqual(status, 409)
        status, _ = self.req("DELETE", base + "/kvd/nodes?address=line/setpoint", scopes=(config.SCOPE_VIEW,))
        self.assertEqual(status, 403)
        status, body = self.req("DELETE", base + "/kvd/nodes?address=line%2Fsetpoint")
        self.assertEqual((status, body["gone"]), (200, True))
        status, _ = self.req("DELETE", base + "/kvd/nodes?address=plant/speed")   # not a KVD node
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
