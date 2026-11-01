"""How the AMRs reach the app (setting fleet.input) - pure Python, no ctrlxdatalayer import.

  fleet-json  (default) the whole fleet as one JSON string: fleet/amrs (fleet.py has the format)
  amr-json    one JSON string node per AMR of the AMR list (amr_list.py):  fleet/amr/<id>
                {"x": 1250, "y": 30480, "rotation": 90, "name": "Tugger", "state": "driving",
                 "battery": 80, "info": {"task": "T-4711"}}     (x, y, rotation required)
  amr-fields  one node per value and AMR:  fleet/amr/<id>/x, /y, /rotation, /battery (float64; other
              number types are accepted), /name, /state (string), /info (string: a JSON object)

All writes are merged into one fleet: when the writes pause for QUIET_S (a PLC writes the values of a
cycle together), at the latest MAX_DELAY_S after the first one, the AMRs of the list that have x, y and
rotation go to FleetState.accept() in list order - the speed estimate, the paths and the web UI get
one update per write cycle, not an AMR with a new x and the old y. A write is validated on its own
and rejected as a whole (DL_INVALID_VALUE / DL_TYPE_MISMATCH, reason in fleet/last-error). An empty
string clears name, state or info. Reads return the current fleet, also simulated AMRs (rotation in
fleet.rotationUnit, as written). In the per-AMR modes fleet/amrs is read-only and shows the merged
fleet; writes go to the per-AMR nodes only.

datalayer_service.py serves this under <root>/fleet/amr/** with ONE wildcard provider node: the
addresses come and go with the list without registering a node per AMR and value (200 AMRs x 7
values = 1400 nodes). Paths here are relative to <root>/fleet/amr ("" = that folder).
"""

import json
import logging
import math
import threading
import time

from app.datalayer_nodes import NodeWriteError
from app.fleet import MAX_TEXT, FleetError, _info, parse_amr, rotation_degrees

log = logging.getLogger("amr-inputs")

MODES = ("fleet-json", "amr-json", "amr-fields")
FIELDS = {"x": "float64", "y": "float64", "rotation": "float64", "battery": "float64",
          "name": "string", "state": "string", "info": "string"}
REQUIRED = ("x", "y", "rotation")
QUIET_S = 0.02       # writes paused this long: the cycle is complete
MAX_DELAY_S = 0.2    # writes never pause (many AMRs, writers not in step): publish at least this often
DESCRIPTIONS = {
    "x": "x position in map units", "y": "y position in map units",
    "rotation": "Heading, counter-clockwise from +x, in fleet.rotationUnit",
    "battery": "Battery 0..100 %", "name": "Display name (empty = none)", "state": "State text (empty = none)",
    "info": 'More details as a JSON object, e.g. {"task":"T-4711"} (empty = none)',
}


class AmrInputs:
    def __init__(self, fleet, quiet_s=QUIET_S, max_delay_s=MAX_DELAY_S, start_thread=True):
        self._fleet = fleet
        self._quiet_s, self._max_delay_s = quiet_s, max_delay_s
        self._lock = threading.Lock()
        self._mode = "fleet-json"
        self._ids = []
        self._known = set()
        self._staged = {}            # id -> validated values written so far (rotation in degrees)
        self._dirty = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        if start_thread:
            self._thread = threading.Thread(target=self._flush_loop, name="amr-inputs", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        self._dirty.set()
        if self._thread:
            self._thread.join(5)

    # -- configuration --------------------------------------------------------------
    def configure(self, mode, ids):
        """New input mode or AMR list. Values of AMRs that left the list are dropped; a new mode
        starts empty (the per-AMR modes then show only AMRs written in that mode)."""
        if mode not in MODES:
            raise ValueError("unknown input mode " + str(mode))
        with self._lock:
            changed = mode != self._mode or ids != self._ids
            if mode != self._mode:
                self._staged = {}
                log.info("Input mode %s (%d AMRs in the list)", mode, len(ids))
            self._mode, self._ids, self._known = mode, list(ids), set(ids)
            self._staged = {k: v for k, v in self._staged.items() if k in self._known}
        if changed and mode != "fleet-json":
            self._dirty.set()        # publish the fleet of the new list / mode

    def mode(self):
        with self._lock:
            return self._mode

    def per_amr(self):
        return self.mode() != "fleet-json"

    # -- fleet/amrs -------------------------------------------------------------------
    def write_fleet_json(self, text):
        """A write to fleet/amrs: the fleet JSON in mode fleet-json, read-only in the per-AMR modes."""
        mode = self.mode()
        if mode != "fleet-json":
            message = "fleet/amrs is read-only in input mode {} - write fleet/amr/<id>".format(mode)
            self._fleet.reject(message)
            raise NodeWriteError("readonly", message)
        return self._fleet.receive(text)

    def read_fleet_json(self):
        """fleet/amrs in the per-AMR modes: the merged fleet as written (no speed); None in fleet-json."""
        if not self.per_amr():
            return None
        unit = self._fleet.rotation_unit()
        amrs = []
        for a in self._fleet.snapshot()["amrs"]:
            a.pop("speed", None)
            a["rotation"] = self._rotation_out(a["rotation"], unit)
            amrs.append(a)
        return json.dumps({"amrs": amrs}, separators=(",", ":"))

    # -- the Data Layer view of <root>/fleet/amr -------------------------------------
    def _split(self, rel):
        parts = [p for p in rel.strip("/").split("/") if p]
        return parts if len(parts) <= 2 else None

    def describe(self, rel):
        """("folder",) or ("variable", vtype, description) for an address, None if there is none."""
        parts = self._split(rel)
        with self._lock:
            mode, known = self._mode, self._known
        if parts is None or mode == "fleet-json":
            return None
        if not parts:
            return ("folder",)
        if parts[0] not in known:
            return None
        if len(parts) == 1:
            if mode == "amr-json":
                return ("variable", "string", 'AMR {} as JSON: {{"x":..,"y":..,"rotation":..}} + optional name, '
                        'state, battery, info'.format(parts[0]))
            return ("folder",)
        if mode == "amr-fields" and parts[1] in FIELDS:
            return ("variable", FIELDS[parts[1]], DESCRIPTIONS[parts[1]])
        return None

    def browse(self, rel):
        """Child names, or None for an unknown address."""
        kind = self.describe(rel)
        if kind is None:
            return None
        if kind[0] == "variable":
            return []
        parts = self._split(rel)
        with self._lock:
            return list(self._ids) if not parts else list(FIELDS)

    def read(self, rel):
        """Current value of a variable address (from the fleet - also simulated AMRs)."""
        kind = self.describe(rel)
        if kind is None or kind[0] != "variable":
            raise NodeWriteError("unknown", "no value at fleet/amr/" + rel)
        parts = self._split(rel)
        amr = self._fleet.amr(parts[0])
        unit = self._fleet.rotation_unit()
        if len(parts) == 1:              # amr-json
            if amr is None:
                return ""
            out = {k: amr[k] for k in ("x", "y", "rotation", "name", "state", "battery", "info") if k in amr}
            out["rotation"] = self._rotation_out(out["rotation"], unit)
            return json.dumps(out, separators=(",", ":"))
        field = parts[1]
        value = None if amr is None else amr.get(field)
        if field == "rotation" and value is not None:
            value = self._rotation_out(value, unit)
        if field == "info":
            return json.dumps(value, separators=(",", ":")) if value else ""
        if FIELDS[field] == "string":
            return value or ""
        return float(value) if value is not None else 0.0

    @staticmethod
    def _rotation_out(degrees, unit):
        return round(math.radians(degrees), 6) if unit == "rad" else degrees

    def write(self, rel, value):
        """A Data Layer write: validated, staged, merged into the fleet with the next flush."""
        kind = self.describe(rel)
        if kind is None or kind[0] != "variable":
            raise NodeWriteError("unknown", "fleet/amr/{} does not exist in input mode {}".format(rel, self.mode()))
        parts = self._split(rel)
        ident, where = parts[0], "fleet/amr/" + "/".join(parts)
        try:
            if len(parts) == 1:
                staged = self._parse_json(ident, value, where)
                with self._lock:
                    if ident in self._known:
                        self._staged[ident] = staged
            else:
                field, checked = parts[1], self._check_field(parts[1], value, where)
                with self._lock:
                    if ident in self._known:
                        entry = self._staged.setdefault(ident, {})
                        if checked is None:
                            entry.pop(field, None)
                        else:
                            entry[field] = checked
        except FleetError as exc:
            self._fleet.reject(str(exc))
            raise NodeWriteError("invalid", str(exc))
        self._dirty.set()
        return value

    def _parse_json(self, ident, value, where):
        if not isinstance(value, str):
            raise NodeWriteError("type", where + ": expected a JSON string")
        try:
            item = json.loads(value)
        except ValueError as exc:
            raise FleetError("{}: not valid JSON: {}".format(where, exc))
        if not isinstance(item, dict):
            raise FleetError(where + ": must be a JSON object")
        if "id" in item and str(item["id"]) != ident:
            raise FleetError('{}: "id" is "{}" - the node belongs to {}'.format(where, item["id"], ident))
        return parse_amr(item, ident, where, self._fleet.rotation_unit())

    def _check_field(self, field, value, where):
        """The validated value, or None = clear it (empty name / state / info)."""
        if FIELDS[field] == "float64":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise NodeWriteError("type", where + ": expected a number")
            value = float(value)
            if not math.isfinite(value):
                raise FleetError(where + ": must be a finite number")
            if field == "battery" and not 0 <= value <= 100:
                raise FleetError(where + ": must be 0..100")
            return rotation_degrees(value, self._fleet.rotation_unit()) if field == "rotation" else value
        if not isinstance(value, str):
            raise NodeWriteError("type", where + ": expected a string")
        if not value:
            return None
        if field == "info":
            try:
                parsed = json.loads(value)
            except ValueError as exc:
                raise FleetError("{}: not valid JSON: {}".format(where, exc))
            return _info({"info": parsed}, where[:-len("/info")]) or None
        if len(value) > MAX_TEXT:
            raise FleetError("{}: at most {} characters".format(where, MAX_TEXT))
        return value

    # -- merging -------------------------------------------------------------------
    def _flush_loop(self):
        while not self._stop.is_set():
            self._dirty.wait()
            first = time.monotonic()
            while not self._stop.is_set():         # until the writes pause, or MAX_DELAY_S
                self._dirty.clear()
                self._stop.wait(self._quiet_s)
                if not self._dirty.is_set() or time.monotonic() - first >= self._max_delay_s:
                    break
            if self._stop.is_set():
                break
            try:
                self.flush()
            except Exception:
                log.exception("Publishing the per-AMR fleet failed")

    def flush(self):
        """The AMRs of the list with x, y and rotation -> FleetState (also called by tests)."""
        with self._lock:
            if self._mode == "fleet-json":
                return
            amrs = []
            for ident in self._ids:
                values = self._staged.get(ident)
                if values and all(k in values for k in REQUIRED):
                    amr = {"id": ident}
                    amr.update((k, values[k]) for k in FIELDS if k in values)
                    amrs.append(amr)
        self._fleet.accept(amrs, source="datalayer")
