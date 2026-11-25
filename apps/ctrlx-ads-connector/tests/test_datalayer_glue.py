"""Provider glue test against the FAKE ctrlxdatalayer in tests/fakes (see tests/fakes/README.md).

Verifies: nodes get registered under DATALAYER_ROOT, reads return the registry value, the PLC variables
(connections/..., one wildcard node) browse / describe / read / write with exactly their types and the PLC's errors mapped
to Data Layer result codes, every callback answers once.
"""

import importlib
import json
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULTS_FILE = os.path.join(HERE, "..", "defaults", "settings.json")

try:  # use the real package if it is installed (App Build Environment), else the fake
    importlib.import_module("ctrlxdatalayer")
    REAL = True
except Exception:
    sys.path.insert(0, os.path.join(HERE, "fakes"))
    REAL = False


NODES = [
    {"controller": "cx1", "symbol": "MAIN.nInt", "address": "MAIN/nInt", "type": "int16", "writable": True,
     "description": "", "unit": ""},
    {"controller": "cx1", "symbol": "MAIN.aReal", "address": "MAIN/aReal", "type": "array-of-float32",
     "writable": True, "description": "", "unit": ""},
    {"controller": "cx1", "symbol": "MAIN.sText", "address": "text", "type": "string", "writable": False,
     "description": "a text", "unit": ""},
    {"controller": "cx2", "symbol": "GVL.t", "address": "t", "type": "uint32", "writable": True,
     "description": "", "unit": "ms"},
]


@unittest.skipIf(REAL, "real ctrlxdatalayer installed - this test targets the fake")
class GlueTest(unittest.TestCase):
    def setUp(self):
        from app import config
        from app.datalayer_service import DataLayerService
        from app.plc_nodes import NodeMap
        from ctrlxdatalayer import provider as fake_provider

        self.fake_provider = fake_provider
        fake_provider.ALL_PROVIDERS.clear()
        self.config = config
        self._orig = config.datalayer_connection_string
        config.datalayer_connection_string = lambda: "ipc://"
        with open(DEFAULTS_FILE, encoding="utf-8") as f:
            settings = json.load(f)
        settings["general"]["heartbeatIntervalMs"] = 100
        self.writes, self.fail_with = [], None

        def writer(controller, symbol, vtype, value):
            if self.fail_with is not None:
                raise self.fail_with
            self.writes.append((controller, symbol, vtype, value))
        self.nodes = NodeMap(writer)
        self.nodes.configure(NODES)
        self.nodes.update("cx1", "MAIN/nInt", -5)
        self.nodes.update("cx1", "MAIN/aReal", (1.5, 0.10000000149011612))
        self.nodes.set_limits("cx1", "MAIN/aReal", None, 2)
        self.nodes.update("cx1", "text", "hi")
        self.nodes.update("cx2", "t", error="not connected: timeout", offline=True)
        self.svc = DataLayerService(settings, "9.9.9", plc_nodes=self.nodes)
        self.svc.start()
        deadline = time.time() + 3
        while time.time() < deadline and not self.svc.status()["connected"]:
            time.sleep(0.02)
        self.assertTrue(self.svc.status()["connected"], self.svc.status())
        self.provider = fake_provider.ALL_PROVIDERS[-1]
        self.root = config.DATALAYER_ROOT
        self.plc = self.root + "/connections"

    def tearDown(self):
        self.svc.stop()
        self.config.datalayer_connection_string = self._orig

    def call(self, name, address, *args):
        """A callback of the wildcard node, like the broker calls it for any address below connections/."""
        node = self.provider.nodes[self.plc + "/**"]
        out = []
        getattr(node.cbs, name)(None, address, *args,
                                lambda result, data: out.append((result, None if data is None else data.clone()[1])))
        self.assertEqual(len(out), 1, "callback must be called exactly once")
        return out[0]

    def test_registered_and_readable(self):
        from ctrlxdatalayer.variant import Result
        self.assertIn(self.root + "/status/version", self.provider.nodes)
        self.assertIn(self.plc, self.provider.nodes)
        self.assertIn(self.plc + "/**", self.provider.nodes)
        result, variant = self.provider.read(self.root + "/status/version")
        self.assertEqual((result, variant.get_string()), (Result.OK, "9.9.9"))
        result, meta = self.provider.metadata(self.root + "/controllers/configured")
        self.assertEqual(meta.meta["type_path"], "types/datalayer/uint32")
        self.assertEqual(self.svc.status()["registered"], len(self.svc.registry.specs()) + 2)

    def test_plc_tree(self):
        from ctrlxdatalayer.variant import Result
        self.assertEqual(self.call("on_browse", self.plc)[1].get_array_string(), ["cx1", "cx2"])
        self.assertEqual(self.call("on_browse", self.plc + "/cx1")[1].get_array_string(), ["MAIN", "text"])
        self.assertEqual(self.call("on_browse", self.plc + "/cx1/MAIN")[1].get_array_string(), ["aReal", "nInt"])
        self.assertEqual(self.call("on_browse", self.plc + "/cx1/MAIN/nInt")[1].get_array_string(), [])
        self.assertEqual(self.call("on_browse", self.plc + "/nope")[0], Result.INVALID_ADDRESS)
        _, meta = self.call("on_metadata", self.plc + "/cx1/MAIN/aReal")
        self.assertEqual((meta.meta["type_path"], meta.meta["write_allowed"]),
                         ("types/datalayer/array-of-float32", True))
        _, meta = self.call("on_metadata", self.plc + "/cx1/text")
        self.assertEqual((meta.meta["type_path"], meta.meta["write_allowed"]), ("types/datalayer/string", False))
        _, meta = self.call("on_metadata", self.plc + "/cx1")
        self.assertEqual(meta.meta["type_path"], "")
        self.assertEqual(self.call("on_metadata", self.plc + "/cx1/nope")[0], Result.INVALID_ADDRESS)
        result, v = self.call("on_read", self.plc + "/cx1/MAIN/nInt", None)
        self.assertEqual((result, v.get_type().name, v.get_int16()), (Result.OK, "INT16", -5))
        result, v = self.call("on_read", self.plc + "/cx1/MAIN/aReal", None)
        self.assertEqual((result, v.get_type().name, v.get_array_float32()),
                         (Result.OK, "ARRAY_FLOAT32", [1.5, 0.10000000149011612]))
        self.assertEqual(self.call("on_read", self.plc + "/cx2/t", None)[0], Result.COMMUNICATION_ERROR)
        self.nodes.update("cx1", "text", error="symbol not found (0x710)")
        self.assertEqual(self.call("on_read", self.plc + "/cx1/text", None)[0], Result.RESOURCE_UNAVAILABLE)
        self.assertEqual(self.call("on_read", self.plc + "/cx1/MAIN", None)[0], Result.INVALID_ADDRESS)

    def test_plc_writes(self):
        """Exactly the node's type, read-only first, range, then the PLC's answer."""
        from ctrlxdatalayer.variant import Result, Variant
        from app.ads_link import AdsError
        from app.ads_types import AdsTypeError
        from app.controllers import ControllerUnavailable

        def var(setter, value):
            v = Variant()
            getattr(v, setter)(value)
            return v
        n_int = self.plc + "/cx1/MAIN/nInt"
        result, echoed = self.call("on_write", n_int, var("set_int16", 7))
        self.assertEqual((result, echoed.get_int16()), (Result.OK, 7))
        self.assertEqual(self.writes[-1], ("cx1", "MAIN.nInt", "int16", 7))
        self.assertEqual(self.nodes.read("cx1/MAIN/nInt"), ("int16", 7))
        for setter, value in (("set_int32", 7), ("set_float64", 7.0), ("set_uint16", 7), ("set_string", "7")):
            self.assertEqual(self.call("on_write", n_int, var(setter, value))[0], Result.TYPE_MISMATCH, setter)
        self.assertEqual(self.call("on_write", n_int, var("set_int16", 40000))[0], Result.INVALID_VALUE)
        self.assertEqual(self.call("on_write", self.plc + "/cx1/text", var("set_int16", 1))[0],
                         Result.PERMISSION_DENIED)                   # read-only before the type mismatch
        self.assertEqual(self.call("on_write", self.plc + "/cx1", var("set_int16", 1))[0], Result.INVALID_ADDRESS)
        arr = self.plc + "/cx1/MAIN/aReal"
        self.assertEqual(self.call("on_write", arr, var("set_array_float32", [1, 2]))[0], Result.OK)
        self.assertEqual(self.writes[-1], ("cx1", "MAIN.aReal", "array-of-float32", [1.0, 2.0]))
        self.assertEqual(self.call("on_write", arr, var("set_array_float32", [1, 2, 3]))[0], Result.INVALID_VALUE)
        self.assertEqual(self.call("on_write", arr, var("set_array_float64", [1, 2]))[0], Result.TYPE_MISMATCH)
        for exc, expected in ((AdsError(0x704), Result.PERMISSION_DENIED), (AdsError(0x745), Result.TIMEOUT),
                              (AdsError(None, "gone", transport=True), Result.COMMUNICATION_ERROR),
                              (AdsError(0x710), Result.FAILED), (AdsTypeError("too long"), Result.INVALID_VALUE),
                              (ControllerUnavailable("off"), Result.COMMUNICATION_ERROR)):
            self.fail_with = exc
            self.assertEqual(self.call("on_write", n_int, var("set_int16", 1))[0], expected, exc)
        self.assertEqual(self.nodes.read("cx1/MAIN/nInt"), ("int16", 7))   # a failed write changes nothing

    def test_heartbeat_and_reconnect(self):
        hb1 = self.svc.registry.get("status/heartbeat")
        time.sleep(0.35)
        self.assertGreater(self.svc.registry.get("status/heartbeat"), hb1)
        self.provider.connected = False  # simulate broker loss
        deadline = time.time() + 2
        while time.time() < deadline and self.svc.status()["connected"]:
            time.sleep(0.02)
        self.assertFalse(self.svc.status()["connected"])
        self.assertEqual(self.provider.nodes, {})  # unregistered on disconnect (the plc tree too)


@unittest.skipIf(REAL, "real ctrlxdatalayer installed - this test targets the fake")
class ClientGlueTest(unittest.TestCase):
    """DataLayerService(on_client=...) hands out a DataLayerClient while connected; reads and writes through the
    (fake) broker reach the provider glue with exactly the given type."""

    def setUp(self):
        from app import config
        from app.datalayer_service import DataLayerService
        from ctrlxdatalayer import provider as fake_provider

        fake_provider.ALL_PROVIDERS.clear()
        self.config, self._orig = config, config.datalayer_connection_string
        config.datalayer_connection_string = lambda: "ipc://"
        with open(DEFAULTS_FILE, encoding="utf-8") as f:
            settings = json.load(f)
        settings["general"]["heartbeatIntervalMs"] = 100
        self.clients = []
        self.svc = DataLayerService(settings, "9.9.9", on_client=self.clients.append)
        self.svc.start()
        deadline = time.time() + 3
        while time.time() < deadline and not self.clients:
            time.sleep(0.02)
        self.assertTrue(self.clients and self.clients[-1] is not None)
        self.client, self.provider = self.clients[-1], fake_provider.ALL_PROVIDERS[-1]
        self.root = config.DATALAYER_ROOT

    def tearDown(self):
        self.svc.stop()
        self.config.datalayer_connection_string = self._orig

    def test_read_write_browse(self):
        from app.datalayer_client import DataLayerError
        c, root = self.client, self.root
        self.assertEqual(c.read(root + "/status/version"), "9.9.9")
        self.svc.registry.set_internal("controllers/configured", 3)
        self.assertEqual(c.read_typed(root + "/controllers/configured"), ("uint32", 3))
        with self.assertRaises(DataLayerError) as ctx:
            c.write(root + "/status/state", "string", "x")
        self.assertEqual(ctx.exception.result, "PERMISSION_DENIED")
        with self.assertRaises(ValueError):
            c.write(root + "/controllers/configured", "int8", 300)    # checked before it is sent
        with self.assertRaises(DataLayerError) as ctx:
            c.read(root + "/nope")
        self.assertEqual(ctx.exception.result, "INVALID_ADDRESS")
        self.assertEqual(c.browse(root + "/controllers"), ["configured", "connected", "summary"])

    def test_gone_on_disconnect(self):
        self.provider.connected = False
        deadline = time.time() + 2
        while time.time() < deadline and self.clients[-1] is not None:
            time.sleep(0.02)
        self.assertIsNone(self.clients[-1])


class ClientHelpersTest(unittest.TestCase):
    def test_names(self):
        from app.datalayer_client import DataLayerError, normalize_result, type_name
        self.assertEqual(normalize_result("DL_INVALID_ADDRESS"), "INVALID_ADDRESS")          # gotchas #78
        self.assertEqual(normalize_result("sec_noSEC_NO_TOKEN_token"), "SEC_NO_TOKEN")
        self.assertEqual(DataLayerError("DL_TYPE_MISMATCH").result, "TYPE_MISMATCH")
        for variant_name, name in (("FLOAT32", "float32"), ("ARRAY_INT16", "array-of-int16"),
                                   ("ARRAY_OF_TIMESTAMP", "array-of-timestamp"), ("UNKNON", "unknown"),
                                   ("FLATBUFFERS", "flatbuffers")):
            self.assertEqual(type_name(variant_name), name)


class DownTimeLogTest(unittest.TestCase):
    """No ERROR in the Logbook while a missing Data Layer is expected (first start, state switch, gotchas #28)."""

    def test_levels(self):
        from app.datalayer_service import DownTimeLog
        now = [100.0]
        down = DownTimeLog(grace_s=60, repeat_s=600, clock=lambda: now[0])

        def levels(text):
            with self.assertLogs("datalayer", level="DEBUG") as logs:
                down.failed(text)
            return [r.levelname for r in logs.records]

        self.assertEqual(levels("start failed"), ["INFO"])            # right after the app start
        now[0] += 59
        self.assertEqual(levels("start failed"), ["INFO"])
        now[0] += 2
        self.assertEqual(levels("start failed"), ["ERROR"])           # lasts > 60 s
        now[0] += 5
        self.assertEqual(levels("start failed"), ["DEBUG"])           # not at every retry
        now[0] += 600
        self.assertEqual(levels("start failed"), ["ERROR"])           # again after 10 min
        with self.assertLogs("datalayer", level="INFO") as logs:
            down.up()
        self.assertIn("connected again", logs.output[0])
        now[0] += 1000
        down.down()                                                     # e.g. SERVICE <-> OPERATING
        now[0] += 10
        self.assertEqual(levels("start failed"), ["INFO"])


if __name__ == "__main__":
    unittest.main()
