"""In-memory stand-in for datalayer_service.ClientAdapter (browser, KVD and web tests; no ctrlX needed).

nodes: {full address: {"nodeClass", "type", "value", "write", "unit", "description", "readType"}};
folders between the addresses exist implicitly. Behaves like ctrlX OS 4.6 as measured (gotchas #79):
read_json gives {"type", "value"} for simple types and the plain object for flatbuffers, write_json
wants the same forms, folders can't be read, missing nodes answer INVALID_ADDRESS.
kvd_file: simulates KVD - every entry of that nodes.json exists below kvd_base (unless kvd_ignores);
kvd_miss: number of file writes KVD misses (it keeps the nodes of the previous version, gotchas #80).
"""

import json
import os

from app import dltypes
from app.monitor import DataLayerError

TYPE_BASE = "types/datalayer/"


class FakeAdapter:
    def __init__(self, nodes=None, kvd_file=None, kvd_base="", kvd_ignores=False):
        self.nodes = dict(nodes or {})
        self.kvd_file = kvd_file
        self.kvd_base = kvd_base
        self.kvd_ignores = kvd_ignores
        self.kvd_miss = 0
        self.writes = []
        self.calls = []
        self._kvd_version = None
        self._kvd_nodes = []

    def _kvd_entries(self):
        """The entries KVD applied: re-read when the file was written (unless that write is missed)."""
        st = os.stat(self.kvd_file)
        version = (st.st_ino, st.st_mtime_ns, st.st_size)
        if version != self._kvd_version:
            self._kvd_version = version
            if self.kvd_miss:
                self.kvd_miss -= 1
            else:
                with open(self.kvd_file, encoding="utf-8") as f:
                    self._kvd_nodes = json.load(f).get("nodes", [])
        return self._kvd_nodes

    def _all(self):
        nodes = dict(self.nodes)
        if self.kvd_file and not self.kvd_ignores and os.path.exists(self.kvd_file):
            for n in self._kvd_entries():
                meta = n.get("metadata", {})
                cls = meta.get("nodeClass", "Variable")
                nodes.setdefault(self.kvd_base + n["address"], {
                    "nodeClass": cls, "type": n.get("type") if cls == "Variable" else None,
                    "value": n.get("value", 0), "write": (meta.get("operations") or {}).get("write", True),
                    "unit": meta.get("unit", ""), "description": meta.get("description", "")})
        return nodes

    def _children(self, address):
        prefix = address if (not address or address.endswith("/")) else address + "/"
        names = []
        for a in sorted(self._all()):
            if a.startswith(prefix) and len(a) > len(prefix):
                name = a[len(prefix):].split("/")[0]
                if name not in names:
                    names.append(name)
        return names

    def _node(self, address):
        address = address.rstrip("/")
        nodes = self._all()
        if address in nodes:
            return nodes[address]
        if self._children(address):
            return {"nodeClass": "Folder", "type": None}
        raise DataLayerError("INVALID_ADDRESS")

    # -- adapter API ---------------------------------------------------------
    def browse(self, address):
        self.calls.append(("browse", address))
        if address and not address.endswith("/"):
            self._node(address)
        return self._children(address)

    def metadata(self, address):
        self.calls.append(("metadata", address))
        node = self._node(address)
        cls = node["nodeClass"]
        readable = cls in ("Variable", "Resource") or node.get("empty", False)
        meta = {"nodeClass": cls, "description": node.get("description", ""), "unit": node.get("unit", ""),
                "displayFormat": "Auto", "extensions": [], "references": [],
                "operations": {"read": readable, "write": readable and node.get("write", False), "create": False,
                               "delete": False, "browse": True}}
        t = node.get("type")
        vtype = node.get("readType") or (TYPE_BASE + ("array-of-" + t[2:] if t.startswith("ar") else t) if t else None)
        if vtype:
            base = self._base_of(address)
            meta["references"] = [{"type": "readType", "targetAddress": base + vtype}]
            if meta["operations"]["write"]:
                meta["references"].append({"type": "writeType", "targetAddress": base + vtype})
        return meta

    @staticmethod
    def _base_of(address):
        """References of a remote's nodes carry the remote prefix (seen on ctrlX OS 4.6)."""
        if address.startswith("devices/remotes/"):
            return "/".join(address.split("/")[:3]) + "/"
        return ""

    def read_json(self, address):
        node = self._node(address)
        if node.get("empty"):
            return None   # read OK, empty value (a KVD collection on ctrlX OS 4.6)
        if node["nodeClass"] not in ("Variable", "Resource"):
            raise DataLayerError("INVALID_ADDRESS")
        vtype = node.get("type")
        if vtype in dltypes.SIMPLE_TYPES:
            return {"type": vtype, "value": node.get("value")}
        return node.get("value")

    def write_json(self, address, text):
        node = self._node(address)
        if not node.get("write"):
            raise DataLayerError("PERMISSION_DENIED")
        data = json.loads(text)
        if node.get("type") in dltypes.SIMPLE_TYPES:
            if not isinstance(data, dict) or set(data) != {"type", "value"}:
                raise DataLayerError("FAILED", "1: 2: error: declaration expected")
            node["value"] = data["value"]
        else:
            if isinstance(data, dict) and "type" in data:
                raise DataLayerError("FAILED", "1: 16: error: unknown field: type")
            node["value"] = data
        self.writes.append((address, text))

    def _many(self, fn, addresses, failed):
        out = []
        for a in addresses:
            try:
                out.append(("OK",) + fn(a))
            except DataLayerError as exc:
                out.append((exc.result,) + failed)
        return out

    def metadata_many(self, addresses):
        self.calls.append(("metadata_many", len(addresses)))
        return self._many(lambda a: (self.metadata(a),), addresses, (None,))

    def browse_many(self, addresses):
        return self._many(lambda a: (self._children(a),), addresses, (None,))

    def read_many(self, addresses):
        def one(a):
            node = self._node(a)
            if node["nodeClass"] not in ("Variable", "Resource"):
                raise DataLayerError("INVALID_ADDRESS")
            vtype = node.get("type")
            return (vtype, node.get("value")) if vtype in dltypes.SIMPLE_TYPES else ("flatbuffers", None)
        return self._many(one, addresses, (None, None))
