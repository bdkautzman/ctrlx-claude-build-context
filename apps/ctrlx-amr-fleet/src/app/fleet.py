"""Fleet state: the AMR list written as one JSON string - pure Python, no ctrlX.

Input (Data Layer node <snap>/fleet/amrs, or the built-in simulation), the whole fleet at once:

    {"amrs": [{"id": "AMR-01", "x": 1250.0, "y": 30480.0, "rotation": 90.0}, ...]}
    or just the array:  [{"id": "AMR-01", "x": 1250.0, "y": 30480.0, "rotation": 90.0}, ...]

  id        string or integer, unique, 1..64 characters
  x, y      position in map units (the coordinates of the map file)
  rotation  heading, counter-clockwise from the +x axis; degrees or radians (fleet.rotationUnit)
  optional: name (string), state (string), battery (number 0..100); null = not set.
  Other keys are ignored (room for later extensions).

A write that is not valid is rejected as a whole (the Data Layer write fails with
DL_INVALID_VALUE) and the reason goes to <snap>/fleet/last-error; the previous fleet stays.
Positions are kept in memory only - nothing is written to disk (a PLC may write cyclically).
"""

import json
import logging
import math
import threading
import time

log = logging.getLogger("fleet")

MAX_AMRS = 1000
MAX_TEXT = 64


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
        amrs.append(amr)
    return amrs


class FleetState:
    def __init__(self, rotation_unit="deg", clock=time.monotonic, wallclock=time.time):
        self._rotation_unit = rotation_unit
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
        self._listeners = []

    def configure(self, rotation_unit):
        with self._lock:
            self._rotation_unit = rotation_unit

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
            self._amrs = amrs
            self._revision += 1
            self._updated = self._clock()
            self._received_at = self._wallclock()
            self._source = source
            self._error = ""
        self._notify()
        return amrs

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
                snap["amrs"] = [dict(a) for a in self._amrs]
        return snap

    def _notify(self):
        snap = self.snapshot(include_amrs=False)
        for callback in self._listeners:
            try:
                callback(snap)
            except Exception:
                log.exception("fleet listener failed")
