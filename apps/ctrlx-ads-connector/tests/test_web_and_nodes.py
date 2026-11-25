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
from app.datalayer_nodes import (INT_RANGES, SUPPORTED_TYPES, CommandError, Commands, NodeRegistry, NodeSpec,
                                  NodeWriteError, PlcCommand, build_node_specs, need)
from app.controllers import ControllerManager
from app.datalayer_service import DataLayerService
from app.dev_fake_plc import FAKE_INFO, FakeLink, FakePlc
from app.plc_nodes import NodeMap
from app.settings_rules import RULES, validate_settings
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

    def test_integer_types_have_their_ranges(self):
        """Every IEC integer type is its own node type, with exactly its range - nothing is clipped or converted."""
        for vtype, (low, high) in INT_RANGES.items():
            reg = NodeRegistry("root", [NodeSpec("n", vtype, 0, writable=True)])
            self.assertEqual(reg.write("n", low), low)
            self.assertEqual(reg.write("n", high), high)
            for bad, reason in ((low - 1, "range"), (high + 1, "range"), (1.5, "type"), (True, "type"), ("1", "type")):
                with self.assertRaises(NodeWriteError) as ctx:
                    reg.write("n", bad)
                self.assertEqual(ctx.exception.reason, reason, (vtype, bad))
            self.assertEqual(reg.get("n"), high)                     # rejected writes change nothing

    def test_float32_holds_what_a_plc_real_holds(self):
        reg = NodeRegistry("root", [NodeSpec("r", "float32", 0.1, writable=True)])
        self.assertEqual(reg.get("r"), 0.10000000149011612)          # the float32 nearest to 0.1, as a REAL
        self.assertEqual(reg.write("r", 0.10000000149011612), 0.10000000149011612)   # a float32 value stays exact
        self.assertEqual(reg.write("r", 3.4028234663852886e38), 3.4028234663852886e38)
        for bad, reason in ((1e39, "range"), (float("inf"), "invalid"), (float("nan"), "invalid"), (False, "type")):
            with self.assertRaises(NodeWriteError) as ctx:
                reg.write("r", bad)
            self.assertEqual(ctx.exception.reason, reason, bad)

    def test_arrays(self):
        reg = NodeRegistry("root", [
            NodeSpec("a/i", "array-of-int16", [1, 2], writable=True, max_items=3, minimum=-10, maximum=10),
            NodeSpec("a/s", "array-of-string", ["x"], writable=True, max_length=3),
            NodeSpec("a/r", "array-of-float32", [], writable=True),
        ])
        self.assertEqual(reg.get("a/i"), (1, 2))                       # arrays are tuples
        self.assertEqual(reg.write("a/i", [-10, 0, 10]), (-10, 0, 10))
        self.assertEqual(reg.write("a/s", ["abc", ""]), ("abc", ""))
        self.assertEqual(reg.write("a/r", [0.5, 0.1]), (0.5, 0.10000000149011612))
        for name, bad, reason in (("a/i", [1, 2, 3, 4], "range"), ("a/i", [11], "range"), ("a/i", [1.5], "type"),
                                  ("a/i", [40000], "range"), ("a/i", 1, "type"), ("a/s", ["abcd"], "range"),
                                  ("a/s", [1], "type"), ("a/r", [float("nan")], "invalid")):
            with self.assertRaises(NodeWriteError) as ctx:
                reg.write(name, bad)
            self.assertEqual(ctx.exception.reason, reason, (name, bad))
        self.assertEqual(json.loads(json.dumps(reg.snapshot()))["a/i"]["value"], [-10, 0, 10])

    def test_every_type_can_be_declared(self):
        initial = {"bool8": False, "string": "", "float32": 0.0, "float64": 0.0}
        specs = [NodeSpec(t, t, [] if t.startswith("array-of-") else initial.get(t, 0)) for t in SUPPORTED_TYPES]
        self.assertEqual(len(NodeRegistry("root", specs).specs()), 24)
        with self.assertRaises(ValueError):
            NodeRegistry("root", [NodeSpec("x", "timestamp", 0)])

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

    def test_commands_bound_later(self):
        """Command nodes call the app through a Commands that is bound after the registry exists."""
        class Worker:
            def __init__(self):
                self.started = []

            def start(self, what):
                self.started.append(what)

        commands = Commands()
        reg = NodeRegistry("root", [NodeSpec("cmd/start", "string", "", writable=True,
                                             on_write=lambda v: commands.start(v))])
        with self.assertRaises(NodeWriteError) as ctx:          # not bound yet: rejected, no exception
            reg.write("cmd/start", "a")
        self.assertEqual(ctx.exception.reason, "invalid")
        worker = Worker()
        commands.bind(worker)
        reg.write("cmd/start", "b")
        self.assertEqual(worker.started, ["b"])
        with self.assertRaises(NodeWriteError):
            need(None)

    def test_template_specs_are_valid(self):
        with open(DEFAULTS_FILE, encoding="utf-8") as f:
            settings = json.load(f)
        reg = NodeRegistry(config.DATALAYER_ROOT, build_node_specs(settings, "1.0.0"))
        for name in reg.snapshot():
            self.assertRegex(name, r"^[a-z0-9-]+(/[a-z0-9-]+)*$", "use lowercase-dash node names")


class PlcCommandTest(unittest.TestCase):
    """The kit's PLC handshake: execute (rising edge) -> busy -> done | error (+ error-text) -> execute false."""

    def setUp(self):
        import threading
        self.go = threading.Event()        # the action waits for it: the test decides when it finishes
        self.calls = []
        self.fail = None

        def run(params, registry):
            self.calls.append(params)
            self.assertTrue(self.go.wait(5))
            if self.fail:
                raise self.fail
            registry.set_internal("result", params["target"] * 2)

        self.cmd = PlcCommand("cmd/do", run, params=[NodeSpec("target", "float64", 1.0)])
        self.reg = NodeRegistry("root", [NodeSpec("result", "float64", 0.0)] + self.cmd.specs())

    def status(self):
        return tuple(self.reg.get("cmd/do/" + n) for n in PlcCommand.STATUS)   # busy, done, error, error-text

    def finish(self):
        self.go.set()
        self.assertTrue(self.cmd.wait(5))
        self.go.clear()

    def test_done(self):
        self.reg.write("cmd/do/target", 21.0)
        self.reg.write("cmd/do/execute", True)
        self.assertEqual(self.status(), (True, False, False, ""))
        self.reg.write("cmd/do/execute", True)                   # cyclic writes: no new start
        self.finish()
        self.assertEqual(self.status(), (False, True, False, ""))
        self.assertEqual(self.reg.get("result"), 42.0)          # results are there when done is true
        self.assertEqual(self.calls, [{"target": 21.0}])
        self.reg.write("cmd/do/execute", False)                  # reset
        self.assertEqual(self.status(), (False, False, False, ""))

    def test_error_and_restart(self):
        self.fail = CommandError("no part")
        self.reg.write("cmd/do/execute", True)
        self.finish()
        self.assertEqual(self.status(), (False, False, True, "no part"))
        self.reg.write("cmd/do/execute", False)
        self.fail = RuntimeError("bug")
        self.reg.write("cmd/do/execute", True)
        self.finish()
        self.assertEqual(self.status()[:3], (False, False, True))
        self.assertIn("internal error", self.status()[3])

    def test_start_while_busy_is_rejected(self):
        self.reg.write("cmd/do/execute", True)
        self.reg.write("cmd/do/execute", False)                  # reset while busy: allowed, keeps running
        with self.assertRaises(NodeWriteError) as ctx:
            self.reg.write("cmd/do/execute", True)               # a new start while busy
        self.assertEqual(ctx.exception.reason, "invalid")
        self.assertFalse(self.reg.get("cmd/do/execute"))        # the rejected write changed nothing
        self.finish()
        self.assertEqual(self.status(), (False, True, False, ""))  # result stays until the next start
        self.reg.write("cmd/do/execute", True)
        self.assertEqual(self.status(), (True, False, False, ""))  # the next start clears it
        self.finish()
        self.assertEqual(len(self.calls), 2)

    def test_nodes(self):
        specs = {s.name: s for s in self.cmd.specs()}
        self.assertEqual(set(specs), {"cmd/do/execute", "cmd/do/busy", "cmd/do/done", "cmd/do/error",
                                      "cmd/do/error-text", "cmd/do/target"})
        self.assertTrue(specs["cmd/do/execute"].writable and specs["cmd/do/target"].writable)
        self.assertFalse(any(specs["cmd/do/" + n].writable for n in PlcCommand.STATUS))
        self.assertEqual(specs["cmd/do/execute"].write_role, "operate")


class WebApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        active = os.path.join(cls.tmp.name, "activeConfiguration")
        os.makedirs(active)
        cls.store = SettingsStore(os.path.join(active, config.SNAP_NAME, "settings.json"),
                                  DEFAULTS_FILE, active, RULES, validator=validate_settings)
        cls.store.load_from_disk(create_if_missing=True)
        cls.plc = FakePlc()
        cls.nodes = NodeMap()
        cls.manager = ControllerManager(cls.nodes, link_factory=lambda host, net_id, t: FakeLink(cls.plc),
                                        discover=lambda host: dict(FAKE_INFO))
        cls.manager.apply(cls.store.get())
        cls.store.add_listener(cls.manager.apply)
        cls.dl = DataLayerService(cls.store.get(), "test", plc_nodes=cls.nodes)
        cls.store.add_listener(cls.dl.on_settings_changed)
        cls._port = config.DEV_HTTP_PORT
        config.DEV_HTTP_PORT = 0  # let the OS pick a free port
        cls.web = WebServer(cls.store, cls.dl, cls.manager)
        cls.web.start()
        cls.port = cls.web._server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.web.stop()
        cls.manager.stop()
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
        """view: status, values, controllers, browser; operate: + PLC writes; configure: + controllers, nodes,
        settings and app data."""
        api = config.API_PREFIX
        self.add_fake("roles")
        try:
            self._roles_on_routes(api)
        finally:
            self.req("DELETE", api + "/controllers/roles")

    def _roles_on_routes(self, api):
        sym = "/controllers/roles/symbol?path=GVL_Demo.nInt"
        routes = [("GET", "/status", None, auth.VIEW), ("GET", "/values", None, auth.VIEW),
                  ("GET", "/controllers", None, auth.VIEW), ("GET", "/nodes", None, auth.VIEW),
                  ("GET", "/controllers/roles/children?path=GVL_Demo", None, auth.VIEW),
                  ("GET", sym, None, auth.VIEW), ("GET", "/controllers/roles/value?path=GVL_Demo.nInt", None, auth.VIEW),
                  ("POST", "/controllers/roles/values", {"paths": ["GVL_Demo.nInt"]}, auth.VIEW),
                  ("PUT", sym, {"value": "-1234"}, auth.OPERATE),
                  ("POST", "/nodes/plan", {"controller": "roles", "path": "GVL_Demo.nInt"}, auth.CONFIGURE),
                  ("PUT", "/controllers/roles", {"pollIntervalMs": 50}, auth.CONFIGURE),
                  ("POST", "/discover", {"host": "fake-plc"}, auth.CONFIGURE),
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

    def test_status_has_the_page_colors_for_every_role(self):
        status, _ = self.req("PUT", config.API_PREFIX + "/settings", {"style": {"accent": "#aa3300"}})
        self.assertEqual(status, 200)
        try:
            for scope in (config.SCOPE_VIEW, config.SCOPE_OPERATE, config.SCOPE_CONFIGURE):
                status, body = self.req("GET", config.API_PREFIX + "/status", scopes=(scope,))
                self.assertEqual((status, body["style"]["accent"], body["style"]["panelDark"]), (200, "#aa3300", "#1c2128"))
                self.assertEqual((body["about"]["license"], body["about"]["author"]), (config.APP_LICENSE, config.APP_AUTHOR))
        finally:
            self.req("POST", config.API_PREFIX + "/settings/reset")

    def test_settings_roundtrip(self):
        status, body = self.req("PUT", config.API_PREFIX + "/settings", {"ads": {"timeoutMs": 2500}})
        self.assertEqual(status, 200)
        self.assertEqual(body["ads"]["timeoutMs"], 2500)
        self.assertEqual(self.dl.registry.get_json("settings/active")["ads"]["timeoutMs"], 2500)
        self.req("PUT", config.API_PREFIX + "/settings", {"ads": {"timeoutMs": 1000}})
        status, body = self.req("PUT", config.API_PREFIX + "/settings", {"general": {"logLevel": 5}})
        self.assertEqual(status, 400)
        self.assertTrue(body["errors"])

    def test_values(self):
        """The app's own nodes: read-only, settings/active only for configure."""
        status, body = self.req("GET", config.API_PREFIX + "/values", scopes=(config.SCOPE_VIEW,))
        self.assertEqual(status, 200)
        self.assertIn("controllers/summary", body)
        self.assertNotIn("settings/active", body)                      # view_role="configure"
        self.assertNotIn("viewRole", body["status/heartbeat"])
        self.assertFalse(any(v["canWrite"] for v in body.values()))
        status, body = self.req("GET", config.API_PREFIX + "/values")
        self.assertIn("settings/active", body)
        status, _ = self.req("PUT", config.API_PREFIX + "/values/status/heartbeat", {"value": 1})
        self.assertEqual(status, 400)
        status, _ = self.req("PUT", config.API_PREFIX + "/values/nope", {"value": 1})
        self.assertEqual(status, 404)

    # -- controllers, browser, nodes (fake PLC: app/dev_fake_plc.py) -------------------------------------------------
    def add_fake(self, name, **extra):
        entry = dict({"name": name, "host": "fake-plc", "amsNetId": "", "amsPort": 851, "pollIntervalMs": 50}, **extra)
        status, body = self.req("POST", config.API_PREFIX + "/controllers", entry)
        self.assertEqual(status, 201, body)
        deadline = time.time() + 5
        while time.time() < deadline:
            st = next(c for c in self.manager.snapshot() if c["name"] == name)
            if st["state"] == "connected" and st["plcState"] == 5:
                return st
            time.sleep(0.02)
        self.fail("fake controller not connected: {}".format(st))

    def test_controller_dialog_errors(self):
        api = config.API_PREFIX
        status, body = self.req("POST", api + "/controllers", {"name": "bad name", "host": "x"})
        self.assertEqual(status, 400)
        self.assertIn("name: invalid format", body["errors"])
        status, body = self.req("POST", api + "/controllers", {"name": "c1", "host": "1.2.3.4", "amsNetId": "1.2.3"})
        self.assertEqual((status, body["errors"]), (400, ["amsNetId: invalid format"]))
        status, body = self.req("POST", api + "/controllers", {"name": "c1", "host": "1.2.3.4",
                                                               "amsNetId": "1.2.3.4.5.300"})
        self.assertEqual((status, body["errors"]), (400, ["amsNetId: each part must be 0-255"]))
        status, body = self.req("POST", api + "/controllers", {"name": "c1", "host": "1.2.3.4", "enabled": False})
        self.assertEqual(status, 201, body)
        try:
            status, body = self.req("POST", api + "/controllers", {"name": "C1", "host": "1.2.3.5"})
            self.assertEqual((status, body["errors"]), (409, ["name: already used"]))
            status, body = self.req("PUT", api + "/controllers/c1", {"amsPort": 70000})
            self.assertEqual((status, body["errors"]), (400, ["amsPort: must be <= 65535"]))
            status, body = self.req("PUT", api + "/controllers/nope", {"amsPort": 852})
            self.assertEqual(status, 404)
            st = next(c for c in self.manager.snapshot() if c["name"] == "c1")
            self.assertEqual(st["state"], "disabled")
        finally:
            self.assertEqual(self.req("DELETE", api + "/controllers/c1")[0], 200)

    def test_browse_write_and_nodes(self):
        api = config.API_PREFIX
        self.add_fake("cx")
        try:
            self._browse_write_and_nodes(api)
        finally:
            status, body = self.req("DELETE", api + "/controllers/cx")
            self.assertEqual(status, 200)
        self.assertEqual([n for n in self.store.get()["nodes"] if n["controller"] == "cx"], [])

    def _browse_write_and_nodes(self, api):
        status, body = self.req("GET", api + "/controllers/cx/children?path=", scopes=(config.SCOPE_VIEW,))
        self.assertEqual(status, 200, body)
        self.assertEqual([c["name"] for c in body["children"]], ["GVL_Demo", "MAIN"])
        status, body = self.req("GET", api + "/controllers/cx/children?path=GVL_Demo.aAxis")
        self.assertEqual([c["path"] for c in body["children"]], ["GVL_Demo.aAxis[1]", "GVL_Demo.aAxis[2]",
                                                                  "GVL_Demo.aAxis[3]"])
        status, body = self.req("GET", api + "/controllers/cx/children?path=GVL_Demo")
        rows = {c["name"]: c for c in body["children"]}
        self.assertEqual((rows["fReal"]["value"], rows["fReal"]["dlType"]), (0.1, "float32"))
        self.assertEqual(rows["aInt"]["value"], list(range(10, 110, 10)))
        self.assertEqual((rows["stAxis"]["kind"], rows["stAxis"]["hasChildren"]), ("struct", True))
        status, body = self.req("GET", api + "/controllers/cx/symbol?path=gvl_demo.STAXIS")
        self.assertEqual((body["path"], body["value"]["sName"], body["leafCount"]), ("GVL_Demo.stAxis", "X", 5))
        status, body = self.req("GET", api + "/controllers/cx/symbol?path=GVL_Demo.nope")
        self.assertEqual(status, 404)
        status, body = self.req("POST", api + "/controllers/cx/values",
                                {"paths": ["GVL_Demo.sText", "MAIN.nope"]})
        self.assertEqual(body[0]["value"], "Hello ADS")
        self.assertIn("valueError", body[1])

        # writes: operate; text from the input field, the PLC's type decides
        status, body = self.req("PUT", api + "/controllers/cx/symbol?path=GVL_Demo.nLint", {"value": "-5"},
                                scopes=(config.SCOPE_VIEW,))
        self.assertEqual(status, 403)
        status, body = self.req("PUT", api + "/controllers/cx/symbol?path=GVL_Demo.nLint",
                                {"value": "-9223372036854775808"}, scopes=(config.SCOPE_OPERATE,))
        self.assertEqual((status, body["value"]), (200, str(-2 ** 63)))   # exact digits for the page (> 2^53)
        status, body = self.req("PUT", api + "/controllers/cx/symbol?path=GVL_Demo.nUsint", {"value": "256"},
                                scopes=(config.SCOPE_OPERATE,))
        self.assertEqual(status, 400)
        self.assertIn("out of range", body["detail"])
        status, body = self.req("PUT", api + "/controllers/cx/symbol?path=GVL_Demo.nConst", {"value": "1"})
        self.assertEqual(status, 400)
        status, body = self.req("PUT", api + "/controllers/cx/symbol?path=GVL_Demo.stAxis", {"value": "{}"})
        self.assertEqual(status, 400)

        # Data Layer nodes: a value, then a struct (one node per member)
        status, body = self.req("POST", api + "/nodes/plan", {"controller": "cx", "path": "GVL_Demo.stAxis"})
        self.assertEqual([n["address"] for n in body["nodes"]], ["GVL_Demo/stAxis/fPos", "GVL_Demo/stAxis/fVel",
                         "GVL_Demo/stAxis/bEnabled", "GVL_Demo/stAxis/eState", "GVL_Demo/stAxis/sName"])
        status, body = self.req("POST", api + "/nodes", {"controller": "cx", "path": "GVL_Demo.fReal",
                                                         "writable": True, "description": "the setpoint"})
        self.assertEqual(status, 201, body)
        self.assertEqual(body["added"][0], {"controller": "cx", "symbol": "GVL_Demo.fReal",
                                            "address": "GVL_Demo/fReal", "type": "float32", "writable": True,
                                            "description": "the setpoint", "unit": ""})
        status, body = self.req("POST", api + "/nodes", {"controller": "cx", "path": "GVL_Demo.fReal"})
        self.assertEqual(status, 409)
        status, body = self.req("POST", api + "/nodes", {"controller": "cx", "path": "GVL_Demo.aAxis",
                                                         "address": "axes"})
        self.assertEqual((status, len(body["added"])), (201, 15))
        self.assertEqual(body["added"][0]["address"], "axes/1/fPos")
        status, body = self.req("POST", api + "/nodes", {"controller": "cx", "path": "GVL_Demo.nInt",
                                                         "address": "axes/1/fPos/x"})
        self.assertEqual(status, 400)          # axes/1/fPos is a node: no folder
        deadline = time.time() + 5
        while time.time() < deadline:
            status, nodes = self.req("GET", api + "/nodes?controller=cx", scopes=(config.SCOPE_VIEW,))
            if all(n["ok"] for n in nodes):
                break
            time.sleep(0.05)
        by_addr = {n["address"]: n for n in nodes}
        self.assertTrue(by_addr["GVL_Demo/fReal"]["ok"], by_addr["GVL_Demo/fReal"])
        self.assertAlmostEqual(by_addr["GVL_Demo/fReal"]["value"], 0.1, places=6)
        self.assertEqual(by_addr["axes/2/sName"]["value"], "Axis 2")
        status, body = self.req("GET", api + "/controllers/cx/symbol?path=GVL_Demo.fReal")
        self.assertEqual(body["mapped"], ["GVL_Demo/fReal"])
        status, body = self.req("POST", api + "/nodes/delete", {"controller": "cx", "addresses": ["GVL_Demo/fReal"]})
        self.assertEqual((status, body["deleted"]), (200, 1))
        status, body = self.req("POST", api + "/nodes/delete", {"controller": "cx", "addresses": ["GVL_Demo/fReal"]})
        self.assertEqual(status, 404)

    def test_unavailable_controller_is_503(self):
        api = config.API_PREFIX
        status, body = self.req("POST", api + "/controllers", {"name": "off", "host": "fake-plc", "enabled": False})
        self.assertEqual(status, 201, body)
        try:
            status, body = self.req("GET", api + "/controllers/off/children?path=")
            self.assertEqual(status, 503)
            status, body = self.req("GET", api + "/controllers/nope/children?path=")
            self.assertEqual(status, 404)
        finally:
            self.req("DELETE", api + "/controllers/off")

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
