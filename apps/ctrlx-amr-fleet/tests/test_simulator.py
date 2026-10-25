"""Built-in simulation: navigation grid, paths and the published fleet JSON."""

import json
import math
import time
import unittest

from app.fleet import parse_fleet
from app.occupancy_map import OccupancyMap
from app.simulator import NavGrid, Simulator


def room_map(size=40, stray=()):
    """Square room of size x size cells (cell 10 units) with a pillar block in the middle.
    Points are (x index, y index); y goes up, so grid row = max y index - y index."""
    pts = set()
    for i in range(size):
        for p in ((i, 0), (i, size - 1), (0, i), (size - 1, i)):
            pts.add(p)
    for c in range(16, 24):
        for r in range(16, 24):
            pts.add((c, r))
    pts.update(stray)
    text = "\n".join("{}\t{}".format(c * 10, r * 10) for c, r in sorted(pts))
    return OccupancyMap.from_text(text)


class FakeMaps:
    def __init__(self, occ_map):
        self.map = occ_map

    def current(self):
        return self.map


def settings(enabled=True, count=3, unit="deg"):
    return {"map": {"unitsPerMeter": 10.0},
            "fleet": {"rotationUnit": unit, "amrLengthM": 4.0, "amrWidthM": 2.0},
            "simulation": {"enabled": enabled, "amrCount": count, "speedMps": 5.0}}


class NavGridTest(unittest.TestCase):
    def test_free_region_avoids_walls_and_pillar(self):
        m = room_map()
        nav = NavGrid(m, 20)  # nav cell = 2 map cells -> 20 x 20
        self.assertEqual((nav.cols, nav.rows), (20, 20))
        self.assertTrue(nav.free)
        for idx in nav.free:
            r, c = divmod(idx, nav.cols)
            self.assertTrue(2 <= r <= 17 and 2 <= c <= 17, (r, c))           # wall + 1 cell margin
            self.assertFalse(7 <= r <= 12 and 7 <= c <= 12, (r, c))         # pillar + margin

    def test_stray_points_block_only_their_cell(self):
        nav = NavGrid(room_map(stray=[(8, 31)]), 20)  # grid col 8, row 39 - 31 = 8 -> nav cell (4, 4)
        self.assertNotIn(4 * nav.cols + 4, nav.free)
        self.assertIn(4 * nav.cols + 5, nav.free)

    def test_path_is_connected_and_free(self):
        nav = NavGrid(room_map(), 20)
        start, goal = min(nav.free), max(nav.free)
        cells = nav.path(start, goal)
        self.assertEqual((cells[0], cells[-1]), (start, goal))
        free = set(nav.free)
        for a, b in zip(cells, cells[1:]):
            self.assertIn(b, free)
            ra, ca = divmod(a, nav.cols)
            rb, cb = divmod(b, nav.cols)
            self.assertLessEqual(max(abs(ra - rb), abs(ca - cb)), 1)
        pts = nav.waypoints(cells)
        self.assertEqual((pts[0], pts[-1]), (nav.center(start), nav.center(goal)))
        self.assertLess(len(pts), len(cells))

    def test_inner_region_preferred(self):
        # a far stray point makes the grid larger: the free area outside the room is bigger than
        # the room, but touches the border -> the room is chosen
        nav = NavGrid(room_map(stray=[(99, 0)]), 20)
        self.assertEqual((nav.cols, nav.rows), (50, 20))
        for idx in nav.free:
            r, c = divmod(idx, nav.cols)
            self.assertTrue(r <= 17 and c <= 17, (r, c))


    def test_door_in_the_outer_wall(self):
        """The walls are the map border; a door gap connects the floor with the edge (Warehouse map,
        0.2.2): the floor must still be chosen, not a small enclosed pocket (e.g. inside a rack pair)."""
        box = [(c, r) for c in range(40, 76) for r in range(8, 44) if c in (40, 75) or r in (8, 43)]
        m = room_map(size=80, stray=box)                               # hollow 36 x 36 box: a pocket
        door = {(c, 0) for c in range(10, 30)}                          # gap in the top wall (row 0)
        text = "\n".join("{}\t{}".format(c * 10, (m.height - 1 - r) * 10) for c, r in sorted(set(m.cells()))
                          if (c, r) not in door)
        nav = NavGrid(OccupancyMap.from_text(text), 20)
        self.assertGreater(len(nav.free), 700)                         # the floor ...
        self.assertNotIn(29 * nav.cols + 29, nav.free)                  # ... not the pocket inside the box
        self.assertIn(5 * nav.cols + 10, nav.free)                      # (nav row 29, col 29 / row 5, col 10)


class SimulatorTest(unittest.TestCase):
    def test_publishes_valid_fleet_inside_free_space(self):
        sim = Simulator(FakeMaps(room_map()), publish=None)
        sim.configure(settings(count=3))
        for _ in range(200):
            text = sim.step(0.2)
            for raw in json.loads(text)["amrs"]:
                self.assertTrue(-180 <= raw["rotation"] <= 180, raw)  # wrapped, not accumulated
        amrs = parse_fleet(text, "deg")
        self.assertEqual([a["id"] for a in amrs], ["AMR-01", "AMR-02", "AMR-03"])
        for a in amrs:
            self.assertTrue(20 <= a["x"] <= 370 and 20 <= a["y"] <= 370, a)    # inside the walls
            self.assertFalse(160 < a["x"] < 230 and 160 < a["y"] < 230, a)    # not in the pillar
            self.assertIn(a["state"], ("driving", "turning", "waiting", "blocked"))
        moved = False
        for _ in range(30):                                   # something moves (not in every single step:
            later = parse_fleet(sim.step(0.2))                # all may be waiting at their goals)
            moved = moved or any((a["x"], a["y"], a["rotation"]) != (b["x"], b["y"], b["rotation"])
                                 for a, b in zip(amrs, later))
        self.assertTrue(moved)

    def test_amrs_keep_their_distance(self):
        sim = Simulator(FakeMaps(room_map(size=60)), publish=None, seed=3)
        cfg = settings(count=5)
        cfg["fleet"].update(amrLengthM=2.0, amrWidthM=1.4)    # clearance 2.56 m = 25.6 map units
        sim.configure(cfg)
        clearance = sim._cfg["clearance"]
        sim.step(0.2)
        start = [(a.x, a.y) for a in sim._amrs]
        self.assertTrue(all(math.hypot(a[0] - b[0], a[1] - b[1]) >= clearance
                            for i, a in enumerate(start) for b in start[i + 1:]), "spawned apart")
        closest, states = float("inf"), set()
        for _ in range(500):
            amrs = json.loads(sim.step(0.4))["amrs"]
            states.update(a["state"] for a in amrs)
            for i, a in enumerate(amrs):
                for b in amrs[i + 1:]:
                    closest = min(closest, math.hypot(a["x"] - b["x"], a["y"] - b["y"]))
        self.assertGreaterEqual(closest, clearance - 0.05)
        self.assertGreater(sum(a.trips for a in sim._amrs), 4, "they still get somewhere (no deadlock)")
        self.assertIn("info", amrs[0])

    def test_no_deadlock_in_narrow_corridors(self):
        """Regression (0.2.0 on the COREvirtual): on the bundled map - narrow free space - all AMRs stayed
        'blocked' for minutes: after replanning they first went back to their own cell centre, i.e.
        towards the AMR that blocked them."""
        import os
        from app import config
        sim = Simulator(FakeMaps(OccupancyMap.from_file(os.path.join(config.BUNDLED_MAPS_DIR, "HMI_Map.txt"))),
                        publish=None, seed=2)
        cfg = settings(count=4)
        cfg["map"]["unitsPerMeter"] = 1000.0
        cfg["fleet"].update(amrLengthM=1.0, amrWidthM=0.7)
        cfg["simulation"]["speedMps"] = 0.8
        sim.configure(cfg)
        stuck = longest = 0
        for _ in range(1500):       # 5 simulated minutes at the real step (INTERVAL_S); old code: 223 s stuck
            amrs = json.loads(sim.step(0.2))["amrs"]
            if all(a["state"] in ("blocked", "waiting") for a in amrs) and any(a["state"] == "blocked" for a in amrs):
                stuck += 1
                longest = max(longest, stuck)
            else:
                stuck = 0
        self.assertLess(longest * 0.2, 30, "all AMRs stuck for {:.0f} s".format(longest * 0.2))
        self.assertGreater(sum(a.trips for a in sim._amrs), 40)

    def test_count_unit_and_disable(self):
        sim = Simulator(FakeMaps(room_map()), publish=None)
        sim.configure(settings(count=4, unit="rad"))
        amrs = json.loads(sim.step(0.2))["amrs"]
        self.assertEqual(len(amrs), 4)
        self.assertTrue(all(-3.2 < a["rotation"] < 3.2 for a in amrs))   # radians
        sim.configure(settings(count=2))
        self.assertEqual(len(json.loads(sim.step(0.2))["amrs"]), 2)
        sim.configure(settings(enabled=False))
        self.assertEqual(json.loads(sim.step(0.2)), {"amrs": []})       # once, to clear the view
        self.assertIsNone(sim.step(0.2))

    def test_fixed_rhythm(self):
        """5 updates/s even when a step's work takes most of the period (here 120 ms of 200)."""
        published = []

        def slow_publish(text):
            time.sleep(0.12)
            published.append(time.monotonic())
        sim = Simulator(FakeMaps(room_map()), publish=slow_publish)
        sim.configure(settings(count=2))
        sim.start()
        time.sleep(2.1)
        sim.stop()
        self.assertGreaterEqual(len(published), 9)       # a wait *after* the work gave 1 / 0.32 s = 6
        gaps = [b - a for a, b in zip(published, published[1:])]
        self.assertLess(max(gaps), 0.35)

    def test_no_map(self):
        sim = Simulator(FakeMaps(None), publish=None)
        sim.configure(settings())
        self.assertEqual(json.loads(sim.step(0.2)), {"amrs": []})


if __name__ == "__main__":
    unittest.main()
