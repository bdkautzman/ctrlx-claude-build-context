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
    # called with (new value, old value) AFTER an accepted write was stored - for actions that
    # set the node again themselves (a trigger the app resets to false when done)
    after_write: Optional[Callable[[Any, Any], None]] = field(default=None, repr=False)


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
            old = self._values[name]
            self._values[name] = value
        if spec.after_write:
            spec.after_write(value, old)
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




class ReaderBusyError(Exception):
    """Raised by the reader when no more reads can be queued."""


def build_node_specs(settings, version, commands=None):
    """The nodes of this app. commands: the reader (request_read, can_queue, set_continuous);
    None in tests that only look at the values."""

    def need_commands():
        if commands is None:
            raise NodeWriteError("invalid", "reader not available")
        return commands

    def on_trigger(value):
        if value and not need_commands().can_queue():
            raise NodeWriteError("invalid", "busy: too many reads waiting")

    def after_trigger(value, old):
        # true while a trigger read is queued/running: a second true is ignored; the reader sets
        # the node back to false when that read's result is published
        if value and not old:
            commands.request_read("trigger")

    def on_request_id(value):
        if value != 0 and not need_commands().can_queue():
            raise NodeWriteError("invalid", "busy: too many reads waiting")

    def after_request_id(value, old):
        # a new id starts a read; writing the same id again (cyclic writes) or 0 does nothing
        if value != 0 and value != old:
            commands.request_read("request", request_id=value)

    def on_continuous(value):
        need_commands()

    def after_continuous(value, old):
        commands.set_continuous(value)

    return [
        NodeSpec("status/version", "string", version, description="Installed app version"),
        NodeSpec("status/state", "string", "starting", description="Application state"),
        NodeSpec("status/heartbeat", "uint64", 0,
                 description="Counter incremented every general.heartbeatIntervalMs"),
        NodeSpec("settings/active", "string", "{}",
                 description="Active settings as JSON (read-only, change via web UI or app data)"),
        # -- commands ------------------------------------------------------------------
        NodeSpec("trigger", "bool8", False, writable=True, on_write=on_trigger, after_write=after_trigger,
                 description="Write true to capture an image and decode it. The app sets it back to false "
                             "when the result is published (result/sequence counts up)."),
        NodeSpec("request-id", "uint64", 0, writable=True, on_write=on_request_id, after_write=after_request_id,
                 description="Write a new id (not 0, not the current value) to capture and decode; the result "
                             "carries it in result/request-id. Rejected with DL_INVALID_VALUE when 4 reads wait."),
        NodeSpec("continuous", "bool8", False, writable=True, on_write=on_continuous, after_write=after_continuous,
                 description="true: read continuously (settings continuous.intervalMs) until false is written"),
        NodeSpec("busy", "bool8", False, description="A read is waiting or running"),
        # -- newest result ---------------------------------------------------------------
        NodeSpec("result/sequence", "uint64", 0,
                 description="Counts up by one for every published result (after all other result nodes)"),
        NodeSpec("result/request-id", "uint64", 0,
                 description="request-id of the read this result belongs to (0: trigger, UI or continuous)"),
        NodeSpec("result/source", "string", "", description="trigger | request | ui | continuous"),
        NodeSpec("result/ok", "bool8", False, description="At least one code was decoded and no error occurred"),
        NodeSpec("result/count", "uint64", 0, description="Number of decoded codes"),
        NodeSpec("result/text", "string", "", description="Text of the first code (top left first)"),
        NodeSpec("result/format", "string", "", description="Symbology of the first code, e.g. QRCode, Code128, DataMatrix"),
        NodeSpec("result/codes", "string", "[]",
                 description='All codes as JSON: [{"format","text","corners":[[x,y],...]}] (sensor pixels)'),
        NodeSpec("result/duration-ms", "float64", 0.0, unit="ms",
                 description="Time from the trigger to the decoded result"),
        NodeSpec("result/grab-ms", "float64", 0.0, unit="ms",
                 description="Time from the start of the acquisition to the complete image"),
        NodeSpec("result/decode-ms", "float64", 0.0, unit="ms", description="Time spent decoding the image"),
        NodeSpec("result/timestamp", "string", "", description="Time of the image (ISO 8601, device time zone)"),
        NodeSpec("result/error", "string", "", description="Error text when the read failed (camera, timeout), else empty"),
        NodeSpec("result/json", "string", "{}",
                 description="The whole result as one JSON object (sequence, requestId, ok, codes, ...) - "
                             "read this node to get a consistent set"),
        # -- camera ------------------------------------------------------------------------
        NodeSpec("camera/state", "string", "disconnected",
                 description="connected | connecting | disconnected | error"),
        NodeSpec("camera/model", "string", "", description="Vendor and model of the connected camera"),
        NodeSpec("camera/address", "string", "", description="Address of the connected camera"),
        NodeSpec("camera/error", "string", "", description="Last camera error"),
        NodeSpec("camera/frames", "uint64", 0, description="Images received since the app started"),
        NodeSpec("camera/failures", "uint64", 0, description="Failed or incomplete images since the app started"),
        NodeSpec("camera/resent-packets", "uint64", 0,
                 description="Image packets requested again since the camera was opened: the device is close to its limit"),
        NodeSpec("camera/missing-packets", "uint64", 0,
                 description="Image packets lost for good since the camera was opened"),
    ]
