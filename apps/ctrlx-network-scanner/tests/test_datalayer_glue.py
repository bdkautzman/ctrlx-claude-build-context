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
        self.started = []

        class Commands:
            def start(inner, profile_id):
                if profile_id != "quick":
                    raise ValueError("unknown scan profile " + profile_id)
                self.started.append(profile_id)

            def abort(inner):
                self.started.append("abort")

        self.svc = DataLayerService(settings, "9.9.9", Commands())
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
        result, meta = self.provider.metadata(self.root + "/scan/start")
        self.assertTrue(meta.meta["write_allowed"])
        self.assertEqual(meta.meta["type_path"], "types/datalayer/string")
        result, meta = self.provider.metadata(self.root + "/last/hosts")
        self.assertFalse(meta.meta["write_allowed"])

    def test_write_paths(self):
        from ctrlxdatalayer.variant import Result, Variant
        ok = Variant()
        ok.set_string("quick")
        self.assertEqual(self.provider.write(self.root + "/scan/start", ok)[0], Result.OK)
        self.assertEqual(self.started, ["quick"])
        unknown = Variant()
        unknown.set_string("nope")   # the command refuses it -> DL_INVALID_VALUE, node keeps "quick"
        self.assertEqual(self.provider.write(self.root + "/scan/start", unknown)[0], Result.INVALID_VALUE)
        self.assertEqual(self.svc.registry.get("scan/start"), "quick")
        wrong_type = Variant()
        wrong_type.set_float64(1.0)
        self.assertEqual(self.provider.write(self.root + "/scan/start", wrong_type)[0], Result.TYPE_MISMATCH)
        abort = Variant()
        abort.set_bool8(True)
        self.assertEqual(self.provider.write(self.root + "/scan/abort", abort)[0], Result.OK)
        self.assertEqual(self.started, ["quick", "abort"])
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


if __name__ == "__main__":
    unittest.main()
