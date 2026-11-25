"""The Data Layer nodes of PLC variables: <root>/connections/<controller>/<address> - pure Python, no ctrlxdatalayer.

settings["nodes"] lists them (added in the web UI's browser, configure role): controller, symbol (TwinCAT path),
address (below connections/<controller>/, segments with '/'), type (the Data Layer type of the PLC variable when it was
added: REAL = float32, ...; arrays array-of-<type>), writable, description, unit.

One wildcard provider node serves all of them (datalayer_service.py, gotchas #88): its callbacks ask this class.
Reads answer the value of the controller's last poll (controllers.py reads every mapped variable of a controller
in one ADS sum read per cycle); writes go to the PLC right away and wait for its answer. A node keeps the type it
was added with: when the PLC variable's type changes (online change), the node reports an error instead of
another type (no conversions, user's choice 2026-11-08).
"""

import threading
import time
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.datalayer_nodes import NodeWriteError, coerce, element_type

BASE = "connections"   # was "plc" up to 0.1.1 (user's choice 2026-11-25)


class NodeUnavailable(Exception):
    """No value to answer: reason "offline" (controller not connected) or "error" (PLC / variable problem)."""

    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


@dataclass
class NodeEntry:
    controller: str
    address: str              # below connections/<controller>/
    symbol: str
    vtype: str
    writable: bool = False
    description: str = ""
    unit: str = ""
    value: Any = None
    ok: bool = False
    error: str = "waiting for the first read"
    offline: bool = True
    updated: float = 0.0      # time.time() of the last value
    max_length: Optional[int] = None    # strings: bytes in the PLC (STRING(80): 80)
    count: Optional[int] = None         # arrays: elements

    @property
    def rel(self):
        return self.controller + "/" + self.address


def node_key(controller, address):
    return controller + "/" + address


SEGMENT_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def default_address(symbol_path):
    """A Data Layer address for a TwinCAT path: POU_Counter.iCounter -> POU_Counter/iCounter,
    GVL.aAxis[1].fPos -> GVL/aAxis/1/fPos, aGrid[1,2] -> aGrid/1_2. Characters a segment can't have become '_'."""
    text = re.sub(r"\[([^\]]*)\]", lambda m: "." + m.group(1).replace(" ", "").replace(",", "_"), symbol_path)
    segments = [re.sub(r"[^A-Za-z0-9_-]", "_", seg)[:64] for seg in text.split(".") if seg]
    return "/".join(segments)


def valid_address(address):
    """Segments of letters, digits, '_' and '-' (1-64 each), at most 16 of them."""
    parts = address.split("/") if address else []
    return 0 < len(parts) <= 16 and all(SEGMENT_RE.fullmatch(p) for p in parts)


class NodeMap:
    def __init__(self, writer: Optional[Callable[[str, str, str, Any], Any]] = None):
        """writer(controller, symbol, vtype, value): writes into the PLC (blocking), raises on failure."""
        self._lock = threading.Lock()
        self._entries: Dict[str, NodeEntry] = {}
        self._folders: Dict[str, List[str]] = {"": []}
        self.writer = writer

    # -- configuration ------------------------------------------------------------------------------------------
    def configure(self, nodes):
        """settings["nodes"] -> entries. Values of nodes that stay (same controller, address, symbol, type) are
        kept."""
        with self._lock:
            old = self._entries
            entries = {}
            for n in nodes:
                key = node_key(n["controller"], n["address"])
                prev = old.get(key)
                e = NodeEntry(n["controller"], n["address"], n["symbol"], n["type"], bool(n.get("writable")),
                              n.get("description", ""), n.get("unit", ""))
                if prev is not None and prev.symbol.lower() == e.symbol.lower() and prev.vtype == e.vtype:
                    e.value, e.ok, e.error, e.offline, e.updated = prev.value, prev.ok, prev.error, prev.offline, \
                        prev.updated
                    e.max_length, e.count = prev.max_length, prev.count
                entries[key] = e
            self._entries = entries
            self._folders = _folders(entries)

    def entries(self, controller=None) -> List[NodeEntry]:
        with self._lock:
            return [e for e in self._entries.values() if controller is None or e.controller == controller]

    def get(self, controller, address):
        with self._lock:
            return self._entries.get(node_key(controller, address))

    def for_symbol(self, controller, symbol):
        """Addresses of the nodes of a PLC variable (browser: 'in the Data Layer as ...')."""
        s = symbol.lower()
        with self._lock:
            return [e.address for e in self._entries.values() if e.controller == controller and e.symbol.lower() == s]

    # -- values from the poll loop --------------------------------------------------------------------------------
    def update(self, controller, address, value=None, error=None, offline=False):
        """A new value (error None) or why there is none."""
        with self._lock:
            e = self._entries.get(node_key(controller, address))
            if e is None:
                return
            if error is None:
                e.value, e.ok, e.error, e.offline, e.updated = value, True, "", False, time.time()
            else:
                e.ok, e.error, e.offline = False, error, offline

    def set_limits(self, controller, address, max_length, count):
        """What the PLC variable allows (STRING length, array elements) - the value stays as it is."""
        with self._lock:
            e = self._entries.get(node_key(controller, address))
            if e is not None:
                e.max_length, e.count = max_length, count

    def set_all(self, controller, error, offline):
        with self._lock:
            for e in self._entries.values():
                if e.controller == controller:
                    e.ok, e.error, e.offline = False, error, offline

    # -- Data Layer (rel = path below <root>/plc, "" = plc itself) ---------------------------------------------------
    def browse(self, rel) -> Optional[List[str]]:
        rel = rel.strip("/")
        with self._lock:
            if rel in self._folders:
                return list(self._folders[rel])
            return [] if rel in self._entries else None

    def describe(self, rel) -> Optional[Tuple]:
        """("folder",) or ("variable", vtype, writable, description, unit), None for an unknown address."""
        rel = rel.strip("/")
        with self._lock:
            if rel in self._folders:
                return ("folder",)
            e = self._entries.get(rel)
            if e is None:
                return None
            return ("variable", e.vtype, e.writable, e.description or "{} ({})".format(e.symbol, e.controller), e.unit)

    def read(self, rel):
        """The last value; NodeWriteError("unknown") / NodeUnavailable."""
        rel = rel.strip("/")
        with self._lock:
            e = self._entries.get(rel)
            if e is None:
                raise NodeWriteError("unknown", "no node " + rel)
            if not e.ok:
                raise NodeUnavailable("offline" if e.offline else "error", e.error)
            return e.vtype, e.value

    def write(self, rel, value):
        """A Data Layer write (value already of the node's type, checked by the caller): range, then the PLC."""
        rel = rel.strip("/")
        with self._lock:
            e = self._entries.get(rel)
        if e is None:
            raise NodeWriteError("unknown", "no node " + rel)
        if not e.writable:
            raise NodeWriteError("readonly", rel + " is read-only")
        value = coerce(e.vtype, value)
        if element_type(e.vtype) is not None and e.count is not None and len(value) != e.count:
            raise NodeWriteError("range", "{}: {} elements expected".format(rel, e.count))
        if self.writer is None:
            raise NodeUnavailable("offline", "not ready")
        self.writer(e.controller, e.symbol, e.vtype, list(value) if isinstance(value, tuple) else value)
        self.update(e.controller, e.address, value)
        return value

    # -- web UI -------------------------------------------------------------------------------------------------
    def snapshot(self, controller=None):
        out = []
        for e in self.entries(controller):
            item = {"controller": e.controller, "address": e.address, "symbol": e.symbol, "type": e.vtype,
                    "writable": e.writable, "description": e.description, "unit": e.unit, "ok": e.ok,
                    "error": e.error or None, "updated": e.updated or None}
            if e.ok:
                item["value"] = _web_value(e.value)
            out.append(item)
        return out

    def counts(self):
        with self._lock:
            return len(self._entries), sum(1 for e in self._entries.values() if not e.ok)


def _folders(entries):
    """Folder path -> child names, for every level above the nodes ('' = plc itself)."""
    folders: Dict[str, List[str]] = {"": []}
    for key in sorted(entries):
        parts = key.split("/")
        for i in range(len(parts)):
            parent = "/".join(parts[:i])
            children = folders.setdefault(parent, [])
            if parts[i] not in children:
                children.append(parts[i])
    for key in entries:
        folders.pop(key, None)   # a node is a variable, not a folder (the settings validator keeps them apart)
    return folders


def _web_value(value):
    """For the web page: 64-bit integers beyond 2^53 as text (JavaScript numbers would round them), arrays as lists."""
    if isinstance(value, (list, tuple)):
        return [_web_value(v) for v in value]
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) > 2 ** 53 - 1:
        return str(value)
    return value
