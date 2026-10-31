"""KVD node editing tests: nodes.json entries, local file, remote WebDAV file, add/remove with the
Data Layer check (simulated KVD in tests/fake_adapter.py). No ctrlX needed."""

import json
import os
import stat
import tempfile
import unittest

from app import kvd
from app.kvd import KvdError, KvdService, LocalNodesFile, RemoteNodesFile, build_entry
from app.rest_client import CoreRestClient, RestError, _token_expiry
from tests.fake_adapter import FakeAdapter

R = "devices/remotes/cm-a/"
SAMPLE = {"$schema": "./schemas/nodes.v1.0.schema.json", "description": "keep me",
          "types": [{"address": "types/x", "type": "x.bfbs", "metadata": {"nodeClass": "Type"}}],
          "nodes": [{"address": "existing/var", "type": "int32", "value": 1, "metadata": {"nodeClass": "Variable"}}]}


class EntryTest(unittest.TestCase):
    def test_variable(self):
        e = build_entry({"address": "/plant/speed/", "nodeClass": "Variable", "type": "int16", "value": "-5",
                         "unit": "mm/s", "description": " Speed ", "readOnly": True})
        self.assertEqual(e, {"address": "plant/speed", "type": "int16", "value": -5,
                             "metadata": {"nodeClass": "Variable", "unit": "mm/s", "operations": {"write": False},
                                          "description": "Speed"}})
        e = build_entry({"address": "a/b", "type": "arstring", "value": '["x", "y"]'})
        self.assertEqual(e["value"], ["x", "y"])
        self.assertNotIn("value", build_entry({"address": "a/c", "type": "float64", "value": ""}))

    def test_folder_ignores_value_fields(self):
        e = build_entry({"address": "plant", "nodeClass": "Folder", "type": "int8", "value": "x", "unit": "u",
                         "readOnly": True, "description": "Line 1"})
        self.assertEqual(e, {"address": "plant", "metadata": {"nodeClass": "Folder", "description": "Line 1"}})

    def test_errors_name_the_fields(self):
        with self.assertRaises(KvdError) as ctx:
            build_entry({"address": "bad name/x*", "type": "int8", "value": "300", "unit": "u" * 40})
        fields = sorted(e.split(":")[0] for e in ctx.exception.errors)
        self.assertEqual(fields, ["address", "unit", "value"])
        with self.assertRaises(KvdError) as ctx:
            build_entry({"address": "a", "nodeClass": "Method"})
        self.assertEqual(ctx.exception.errors, ["nodeClass: Variable or Folder"])
        with self.assertRaises(KvdError) as ctx:
            build_entry({"address": "a", "type": "types/custom"})
        self.assertEqual(ctx.exception.errors[0].split(":")[0], "type")
        with self.assertRaises(KvdError):
            build_entry({"address": "a//b", "type": "int8"})

    def test_64_bit_initial_values_kvd_can_take(self):
        # KVD reads numbers as float64: 2^64-1 made it exit on the COREvirtual (gotchas #80)
        self.assertEqual(build_entry({"address": "a", "type": "uint64", "value": str(2 ** 53 - 1)})["value"], 2 ** 53 - 1)
        for vtype, value in (("uint64", str(2 ** 64 - 1)), ("int64", str(-2 ** 53 - 1)), ("aruint64", "1, 9007199254740993")):
            with self.assertRaises(KvdError) as ctx:
                build_entry({"address": "a", "type": vtype, "value": value})
            self.assertIn("up to 9007199254740991", ctx.exception.errors[0])
        self.assertEqual(build_entry({"address": "a", "type": "uint32", "value": "4294967295"})["value"], 2 ** 32 - 1)


class LocalFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "kvd", "nodes.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_folder_and_file(self):
        with self.assertRaises(KvdError) as ctx:
            LocalNodesFile(self.path).load()
        self.assertEqual(ctx.exception.status, 503)
        os.makedirs(os.path.dirname(self.path))
        self.assertEqual(LocalNodesFile(self.path).load()["nodes"], [])

    def test_roundtrip_keeps_everything_and_mode(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            json.dump(SAMPLE, f)
        os.chmod(self.path, 0o600)
        store = LocalNodesFile(self.path)
        doc = store.load()
        doc["nodes"].append({"address": "n"})
        store.save(doc)
        again = store.load()
        self.assertEqual((again["description"], again["types"], len(again["nodes"])), ("keep me", SAMPLE["types"], 2))
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["nodes.json"])   # no temp file left
        with open(self.path, "rb") as f:
            self.assertTrue(f.read().endswith(b"}\n"))

    def test_invalid_json(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            f.write("{broken")
        with self.assertRaises(KvdError) as ctx:
            LocalNodesFile(self.path).load()
        self.assertEqual(ctx.exception.status, 502)


class FakeRest:
    def __init__(self, status=200, body=None):
        self.status, self.body, self.calls = status, body, []

    def request(self, method, path, data=None, content_type=None):
        self.calls.append((method, path, data, content_type))
        if method == "PUT":
            self.body = data
            return 201, b"Created"
        return self.status, self.body


class RemoteFileTest(unittest.TestCase):
    def test_load_save(self):
        rest = FakeRest(200, json.dumps(SAMPLE).encode())
        store = RemoteNodesFile(rest)
        doc = store.load()
        doc["nodes"].append({"address": "n"})
        store.save(doc)
        self.assertEqual(rest.calls[-1][:2], ("PUT", "/solutions/webdav/appdata/kvd/nodes.json"))
        self.assertEqual(rest.calls[-1][3], "application/json")
        self.assertEqual(len(json.loads(rest.body)["nodes"]), 2)

    def test_missing_and_forbidden(self):
        self.assertEqual(RemoteNodesFile(FakeRest(404, b"")).load()["nodes"], [])
        for status, expected in ((403, 403), (500, 502)):
            with self.assertRaises(KvdError) as ctx:
                RemoteNodesFile(FakeRest(status, b"")).load()
            self.assertEqual(ctx.exception.status, expected)


class RestClientTest(unittest.TestCase):
    def make(self, answers):
        client = CoreRestClient("10.0.0.1", 8443, "u", "p", True)
        calls = []

        def fake_open(method, path, data=None, headers=None):
            calls.append((method, path, (headers or {}).get("Authorization")))
            return answers.pop(0)
        client._open = fake_open
        return client, calls

    def test_login_once_and_retry_after_401(self):
        token = "h." + "eyJleHAiOjQxMDI0NDQ4MDB9" + ".s"   # {"exp": 4102444800}
        self.assertEqual(_token_expiry(token), 4102444800)
        login = (201, json.dumps({"access_token": token}).encode())
        client, calls = self.make([login, (200, b"a"), (200, b"b"), (401, b""), login, (200, b"c")])
        self.assertEqual(client.base, "https://10.0.0.1:8443")
        self.assertEqual(client.request("GET", "/x"), (200, b"a"))
        self.assertEqual(client.request("GET", "/x"), (200, b"b"))      # token reused
        self.assertEqual(client.request("GET", "/x"), (200, b"c"))      # 401 -> new login, one retry
        self.assertEqual([c[1] for c in calls].count("/identity-manager/api/v2/auth/token"), 2)

    def test_login_rejected(self):
        client, _ = self.make([(401, b"")])
        with self.assertRaises(RestError) as ctx:
            client.request("GET", "/x")
        self.assertEqual(ctx.exception.status, 401)


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "kvd", "nodes.json")
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            json.dump(SAMPLE, f)
        self.dl = FakeAdapter({"kvd/value-count": {"nodeClass": "Variable", "type": "uint32", "value": 1},
                               "system/info": {"nodeClass": "Resource", "readType": "types/x", "value": {}}},
                              kvd_file=self.path)
        self.svc = KvdService(lambda: self.dl, self.path, apply_timeout_s=0.3)

    def tearDown(self):
        self.tmp.cleanup()

    def test_status(self):
        self.assertEqual(self.svc.status("", None),
                         {"available": True, "running": True, "nodes": ["existing/var"], "error": None})
        self.dl.nodes.pop("kvd/value-count")
        self.assertFalse(self.svc.status("", None)["available"])
        # installed but restarting (its own nodes are gone for ~1 s after every change)
        self.dl.nodes["system/apps/installed/rexroth-kvd"] = {"nodeClass": "Resource", "readType": "types/x", "value": {}}
        status = self.svc.status("", None)
        self.assertEqual((status["available"], status["running"]), (True, False))
        with self.assertRaises(KvdError) as ctx:
            self.svc.add("", None, {"address": "a", "type": "int8"}, "_local")
        self.assertEqual(ctx.exception.status, 503)

    def test_missed_change_is_saved_again(self):
        self.svc.status("", None)
        self.dl.kvd_miss = 1          # KVD was restarting when the file changed
        self.svc.add("", None, {"address": "plant/speed", "type": "int32"}, "_local")
        self.assertEqual(self.dl.read_json("plant/speed"), {"type": "int32", "value": 0})
        self.dl.kvd_miss = 1
        res = self.svc.remove("", None, "plant/speed", "_local")
        self.assertTrue(res["gone"])

    def test_add_and_remove(self):
        entry = self.svc.add("", None, {"address": "plant/speed", "type": "int32", "value": "3"}, "_local")
        self.assertEqual(entry["value"], 3)
        self.assertIn("plant/speed", self.svc.status("", None)["nodes"])
        self.assertEqual(self.dl.read_json("plant/speed"), {"type": "int32", "value": 3})
        with self.assertRaises(KvdError) as ctx:   # exists in the Data Layer now
            self.svc.add("", None, {"address": "plant/speed", "type": "int32"}, "_local")
        self.assertEqual(ctx.exception.status, 409)
        with self.assertRaises(KvdError) as ctx:   # a node of another app
            self.svc.add("", None, {"address": "system/info", "type": "int32"}, "_local")
        self.assertEqual(ctx.exception.status, 409)
        res = self.svc.remove("", None, "plant/speed", "_local")
        self.assertEqual((res["removed"], res["gone"]), ("plant/speed", True))
        with open(self.path) as f:
            doc = json.load(f)
        self.assertEqual(([n["address"] for n in doc["nodes"]], doc["description"]), (["existing/var"], "keep me"))
        with self.assertRaises(KvdError) as ctx:
            self.svc.remove("", None, "plant/speed", "_local")
        self.assertEqual(ctx.exception.status, 404)

    def test_folder_with_defined_children_stays(self):
        self.svc.add("", None, {"address": "line", "nodeClass": "Folder"}, "_local")
        self.svc.add("", None, {"address": "line/x", "type": "bool8"}, "_local")
        res = self.svc.remove("", None, "line", "_local")
        self.assertFalse(res["gone"])
        self.assertIn("still defined", res["note"])

    def test_not_created_by_kvd_is_rolled_back(self):
        self.dl.kvd_ignores = True
        with self.assertRaises(KvdError) as ctx:
            self.svc.add("", None, {"address": "never", "type": "int8"}, "_local")
        self.assertEqual(ctx.exception.status, 502)
        with open(self.path) as f:
            self.assertEqual([n["address"] for n in json.load(f)["nodes"]], ["existing/var"])

    def test_kvd_not_running(self):
        self.dl.nodes.pop("kvd/value-count")
        with self.assertRaises(KvdError) as ctx:
            self.svc.add("", None, {"address": "a", "type": "int8"}, "_local")
        self.assertEqual(ctx.exception.status, 409)

    def test_remote_core_uses_rest_client_of_its_settings(self):
        core = {"name": "a", "host": "10.0.0.9", "sslPort": 443, "user": "u", "password": "p", "skipCertificateCheck": True}
        store = self.svc._store(core)
        self.assertIsInstance(store, RemoteNodesFile)
        self.assertIs(self.svc._store(core).client, store.client)             # cached
        changed = dict(core, password="new")
        self.assertIsNot(self.svc._store(changed).client, store.client)       # new login data -> new client
        self.assertEqual(kvd.WEBDAV_NODES, "/solutions/webdav/appdata/kvd/nodes.json")


if __name__ == "__main__":
    unittest.main()
