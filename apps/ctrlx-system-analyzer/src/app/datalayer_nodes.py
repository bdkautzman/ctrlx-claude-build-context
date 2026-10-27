"""The Data Layer nodes this app provides - pure Python, no ctrlxdatalayer import.

Define your nodes in build_node_specs(). The registry holds the current values; the
ctrlX glue (datalayer_service.py) reads/writes them from the Data Layer callbacks, and
the web API reads/writes them too. Keeping this free of ctrlxdatalayer makes it
unit-testable on any PC.

Address convention:  <DATALAYER_ROOT>/<group>/<name>, lowercase, '-' between words.
Types: bool8, int64, uint64, float64, string  (map to types/datalayer/<type>).
Writable nodes: validate EVERY write (type, range, finite). Never persist a value to
disk on each Data Layer write - a PLC may write cyclically (flash wear).
"""

import math
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

SUPPORTED_TYPES = ("bool8", "int64", "uint64", "float64", "string")


@dataclass
class NodeSpec:
    name: str                      # address relative to DATALAYER_ROOT, e.g. "values/setpoint"
    vtype: str                     # one of SUPPORTED_TYPES
    initial: Any
    writable: bool = False
    description: str = ""
    unit: str = ""
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    max_length: Optional[int] = None
    # called with the validated value BEFORE it is stored; raise NodeWriteError to reject it
    on_write: Optional[Callable[[Any], None]] = field(default=None, repr=False)


class NodeWriteError(Exception):
    """reason is one of: unknown, readonly, type, range, invalid"""

    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


def coerce(vtype, value):
    """Convert a JSON/Python value to the node type or raise NodeWriteError('type')."""
    if vtype == "bool8":
        if isinstance(value, bool):
            return value
        raise NodeWriteError("type", "expected boolean")
    if vtype in ("int64", "uint64"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise NodeWriteError("type", "expected integer")
        if isinstance(value, float):
            if not value.is_integer():
                raise NodeWriteError("type", "expected integer")
            value = int(value)
        if vtype == "uint64" and not 0 <= value <= 2 ** 64 - 1:
            raise NodeWriteError("range", "uint64 out of range")
        if vtype == "int64" and not -(2 ** 63) <= value <= 2 ** 63 - 1:
            raise NodeWriteError("range", "int64 out of range")
        return value
    if vtype == "float64":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise NodeWriteError("type", "expected number")
        value = float(value)
        if not math.isfinite(value):
            raise NodeWriteError("invalid", "value must be finite")
        return value
    if vtype == "string":
        if isinstance(value, str):
            return value
        raise NodeWriteError("type", "expected string")
    raise NodeWriteError("type", "unsupported node type " + vtype)


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
            self._specs[spec.name] = spec
            self._values[spec.name] = coerce(spec.vtype, spec.initial)

    def specs(self):
        return list(self._specs.values())

    def address(self, name):
        return self.root + "/" + name

    def get(self, name):
        with self._lock:
            return self._values[name]

    def set_internal(self, name, value):
        """Set a value from inside the app (no write-permission check, no on_write)."""
        spec = self._specs[name]
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
        if spec.minimum is not None and value < spec.minimum:
            raise NodeWriteError("range", "{} must be >= {}".format(name, spec.minimum))
        if spec.maximum is not None and value > spec.maximum:
            raise NodeWriteError("range", "{} must be <= {}".format(name, spec.maximum))
        if spec.max_length is not None and isinstance(value, str) and len(value) > spec.max_length:
            raise NodeWriteError("range", "{} longer than {} characters".format(name, spec.max_length))
        if spec.on_write:
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
            }
            for name, spec in self._specs.items()
        }


def build_node_specs(settings, version, commands=None):
    """The nodes of this app. commands: object with add_marker(text) for the writable marker node
    (None in tests that only look at the values). Summary nodes are JSON strings: the set of apps
    changes at runtime, the registry's nodes don't."""

    def on_marker(value):
        value = value.strip()
        if not value:
            return                       # writing "" just clears the node
        if commands is None:
            raise NodeWriteError("invalid", "markers not available")
        commands.add_marker(value)

    return [
        NodeSpec("status/version", "string", version, description="Installed app version"),
        NodeSpec("status/state", "string", "starting", description="Application state"),
        NodeSpec("status/heartbeat", "uint64", 0,
                 description="Counter incremented every general.heartbeatIntervalMs"),
        NodeSpec("settings/active", "string", "{}",
                 description="Active settings as JSON (read-only, change via web UI or app data)"),
        NodeSpec("collector/samples", "uint64", 0, description="Samples taken since the app started"),
        NodeSpec("collector/sample-cpu-ms", "float64", 0.0, unit="ms",
                 description="CPU time the last sample of /proc took (the analyzer's own load)"),
        NodeSpec("system/cpu-percent", "float64", 0.0, unit="%",
                 description="CPU load of all cores over the last sample interval"),
        NodeSpec("apps/summary", "string", "[]",
                 description="Per app (JSON array): key, name, cpu [% of one core], anonMb, rssMb, pssMb, "
                             "threads, processes - updated every sample"),
        NodeSpec("analysis/growing", "string", "[]",
                 description="Apps whose memory grew steadily over the last leak.nodeWindowMinutes (JSON "
                             "array: key, name, growthMbPerHour, fit, hoursUntilFull) - updated every minute"),
        NodeSpec("events/marker", "string", "", writable=True, max_length=200, on_write=on_marker,
                 description="Write a text to mark a moment in the charts and the recording, e.g. "
                             "'test started' from the PLC"),
        NodeSpec("recording/state", "string", "off",
                 description="External medium recording: off, waiting (medium not mounted), recording, error"),
    ]
