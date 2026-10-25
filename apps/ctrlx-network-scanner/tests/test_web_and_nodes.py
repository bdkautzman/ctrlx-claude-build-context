"""Web API, auth, node registry and scan manager tests (no ctrlX needed; scans only touch 127.0.0.1)."""

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
from app.history import History
from app.scan_manager import BusyError, ScanManager, UnknownProfileError
from app.settings_rules import RULES, validate_settings
from app.settings_store import SettingsStore
from app.web_server import WebServer
from tests.helpers import DEFAULTS_FILE, FakeEnv, TcpServer, load_defaults, profile


def make_token(scopes, exp_offset=3600, name="tester"):
    def b64(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    return "{}.{}.sig".format(b64({"alg": "none"}), b64({"scope": scopes, "exp": time.time() + exp_offset, "name": name}))


class AuthTest(unittest.TestCase):
    def test_scopes(self):
        headers = {"Authorization": "Bearer " + make_token([config.SCOPE_READ])}
        self.assertTrue(auth.can_read(auth.scopes_from_headers(headers)))
        self.assertFalse(auth.can_write(auth.scopes_from_headers(headers)))
        with self.assertRaises(auth.AuthError) as ctx:
            auth.require(headers, write=True)
        self.assertEqual(ctx.exception.status, 403)
        self.assertEqual(auth.user_name(headers), "tester")

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

    def test_app_specs_are_valid(self):
        reg = NodeRegistry(config.DATALAYER_ROOT, build_node_specs(load_defaults(), "1.0.0"))
        for name in reg.snapshot():
            self.assertRegex(name, r"^[a-z0-9-]+(/[a-z0-9-]+)*$", "use lowercase-dash node names")

    def test_start_node_rejects_without_storing(self):
        class Commands:
            started = []

            def start(self, pid):
                if pid != "quick":
                    raise UnknownProfileError("unknown scan profile " + pid)
                self.started.append(pid)

            def abort(self):
                pass
        reg = NodeRegistry("r", build_node_specs(load_defaults(), "1", Commands()))
        self.assertEqual(reg.write("scan/start", "quick"), "quick")
        with self.assertRaises(NodeWriteError):
            reg.write("scan/start", "nope")
        self.assertEqual(reg.get("scan/start"), "quick")
        self.assertEqual(reg.write("scan/start", ""), "")   # clearing is allowed, starts nothing
        self.assertEqual(Commands.started, ["quick"])


class Fixture:
    """Settings store + history + manager + Data Layer registry (+ web server) in a temp folder."""

    def __init__(self, with_web=False):
        self.tmp = tempfile.TemporaryDirectory()
        active = os.path.join(self.tmp.name, "activeConfiguration")
        os.makedirs(active)
        self.store = SettingsStore(os.path.join(active, config.SNAP_NAME, "settings.json"), DEFAULTS_FILE, active,
                                   RULES, validator=validate_settings)
        self.store.load_from_disk(create_if_missing=True)
        self.dl = DataLayerService(self.store.get(), "test")
        self.history = History(os.path.join(self.tmp.name, "history"))
        self.manager = ScanManager(self.store, self.history, registry=self.dl.registry, env=FakeEnv())
        self.manager.load()
        self.store.add_listener(self.manager.on_settings_changed)
        self.web = None
        if with_web:
            self._port = config.DEV_HTTP_PORT
            config.DEV_HTTP_PORT = 0  # let the OS pick a free port
            self.web = WebServer(self.store, self.dl, self.manager)
            self.web.start()
            self.port = self.web._server.server_address[1]

    def add_profile(self, **kw):
        settings = self.store.get()
        settings["profiles"].append(profile(**kw))
        self.store.update(settings)

    def close(self):
        if self.web:
            self.web.stop()
            config.DEV_HTTP_PORT = self._port
        self.tmp.cleanup()


class ManagerTest(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.server = TcpServer()
        self.f.add_profile(tcpPorts=str(self.server.port))
        s = self.f.store.get()
        s["lists"]["blacklist"] = [{"hosts": "", "ports": "tcp/{}".format(self.server.port), "note": "test port"}]
        self.f.store.update(s)

    def tearDown(self):
        self.server.close()
        self.f.close()

    def test_scan_stored_rated_and_published(self):
        m, reg = self.f.manager, self.f.dl.registry
        events = []
        m.add_listener(lambda e, r: events.append(r))
        scan_id = m.start("test", user="tester")
        with self.assertRaises(BusyError):
            m.start("test")
        m.wait(30)
        self.assertEqual(self.f.history.list()[0]["id"], scan_id)
        self.assertEqual(self.f.history.list()[0]["alarmsAtScan"], 1)
        self.assertEqual(reg.get("last/id"), scan_id)
        self.assertEqual(reg.get("last/alarms"), 1)
        self.assertEqual(reg.get("scan/state"), "idle")
        hosts = json.loads(reg.get("last/hosts"))
        self.assertEqual(hosts[0]["forbidden"], ["tcp/{}".format(self.server.port)])
        self.assertEqual(json.loads(reg.get("last/alarm-list"))[0]["note"], "test port")
        self.assertEqual(events[0]["user"], "tester")
        # whitelisting the finding takes effect at once (rated with the current lists)
        s = self.f.store.get()
        s["lists"]["blacklist"] = []
        s["lists"]["whitelist"] = [{"hosts": "127.0.0.1", "ports": "tcp/{}".format(self.server.port), "note": "ok"}]
        self.f.store.update(s)
        self.assertEqual(reg.get("last/alarms"), 0)
        self.assertEqual(m.rated(scan_id)["summary"]["rating"]["ok"], 1)
        self.assertEqual(m.rated(scan_id)["summary"]["ratingAtScan"]["forbidden"], 1)
        # second scan: compared with the first
        m.start("test")
        m.wait(30)
        self.assertEqual(m.last()["changes"]["previousId"], scan_id)
        self.assertEqual(reg.get("last/changes"), 0)
        self.assertEqual(reg.get("history/count"), 2)

    def test_unknown_profile_and_abort_without_scan(self):
        with self.assertRaises(UnknownProfileError):
            self.f.manager.start("nope")
        self.assertFalse(self.f.manager.abort())

    def test_schedule_fires_and_queues(self):
        from datetime import datetime, timedelta, timezone
        from app.scheduler import Scheduler
        now = [datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc)]
        self.f.manager.scheduler = Scheduler(clock=lambda: now[0])
        s = self.f.store.get()
        for p in s["profiles"]:
            if p["id"] == "test":
                p["scheduleMode"], p["scheduleIntervalMinutes"] = "interval", 5
        self.f.store.update(s)
        self.f.manager.tick()
        self.assertFalse(self.f.manager.current()["running"])
        now[0] += timedelta(seconds=61)
        self.f.manager.tick()
        self.assertTrue(self.f.manager.current()["running"])
        self.assertEqual(self.f.manager.current()["progress"]["trigger"], "schedule")
        self.f.manager.wait(30)
        self.assertIn("test", json.loads(self.f.dl.registry.get("schedule/next")))


class WebApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = Fixture(with_web=True)
        cls.server = TcpServer(greeting=b"SSH-2.0-test\r\n")
        cls.f.add_profile(tcpPorts=str(cls.server.port))
        cls.port = cls.f.port

    @classmethod
    def tearDownClass(cls):
        cls.server.close()
        cls.f.close()

    def req(self, method, path, body=None, scopes=(config.SCOPE_WRITE,)):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Authorization": "Bearer " + make_token(list(scopes))} if scopes is not None else {}
        data = json.dumps(body) if body is not None else None
        conn.request(method, path, body=data, headers=headers)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        ctype = res.getheader("Content-Type", "")
        return res.status, (json.loads(raw) if raw and ctype.startswith("application/json") else raw), res

    def run_scan(self):
        status, body, _ = self.req("POST", config.API_PREFIX + "/scans", {"profile": "test"})
        self.assertEqual(status, 202, body)
        self.f.manager.wait(30)
        return body["id"]

    def test_static_index_without_token(self):
        status, body, _ = self.req("GET", config.URL_PREFIX + "/", scopes=None)
        self.assertEqual(status, 200)
        self.assertIn(b"<html", body)

    def test_static_traversal_blocked(self):
        self.assertEqual(self.req("GET", config.URL_PREFIX + "/../app/config.py", scopes=None)[0], 404)

    def test_api_requires_token_and_write_scope(self):
        self.assertEqual(self.req("GET", config.API_PREFIX + "/scans", scopes=None)[0], 401)
        for method, path, body in (("POST", "/scans", {"profile": "test"}), ("POST", "/scans/current/abort", None),
                                   ("DELETE", "/scans/x", None), ("PUT", "/settings", {})):
            self.assertEqual(self.req(method, config.API_PREFIX + path, body, scopes=(config.SCOPE_READ,))[0], 403, path)

    def test_scan_lifecycle_and_downloads(self):
        scan_id = self.run_scan()
        status, body, _ = self.req("GET", config.API_PREFIX + "/scans", scopes=(config.SCOPE_READ,))
        self.assertIn(scan_id, [s["id"] for s in body["scans"]])
        status, body, _ = self.req("GET", config.API_PREFIX + "/scans/" + scan_id, scopes=(config.SCOPE_READ,))
        self.assertEqual(body["hosts"][0]["ports"][0]["service"], "ssh")
        self.assertEqual(body["user"], "tester")
        self.assertEqual(body["ratedWith"], "current lists")
        status, data, res = self.req("GET", config.API_PREFIX + "/scans/{}/report.pdf".format(scan_id), scopes=(config.SCOPE_READ,))
        self.assertEqual((status, res.getheader("Content-Type")), (200, "application/pdf"))
        self.assertTrue(data.startswith(b"%PDF-"))
        self.assertIn(scan_id, res.getheader("Content-Disposition"))
        status, data, _ = self.req("GET", config.API_PREFIX + "/scans/{}/export.csv".format(scan_id), scopes=(config.SCOPE_READ,))
        lines = data.decode("utf-8-sig").splitlines()
        self.assertTrue(lines[0].startswith("scan,ip,mac"))
        self.assertIn("127.0.0.1", lines[1])
        status, data, _ = self.req("GET", config.API_PREFIX + "/scans/{}/export.json".format(scan_id), scopes=(config.SCOPE_READ,))
        self.assertEqual(data["id"], scan_id)   # req() already parsed the JSON download
        status, body, _ = self.req("GET", config.API_PREFIX + "/status", scopes=(config.SCOPE_READ,))
        self.assertEqual(body["last"]["id"], scan_id)
        self.assertFalse(body["canWrite"])
        self.assertEqual(self.req("DELETE", config.API_PREFIX + "/scans/" + scan_id)[0], 200)
        self.assertEqual(self.req("GET", config.API_PREFIX + "/scans/" + scan_id)[0], 404)
        self.assertEqual(self.req("GET", config.API_PREFIX + "/scans/" + scan_id + "/x.pdf")[0], 404)

    def test_start_errors(self):
        self.assertEqual(self.req("POST", config.API_PREFIX + "/scans", {"profile": "nope"})[0], 404)
        self.assertEqual(self.req("POST", config.API_PREFIX + "/scans", {"x": 1})[0], 400)
        self.assertEqual(self.req("POST", config.API_PREFIX + "/scans/current/abort")[0], 409)
        status, body, _ = self.req("GET", config.API_PREFIX + "/scans/current?hosts=1", scopes=(config.SCOPE_READ,))
        self.assertEqual(body["running"], False)

    def test_estimate_and_network(self):
        status, body, _ = self.req("POST", config.API_PREFIX + "/estimate",
                                   {"targets": "10.0.0.0/24, 10.0.1.5", "exclude": "", "tcpPorts": "common", "udpPorts": "x"},
                                   scopes=(config.SCOPE_READ,))
        self.assertEqual(body["addresses"], 255)
        self.assertGreater(body["tcpPorts"], 50)
        self.assertTrue(any(e.startswith("udpPorts") for e in body["errors"]))
        status, body, _ = self.req("GET", config.API_PREFIX + "/network", scopes=(config.SCOPE_READ,))
        self.assertIn("interfaces", body)

    def test_settings_validation_errors(self):
        settings = self.f.store.get()
        settings["profiles"][0]["targets"] = "999.1.1.1"
        status, body, _ = self.req("PUT", config.API_PREFIX + "/settings", settings)
        self.assertEqual(status, 400)
        self.assertTrue(any(e.startswith("profiles[0].targets") for e in body["errors"]))

    def test_values_include_scan_nodes(self):
        status, body, _ = self.req("GET", config.API_PREFIX + "/values", scopes=(config.SCOPE_READ,))
        self.assertIn("scan/start", body)
        self.assertTrue(body["scan/start"]["writable"])

    def test_appdata_endpoints(self):
        self.assertEqual(self.req("POST", config.API_PREFIX + "/appdata/save", {"phase": "save", "id": "t"})[0], 204)
        self.assertEqual(self.req("POST", config.API_PREFIX + "/appdata/load", {"phase": "query", "id": "t"},
                                  scopes=(config.SCOPE_READ,))[0], 403)
        self.assertEqual(self.req("POST", config.API_PREFIX + "/appdata/load", {"phase": "abort", "id": "t"})[0], 204)


if __name__ == "__main__":
    unittest.main()
