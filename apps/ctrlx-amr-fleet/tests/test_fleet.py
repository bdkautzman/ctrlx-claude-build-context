"""Fleet JSON validation and FleetState."""

import json
import math
import unittest

from app import fleet as fleet_module
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



def _amrs(*positions):
    return json.dumps([{"id": "a{}".format(i), "x": x, "y": y, "rotation": 0} for i, (x, y) in enumerate(positions)])


class PathHistoryTest(unittest.TestCase):
    """Map units mm (1000 per m): a point per 250 mm driven."""

    def setUp(self):
        self.now = [0.0]
        self.fleet = FleetState(clock=lambda: self.now[0], path_minutes=1, units_per_meter=1000.0)

    def drive(self, positions, dt=0.2):
        for pos in positions:
            self.fleet.receive(_amrs(pos))
            self.now[0] += dt

    def points(self, ident="a0"):
        return [p[:2] for p in self.fleet.path(ident)["points"]]

    def test_a_point_per_step(self):
        self.drive([(x, 0) for x in range(0, 1001, 100)])          # 0.1 m per update, 1 m in total
        self.assertEqual(self.points(), [[0, 0], [300, 0], [600, 0], [900, 0]])
        ages = [p[2] for p in self.fleet.path("a0")["points"]]
        self.assertEqual(ages, [2.2, 1.6, 1.0, 0.4])               # oldest first

    def test_old_points_dropped_and_parked_amr(self):
        self.drive([(x, 0) for x in range(0, 3001, 500)], dt=10.0)  # t = 0..60 s, now 70 s
        self.assertEqual(self.points(), [[x, 0] for x in range(500, 3001, 500)])   # x=500: 60 s old
        self.now[0] += 200.0                                          # parked for longer than the history
        self.assertEqual(self.points(), [])
        self.drive([(3000, 0), (3000, 0)])
        self.assertEqual(self.points(), [[3000, 0]])                  # starts again where it stands

    def test_jump_starts_a_new_path(self):
        self.drive([(0, 0), (300, 0), (600, 0)])
        self.drive([(20000, 0), (20300, 0)])                          # 19.4 m in 0.2 s: relocalized
        self.assertEqual(self.points(), [[20000, 0], [20300, 0]])
        self.now[0] += 30.0
        self.drive([(30000, 0)])                                      # 9.7 m in 30 s: real driving
        self.assertEqual(self.points(), [[20000, 0], [20300, 0], [30000, 0]])

    def test_amr_missing_from_an_update_loses_its_path(self):
        self.fleet.receive(_amrs((0, 0), (0, 0)))
        self.fleet.receive(_amrs((500, 0), (0, 500)))
        self.assertEqual(len(self.points("a1")), 2)
        self.fleet.receive(_amrs((900, 0)))
        self.assertIsNone(self.fleet.path("a1"))
        self.assertIsNone(self.fleet.path("unknown"))
        self.fleet.receive(_amrs((900, 0), (0, 600)))
        self.assertEqual(self.points("a1"), [[0, 600]])

    def test_budget_shortens_paths_of_many_amrs(self):
        budget = fleet_module.PATH_POINT_BUDGET
        fleet_module.PATH_POINT_BUDGET = 10
        try:
            for k in range(8):                                        # 2 AMRs: at most 5 points each
                self.fleet.receive(_amrs((k * 500, 0), (0, k * 500)))
        finally:
            fleet_module.PATH_POINT_BUDGET = budget
        self.assertEqual(self.points("a0"), [[x, 0] for x in range(1500, 3501, 500)])

    def test_off_and_unit_change(self):
        self.drive([(0, 0), (500, 0)])
        self.fleet.configure("deg", 0, 1000.0)                        # 0 minutes: no paths, memory freed
        self.assertEqual(self.points(), [])
        self.drive([(1000, 0), (1500, 0)])
        self.assertEqual(self.points(), [])
        self.fleet.configure("deg", 1, 1.0)                           # map in metres: 0.25 per point
        self.drive([(0, 0), (0.2, 0), (0.3, 0)])
        self.assertEqual(self.points(), [[0, 0], [0.3, 0]])


if __name__ == "__main__":
    unittest.main()
