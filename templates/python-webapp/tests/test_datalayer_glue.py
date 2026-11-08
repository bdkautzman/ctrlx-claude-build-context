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
