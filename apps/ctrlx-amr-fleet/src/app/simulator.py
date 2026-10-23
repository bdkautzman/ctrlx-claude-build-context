"""Built-in AMR simulation, for demos and tests without a fleet manager or PLC.

With simulation.enabled the app writes the fleet JSON itself every 200 ms (same format and
validation as a Data Layer write to fleet/amrs, rotation in fleet.rotationUnit). The AMRs drive
between random goals through the free space of the map on a coarse navigation grid (cell =
half the AMR length). Cells with occupied map cells are blocked; cells with a wall in them
(>= SOLID_POINTS points, not single stray lidar points) also block their 8 neighbours. The AMRs
stay in the largest free region that does not touch the map border (= inside the building)
when there is one. While it runs, Data Layer writes to fleet/amrs are overwritten every 200 ms.
Switching it off publishes an empty fleet once.
"""

import collections
import json
import logging
import math
import random
import threading

log = logging.getLogger("simulator")

INTERVAL_S = 0.2
SOLID_POINTS = 3
TURN_RATE = math.radians(120.0)     # rad/s
ALIGN_BEFORE_DRIVING = math.radians(25.0)


class NavGrid:
    """Coarse grid of free cells over an OccupancyMap."""

    def __init__(self, occ_map, cell_units):
        k = max(1, int(round(cell_units / occ_map.resolution)))   # map cells per nav cell
        cols, rows = (occ_map.width + k - 1) // k, (occ_map.height + k - 1) // k
        self.cols, self.rows = cols, rows
        self.cell = k * occ_map.resolution
        self.left = occ_map.min_x - occ_map.resolution / 2
        self.top = occ_map.max_y + occ_map.resolution / 2
        points = collections.Counter((r // k) * cols + c // k for c, r in occ_map.cells())
        solid = min(SOLID_POINTS, k)   # a wall through a nav cell has about k points
        blocked = bytearray(cols * rows)
        for idx, count in points.items():
            blocked[idx] = 1
            if count < solid:
                continue  # a stray lidar point: block only its own cell
            r0, c0 = divmod(idx, cols)
            for r in range(max(0, r0 - 1), min(rows, r0 + 2)):
                for c in range(max(0, c0 - 1), min(cols, c0 + 2)):
                    blocked[r * cols + c] = 1
        self.free = self._pick_region(blocked)
        self._free_set = set(self.free)

    def _neighbours4(self, idx):
        r, c = divmod(idx, self.cols)
        if r > 0:
            yield idx - self.cols
        if r < self.rows - 1:
            yield idx + self.cols
        if c > 0:
            yield idx - 1
        if c < self.cols - 1:
            yield idx + 1

    def _pick_region(self, blocked):
        seen = bytearray(len(blocked))
        best_inner, best_any = [], []
        for start in range(len(blocked)):
            if blocked[start] or seen[start]:
                continue
            region, touches_border = [], False
            queue = collections.deque([start])
            seen[start] = 1
            while queue:
                idx = queue.popleft()
                region.append(idx)
                r, c = divmod(idx, self.cols)
                if r in (0, self.rows - 1) or c in (0, self.cols - 1):
                    touches_border = True
                for n in self._neighbours4(idx):
                    if not blocked[n] and not seen[n]:
                        seen[n] = 1
                        queue.append(n)
            if not touches_border and len(region) > len(best_inner):
                best_inner = region
            if len(region) > len(best_any):
                best_any = region
        return best_inner or best_any

    def center(self, idx):
        r, c = divmod(idx, self.cols)
        return self.left + (c + 0.5) * self.cell, self.top - (r + 0.5) * self.cell

    def cell_of(self, x, y):
        """Nearest free cell of the region to a position."""
        c = int((x - self.left) // self.cell)
        r = int((self.top - y) // self.cell)
        idx = r * self.cols + c
        if 0 <= c < self.cols and 0 <= r < self.rows and idx in self._free_set:
            return idx
        return min(self.free, key=lambda i: math.hypot(self.center(i)[0] - x, self.center(i)[1] - y))

    def path(self, start, goal):
        """Cell indices from start to goal (8-connected, no corner cutting), [] if unreachable."""
        prev = {start: None}
        queue = collections.deque([start])
        while queue:
            idx = queue.popleft()
            if idx == goal:
                break
            r, c = divmod(idx, self.cols)
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)):
                nr, nc = r + dr, c + dc
                n = nr * self.cols + nc
                if not (0 <= nr < self.rows and 0 <= nc < self.cols) or n in prev or n not in self._free_set:
                    continue
                if dr and dc and ((r * self.cols + nc) not in self._free_set or (nr * self.cols + c) not in self._free_set):
                    continue
                prev[n] = idx
                queue.append(n)
        if goal not in prev:
            return []
        cells = []
        while goal is not None:
            cells.append(goal)
            goal = prev[goal]
        return cells[::-1]

    def waypoints(self, cells):
        """Cell centres without the points in the middle of straight runs."""
        pts = [self.center(i) for i in cells]
        out = pts[:1]
        for a, b, c in zip(pts, pts[1:], pts[2:]):
            if abs((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])) > 1e-9:
                out.append(b)
        if len(pts) > 1:
            out.append(pts[-1])
        return out


class _SimAmr:
    def __init__(self, ident, x, y, heading, battery):
        self.id, self.x, self.y, self.heading, self.battery = ident, x, y, heading, battery
        self.path = []
        self.wait = 0.0
        self.state = "waiting"

    def step(self, dt, speed, nav, rng):
        if not self.path:
            self.wait -= dt
            self.state = "waiting"
            if self.wait <= 0:
                start = nav.cell_of(self.x, self.y)
                goal = rng.choice(nav.free)
                self.path = nav.waypoints(nav.path(start, goal))
                self.wait = rng.uniform(1.0, 4.0)
            return
        tx, ty = self.path[0]
        dx, dy = tx - self.x, ty - self.y
        dist = math.hypot(dx, dy)
        if dist < 1e-6:
            self.path.pop(0)
            return
        err = (math.atan2(dy, dx) - self.heading + math.pi) % (2 * math.pi) - math.pi
        self.heading += max(-TURN_RATE * dt, min(TURN_RATE * dt, err))
        self.heading = (self.heading + math.pi) % (2 * math.pi) - math.pi   # keep it in [-pi, pi)
        if abs(err) > ALIGN_BEFORE_DRIVING:
            self.state = "turning"
            return
        self.state = "driving"
        move = speed * dt
        if move >= dist:
            self.x, self.y = tx, ty
            self.path.pop(0)
        else:
            self.x += dx / dist * move
            self.y += dy / dist * move
        self.battery -= 0.2 * dt
        if self.battery < 15.0:
            self.battery = 100.0


class Simulator:
    def __init__(self, maps, publish, seed=1):
        """maps: MapSource; publish(text): hands the fleet JSON on (may raise, is logged)."""
        self._maps = maps
        self._publish = publish
        self._rng = random.Random(seed)
        self._lock = threading.Lock()
        self._cfg = {"enabled": False, "count": 0, "speed": 0.0, "cell": 1.0, "unit": "deg"}
        self._nav = None
        self._nav_key = None
        self._amrs = []
        self._was_enabled = False
        self._stop = threading.Event()
        self._thread = None

    def configure(self, settings):
        upm = settings["map"]["unitsPerMeter"]
        sim = settings["simulation"]
        with self._lock:
            self._cfg = {
                "enabled": sim["enabled"],
                "count": sim["amrCount"],
                "speed": sim["speedMps"] * upm,
                "cell": 0.5 * max(settings["fleet"]["amrLengthM"], settings["fleet"]["amrWidthM"]) * upm,
                "unit": settings["fleet"]["rotationUnit"],
            }

    def start(self):
        self._thread = threading.Thread(target=self._run, name="simulator", daemon=True)
        self._thread.start()

    def stop(self, timeout=5.0):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def _run(self):
        while not self._stop.wait(INTERVAL_S):
            try:
                text = self.step(INTERVAL_S)
                if text is not None:
                    self._publish(text)
            except Exception:
                log.exception("simulation step failed")

    def step(self, dt):
        """Advance the simulation; return the fleet JSON to publish, or None."""
        with self._lock:
            cfg = dict(self._cfg)
        if not cfg["enabled"]:
            if self._was_enabled:
                self._was_enabled = False
                self._amrs = []
                log.info("Simulation stopped")
                return json.dumps({"amrs": []})
            return None
        if not self._was_enabled:
            log.info("Simulation running with %d AMRs", cfg["count"])
        self._was_enabled = True
        occ_map = self._maps.current()
        if occ_map is None:
            self._amrs = []
            return json.dumps({"amrs": []})
        key = (occ_map.id, cfg["cell"])
        if key != self._nav_key:
            self._nav_key = key
            self._nav = NavGrid(occ_map, cfg["cell"])
            self._amrs = []
            log.info("Simulation grid: %d x %d cells of %g, %d free", self._nav.cols, self._nav.rows,
                     self._nav.cell, len(self._nav.free))
        nav = self._nav
        if not nav.free:
            return json.dumps({"amrs": []})
        while len(self._amrs) < cfg["count"]:
            n = len(self._amrs) + 1
            x, y = nav.center(self._rng.choice(nav.free))
            self._amrs.append(_SimAmr("AMR-{:02d}".format(n), x, y,
                                      self._rng.uniform(-math.pi, math.pi), 100.0 - 7 * n % 60))
        del self._amrs[cfg["count"]:]
        for amr in self._amrs:
            amr.step(dt, cfg["speed"], nav, self._rng)
        to_unit = (lambda a: a) if cfg["unit"] == "rad" else math.degrees
        return json.dumps({"amrs": [
            {"id": a.id, "x": round(a.x, 2), "y": round(a.y, 2), "rotation": round(to_unit(a.heading), 4),
             "state": a.state, "battery": round(a.battery, 1)}
            for a in self._amrs]}, separators=(",", ":"))
