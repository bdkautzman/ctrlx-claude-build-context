"""Built-in AMR simulation, for demos and tests without a fleet manager or PLC.

With simulation.enabled the app writes the fleet JSON itself every 200 ms (same format and
validation as a Data Layer write to fleet/amrs, rotation in fleet.rotationUnit). The AMRs drive
between random goals through the free space of the map on a coarse navigation grid (cell =
half the AMR length). Cells with occupied map cells are blocked; cells with a wall in them
(>= SOLID_POINTS points, not single stray lidar points) also block their 8 neighbours. The AMRs
stay in the largest free region that does not touch the map border (= inside the building)
when there is one. While it runs, Data Layer writes to fleet/amrs are overwritten every 200 ms.
Switching it off publishes an empty fleet once.
The AMRs keep their footprints apart (circumscribed circles, clearance): an AMR never moves closer
than that to another one; when it is blocked for a few seconds it plans a new path around the others.
Each AMR reports `info` (goal, distance to it, finished trips) to show the detail view.
"""

import collections
import heapq
import json
import logging
import math
import random
import threading
import time

log = logging.getLogger("simulator")

INTERVAL_S = 0.2
SOLID_POINTS = 3
MAX_EDGE_SHARE = 0.05         # a free region with more of its cells on the map edge is "outside"
GOAL_RANGE_M = 25.0           # new goals within this distance: short, cheap paths on big maps
PLANS_PER_STEP = 4            # path searches per step at most; the other AMRs wait a step
MAX_EXPANSIONS = 20000        # give up a path search after this many cells
_DIAGONAL = math.sqrt(2.0)
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
        """The largest free region that is inside the building: at most MAX_EDGE_SHARE of its cells on
        the map edge. A building's free floor touches the edge only at door gaps (the walls are the map
        border); the space around a building is a band along the edge."""
        seen = bytearray(len(blocked))
        best_inner, best_any = [], []
        for start in range(len(blocked)):
            if blocked[start] or seen[start]:
                continue
            region, edge_cells = [], 0
            queue = collections.deque([start])
            seen[start] = 1
            while queue:
                idx = queue.popleft()
                region.append(idx)
                r, c = divmod(idx, self.cols)
                if r in (0, self.rows - 1) or c in (0, self.cols - 1):
                    edge_cells += 1
                for n in self._neighbours4(idx):
                    if not blocked[n] and not seen[n]:
                        seen[n] = 1
                        queue.append(n)
            if edge_cells <= MAX_EDGE_SHARE * len(region) and len(region) > len(best_inner):
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

    def path(self, start, goal, avoid=frozenset()):
        """Cell indices from start to goal (A*, 8-connected, no corner cutting), [] if unreachable or too
        far (MAX_EXPANSIONS). avoid: cells to treat as blocked (e.g. around other AMRs)."""
        if start == goal:
            return [start]
        free, cols, rows = self._free_set, self.cols, self.rows
        gr, gc = divmod(goal, cols)

        def h(i):   # octile distance
            r, c = divmod(i, cols)
            dr, dc = abs(r - gr), abs(c - gc)
            return max(dr, dc) + (_DIAGONAL - 1) * min(dr, dc)

        best = {start: 0.0}
        prev = {start: None}
        heap = [(h(start), 0.0, start)]
        expanded = 0
        while heap:
            _, g, idx = heapq.heappop(heap)
            if idx == goal:
                break
            if g > best[idx]:
                continue
            expanded += 1
            if expanded > MAX_EXPANSIONS:
                return []
            r, c = divmod(idx, cols)
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)):
                nr, nc = r + dr, c + dc
                n = nr * cols + nc
                if not (0 <= nr < rows and 0 <= nc < cols) or n not in free or n in avoid:
                    continue
                if dr and dc and ((r * cols + nc) not in free or (nr * cols + c) not in free):
                    continue
                ng = g + (_DIAGONAL if dr and dc else 1.0)
                if ng < best.get(n, float("inf")):
                    best[n] = ng
                    prev[n] = idx
                    heapq.heappush(heap, (ng + h(n), ng, n))
        if goal not in prev:
            return []
        cells = []
        while goal is not None:
            cells.append(goal)
            goal = prev[goal]
        return cells[::-1]

    def cells_near(self, points, radius):
        """Free cells whose centre is closer than radius to one of the points (plain arithmetic: it runs
        for every path search; the points are only the AMRs near the one that plans)."""
        near = set()
        free, cols, rows, cell, left, top = self._free_set, self.cols, self.rows, self.cell, self.left, self.top
        reach = int(radius // cell) + 1
        radius2 = radius * radius
        for x, y in points:
            c0 = int((x - left) // cell)
            r0 = int((top - y) // cell)
            for r in range(max(0, r0 - reach), min(rows, r0 + reach + 1)):
                dy2 = (top - (r + 0.5) * cell - y) ** 2
                if dy2 >= radius2:
                    continue
                for c in range(max(0, c0 - reach), min(cols, c0 + reach + 1)):
                    idx = r * cols + c
                    if idx in free and (left + (c + 0.5) * cell - x) ** 2 + dy2 < radius2:
                        near.add(idx)
        return near

    def random_goal(self, rng, start, avoid, max_cells):
        """A random free cell within max_cells of start (else anywhere), not in avoid."""
        r0, c0 = divmod(start, self.cols)
        fallback = None
        for _ in range(30):
            cand = rng.choice(self.free)
            if cand == start or cand in avoid:
                continue
            r, c = divmod(cand, self.cols)
            if max(abs(r - r0), abs(c - c0)) <= max_cells:
                return cand
            fallback = fallback or cand
        return fallback

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
        self.goal = None
        self.wait = 0.0
        self.blocked = 0.0          # seconds blocked by another AMR
        self.patience = 2.0
        self.trips = 0
        self.state = "waiting"

    def _plan(self, nav, rng, others, clearance):
        """New path to a random goal nearby, away from and around the nearby AMRs."""
        start = nav.cell_of(self.x, self.y)
        avoid = nav.cells_near(others, clearance) - {start}
        max_cells = max(4, int(GOAL_RANGE_M * 1000.0 / nav.cell)) if nav.cell < 1000.0 else 50
        for _ in range(4):
            goal = nav.random_goal(rng, start, avoid, max_cells)
            if goal is None:
                break
            cells = nav.path(start, goal, avoid)
            if cells:
                # skip the own cell centre: going back to it can mean moving towards the AMR that blocks
                # this one (endless "blocked"). Safe: this AMR is inside the start cell, which belongs to
                # the first straight run of the path, so the direct line stays inside free cells.
                waypoints = nav.waypoints(cells)
                self.path = waypoints[1:] or waypoints
                self.goal = nav.center(goal)
                return True
        return False

    def step(self, dt, speed, nav, rng, others=(), clearance=0.0, budget=None):
        """others: (x, y) of the nearby AMRs - this AMR never moves closer than clearance to one.
        budget: [path searches left in this step]; without one left the AMR waits a step."""
        if budget is None:
            budget = [1]
        if not self.path:
            self.wait -= dt
            self.state = "waiting"
            self.goal = None
            if self.wait <= 0 and budget[0] > 0:
                budget[0] -= 1
                self._plan(nav, rng, others, clearance)
                self.wait = rng.uniform(1.0, 4.0)
                self.blocked = 0.0
            return
        tx, ty = self.path[0]
        dx, dy = tx - self.x, ty - self.y
        dist = math.hypot(dx, dy)
        if dist < 1e-6:
            self.path.pop(0)
            if not self.path:
                self.trips += 1
            return
        err = (math.atan2(dy, dx) - self.heading + math.pi) % (2 * math.pi) - math.pi
        self.heading += max(-TURN_RATE * dt, min(TURN_RATE * dt, err))
        self.heading = (self.heading + math.pi) % (2 * math.pi) - math.pi   # keep it in [-pi, pi)
        if abs(err) > ALIGN_BEFORE_DRIVING:
            self.state = "turning"
            return
        move = min(speed * dt, dist)
        nx, ny = self.x + dx / dist * move, self.y + dy / dist * move
        for ox, oy in others:   # don't get closer than clearance to another AMR (moving apart is fine)
            d_new = math.hypot(nx - ox, ny - oy)
            if d_new < clearance and d_new < math.hypot(self.x - ox, self.y - oy):
                self.state = "blocked"
                self.blocked += dt
                if self.blocked > self.patience and budget[0] > 0:   # wait, then find a way around
                    budget[0] -= 1
                    self.patience = rng.uniform(1.0, 3.0)
                    self.blocked = 0.0
                    if not self._plan(nav, rng, others, clearance):
                        self.path = []
                return
        self.blocked = 0.0
        self.state = "driving"
        self.x, self.y = nx, ny
        if move >= dist:
            self.path.pop(0)
            if not self.path:
                self.trips += 1
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
        self._cfg = {"enabled": False, "count": 0, "speed": 0.0, "cell": 1.0, "clearance": 0.0, "unit": "deg",
                     "upm": 1000.0}
        self._nav = None
        self._nav_key = None
        self._amrs = []
        self._was_enabled = False
        self._stop = threading.Event()
        self._thread = None

    def configure(self, settings):
        upm = settings["map"]["unitsPerMeter"]
        sim = settings["simulation"]
        length, width = settings["fleet"]["amrLengthM"], settings["fleet"]["amrWidthM"]
        with self._lock:
            self._cfg = {
                "enabled": sim["enabled"],
                "count": sim["amrCount"],
                "speed": sim["speedMps"] * upm,
                "cell": 0.5 * max(length, width) * upm,
                # centre distance at which two footprints (circumscribed circles) can't touch
                "clearance": 1.05 * math.hypot(length, width) * upm,
                "unit": settings["fleet"]["rotationUnit"],
                "upm": upm,
            }

    def start(self):
        self._thread = threading.Thread(target=self._run, name="simulator", daemon=True)
        self._thread.start()

    def stop(self, timeout=5.0):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def _run(self, clock=time.monotonic):
        """Every INTERVAL_S on a fixed rhythm: the work of a step doesn't stretch the period (it waited
        INTERVAL_S *after* the work: 200 AMRs on a CORE = 3.6 instead of 5 updates/s). Behind by more than
        one period (e.g. a long map load) -> continue from now instead of catching up in a burst."""
        last = clock()
        next_tick = last + INTERVAL_S
        while not self._stop.wait(max(0.0, next_tick - clock())):
            now = clock()
            dt, last = min(now - last, 2 * INTERVAL_S), now    # simulated time = real time (capped)
            next_tick += INTERVAL_S
            if next_tick < now:
                next_tick = now + INTERVAL_S
            try:
                text = self.step(dt)
                if text is not None:
                    self._publish(text)
            except Exception:
                log.exception("simulation step failed")

    def _spawn(self, nav, clearance):
        """A free cell centre at least clearance away from the existing AMRs (best effort)."""
        best, best_d = None, -1.0
        for _ in range(60):
            x, y = nav.center(self._rng.choice(nav.free))
            d = min((math.hypot(x - a.x, y - a.y) for a in self._amrs), default=float("inf"))
            if d >= clearance:
                return x, y
            if d > best_d:
                best, best_d = (x, y), d
        return best

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
            x, y = self._spawn(nav, cfg["clearance"])
            self._amrs.append(_SimAmr("AMR-{:02d}".format(n), x, y,
                                      self._rng.uniform(-math.pi, math.pi), 100.0 - 7 * n % 60))
        del self._amrs[cfg["count"]:]
        # neighbours from a coarse grid of positions (bucket >= clearance + this step's moves); positions are
        # read live, so each AMR sees the others' newest position
        size = cfg["clearance"] + 2 * cfg["speed"] * dt + 1.0
        buckets = collections.defaultdict(list)
        for a in self._amrs:
            buckets[(int(a.x // size), int(a.y // size))].append(a)
        budget = [PLANS_PER_STEP]
        for amr in self._amrs:
            bx, by = int(amr.x // size), int(amr.y // size)
            others = [(o.x, o.y) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                      for o in buckets.get((bx + dx, by + dy), ()) if o is not amr]
            amr.step(dt, cfg["speed"], nav, self._rng, others, cfg["clearance"], budget)
        to_unit = (lambda a: a) if cfg["unit"] == "rad" else math.degrees
        return json.dumps({"amrs": [self._report(a, to_unit, cfg["upm"]) for a in self._amrs]},
                          separators=(",", ":"))

    @staticmethod
    def _report(a, to_unit, upm):
        info = {"trips": a.trips}
        if a.goal is not None:
            info["goal"] = "x {:.0f}  y {:.0f}".format(*a.goal)
            info["toGoal"] = "{:.2f} m".format(math.hypot(a.goal[0] - a.x, a.goal[1] - a.y) / upm)
        return {"id": a.id, "x": round(a.x, 2), "y": round(a.y, 2), "rotation": round(to_unit(a.heading), 4),
                "state": a.state, "battery": round(a.battery, 1), "info": info}
