"""Fleet JSON validation and FleetState."""

import json
import math
import unittest

from app.fleet import MAX_AMRS, FleetError, FleetState, parse_fleet


class ParseFleetTest(unittest.TestCase):
    def test_object_and_array(self):
        amr = {"id": "AMR-01", "x": 1, "y": 2.5, "rotation": -90, "name": "Tugger", "state": "driving",
               "battery": 55, "extra": {"ignored": True}}
        expected = [{"id": "AMR-01", "x": 1.0, "y": 2.5, "rotation": 270.0, "name": "Tugger",
                     "state": "driving", "battery": 55.0}]
        self.assertEqual(parse_fleet(json.dumps({"amrs": [amr]})), expected)
        self.assertEqual(parse_fleet(json.dumps([amr])), expected)
        self.assertEqual(parse_fleet('{"amrs": []}'), [])
        self.assertEqual(parse_fleet('[{"id": 7, "x": 0, "y": 0, "rotation": 0, "name": null}]'),
                         [{"id": "7", "x": 0.0, "y": 0.0, "rotation": 0.0}])

    def test_info(self):
        item = {"id": "a", "x": 0, "y": 0, "rotation": 0,
                "info": {"task": "T-4711", "load": 12.5, "error": False, "note": None}}
        self.assertEqual(parse_fleet(json.dumps([item]))[0]["info"], {"task": "T-4711", "load": 12.5, "error": False})
        self.assertNotIn("info", parse_fleet(json.dumps([dict(item, info={})]))[0])
        for info, message in [([1], "must be an object"), ({"k{}".format(i): 1 for i in range(17)}, "at most 16"),
                              ({"": 1}, "keys must have"), ({"x" * 33: 1}, "keys must have"),
                              ({"t": "x" * 65}, "info.t: must be a string"), ({"t": [1]}, "info.t: must be")]:
            with self.assertRaises(FleetError, msg=str(info)[:40]) as ctx:
                parse_fleet(json.dumps([dict(item, info=info)]))
            self.assertIn(message, str(ctx.exception))
        with self.assertRaises(FleetError):   # 1e999 parses to infinity
            parse_fleet('[{"id": "a", "x": 0, "y": 0, "rotation": 0, "info": {"t": 1e999}}]')

    def test_rotation_units(self):
        rot = lambda value, unit: parse_fleet(json.dumps([{"id": "a", "x": 0, "y": 0, "rotation": value}]), unit)[0]["rotation"]  # noqa: E731
        self.assertAlmostEqual(rot(math.pi / 2, "rad"), 90.0)
        self.assertAlmostEqual(rot(-math.pi, "rad"), 180.0)
        self.assertEqual(rot(720.5, "deg"), 0.5)
        self.assertEqual(rot(-1e-9, "deg"), 0.0)

    def test_errors_name_the_element(self):
        good = {"id": "a", "x": 0, "y": 0, "rotation": 0}
        cases = [
            ("{", "not valid JSON"),
            ('{"robots": []}', "expected a JSON array"),
            ('"text"', "expected a JSON array"),
            (json.dumps([good, 5]), "amrs[1]: must be an object"),
            (json.dumps([dict(good, id=True)]), "amrs[0].id: must be a string"),
            (json.dumps([dict(good, id=" ")]), "amrs[0].id: must have"),
            (json.dumps([good, good]), 'amrs[1].id: duplicate id "a"'),
            (json.dumps([{"id": "a", "y": 0, "rotation": 0}]), "amrs[0].x: missing"),
            (json.dumps([dict(good, y="5")]), "amrs[0].y: must be a finite number"),
            ('[{"id": "a", "x": 0, "y": 0, "rotation": NaN}]', "amrs[0].rotation: must be a finite number"),
            (json.dumps([dict(good, battery=120)]), "amrs[0].battery: must be 0..100"),
            (json.dumps([dict(good, state=3)]), "amrs[0].state: must be a string"),
            (json.dumps([dict(good, id="a{}".format(i)) for i in range(MAX_AMRS + 1)]), "more than"),
        ]
        for text, message in cases:
            with self.assertRaises(FleetError, msg=text[:40]) as ctx:
                parse_fleet(text)
            self.assertIn(message, str(ctx.exception))


class FleetStateTest(unittest.TestCase):
    def test_receive_reject_and_snapshot(self):
        now = [100.0]
        fleet = FleetState("deg", clock=lambda: now[0], wallclock=lambda: 1700000000.0)
        seen = []
        fleet.add_listener(seen.append)
        snap = fleet.snapshot(5.0)
        self.assertEqual((snap["revision"], snap["count"], snap["stale"], snap["ageMs"]), (0, 0, True, None))
        fleet.receive('[{"id": "a", "x": 1, "y": 2, "rotation": 3}]', source="simulation")
        now[0] += 1.0
        snap = fleet.snapshot(5.0)
        self.assertEqual((snap["revision"], snap["count"], snap["stale"], snap["ageMs"], snap["source"]),
                         (1, 1, False, 1000, "simulation"))
        self.assertEqual(snap["amrs"][0]["id"], "a")
        with self.assertRaises(FleetError):
            fleet.receive("[1]")
        snap = fleet.snapshot(5.0)
        self.assertEqual((snap["revision"], snap["count"], snap["rejected"]), (1, 1, 1))  # previous fleet stays
        self.assertIn("amrs[0]", snap["lastError"])
        now[0] += 10.0
        self.assertTrue(fleet.snapshot(5.0)["stale"])
        self.assertNotIn("amrs", fleet.snapshot(include_amrs=False))
        fleet.receive("[]")
        self.assertEqual(fleet.snapshot()["lastError"], "")  # cleared by the next accepted write
        self.assertEqual([s["revision"] for s in seen], [1, 1, 2])

    def test_speed(self):
        now = [0.0]
        fleet = FleetState(clock=lambda: now[0])
        fleet.receive('[{"id": "a", "x": 0, "y": 0, "rotation": 0}, {"id": "b", "x": 5, "y": 5, "rotation": 0}]')
        self.assertNotIn("speed", fleet.snapshot()["amrs"][0])           # needs two updates
        now[0] = 0.5
        fleet.receive('[{"id": "a", "x": 300, "y": 400, "rotation": 0}, {"id": "b", "x": 5, "y": 5, "rotation": 0}]')
        a, b = fleet.snapshot()["amrs"]
        self.assertEqual((a["speed"], b["speed"]), (1000.0, 0.0))        # 500 units in 0.5 s
        now[0] = 1.0
        fleet.receive('[{"id": "a", "x": 300, "y": 400, "rotation": 0}]')
        self.assertEqual(fleet.snapshot()["amrs"][0]["speed"], 500.0)   # smoothed: (0 + 1000) / 2
        now[0] = 1.005
        fleet.receive('[{"id": "a", "x": 900, "y": 400, "rotation": 0}]')
        self.assertEqual(fleet.snapshot()["amrs"][0]["speed"], 500.0)   # too close in time: kept

    def test_rotation_unit_applies_to_next_update(self):
        fleet = FleetState("deg")
        fleet.configure("rad")
        fleet.receive('[{"id": "a", "x": 0, "y": 0, "rotation": 3.141592653589793}]')
        self.assertAlmostEqual(fleet.snapshot()["amrs"][0]["rotation"], 180.0)

    def test_listener_errors_do_not_break_writes(self):
        fleet = FleetState()
        fleet.add_listener(lambda snap: 1 / 0)
        with self.assertLogs("fleet", "ERROR"):
            fleet.receive("[]")
        self.assertEqual(fleet.snapshot()["revision"], 1)


if __name__ == "__main__":
    unittest.main()
