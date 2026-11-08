"""Provider glue test against the FAKE ctrlxdatalayer in tests/fakes (see tests/fakes/README.md).

Verifies: nodes get registered under DATALAYER_ROOT, reads return the registry value,
writes are validated and mapped to Data Layer result codes, every callback answers once.
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


@unittest.skipIf(REAL, "real ctrlxdatalayer installed - this test targets the fake")
class GlueTest(unittest.TestCase):
    def setUp(self):
        from app import config
        from app.datalayer_service import DataLayerService
        from ctrlxdatalayer import provider as fake_provider

        self.fake_provider = fake_provider
        fake_provider.ALL_PROVIDERS.clear()
        self.config = config
        self._orig = config.datalayer_connection_string
        config.datalayer_connection_string = lambda: "ipc://"
        with open(DEFAULTS_FILE, encoding="utf-8") as f:
            settings = json.load(f)
        settings["general"]["heartbeatIntervalMs"] = 100
        self.svc = DataLayerService(settings, "9.9.9")
        self.svc.start()
        deadline = time.time() + 3
        while time.time() < deadline and not self.svc.status()["connected"]:
            time.sleep(0.02)
        self.assertTrue(self.svc.status()["connected"], self.svc.status())
        self.provider = fake_provider.ALL_PROVIDERS[-1]
        self.root = config.DATALAYER_ROOT

    def tearDown(self):
        self.svc.stop()
        self.config.datalayer_connection_string = self._orig

    def test_registered_and_readable(self):
        from ctrlxdatalayer.variant import Result
        self.assertIn(self.root + "/status/version", self.provider.nodes)
        result, variant = self.provider.read(self.root + "/status/version")
        self.assertEqual(result, Result.OK)
        self.assertEqual(variant.get_string(), "9.9.9")
        result, meta = self.provider.metadata(self.root + "/values/setpoint")
        self.assertTrue(meta.meta["write_allowed"])
        self.assertEqual(meta.meta["type_path"], "types/datalayer/float64")

    def test_write_paths(self):
        from ctrlxdatalayer.variant import Result, Variant
        ok = Variant()
        ok.set_float64(2.5)
        self.assertEqual(self.provider.write(self.root + "/values/setpoint", ok)[0], Result.OK)
        self.assertEqual(self.svc.registry.get("values/setpoint"), 2.5)
        wrong_type = Variant()
        wrong_type.set_string("x")
        self.assertEqual(self.provider.write(self.root + "/values/setpoint", wrong_type)[0], Result.TYPE_MISMATCH)
        too_big = Variant()
        too_big.set_float64(1e12)
        self.assertEqual(self.provider.write(self.root + "/values/setpoint", too_big)[0], Result.INVALID_VALUE)
        ro = Variant()
        ro.set_string("x")
        self.assertEqual(self.provider.write(self.root + "/status/state", ro)[0], Result.PERMISSION_DENIED)
        ro_wrong_type = Variant()
        ro_wrong_type.set_float64(1.0)  # read-only wins over the type mismatch
        self.assertEqual(self.provider.write(self.root + "/status/state", ro_wrong_type)[0], Result.PERMISSION_DENIED)

    def test_exact_types_only(self):
        """A write must come with exactly the node's type - like the X3 answered for REST writes (2026-11-07)."""
        from ctrlxdatalayer.variant import Result, Variant
        setpoint, limits = self.root + "/values/setpoint", self.root + "/values/limits"
        for setter, value in (("set_float32", 1.5), ("set_int32", 1), ("set_int64", 1), ("set_uint8", 1)):
            v = Variant()
            getattr(v, setter)(value)
            self.assertEqual(self.provider.write(setpoint, v)[0], Result.TYPE_MISMATCH, setter)
        ok = Variant()
        ok.set_array_float32([0.1, -2.0])
        result, echoed = self.provider.write(limits, ok)
        self.assertEqual(result, Result.OK)
        self.assertEqual(self.svc.registry.get("values/limits"), (0.10000000149011612, -2.0))
        self.assertEqual(echoed.get_type().name, "ARRAY_FLOAT32")
        result, variant = self.provider.read(limits)
        self.assertEqual((result, variant.get_array_float32()), (Result.OK, [0.10000000149011612, -2.0]))
        as64 = Variant()
        as64.set_array_float64([0.1])
        self.assertEqual(self.provider.write(limits, as64)[0], Result.TYPE_MISMATCH)
        result, meta = self.provider.metadata(limits)
        self.assertEqual(meta.meta["type_path"], "types/datalayer/array-of-float32")

    def test_heartbeat_and_reconnect(self):
        hb1 = self.svc.registry.get("status/heartbeat")
        time.sleep(0.35)
        self.assertGreater(self.svc.registry.get("status/heartbeat"), hb1)
        self.provider.connected = False  # simulate broker loss
        deadline = time.time() + 2
        while time.time() < deadline and self.svc.status()["connected"]:
            time.sleep(0.02)
        self.assertFalse(self.svc.status()["connected"])
        self.assertEqual(self.provider.nodes, {})  # unregistered on disconnect


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
        self.assertEqual(c.read_typed(root + "/values/limits"), ("array-of-float32", [0.0, 100.0]))
        c.write(root + "/values/limits", "array-of-float32", [0.1, 2])
        self.assertEqual(self.svc.registry.get("values/limits"), (0.10000000149011612, 2.0))
        with self.assertRaises(DataLayerError) as ctx:
            c.write(root + "/values/setpoint", "float32", 1.0)          # the node is float64: no conversion
        self.assertEqual(ctx.exception.result, "TYPE_MISMATCH")
        with self.assertRaises(DataLayerError) as ctx:
            c.write(root + "/status/state", "string", "x")
        self.assertEqual(ctx.exception.result, "PERMISSION_DENIED")
        with self.assertRaises(ValueError):
            c.write(root + "/values/setpoint", "int8", 300)              # checked before it is sent
        with self.assertRaises(DataLayerError) as ctx:
            c.read(root + "/nope")
        self.assertEqual(ctx.exception.result, "INVALID_ADDRESS")
        self.assertEqual(c.browse(root + "/values"), ["enabled", "label", "limits", "setpoint"])

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
