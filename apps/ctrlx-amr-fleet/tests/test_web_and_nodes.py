"""Web API, auth and node registry tests (no ctrlX needed)."""

import base64
import http.client
import json
import os
import tempfile
import time
import unittest
from urllib.parse import quote

from app import auth, config
from app.datalayer_nodes import NodeRegistry, NodeSpec, NodeWriteError, build_node_specs
from app.datalayer_service import DataLayerService
from app.fleet import FleetState
from app.map_source import MapSource
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
        cls.fleet = FleetState(path_minutes=5)
        cls.dl = DataLayerService(cls.store.get(), "test", on_fleet_write=cls.fleet.receive)
        cls.store.add_listener(cls.dl.on_settings_changed)
        cls.maps = MapSource(config.MAP_SEARCH_DIRS)
        cls.maps.configure("HMI_Map.txt", 0)
        cls.maps.wait_idle(60)
        cls._port = config.DEV_HTTP_PORT
        config.DEV_HTTP_PORT = 0  # let the OS pick a free port
        cls.web = WebServer(cls.store, cls.dl, cls.fleet, cls.maps)
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
        self.last_headers = dict(res.getheaders())
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
        status, body = self.req("PUT", config.API_PREFIX + "/settings", {"fleet": {"staleAfterMs": 9000}})
        self.assertEqual(status, 200)
        self.assertEqual(body["fleet"]["staleAfterMs"], 9000)
        self.assertIn('"staleAfterMs":9000', self.dl.registry.get("settings/active"))
        status, body = self.req("PUT", config.API_PREFIX + "/settings", {"fleet": {"rotationUnit": "grad"}})
        self.assertEqual(status, 400)
        self.assertTrue(body["errors"])

    def test_values(self):
        status, body = self.req("GET", config.API_PREFIX + "/values")
        self.assertEqual(status, 200)
        self.assertIn("fleet/amrs", body)
        status, _ = self.req("PUT", config.API_PREFIX + "/values/status/heartbeat", {"value": 1})
        self.assertEqual(status, 400)

    def test_fleet(self):
        fleet_json = json.dumps({"amrs": [{"id": "A1", "x": 100, "y": 200, "rotation": 45, "state": "driving"}]})
        status, _ = self.req("PUT", config.API_PREFIX + "/values/fleet/amrs", {"value": fleet_json})
        self.assertEqual(status, 200)
        status, body = self.req("GET", config.API_PREFIX + "/fleet", scopes=(config.SCOPE_READ,))
        self.assertEqual(status, 200)
        self.assertEqual(body["amrs"], [{"id": "A1", "x": 100.0, "y": 200.0, "rotation": 45.0, "state": "driving"}])
        self.assertFalse(body["stale"])
        self.assertEqual(body["mapId"], self.maps.current().id)
        status, small = self.req("GET", config.API_PREFIX + "/fleet?since={}".format(body["revision"]),
                                 scopes=(config.SCOPE_READ,))
        self.assertTrue(small["unchanged"])
        self.assertNotIn("amrs", small)
        status, problem = self.req("PUT", config.API_PREFIX + "/values/fleet/amrs", {"value": "[{}]"})
        self.assertEqual(status, 400)
        self.assertIn("amrs[0].id", problem["detail"])
        status, body2 = self.req("GET", config.API_PREFIX + "/fleet", scopes=(config.SCOPE_READ,))
        self.assertEqual(body2["revision"], body["revision"])  # rejected write: fleet unchanged
        self.assertIn("amrs[0].id", body2["lastError"])
        status, _ = self.req("GET", config.API_PREFIX + "/fleet", scopes=None)
        self.assertEqual(status, 401)
        status, _ = self.req("PUT", config.API_PREFIX + "/values/fleet/amrs", {"value": fleet_json},
                             scopes=(config.SCOPE_READ,))
        self.assertEqual(status, 403)

    def test_fleet_path(self):
        for x in (0, 400, 800):
            fleet_json = json.dumps([{"id": "P 1/ä", "x": x, "y": 0, "rotation": 0}])
            status, _ = self.req("PUT", config.API_PREFIX + "/values/fleet/amrs", {"value": fleet_json})
            self.assertEqual(status, 200)
        url = config.API_PREFIX + "/fleet/path?id=" + quote("P 1/ä")
        status, body = self.req("GET", url, scopes=(config.SCOPE_READ,))
        self.assertEqual(status, 200)
        self.assertEqual(body["id"], "P 1/ä")
        self.assertEqual([p[:2] for p in body["points"]], [[0, 0], [400, 0], [800, 0]])
        self.assertTrue(all(0 <= p[2] < 60 for p in body["points"]))
        status, _ = self.req("GET", config.API_PREFIX + "/fleet/path?id=nobody", scopes=(config.SCOPE_READ,))
        self.assertEqual(status, 404)
        status, _ = self.req("GET", url, scopes=None)
        self.assertEqual(status, 401)

    def test_read_only_users_get_no_settings(self):
        reader = (config.SCOPE_READ,)
        for path in ("/settings", "/settings/meta", "/values"):   # /values holds settings/active
            status, _ = self.req("GET", config.API_PREFIX + path, scopes=reader)
            self.assertEqual(status, 403, path)
        status, view = self.req("GET", config.API_PREFIX + "/view-config", scopes=reader)
        self.assertEqual(status, 200)
        self.assertEqual(set(view), {"style", "unitsPerMeter", "amrLengthM", "amrWidthM", "staleAfterMs",
                                     "refreshIntervalMs", "pathHistoryMinutes", "pathStepM"})
        self.assertEqual((view["pathHistoryMinutes"], view["pathStepM"]), (5, 0.25))
        self.assertRegex(view["style"]["accent"], r"^#[0-9a-f]{6}$")
        self.assertGreaterEqual(len(view["style"]["amrColors"]), 1)
        status, _ = self.req("GET", config.API_PREFIX + "/view-config", scopes=None)
        self.assertEqual(status, 401)
        status, s = self.req("GET", config.API_PREFIX + "/status", scopes=reader)
        self.assertFalse(s["canWrite"])

    def test_style_settings_validated(self):
        status, body = self.req("PUT", config.API_PREFIX + "/settings",
                                {"style": {"accent": "#12ab34", "amrColors": ["#000000", "#ffffff"]}})
        self.assertEqual(status, 200)
        status, view = self.req("GET", config.API_PREFIX + "/view-config", scopes=(config.SCOPE_READ,))
        self.assertEqual((view["style"]["accent"], view["style"]["amrColors"]), ("#12ab34", ["#000000", "#ffffff"]))
        status, problem = self.req("PUT", config.API_PREFIX + "/settings",
                                   {"style": {"accent": "blue", "amrColors": ["#12345", "#123456"]}})
        self.assertEqual(status, 400)
        self.assertIn("style.accent: invalid format", problem["errors"])
        self.assertIn("style.amrColors[0]: invalid format", problem["errors"])
        status, problem = self.req("PUT", config.API_PREFIX + "/settings", {"style": {"amrColors": []}})
        self.assertIn("style.amrColors: at least 1 entries", problem["errors"])
        self.req("POST", config.API_PREFIX + "/settings/reset")

    def test_widget_files(self):
        """Home screen widget: static script + image, tag and URL consistent with the manifest."""
        manifest_file = os.path.join(HERE, "..", "configs", "package-assets", config.SNAP_NAME + ".package-manifest.json")
        with open(manifest_file, encoding="utf-8") as f:
            widgets = json.load(f)["uiExtensions"]["dashboard"]
        self.assertEqual(len(widgets), 1)
        w = widgets[0]
        for key in ("src", "image"):
            self.assertTrue(w[key].startswith(config.URL_PREFIX + "/"), key)
            status, body = self.req("GET", w[key], scopes=None)   # static: the ctrlX UI loads it without header
            self.assertEqual(status, 200, w[key])
        status, script = self.req("GET", w["src"], scopes=None)
        self.assertIn('TAG = "{}"'.format(w["tag"]).encode(), script)
        self.assertIn(b'sessionStorage.getItem("idm_token")', script)
        self.assertEqual(set(w["permissions"]), {config.SCOPE_READ, config.SCOPE_WRITE})
        self.assertTrue(w["tag"].startswith(config.SNAP_NAME + "-"))

    def test_map_and_tiles(self):
        status, info = self.req("GET", config.API_PREFIX + "/map", scopes=(config.SCOPE_READ,))
        self.assertEqual(status, 200)
        self.assertTrue(info["loaded"], info)
        self.assertEqual((info["source"], info["width"], info["height"]), ("bundled", 520, 890))
        self.assertEqual(info["unitsPerMeter"], 1000.0)
        tile = config.API_PREFIX + "/map/tiles/{}/0/1/1.png".format(info["id"])
        status, png = self.req("GET", tile + "?color=ff0000", scopes=(config.SCOPE_READ,))
        self.assertEqual(status, 200)
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertEqual(self.last_headers["Content-Type"], "image/png")
        for path, expected in [(tile + "?color=red", 400),
                               (config.API_PREFIX + "/map/tiles/{}/0/9/9.png".format(info["id"]), 404),
                               (config.API_PREFIX + "/map/tiles/0123456789ab/0/0/0.png", 404)]:
            status, _ = self.req("GET", path, scopes=(config.SCOPE_READ,))
            self.assertEqual(status, expected, path)
        status, _ = self.req("GET", tile, scopes=None)
        self.assertEqual(status, 401)

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
