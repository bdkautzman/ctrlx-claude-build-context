"""Data Layer browser of one core (this device or a connected one) - no ctrlxdatalayer import.

A core's tree starts at a base address: "" for this device, "devices/remotes/cm-<name>/" for a
configured core (its Data Layer remote connection mounts the other device's whole tree there).
Paths in the web API are relative to that base; references in metadata are made relative too.

The client adapter (datalayer_service.ClientAdapter on the device, a fake in the tests) offers:
    browse(address) -> [names]                       metadata(address) -> dict
    browse_many(addresses) -> [(result, [names] | None)]
    metadata_many(addresses) -> [(result, dict | None)]
    read_many(addresses) -> [(result, type name, value)]    (flatbuffers: value None)
    read_json(address) -> {"type": t, "value": v} for simple types, the object for flatbuffers
    write_json(address, text)
Each raises monitor.DataLayerError; the *_many calls report per-address results instead.

Bulk calls keep a folder fast through a remote: 60 nodes ~150 ms instead of ~1.7 s one by one
(measured from the build VM through the COREvirtual to the real CORE, gotchas #79).
"""

import json

from app import dltypes
from app.monitor import DataLayerError

MAX_CHILDREN = 2000          # nodes listed per folder
BULK_CHUNK = 500             # addresses per bulk request (the broker allows 1000: maxBulkRequest)
MAX_TREE_STRING = 300        # characters of a string value shown in the tree
MAX_TREE_ITEMS = 20          # array elements shown in the tree
MAX_VALUE_JSON = 512 * 1024  # bytes of a value in the details pane


def clean_path(path):
    """Path relative to the core's root without leading/trailing '/'. Raises ValueError."""
    if not isinstance(path, str) or len(path) > 1024:
        raise ValueError("invalid path")
    path = path.strip("/")
    if any(ord(c) < 32 for c in path) or (path and "" in path.split("/")):
        raise ValueError("invalid path")
    return path


def join(path, name):
    return name if not path else path + "/" + name


def _ok(result):
    return result in ("OK", "OK_NO_CONTENT")


def _relative(base, address):
    return address[len(base):] if base and address.startswith(base) else address


def describe(meta, base):
    """Metadata for the UI: references relative to the core, read/write type names extracted."""
    meta = dict(meta or {})
    refs = []
    for ref in meta.get("references") or []:
        target = _relative(base, str(ref.get("targetAddress", "")))
        refs.append({"type": ref.get("type", ""), "targetAddress": target})
    meta["references"] = refs
    ops = meta.get("operations") or {}
    meta["operations"] = {k: bool(ops.get(k)) for k in ("read", "write", "create", "delete", "browse")}
    by_type = {r["type"]: r["targetAddress"] for r in refs}
    meta["readType"] = dltypes.type_from_reference(by_type["readType"]) if "readType" in by_type else None
    meta["writeType"] = dltypes.type_from_reference(by_type["writeType"]) if "writeType" in by_type else None
    return meta


def _show_value_in_tree(meta):
    """Read in the tree: readable variables of simple type (objects only in the details pane)."""
    if not meta["operations"]["read"] or meta.get("nodeClass") in ("Type", "Folder", "Collection", "Method"):
        return False
    if meta["readType"] is None:
        return meta.get("nodeClass") == "Variable"
    return meta["readType"] in dltypes.SIMPLE_TYPES


def _tree_value(vtype, value):
    """Short value for a tree row: long strings and arrays cut."""
    value = dltypes.json_safe(value)
    if isinstance(value, str) and len(value) > MAX_TREE_STRING:
        return value[:MAX_TREE_STRING] + "…", True
    if isinstance(value, list) and len(value) > MAX_TREE_ITEMS:
        return value[:MAX_TREE_ITEMS], True
    return value, False


def _split_json(js):
    """read_json result -> (type, value): simple types come as {"type", "value"}, objects as they are."""
    if isinstance(js, dict) and set(js) == {"type", "value"}:
        vtype = dltypes.ALIASES.get(str(js["type"]), str(js["type"]))
        if vtype in dltypes.SIMPLE_TYPES or vtype in ("raw", "unknown"):
            return vtype, js["value"]
    return None, js


class Browser:
    def __init__(self, get_client):
        """get_client() -> the connected client adapter, or None."""
        self._get_client = get_client

    def _client(self):
        client = self._get_client()
        if client is None:
            raise DataLayerError("CLIENT_NOT_CONNECTED", "Data Layer not connected")
        return client

    @staticmethod
    def _chunks(items):
        for i in range(0, len(items), BULK_CHUNK):
            yield items[i:i + BULK_CHUNK]

    def _many(self, fn, addresses):
        out = []
        for chunk in self._chunks(addresses):
            out.extend(fn(chunk))
        return out

    # -- tree ----------------------------------------------------------------
    def children(self, base, path):
        """Child nodes of path with node class, operations, 'has children' and (simple) values."""
        client = self._client()
        path = clean_path(path)
        names = client.browse(base + path)
        truncated = len(names) > MAX_CHILDREN
        names = names[:MAX_CHILDREN]
        paths = [join(path, n) for n in names]
        addresses = [base + p for p in paths]
        metas = self._many(client.metadata_many, addresses)
        browses = self._many(client.browse_many, addresses)
        entries = []
        for name, child, (mres, meta), (bres, kids) in zip(names, paths, metas, browses):
            entry = {"name": name, "path": child, "hasChildren": bool(_ok(bres) and kids)}
            if _ok(mres) and meta is not None:
                info = describe(meta, base)
                entry.update(nodeClass=info.get("nodeClass"), operations=info["operations"],
                             type=info["readType"] or info["writeType"], unit=info.get("unit") or "")
                entry["_read"] = _show_value_in_tree(info)
            else:
                entry.update(nodeClass=None, operations=None, type=None, metadataError=mres)
                entry["_read"] = False
            entries.append(entry)
        self._add_values(client, base, [e for e in entries if e.pop("_read")])
        return {"path": path, "children": entries, "truncated": truncated}

    def _add_values(self, client, base, entries):
        reads = self._many(client.read_many, [base + e["path"] for e in entries])
        for entry, (result, vtype, value) in zip(entries, reads):
            if not _ok(result):
                entry["valueError"] = result
                continue
            entry["type"] = entry.get("type") or vtype
            if vtype in ("flatbuffers", "raw", "unknown"):
                entry["valueType"] = vtype
                continue
            entry["value"], cut = _tree_value(vtype, value)
            if cut:
                entry["valueCut"] = True

    def values(self, base, paths):
        """Current values of tree rows (refresh): [{"path", "value" | "valueError"}]."""
        client = self._client()
        paths = [clean_path(p) for p in paths][:MAX_CHILDREN]
        entries = [{"path": p} for p in paths]
        self._add_values(client, base, entries)
        return entries

    # -- one node --------------------------------------------------------------
    def node(self, base, path):
        """Details of one node: metadata, value (also objects), number of children."""
        client = self._client()
        path = clean_path(path)
        address = base + path
        result = {"path": path}
        if path:
            info = describe(client.metadata(address), base)
        else:  # the root: no metadata of its own (a remote's root would show the remote connection node)
            info = describe({"nodeClass": "Folder", "operations": {"browse": True}}, base)
        result["metadata"] = info
        result["type"] = info["readType"] or info["writeType"]
        try:
            result["childCount"] = len(client.browse(address)) if info["operations"]["browse"] else 0
        except DataLayerError:
            result["childCount"] = 0
        if info["operations"]["read"] and info.get("nodeClass") not in ("Type", "Method"):
            result.update(self._value(client, address))
        return result

    def value(self, base, path):
        client = self._client()
        return self._value(client, base + clean_path(path))

    def _value(self, client, address):
        try:
            vtype, value = _split_json(client.read_json(address))
        except DataLayerError as exc:
            return {"valueError": exc.result, "valueErrorDetail": str(exc) if str(exc) != exc.result else ""}
        value = dltypes.json_safe(value)
        if len(json.dumps(value)) > MAX_VALUE_JSON:
            return {"valueType": vtype or "object", "valueError": "TOO_LARGE",
                    "valueErrorDetail": "value larger than {} kB".format(MAX_VALUE_JSON // 1024)}
        out = {"value": value, "valueType": vtype or "object"}
        return out

    def write(self, base, path, body):
        """Write a value. body: {"value": ...} - typed by the node's write type (flatbuffers: a JSON object)."""
        client = self._client()
        path = clean_path(path)
        address = base + path
        if not isinstance(body, dict) or "value" not in body:
            raise ValueError('body must be {"value": ...}')
        info = describe(client.metadata(address), base)
        if not info["operations"]["write"]:
            raise DataLayerError("PERMISSION_DENIED", "the node is read-only")
        vtype = info["writeType"] or info["readType"]
        if vtype is None:  # no type reference: take the type of the current value
            vtype, _ = _split_json(client.read_json(address))
        if vtype in dltypes.SIMPLE_TYPES:
            value = dltypes.coerce(vtype, body["value"])
            text = json.dumps({"type": vtype, "value": value})
        elif vtype in ("raw", "unknown"):
            raise ValueError("values of type {} can't be written here".format(vtype))
        else:  # flatbuffers: the plain JSON object (gotchas #79)
            value = body["value"]
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except ValueError:
                    raise ValueError("expected a JSON object")
            if not isinstance(value, (dict, list)):
                raise ValueError("expected a JSON object")
            text = json.dumps(value)
        client.write_json(address, text)
        out = {"path": path}
        out.update(self._value(client, address))
        return out
