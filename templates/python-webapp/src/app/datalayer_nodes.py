"""The Data Layer nodes this app provides - pure Python, no ctrlxdatalayer import.

Define your nodes in build_node_specs(). The registry holds the current values; the
ctrlX glue (datalayer_service.py) reads/writes them from the Data Layer callbacks, and
the web API reads/writes them too. Keeping this free of ctrlxdatalayer makes it
unit-testable on any PC.

Address convention:  <DATALAYER_ROOT>/<group>/<name>, lowercase, '-' between words.
Types (= types/datalayer/<type>): bool8, int8, uint8, int16, uint16, int32, uint32, int64, uint64, float32,
float64, string, and array-of-<each of them>. Writes must come with exactly the node's type - nothing is
converted (DL_TYPE_MISMATCH otherwise). So give a node the type of the PLC variable that writes or reads it:
REAL = float32, DINT = int32, ... (table: docs/ctrlx/datalayer-python.md "Node types for PLC variables").
float32 nodes hold float32 values: a value set from Python or the web API is stored as the float32 a PLC REAL
reads (0.1 -> 0.100000001490116), so the registry, the web API and the Data Layer always show the same value.
Array values are tuples.
JSON nodes: a string node with json=True holds a JSON document (lists, summaries, a whole record from
a PLC): set_internal() takes the Python object and stores it as compact JSON, get_json() returns it
parsed, external writes must be valid JSON and on_write gets the parsed object.
Writable nodes: validate EVERY write (type, range, finite). Never persist a value to
disk on each Data Layer write - a PLC may write cyclically (flash wear).
Web API roles (auth.py): view_role = who sees the node in GET /api/v1/values, write_role = who may
write it through PUT /api/v1/values/<name>. Data Layer clients are not affected (ctrlX Data Layer
permissions apply there).
Commands a PLC starts (trigger a measurement, start a job): PlcCommand - Execute / Busy / Done / Error,
the kit's standard handshake (example: commands/set-setpoint in build_node_specs).
"""

import dataclasses
import json
import logging
import math
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

INT_RANGES = {
    "int8": (-2 ** 7, 2 ** 7 - 1), "uint8": (0, 2 ** 8 - 1),
    "int16": (-2 ** 15, 2 ** 15 - 1), "uint16": (0, 2 ** 16 - 1),
    "int32": (-2 ** 31, 2 ** 31 - 1), "uint32": (0, 2 ** 32 - 1),
    "int64": (-2 ** 63, 2 ** 63 - 1), "uint64": (0, 2 ** 64 - 1),
}
SCALAR_TYPES = ("bool8",) + tuple(INT_RANGES) + ("float32", "float64", "string")
ARRAY_OF = "array-of-"
SUPPORTED_TYPES = SCALAR_TYPES + tuple(ARRAY_OF + t for t in SCALAR_TYPES)

log = logging.getLogger("commands")


@dataclass
class NodeSpec:
    name: str                      # address relative to DATALAYER_ROOT, e.g. "values/setpoint"
    vtype: str                     # one of SUPPORTED_TYPES (the PLC variable's type: REAL = float32, ...)
    initial: Any
    writable: bool = False
    description: str = ""
    unit: str = ""
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    max_length: Optional[int] = None   # strings (in arrays: of each element)
    max_items: Optional[int] = None    # arrays: most elements a write may have (minimum/maximum: of each element)
    # called with the validated value BEFORE it is stored; raise NodeWriteError to reject it
    # (json=True: with the parsed object)
    on_write: Optional[Callable[[Any], None]] = field(default=None, repr=False)
    json: bool = False             # string node holding JSON (see the module docstring)
    view_role: str = "view"        # web API: lowest role that sees the node (settings/active: "configure")
    write_role: str = "operate"    # web API: lowest role that may write it (writable nodes only)
    # an object with bind(registry) that made this node (PlcCommand): the registry hands itself over once
    group: Any = field(default=None, repr=False)


class NodeWriteError(Exception):
    """reason is one of: unknown, readonly, type, range, invalid"""

    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


def element_type(vtype):
    """'float32' for 'array-of-float32', None for a scalar type."""
    return vtype[len(ARRAY_OF):] if vtype.startswith(ARRAY_OF) else None


def to_float32(value):
    """The float32 nearest to value, as a Python float: what a PLC REAL / a float32 Data Layer node holds."""
    try:
        return struct.unpack("<f", struct.pack("<f", value))[0]
    except OverflowError:
        raise NodeWriteError("range", "out of the float32 range")


def coerce(vtype, value):
    """Check a JSON/Python value against the node type (range of the type included) and return it as stored:
    ints as int, floats as float (float32: the nearest float32), arrays as tuples. Raises NodeWriteError."""
    elem = element_type(vtype)
    if elem is None:
        return _coerce_scalar(vtype, value)
    if not isinstance(value, (list, tuple)):
        raise NodeWriteError("type", "expected an array")
    out = []
    for i, item in enumerate(value):
        try:
            out.append(_coerce_scalar(elem, item))
        except NodeWriteError as exc:
            raise NodeWriteError(exc.reason, "element {}: {}".format(i, exc))
    return tuple(out)


def _coerce_scalar(vtype, value):
    if vtype == "bool8":
        if isinstance(value, bool):
            return value
        raise NodeWriteError("type", "expected boolean")
    if vtype in INT_RANGES:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise NodeWriteError("type", "expected integer")
        if isinstance(value, float):
            if not value.is_integer():
                raise NodeWriteError("type", "expected integer")
            value = int(value)
        low, high = INT_RANGES[vtype]
        if not low <= value <= high:
            raise NodeWriteError("range", "{} out of range ({}..{})".format(vtype, low, high))
        return value
    if vtype in ("float32", "float64"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise NodeWriteError("type", "expected number")
        value = float(value)
        if not math.isfinite(value):
            raise NodeWriteError("invalid", "value must be finite")
        return to_float32(value) if vtype == "float32" else value
    if vtype == "string":
        if isinstance(value, str):
            return value
        raise NodeWriteError("type", "expected string")
    raise NodeWriteError("type", "unsupported node type " + vtype)


def to_json_text(value):
    """Compact JSON for a json=True node (a str is taken as JSON text already)."""
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


ROLE_NAMES = ("view", "operate", "configure")   # = auth.ROLES (no import: this module stays standalone)


class Commands:
    """The app's actions behind writable command nodes, bound once the object that runs them exists.

    The nodes are built first (DataLayerService(..., commands)), but the object behind them usually needs
    the node registry to publish its results - so main.py creates a Commands, hands it to the nodes, creates
    the worker with datalayer.registry and binds it:

        commands = Commands()
        datalayer = DataLayerService(settings, version, commands)
        worker = Worker(registry=datalayer.registry)
        commands.bind(worker)

    In build_node_specs, an on_write calls e.g. commands.start(profile). Before bind() (and in tests that
    pass None for commands: use need(commands)) every call is rejected with NodeWriteError("invalid") -> the
    writer gets DL_INVALID_VALUE instead of an exception."""

    def __init__(self, target=None):
        self.__dict__["_target"] = target

    def bind(self, target):
        self.__dict__["_target"] = target

    def __getattr__(self, name):
        target = self.__dict__["_target"]
        if target is None:
            raise NodeWriteError("invalid", "the app is not ready yet")
        return getattr(target, name)


def need(commands):
    """commands, or NodeWriteError("invalid") when there is none (build_node_specs called without one)."""
    if commands is None:
        raise NodeWriteError("invalid", "not available")
    return commands


class CommandError(Exception):
    """Raised by a PlcCommand's run function: the command ends with error = true and this text."""


class PlcCommand:
    """A command that a PLC (or the web UI) starts through the Data Layer, PLCopen style: Execute / Busy / Done /
    Error - the kit's standard handshake (user's choice 2026-11-07, docs/ctrlx/datalayer-python.md "PLC commands").

    Nodes below <path>/ (runtime only, never persisted):
      execute     bool8   writable  a rising edge (false -> true) starts the command. Rejected (DL_INVALID_VALUE)
                                    while it is busy; true written again while it is true does nothing, so cyclic
                                    writes are fine
      busy        bool8             true while it runs
      done        bool8             true when it finished without error  } back to false / "" when execute is
      error       bool8             true when it failed                  } written false. Written false while
      error-text  string            why it failed, "" otherwise          } busy: they show the result until the next start
      <param>     ...     writable  parameters (params=[NodeSpec("target", ...)]), read at the rising edge

    PLC: write the parameters -> execute := TRUE -> wait for done OR error -> read the results ->
    execute := FALSE. Write roles in the web API: execute and parameters `write_role` (default operate).

    run(params, registry) runs in a thread of its own, one at a time: params = {name: value} of the parameter nodes
    at the rising edge. Set the app's result nodes with registry.set_internal() before returning - they are complete
    when done goes true. Return = done; raise CommandError("text") = error with that text; any other exception =
    error "internal error: ..." (logged with traceback).
        cmd = PlcCommand("scan/start", run_scan, params=[NodeSpec("profile", "string", "quick", max_length=32)])
        specs = [...] + cmd.specs()      # in build_node_specs
    """

    STATUS = ("busy", "done", "error", "error-text")

    def __init__(self, path, run, params=(), description="", write_role="operate"):
        self.path = path.strip("/")
        self._run = run
        self._param_specs = list(params)
        self.description = description
        self.write_role = write_role
        self._registry = None
        self._lock = threading.Lock()
        self._busy = False
        self._execute = False
        self._thread = None

    def _name(self, name):
        return self.path + "/" + name

    def specs(self):
        """The NodeSpecs of this command (add them to build_node_specs' list)."""
        what = self.description or self.path
        specs = [
            NodeSpec(self._name("execute"), "bool8", False, writable=True, on_write=self._on_execute,
                     write_role=self.write_role, group=self,
                     description="Write true to start: {}. Then wait for done or error and write false (resets "
                                 "them). A start while busy is rejected.".format(what)),
            NodeSpec(self._name("busy"), "bool8", False, group=self, description="true while the command runs"),
            NodeSpec(self._name("done"), "bool8", False, group=self,
                     description="true when it finished without error, until execute is written false"),
            NodeSpec(self._name("error"), "bool8", False, group=self,
                     description="true when it failed, until execute is written false"),
            NodeSpec(self._name("error-text"), "string", "", group=self, description="why it failed, else empty"),
        ]
        for param in self._param_specs:
            specs.append(dataclasses.replace(param, name=self._name(param.name), writable=True,
                                             write_role=self.write_role, group=self))
        return specs

    def bind(self, registry):
        self._registry = registry

    def busy(self):
        with self._lock:
            return self._busy

    def wait(self, timeout=None):
        """Wait until a running command has finished (tests, shutdown). True when nothing runs."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return not self.busy()

    def _set(self, name, value):
        self._registry.set_internal(self._name(name), value)

    def _on_execute(self, value):
        """on_write of <path>/execute: edges decide; raising rejects the write (the node keeps its value)."""
        if self._registry is None:
            raise NodeWriteError("invalid", "not ready")
        with self._lock:
            rising, falling = value and not self._execute, self._execute and not value
            if rising and self._busy:
                raise NodeWriteError("invalid", "{} is busy - wait for done or error".format(self.path))
            self._execute = value
            if falling and not self._busy:
                self._set("done", False)
                self._set("error", False)
                self._set("error-text", "")
            if not rising:
                return
            params = {p.name: self._registry.get(self._name(p.name)) for p in self._param_specs}
            self._set("done", False)
            self._set("error", False)
            self._set("error-text", "")
            self._busy = True
            self._set("busy", True)
            self._thread = threading.Thread(target=self._work, args=(params,), name="command " + self.path,
                                            daemon=True)
            self._thread.start()

    def _work(self, params):
        started = time.monotonic()
        error = None
        try:
            self._run(params, self._registry)
        except CommandError as exc:
            error = str(exc) or "failed"
        except Exception as exc:
            log.exception("Command %s failed", self.path)
            error = "internal error: {}".format(exc)
        with self._lock:          # results first, then done/error, busy last: a PLC polling done sees all of it
            self._set("error-text", error or "")
            self._set("error" if error else "done", True)
            self._busy = False
            self._set("busy", False)
        log.info("Command %s %s after %.0f ms%s", self.path, "failed" if error else "done",
                 (time.monotonic() - started) * 1000, ": " + error if error else "")


class NodeRegistry:
    def __init__(self, root, specs: List[NodeSpec]):
        self.root = root.strip("/")
        self._specs: Dict[str, NodeSpec] = {}
        self._values: Dict[str, Any] = {}
        self._lock = threading.Lock()
        for spec in specs:
            if spec.vtype not in SUPPORTED_TYPES:
                raise ValueError("unsupported type {} for {}".format(spec.vtype, spec.name))
            if spec.name in self._specs:
                raise ValueError("duplicate node " + spec.name)
            if spec.json and spec.vtype != "string":
                raise ValueError("json=True needs vtype string: " + spec.name)
            if spec.view_role not in ROLE_NAMES or spec.write_role not in ROLE_NAMES:
                raise ValueError("unknown role for " + spec.name)
            self._specs[spec.name] = spec
            initial = to_json_text(spec.initial) if spec.json else spec.initial
            self._values[spec.name] = coerce(spec.vtype, initial)
        for group in {id(s.group): s.group for s in specs if s.group is not None}.values():
            group.bind(self)

    def specs(self):
        return list(self._specs.values())

    def address(self, name):
        return self.root + "/" + name

    def get(self, name):
        with self._lock:
            return self._values[name]

    def get_json(self, name):
        """Value of a json=True node, parsed."""
        return json.loads(self.get(name))

    def spec(self, name):
        """The NodeSpec of a node, or None."""
        return self._specs.get(name)

    def set_internal(self, name, value):
        """Set a value from inside the app (no write-permission check, no on_write).
        json=True nodes take the Python object (or JSON text)."""
        spec = self._specs[name]
        if spec.json:
            value = to_json_text(value)
        value = coerce(spec.vtype, value)
        with self._lock:
            self._values[name] = value

    def write(self, name, value):
        """External write (Data Layer client or web API). Validates and calls on_write."""
        spec = self._specs.get(name)
        if spec is None:
            raise NodeWriteError("unknown", "unknown node " + name)
        if not spec.writable:
            raise NodeWriteError("readonly", name + " is read-only")
        value = coerce(spec.vtype, value)
        is_array = element_type(spec.vtype) is not None
        if is_array and spec.max_items is not None and len(value) > spec.max_items:
            raise NodeWriteError("range", "{}: more than {} elements".format(name, spec.max_items))
        for item in (value if is_array else (value,)):
            if isinstance(item, str):
                if spec.max_length is not None and len(item) > spec.max_length:
                    raise NodeWriteError("range", "{} longer than {} characters".format(name, spec.max_length))
            elif not isinstance(item, bool):
                if spec.minimum is not None and item < spec.minimum:
                    raise NodeWriteError("range", "{} must be >= {}".format(name, spec.minimum))
                if spec.maximum is not None and item > spec.maximum:
                    raise NodeWriteError("range", "{} must be <= {}".format(name, spec.maximum))
        if spec.json:
            try:
                parsed = json.loads(value)
            except ValueError as exc:
                raise NodeWriteError("invalid", "{}: not valid JSON ({})".format(name, exc))
            if spec.on_write:
                spec.on_write(parsed)  # may reject the document by raising NodeWriteError
        elif spec.on_write:
            spec.on_write(value)  # may reject the value by raising NodeWriteError
        with self._lock:
            self._values[name] = value
        return value

    def snapshot(self):
        with self._lock:
            values = dict(self._values)
        return {
            name: {
                "address": self.address(name),
                "type": spec.vtype,
                "value": values[name],
                "writable": spec.writable,
                "description": spec.description,
                "unit": spec.unit,
                "json": spec.json,
                "viewRole": spec.view_role,
                "writeRole": spec.write_role,
            }
            for name, spec in self._specs.items()
        }


def build_node_specs(settings, version, commands=None):
    """The nodes of this app. Edit this function to add your own nodes.
    commands: the app's actions for command nodes (a Commands, see above); None in tests that only look at values."""
    example = settings.get("example", {})

    def set_setpoint(params, registry):
        """Example command action (runs in its own thread): copies target into values/setpoint after 1 s."""
        time.sleep(1.0)       # stands for the real work: a measurement, a camera image, a calculation
        if not registry.get("values/enabled"):
            raise CommandError("values/enabled is false")
        registry.set_internal("values/setpoint", params["target"])

    set_setpoint_cmd = PlcCommand(
        "commands/set-setpoint", set_setpoint, description="copy target into values/setpoint (takes 1 s)",
        params=[NodeSpec("target", "float64", 0.0, minimum=-1.0e6, maximum=1.0e6,
                         description="Value for values/setpoint - write it before execute")])
    return [
        NodeSpec("status/version", "string", version, description="Installed app version"),
        NodeSpec("status/state", "string", "starting", description="Application state"),
        NodeSpec("status/heartbeat", "uint64", 0,
                 description="Counter incremented every general.heartbeatIntervalMs"),
        NodeSpec("settings/active", "string", {}, json=True, view_role="configure",
                 description="Active settings as JSON (read-only, change via web UI or app data)"),
        NodeSpec("values/label", "string", example.get("label", ""),
                 description="Example read-only string from settings example.label"),
        NodeSpec("values/setpoint", "float64", example.get("setpoint", 0.0), writable=True,
                 minimum=-1.0e6, maximum=1.0e6,
                 description="Example writable value (runtime only, not persisted)"),
        NodeSpec("values/enabled", "bool8", example.get("enabled", True), writable=True,
                 description="Example writable boolean (runtime only, not persisted)"),
        NodeSpec("values/limits", "array-of-float32", [0.0, 100.0], writable=True, max_items=8,
                 minimum=-1.0e6, maximum=1.0e6,
                 description="Example array, as a PLC ARRAY OF REAL writes it (runtime only, not persisted)"),
        # example command: execute / busy / done / error / error-text + parameter target (PlcCommand)
        *set_setpoint_cmd.specs(),
    ]
