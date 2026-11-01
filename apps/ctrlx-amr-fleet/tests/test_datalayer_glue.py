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
        from app.fleet import FleetState
        self.fleet = FleetState()
        self.svc = DataLayerService(settings, "9.9.9", on_fleet_write=self.fleet.receive)
        self.fleet.add_listener(self.svc.on_fleet_changed)
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
        result, meta = self.provider.metadata(self.root + "/fleet/amrs")
        self.assertTrue(meta.meta["write_allowed"])
        self.assertEqual(meta.meta["type_path"], "types/datalayer/string")
        result, meta = self.provider.metadata(self.root + "/fleet/count")
        self.assertFalse(meta.meta["write_allowed"])

    def test_write_paths(self):
        from ctrlxdatalayer.variant import Result, Variant
        fleet_json = '{"amrs": [{"id": "AMR-01", "x": 1.0, "y": 2.0, "rotation": 90}]}'
        ok = Variant()
        ok.set_string(fleet_json)
        self.assertEqual(self.provider.write(self.root + "/fleet/amrs", ok)[0], Result.OK)
        self.assertEqual(self.svc.registry.get("fleet/amrs"), fleet_json)
        self.assertEqual(self.svc.registry.get("fleet/count"), 1)
        self.assertEqual(self.provider.read(self.root + "/fleet/count")[1].get_uint64(), 1)
        invalid = Variant()
        invalid.set_string('{"amrs": [{"id": "AMR-01", "x": 1.0}]}')
        self.assertEqual(self.provider.write(self.root + "/fleet/amrs", invalid)[0], Result.INVALID_VALUE)
        self.assertEqual(self.svc.registry.get("fleet/amrs"), fleet_json)  # rejected value not stored
        self.assertIn("amrs[0].y: missing", self.svc.registry.get("fleet/last-error"))
        self.assertEqual(self.svc.registry.get("fleet/rejected"), 1)
        wrong_type = Variant()
        wrong_type.set_float64(1.0)
        self.assertEqual(self.provider.write(self.root + "/fleet/amrs", wrong_type)[0], Result.TYPE_MISMATCH)
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



@unittest.skipIf(REAL, "real ctrlxdatalayer installed - this test targets the fake")
class AmrTreeTest(unittest.TestCase):
    """fleet/amr/** (per-AMR input modes): one wildcard node, registered only in those modes."""

    def setUp(self):
        from app import config
        from app.amr_inputs import AmrInputs
        from app.datalayer_service import DataLayerService
        from app.fleet import FleetState
        from ctrlxdatalayer import provider as fake_provider

        fake_provider.ALL_PROVIDERS.clear()
        self.config = config
        self._orig = config.datalayer_connection_string
        config.datalayer_connection_string = lambda: "ipc://"
        with open(DEFAULTS_FILE, encoding="utf-8") as f:
            settings = json.load(f)
        self.fleet = FleetState()
        self.inputs = AmrInputs(self.fleet, start_thread=False)
        self.svc = DataLayerService(settings, "9.9.9", on_fleet_write=self.inputs.write_fleet_json,
                                    amr_inputs=self.inputs, fleet_reader=self.inputs.read_fleet_json)
        self.fleet.add_listener(self.svc.on_fleet_changed)
        self.svc.start()
        deadline = time.time() + 3
        while time.time() < deadline and not self.svc.status()["connected"]:
            time.sleep(0.02)
        self.provider = fake_provider.ALL_PROVIDERS[-1]
        self.base = config.DATALAYER_ROOT + "/fleet/amr"

    def tearDown(self):
        self.svc.stop()
        self.config.datalayer_connection_string = self._orig

    def set_mode(self, mode, ids=("AMR-01", "AMR-02")):
        self.inputs.configure(mode, list(ids))
        self.svc.on_input_changed()
        want = mode != "fleet-json"
        deadline = time.time() + 2
        while time.time() < deadline and (self.base + "/**" in self.provider.nodes) != want:
            time.sleep(0.01)
        self.assertEqual(self.base + "/**" in self.provider.nodes, want)

    def test_registered_only_in_per_amr_modes(self):
        self.assertNotIn(self.base + "/**", self.provider.nodes)
        self.set_mode("amr-fields")
        self.assertIn(self.base, self.provider.nodes)
        self.assertEqual(self.svc.status()["registered"], len(self.svc.registry.specs()) + 2)
        self.set_mode("fleet-json")
        self.assertNotIn(self.base, self.provider.nodes)

    def test_amr_fields_through_the_provider(self):
        from ctrlxdatalayer.variant import Result, Variant
        self.set_mode("amr-fields")
        result, names = self.provider.browse(self.base)
        self.assertEqual((result, names.get_array_string()), (Result.OK, ["AMR-01", "AMR-02"]))
        self.assertIn("rotation", self.provider.browse(self.base + "/AMR-01")[1].get_array_string())
        self.assertEqual(self.provider.browse(self.base + "/AMR-07")[0], Result.INVALID_ADDRESS)
        _, meta = self.provider.metadata(self.base + "/AMR-01/x")
        self.assertEqual((meta.meta["name"], meta.meta["type_path"], meta.meta["write_allowed"]),
                         ("x", "types/datalayer/float64", True))
        self.assertFalse(self.provider.metadata(self.base + "/AMR-01")[1].meta["write_allowed"])   # a folder
        for field, setter, value in (("x", "set_float32", 1.5), ("y", "set_int32", 2), ("rotation", "set_float64", 3.0)):
            v = Variant()
            getattr(v, setter)(value)
            self.assertEqual(self.provider.write(self.base + "/AMR-02/" + field, v)[0], Result.OK, field)
        self.inputs.flush()
        self.assertEqual(self.fleet.amr("AMR-02"), {"id": "AMR-02", "x": 1.5, "y": 2.0, "rotation": 3.0})
        self.assertEqual(self.provider.read(self.base + "/AMR-02/y")[1].get_float64(), 2.0)
        self.assertEqual(self.svc.registry.get("fleet/count"), 1)
        bad = Variant()
        bad.set_float64(120.0)
        self.assertEqual(self.provider.write(self.base + "/AMR-02/battery", bad)[0], Result.INVALID_VALUE)
        self.assertIn("battery: must be 0..100", self.svc.registry.get("fleet/last-error"))
        text = Variant()
        text.set_string("1")
        self.assertEqual(self.provider.write(self.base + "/AMR-02/x", text)[0], Result.TYPE_MISMATCH)
        self.assertEqual(self.provider.write(self.base + "/AMR-09/x", bad)[0], Result.INVALID_ADDRESS)
        whole = Variant()                                   # fleet/amrs: read-only here, shows the merge
        whole.set_string("[]")
        self.assertEqual(self.provider.write(self.svc.registry.address("fleet/amrs"), whole)[0],
                         Result.PERMISSION_DENIED)
        merged = json.loads(self.provider.read(self.svc.registry.address("fleet/amrs"))[1].get_string())
        self.assertEqual([a["id"] for a in merged["amrs"]], ["AMR-02"])

    def test_amr_json_through_the_provider(self):
        from ctrlxdatalayer.variant import Result, Variant
        self.set_mode("amr-json")
        _, meta = self.provider.metadata(self.base + "/AMR-01")
        self.assertEqual(meta.meta["type_path"], "types/datalayer/string")
        v = Variant()
        v.set_string('{"x": 1, "y": 2, "rotation": 3}')
        self.assertEqual(self.provider.write(self.base + "/AMR-01", v)[0], Result.OK)
        self.inputs.flush()
        self.assertEqual(json.loads(self.provider.read(self.base + "/AMR-01")[1].get_string()),
                         {"x": 1.0, "y": 2.0, "rotation": 3.0})
        self.assertEqual(self.provider.metadata(self.base + "/AMR-01/x")[0], Result.INVALID_ADDRESS)


if __name__ == "__main__":
    unittest.main()
