"""Fleet state: the AMR list written as one JSON string - pure Python, no ctrlX.

Input (Data Layer node <snap>/fleet/amrs, or the built-in simulation), the whole fleet at once:

    {"amrs": [{"id": "AMR-01", "x": 1250.0, "y": 30480.0, "rotation": 90.0}, ...]}
    or just the array:  [{"id": "AMR-01", "x": 1250.0, "y": 30480.0, "rotation": 90.0}, ...]

  id        string or integer, unique, 1..64 characters
  x, y      position in map units (the coordinates of the map file)
  rotation  heading, counter-clockwise from the +x axis; degrees or radians (fleet.rotationUnit)
  optional: name (string), state (string), battery (number 0..100), info (object with up to 16
            more fields for the detail view, e.g. {"task": "T-4711", "load": "pallet", "errors": 0}:
            keys 1..32 characters, values string (<= 64), number or boolean); null = not set.
  Other keys are ignored (room for later extensions).

The app adds `speed` (map units per second) from the change of position between accepted updates.

Path history (setting fleet.pathHistoryMinutes, 0 = off): per AMR the positions of the last minutes,
a point whenever the AMR has moved PATH_STEP_M from the previous one. In memory only; at most
PATH_POINT_BUDGET points for all AMRs together (with many AMRs the paths get shorter). A jump (more
than PATH_JUMP_M at more than PATH_JUMP_SPEED_MPS, e.g. a relocalization) starts the path anew, and an
AMR that is missing from an update loses its path.

A write that is not valid is rejected as a whole (the Data Layer write fails with
DL_INVALID_VALUE) and the reason goes to <snap>/fleet/last-error; the previous fleet stays.
Positions are kept in memory only - nothing is written to disk (a PLC may write cyclically).
"""

import bisect
import json
import logging
import math
import threading
import time
from array import array

log = logging.getLogger("fleet")

MAX_AMRS = 1000
MAX_TEXT = 64
MAX_INFO = 16
MAX_INFO_KEY = 32
SPEED_SMOOTHING = 0.5     # weight of the newest measurement
MIN_SPEED_DT_S = 0.02     # ignore updates closer together than this for the speed
PATH_STEP_M = 0.25        # a new path point after this distance
PATH_POINT_BUDGET = 200_000   # all AMRs together: 24 bytes per point -> <= 4.8 MB
PATH_MAX_POINTS = 20_000      # one AMR (5 km at 0.25 m)
PATH_JUMP_M = 5.0
PATH_JUMP_SPEED_MPS = 15.0


class _Path:
    """Points of one AMR: time (clock) and position in parallel arrays (no Python object per point)."""
    __slots__ = ("t", "x", "y")

    def __init__(self, t, x, y):
        self.t, self.x, self.y = array("d", [t]), array("d", [x]), array("d", [y])

    def append(self, t, x, y):
        self.t.append(t)
        self.x.append(x)
        self.y.append(y)

    def drop_first(self, n):
        if n > 0:
            del self.t[:n], self.x[:n], self.y[:n]


class FleetError(ValueError):
    """The fleet JSON is not valid (message names the offending element)."""


def _number(item, key, where, required=True):
    value = item.get(key)
    if value is None:
        if required:
            raise FleetError("{}.{}: missing".format(where, key))
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise FleetError("{}.{}: must be a finite number".format(where, key))
    return float(value)


def _text(item, key, where):
    value = item.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > MAX_TEXT:
        raise FleetError("{}.{}: must be a string of at most {} characters".format(where, key, MAX_TEXT))
    return value


def _info(item, where):
    value = item.get("info")
    if value is None:
        return None
    if not isinstance(value, dict) or len(value) > MAX_INFO:
        raise FleetError("{}.info: must be an object with at most {} fields".format(where, MAX_INFO))
    info = {}
    for key, v in value.items():
        if not 1 <= len(key) <= MAX_INFO_KEY:
            raise FleetError("{}.info: keys must have 1..{} characters".format(where, MAX_INFO_KEY))
        if v is None:
            continue
        ok = (isinstance(v, bool) or (isinstance(v, str) and len(v) <= MAX_TEXT)
              or (isinstance(v, (int, float)) and math.isfinite(v)))
        if not ok:
            raise FleetError("{}.info.{}: must be a string (<= {} characters), a finite number or a boolean"
                             .format(where, key, MAX_TEXT))
        info[key] = v
    return info


def parse_fleet(text, rotation_unit="deg"):
    """Validate the fleet JSON; return the AMRs with rotation in degrees [0, 360)."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise FleetError("not valid JSON: {}".format(exc))
    items = data.get("amrs") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise FleetError('expected a JSON array of AMRs or an object {"amrs": [...]}')
    if len(items) > MAX_AMRS:
        raise FleetError("more than {} AMRs".format(MAX_AMRS))
    amrs, seen = [], set()
    for n, item in enumerate(items):
        where = "amrs[{}]".format(n)
        if not isinstance(item, dict):
            raise FleetError(where + ": must be an object")
        ident = item.get("id")
        if isinstance(ident, bool) or not isinstance(ident, (str, int)):
            raise FleetError(where + ".id: must be a string or an integer")
        ident = str(ident).strip()
        if not 1 <= len(ident) <= MAX_TEXT:
            raise FleetError("{}.id: must have 1..{} characters".format(where, MAX_TEXT))
        if ident in seen:
            raise FleetError('{}.id: duplicate id "{}"'.format(where, ident))
        seen.add(ident)
        x, y = _number(item, "x", where), _number(item, "y", where)
        rotation = _number(item, "rotation", where)
        if rotation_unit == "rad":
            rotation = math.degrees(rotation)
        amr = {"id": ident, "x": x, "y": y, "rotation": round(rotation % 360.0, 4) % 360.0}
        for key in ("name", "state"):
            value = _text(item, key, where)
            if value is not None:
                amr[key] = value
        battery = _number(item, "battery", where, required=False)
        if battery is not None:
            if not 0 <= battery <= 100:
                raise FleetError(where + ".battery: must be 0..100")
            amr["battery"] = battery
        info = _info(item, where)
        if info:
            amr["info"] = info
        amrs.append(amr)
    return amrs


class FleetState:
    def __init__(self, rotation_unit="deg", clock=time.monotonic, wallclock=time.time,
                 path_minutes=0, units_per_meter=1000.0):
        self._rotation_unit = rotation_unit
        self._path_s = path_minutes * 60.0
        self._units_per_meter = units_per_meter
        self._paths = {}            # id -> _Path
        self._clock = clock
        self._wallclock = wallclock
        self._lock = threading.Lock()
        self._amrs = []
        self._revision = 0          # +1 per accepted update; the web UI polls with ?since=
        self._rejected = 0
        self._updated = None        # clock() of the last accepted update
        self._received_at = None    # wall clock of the last accepted update
        self._source = ""
        self._error = ""
        self._motion = {}           # id -> (x, y, clock, speed) for the speed estimate
        self._listeners = []

    def configure(self, rotation_unit, path_minutes=0, units_per_meter=1000.0):
        with self._lock:
            self._rotation_unit = rotation_unit
            self._path_s = path_minutes * 60.0
            self._units_per_meter = units_per_meter
            if not self._path_s:
                self._paths = {}

    def add_listener(self, callback):
        """callback(snapshot) after every accepted or rejected update (keep it fast)."""
        self._listeners.append(callback)

    def receive(self, text, source="datalayer"):
        """Accept a new fleet JSON or raise FleetError (the previous fleet stays)."""
        with self._lock:
            unit = self._rotation_unit
        try:
            amrs = parse_fleet(text, unit)
        except FleetError as exc:
            with self._lock:
                self._rejected += 1
                self._error = str(exc)
            self._notify()
            raise
        with self._lock:
            now = self._clock()
            self._record_paths(amrs, now)    # before _add_speed: uses the previous positions
            self._add_speed(amrs, now)
            self._amrs = amrs
            self._revision += 1
            self._updated = now
            self._received_at = self._wallclock()
            self._source = source
            self._error = ""
        self._notify()
        return amrs

    def _add_speed(self, amrs, now):
        """speed = smoothed distance per second between accepted updates (needs two updates)."""
        motion = {}
        for amr in amrs:
            prev = self._motion.get(amr["id"])
            speed = None
            if prev is not None:
                px, py, pt, pspeed = prev
                dt = now - pt
                if dt < MIN_SPEED_DT_S:
                    motion[amr["id"]] = prev
                    if pspeed is not None:
                        amr["speed"] = round(pspeed, 3)
                    continue
                measured = math.hypot(amr["x"] - px, amr["y"] - py) / dt
                speed = measured if pspeed is None else SPEED_SMOOTHING * measured + (1 - SPEED_SMOOTHING) * pspeed
                amr["speed"] = round(speed, 3)
            motion[amr["id"]] = (amr["x"], amr["y"], now, speed)
        self._motion = motion

    def _record_paths(self, amrs, now):
        """Runs on every update (200 AMRs at 5/s): most AMRs moved less than a step since their last
        point - then it is one squared distance. Only a step further can be a jump (the previous
        update was within a step of the last point, and a step is far less than PATH_JUMP_M)."""
        if not self._path_s:
            return
        upm = self._units_per_meter
        step2 = (PATH_STEP_M * upm) ** 2
        jump, jump_speed = PATH_JUMP_M * upm, PATH_JUMP_SPEED_MPS * upm
        cap = max(2, min(PATH_MAX_POINTS, PATH_POINT_BUDGET // max(1, len(amrs))))
        cutoff = now - self._path_s
        old, motion, paths = self._paths, self._motion, {}
        for amr in amrs:
            ident, x, y = amr["id"], amr["x"], amr["y"]
            path = old.get(ident)
            if path is None or not path.t:      # new, or all its points were too old (parked)
                path = _Path(now, x, y)
            else:
                dx, dy = x - path.x[-1], y - path.y[-1]
                if dx * dx + dy * dy >= step2:
                    prev = motion.get(ident)
                    d = math.hypot(x - prev[0], y - prev[1]) if prev else 0.0
                    if d > jump and d > jump_speed * (now - prev[2]):   # relocalized: start anew
                        path = _Path(now, x, y)
                    else:
                        path.append(now, x, y)
                if path.t[0] < cutoff or len(path.t) > cap:
                    path.drop_first(max(bisect.bisect_left(path.t, cutoff), len(path.t) - cap))
            paths[ident] = path
        self._paths = paths

    def path(self, ident):
        """{"id", "points": [[x, y, age_s], ...]} oldest first, None for an unknown id. Points older
        than the history are left out (they are dropped from memory with the next update)."""
        with self._lock:
            if ident not in self._paths and not any(a["id"] == ident for a in self._amrs):
                return None
            path = self._paths.get(ident)
            if path is None:
                return {"id": ident, "points": []}
            now = self._clock()
            first = bisect.bisect_left(path.t, now - self._path_s)
            return {"id": ident, "points": [[round(x, 3), round(y, 3), round(now - t, 1)] for t, x, y
                                            in zip(path.t[first:], path.x[first:], path.y[first:])]}

    def snapshot(self, stale_after_s=None, include_amrs=True):
        with self._lock:
            age = None if self._updated is None else max(0.0, self._clock() - self._updated)
            snap = {
                "revision": self._revision,
                "count": len(self._amrs),
                "rejected": self._rejected,
                "source": self._source,
                "lastError": self._error,
                "ageMs": None if age is None else int(age * 1000),
                "receivedAt": self._received_at,
                "stale": age is None or (stale_after_s is not None and age > stale_after_s),
            }
            if include_amrs:
                snap["amrs"] = [dict(a, info=dict(a["info"])) if "info" in a else dict(a) for a in self._amrs]
        return snap

    def _notify(self):
        snap = self.snapshot(include_amrs=False)
        for callback in self._listeners:
            try:
                callback(snap)
            except Exception:
                log.exception("fleet listener failed")
