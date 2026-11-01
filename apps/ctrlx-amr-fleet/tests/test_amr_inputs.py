"""Input modes fleet-json / amr-json / amr-fields: addresses, validation, merging into the fleet."""

import json
import math
import time
import unittest

from app.amr_inputs import FIELDS, AmrInputs
from app.datalayer_nodes import NodeWriteError
from app.fleet import FleetError, FleetState

IDS = ["AMR-01", "AMR-02"]


class InputsTest(unittest.TestCase):
    def setUp(self):
        self.fleet = FleetState()
        self.inputs = AmrInputs(self.fleet, start_thread=False)   # tests call flush() themselves

    def amrs(self):
        self.inputs.flush()
        return self.fleet.snapshot()["amrs"]

    def write_error(self, rel, value):
        with self.assertRaises(NodeWriteError) as ctx:
            self.inputs.write(rel, value)
        return ctx.exception

    def test_fleet_json_mode(self):
        self.inputs.configure("fleet-json", IDS)
        self.assertIsNone(self.inputs.describe(""))           # no fleet/amr nodes
        self.assertIsNone(self.inputs.browse("AMR-01"))
        self.assertIsNone(self.inputs.read_fleet_json())      # fleet/amrs shows what was written
        self.inputs.write_fleet_json('[{"id": "A", "x": 1, "y": 2, "rotation": 3}]')
        self.assertEqual(self.fleet.snapshot()["count"], 1)
        self.assertEqual(self.write_error("AMR-01/x", 1.0).reason, "unknown")
        with self.assertRaises(ValueError):
            self.inputs.configure("plc", IDS)

    def test_amr_fields_addresses(self):
        self.inputs.configure("amr-fields", IDS)
        self.assertEqual(self.inputs.browse(""), IDS)
        self.assertEqual(self.inputs.browse("AMR-02"), list(FIELDS))
        self.assertEqual(self.inputs.browse("AMR-02/x"), [])
        self.assertEqual(self.inputs.describe(""), ("folder",))
        self.assertEqual(self.inputs.describe("AMR-01"), ("folder",))
        self.assertEqual(self.inputs.describe("AMR-01/battery")[:2], ("variable", "float64"))
        self.assertEqual(self.inputs.describe("AMR-01/info")[:2], ("variable", "string"))
        for missing in ("AMR-03", "AMR-01/speed", "AMR-01/x/y", "AMR-01/xz"):
            self.assertIsNone(self.inputs.describe(missing), missing)
        self.assertEqual(self.write_error("AMR-03/x", 1.0).reason, "unknown")   # not in the list

    def test_amr_fields_merge(self):
        self.inputs.configure("amr-fields", IDS)
        self.inputs.write("AMR-02/x", 100)                     # an INT from a PLC is fine
        self.inputs.write("AMR-02/y", 200.5)
        self.assertEqual(self.amrs(), [])                     # rotation missing: not shown yet
        self.inputs.write("AMR-02/rotation", -90.0)
        self.inputs.write("AMR-02/name", "Tugger")
        self.inputs.write("AMR-02/battery", 55)
        self.inputs.write("AMR-02/info", '{"task": "T-1", "load": 2}')
        self.inputs.write("AMR-01/x", 1.0)
        self.inputs.write("AMR-01/y", 2.0)
        self.inputs.write("AMR-01/rotation", 0.0)
        self.assertEqual(self.amrs(), [                       # list order
            {"id": "AMR-01", "x": 1.0, "y": 2.0, "rotation": 0.0},
            {"id": "AMR-02", "x": 100.0, "y": 200.5, "rotation": 270.0, "battery": 55.0, "name": "Tugger",
             "info": {"task": "T-1", "load": 2}}])
        self.assertEqual(self.inputs.read("AMR-02/rotation"), 270.0)
        self.assertEqual(self.inputs.read("AMR-02/name"), "Tugger")
        self.assertEqual(json.loads(self.inputs.read("AMR-02/info")), {"task": "T-1", "load": 2})
        self.assertEqual((self.inputs.read("AMR-01/state"), self.inputs.read("AMR-01/battery")), ("", 0.0))
        self.inputs.write("AMR-02/name", "")                   # empty clears it
        self.inputs.write("AMR-02/info", "")
        self.assertNotIn("name", self.amrs()[1])
        self.assertNotIn("info", self.amrs()[1])
        merged = json.loads(self.inputs.read_fleet_json())    # fleet/amrs: read-only, the merged fleet
        self.assertEqual([a["id"] for a in merged["amrs"]], IDS)
        self.assertNotIn("speed", merged["amrs"][1])
        with self.assertRaises(NodeWriteError) as ctx:
            self.inputs.write_fleet_json("[]")
        self.assertEqual(ctx.exception.reason, "readonly")
        self.assertIn("read-only in input mode amr-fields", self.fleet.snapshot()["lastError"])

    def test_amr_fields_validation(self):
        self.inputs.configure("amr-fields", IDS)
        rejected = self.fleet.snapshot()["rejected"]
        for rel, value, reason in [("AMR-01/x", "1", "type"), ("AMR-01/x", True, "type"),
                                   ("AMR-01/x", math.inf, "invalid"), ("AMR-01/battery", 101, "invalid"),
                                   ("AMR-01/name", 5.0, "type"), ("AMR-01/name", "n" * 65, "invalid"),
                                   ("AMR-01/info", "{", "invalid"), ("AMR-01/info", "[1]", "invalid"),
                                   ("AMR-01/info", '{"t": [1]}', "invalid")]:
            self.assertEqual(self.write_error(rel, value).reason, reason, (rel, value))
        self.assertEqual(self.fleet.snapshot()["rejected"], rejected + 6)   # invalid values; not type mismatches
        self.assertIn("fleet/amr/AMR-01.info.t", self.fleet.snapshot()["lastError"])

    def test_amr_json(self):
        self.inputs.configure("amr-json", IDS)
        self.assertEqual(self.inputs.describe("AMR-01")[:2], ("variable", "string"))
        self.assertEqual(self.inputs.browse("AMR-01"), [])
        self.assertIsNone(self.inputs.describe("AMR-01/x"))
        self.assertEqual(self.inputs.read("AMR-01"), "")     # nothing written yet
        self.inputs.write("AMR-01", json.dumps({"x": 1, "y": 2, "rotation": 450, "state": "driving"}))
        self.inputs.write("AMR-02", json.dumps({"id": "AMR-02", "x": 3, "y": 4, "rotation": 0}))
        self.assertEqual(self.amrs(), [{"id": "AMR-01", "x": 1.0, "y": 2.0, "rotation": 90.0, "state": "driving"},
                                       {"id": "AMR-02", "x": 3.0, "y": 4.0, "rotation": 0.0}])
        self.assertEqual(json.loads(self.inputs.read("AMR-01")),
                         {"x": 1.0, "y": 2.0, "rotation": 90.0, "state": "driving"})
        for value, message in [('{"x": 1, "y": 2, "rotation": 3, "id": "AMR-02"}', "belongs to AMR-01"),
                               ('{"x": 1, "y": 2}', "rotation: missing"), ("[1]", "JSON object"),
                               ("{", "not valid JSON")]:
            self.assertEqual(self.write_error("AMR-01", value).reason, "invalid")
            self.assertIn(message, self.fleet.snapshot()["lastError"])
        self.assertEqual(self.write_error("AMR-01", 1.0).reason, "type")
        self.assertEqual(self.amrs()[0]["x"], 1.0)            # rejected writes change nothing

    def test_list_and_mode_changes(self):
        self.inputs.configure("amr-fields", IDS)
        for ident in IDS:
            for field in ("x", "y", "rotation"):
                self.inputs.write(ident + "/" + field, 1.0)
        self.assertEqual(len(self.amrs()), 2)
        self.inputs.configure("amr-fields", ["AMR-02", "AMR-09"])       # AMR-01 left the list
        self.assertEqual([a["id"] for a in self.amrs()], ["AMR-02"])
        self.inputs.configure("amr-fields", IDS)                          # back: its old values are gone
        self.assertEqual([a["id"] for a in self.amrs()], ["AMR-02"])
        self.inputs.configure("amr-json", IDS)                            # another mode starts empty
        self.assertEqual(self.amrs(), [])

    def test_rotation_in_radians(self):
        self.fleet.configure("rad")
        self.inputs.configure("amr-fields", IDS)
        for field, value in (("x", 0.0), ("y", 0.0), ("rotation", math.pi / 2)):
            self.inputs.write("AMR-01/" + field, value)
        self.assertAlmostEqual(self.amrs()[0]["rotation"], 90.0)          # degrees inside the app
        self.assertAlmostEqual(self.inputs.read("AMR-01/rotation"), math.pi / 2, places=5)   # as written
        self.assertAlmostEqual(json.loads(self.inputs.read_fleet_json())["amrs"][0]["rotation"], math.pi / 2, places=5)

    def test_reads_show_simulated_amrs(self):
        self.inputs.configure("amr-json", IDS)
        self.fleet.receive('[{"id": "AMR-02", "x": 5, "y": 6, "rotation": 7}]', source="simulation")
        self.assertEqual(json.loads(self.inputs.read("AMR-02"))["x"], 5.0)

    def wait_for(self, condition, seconds=2.0):
        deadline = time.monotonic() + seconds
        while not condition():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.005)

    def test_one_update_per_write_burst(self):
        inputs = AmrInputs(self.fleet, quiet_s=0.03, max_delay_s=0.5)
        try:
            inputs.configure("amr-fields", IDS)
            self.wait_for(lambda: self.fleet.snapshot()["revision"] == 1)      # the new mode: empty fleet
            for burst in range(3):                                            # a PLC cycle: all values, then a pause
                for ident in IDS:
                    for field in ("x", "y", "rotation"):
                        inputs.write(ident + "/" + field, float(burst))
                self.wait_for(lambda: self.fleet.snapshot()["revision"] == 2 + burst)
                time.sleep(0.1)
                self.assertEqual(self.fleet.snapshot()["revision"], 2 + burst)   # exactly one update per burst
                self.assertEqual({a["x"] for a in self.fleet.snapshot()["amrs"]}, {float(burst)})
        finally:
            inputs.stop()

    def test_writes_without_pause_still_publish(self):
        inputs = AmrInputs(self.fleet, quiet_s=0.05, max_delay_s=0.15)
        try:
            inputs.configure("amr-fields", IDS)
            self.wait_for(lambda: self.fleet.snapshot()["revision"] == 1)
            t0 = time.monotonic()
            i = 0
            while time.monotonic() - t0 < 0.6:                                  # never 50 ms quiet
                for field in ("x", "y", "rotation"):
                    inputs.write("AMR-01/" + field, float(i))
                i += 1
                time.sleep(0.01)
            updates = self.fleet.snapshot()["revision"] - 1
            self.assertTrue(2 <= updates <= 6, updates)                        # about every max_delay_s
        finally:
            inputs.stop()

if __name__ == "__main__":
    unittest.main()
