"""Data Layer client: read, write, browse and metadata of OTHER nodes (a PLC's variables, system/info, nodes of
another app). From ctrlx-core-connector's ClientAdapter (verified there on ctrlX OS 4.6, CORE X3 + X7; gotchas
#79, #87), moved into the template 2026-11-08 without its app-specific calls.

Get one from the provider's connection: DataLayerService(..., on_client=callback). The service opens the client
when the provider is running and calls callback(client); when the connection is gone it calls callback(None)
and closes the client (after a reconnect: callback(new client)). Keep the reference only in between:

    def attach(client):              # in your worker: a Data Layer thread calls it
        self._dl = client            # None = no Data Layer right now
    datalayer = DataLayerService(settings, version, commands, on_client=worker.attach)

Single-address calls raise DataLayerError(result name, e.g. "INVALID_ADDRESS"); the *_many calls (one bulk
request for many addresses) return one tuple per address instead. One lock: worker threads and web server
threads may share the client; every call waits for the answer (sync API) - keep them out of Data Layer callbacks.
Type names are the template's (datalayer_nodes.SUPPORTED_TYPES: float32, array-of-int16, ...): write() needs the
exact type of the node it writes (a PLC REAL: "float32") - nothing is converted (user's choice 2026-11-08).
Values that change often: subscriptions are cheaper than polling (SDK sample datalayer.client.sub) - not in the
template yet, no kit app has needed one.

The ctrlxdatalayer imports are inside the methods: this module also loads on a PC without the native library.
"""

import json
import threading

from app.datalayer_nodes import SUPPORTED_TYPES, NodeWriteError, coerce

# Result names differ between the APIs (gotchas #78): REST and C say DL_INVALID_ADDRESS, the Python enum
# INVALID_ADDRESS; two enum names are garbled in ctrlxdatalayer 3.x.
_RESULT_ALIASES = {"sec_noSEC_NO_TOKEN_token": "SEC_NO_TOKEN", "SEC_INVALIDSESSION": "SEC_INVALID_SESSION"}


def normalize_result(result):
    """Result name as the Python library spells it, without "DL_" (REST/C) and with the garbled names fixed."""
    result = str(result or "")
    if result.startswith("DL_"):
        result = result[3:]
    return _RESULT_ALIASES.get(result, result)


class DataLayerError(Exception):
    """result: the Data Layer result name, normalized (INVALID_ADDRESS, TYPE_MISMATCH, ...)."""

    def __init__(self, result, message=""):
        self.result = normalize_result(result)
        super().__init__(message or self.result)


def type_name(variant_type_name):
    """VariantType name -> the template's type name: FLOAT32 -> float32, ARRAY_INT16 -> array-of-int16,
    ARRAY_OF_TIMESTAMP -> array-of-timestamp, FLATBUFFERS -> flatbuffers, UNKNON -> unknown."""
    name = variant_type_name.lower()
    if name == "unknon":                      # sic, ctrlxdatalayer 3.x
        return "unknown"
    for prefix in ("array_of_", "array_"):
        if name.startswith(prefix):
            return "array-of-" + name[len(prefix):]
    return name


def variant_value(data):
    """(type name, JSON-able value) of a Variant. Flatbuffers / raw / unknown: value None (read those with
    read_json). Timestamps as ISO text."""
    from ctrlxdatalayer.variant import VariantType
    vtype = data.get_type()
    name = type_name(vtype.name)
    if vtype in (VariantType.FLATBUFFERS, VariantType.RAW, VariantType.UNKNON):
        return name, None
    if vtype == VariantType.TIMESTAMP:
        return name, data.get_datetime().isoformat()
    if vtype == VariantType.ARRAY_OF_TIMESTAMP:
        return name, [d.isoformat() for d in data.get_array_datetime()]
    getter = getattr(data, "get_" + vtype.name.lower(), None)
    return (name, getter()) if getter else ("unknown", None)


class DataLayerClient:
    def __init__(self, system, client):
        """system: the ctrlxdatalayer System, client: system.factory().create_client(connection)."""
        self._system = system
        self._client = client
        self._lock = threading.RLock()
        self._schema = None   # metadata schema for the JSON converter, fetched once

    def close(self):
        with self._lock:
            if self._schema is not None:
                self._schema.close()
                self._schema = None

    # -- single addresses ---------------------------------------------------------------------------------------
    def read(self, address):
        """Value of a node as a Python object: scalars, lists for arrays, flatbuffers objects as dict (via the
        JSON converter), timestamps as ISO text."""
        from ctrlxdatalayer.variant import Result, VariantType
        with self._lock:
            result, data = self._client.read_sync(address)
            with data:
                if result != Result.OK:
                    raise DataLayerError(result.name)
                if data.get_type() != VariantType.FLATBUFFERS:
                    name, value = variant_value(data)
                    if name in ("raw", "unknown"):
                        raise DataLayerError("TYPE_MISMATCH", "{}: type {} can't be read here".format(address, name))
                    return value
        value = self.read_json(address)
        # the converter wraps simple values like the REST API does: {"type": ..., "value": ...}
        if isinstance(value, dict) and set(value) >= {"type", "value"}:
            value = value["value"]
        return value

    def read_typed(self, address):
        """(type name, value) of a node with a simple type; flatbuffers: ("flatbuffers", None) - use read()."""
        from ctrlxdatalayer.variant import Result
        with self._lock:
            result, data = self._client.read_sync(address)
            with data:
                if result != Result.OK:
                    raise DataLayerError(result.name)
                return variant_value(data)

    def read_json(self, address):
        """Value as the JSON converter writes it: {"type": t, "value": v} for simple types, the plain object for
        flatbuffers (gotchas #79); None for a node without a value (a KVD collection reads OK with "", #87)."""
        from ctrlxdatalayer.variant import Result
        with self._lock:
            result, data = self._client.read_json_sync(self._system.json_converter(), address, -1)
            with data:
                if result != Result.OK:
                    raise DataLayerError(result.name)
                text = data.get_string()
        return json.loads(text) if text and text.strip() else None

    def write(self, address, vtype, value):
        """Write value with exactly the type vtype (the target node's type, e.g. "float32" for a PLC REAL,
        "array-of-int16"). The value is checked against the type's range first (ValueError); the node answers
        TYPE_MISMATCH for any other type. Returns nothing; raises DataLayerError."""
        from ctrlxdatalayer.variant import Result, Variant
        if vtype not in SUPPORTED_TYPES:
            raise ValueError("unsupported type {} (datalayer_nodes.SUPPORTED_TYPES)".format(vtype))
        try:
            value = coerce(vtype, value)
        except NodeWriteError as exc:
            raise ValueError("{}: {}".format(address, exc))
        with self._lock, Variant() as data:
            setter = "set_" + vtype.replace("array-of-", "array_")
            result = getattr(data, setter)(value)
            if result != Result.OK:
                raise DataLayerError(result.name, "Variant.{} failed".format(setter))
            result, _ = self._client.write_sync(address, data)
        if result != Result.OK:
            raise DataLayerError(result.name)

    def write_json(self, address, value):
        """Write through the JSON converter: {"type": "int32", "value": 5} for simple types, the object for a
        flatbuffers node (a dict, or JSON text)."""
        from ctrlxdatalayer.variant import Result, VariantType
        text = value if isinstance(value, str) else json.dumps(value)
        with self._lock:
            result, error = self._client.write_json_sync(self._system.json_converter(), address, text)
            with error:
                detail = error.get_string() if error.get_type() == VariantType.STRING else ""
        if result != Result.OK:
            raise DataLayerError(result.name, detail or result.name)

    def browse(self, address):
        """Names of the child nodes."""
        from ctrlxdatalayer.variant import Result
        with self._lock:
            result, data = self._client.browse_sync(address)
            with data:
                if result != Result.OK:
                    raise DataLayerError(result.name)
                return list(data.get_array_string() or [])

    def metadata(self, address):
        """Metadata as dict (nodeClass, operations, description, unit, references: readType ...)."""
        from ctrlxdatalayer.variant import Result
        with self._lock:
            result, data = self._client.metadata_sync(address)
            with data:
                if result != Result.OK:
                    raise DataLayerError(result.name)
                return self._metadata_json(data)

    def remove(self, address):
        """Remove a node that allows it (a KVD entry, a remote connection)."""
        from ctrlxdatalayer.variant import Result
        with self._lock:
            result = self._client.remove_sync(address)
        if result != Result.OK:
            raise DataLayerError(result.name)

    # -- many addresses, one bulk request each ------------------------------------------------------------------
    def read_many(self, addresses):
        """[(result name, type name, value)] per address ("OK" or e.g. "INVALID_ADDRESS"; failed: None, None)."""
        from ctrlxdatalayer.bulk import BulkReadRequest
        return self._bulk("read", [BulkReadRequest(a) for a in addresses], variant_value, (None, None))

    def browse_many(self, addresses):
        """[(result name, [child names] or None)] per address."""
        return self._bulk("browse", list(addresses), lambda d: (list(d.get_array_string() or []),), (None,))

    def metadata_many(self, addresses):
        """[(result name, metadata dict or None)] per address."""
        return self._bulk("metadata", list(addresses), lambda d: (self._metadata_json(d),), (None,))

    # -- internals ----------------------------------------------------------------------------------------------
    def _metadata_json(self, data):
        """Metadata flatbuffers variant -> dict (converter + metadata schema). Call with the lock held."""
        from ctrlxdatalayer.converter import C_DLR_SCHEMA
        from ctrlxdatalayer.variant import Result
        conv = self._system.json_converter()
        if self._schema is None:
            result, schema = conv.get_schema(C_DLR_SCHEMA.METADATA)
            if result != Result.OK:
                schema.close()
                raise DataLayerError(result.name, "metadata schema")
            self._schema = schema
        result, js = conv.converter_generate_json_complex(data, self._schema, -1)
        with js:
            if result != Result.OK:
                raise DataLayerError(result.name, "metadata conversion")
            return json.loads(js.get_string())

    def _bulk(self, operation, request, convert, failed):
        """One bulk request; per address (result name,) + convert(data), or + failed when it failed."""
        from ctrlxdatalayer.bulk import Bulk
        from ctrlxdatalayer.variant import Result
        if not request:
            return []
        with self._lock, Bulk(self._client) as bulk:
            result = getattr(bulk, operation)(request)
            if result != Result.OK:
                return [(normalize_result(result.name),) + failed] * len(request)
            out = []
            for response in bulk.get_response():
                res = response.get_result()
                if res not in (Result.OK, Result.OK_NO_CONTENT):
                    out.append((normalize_result(res.name),) + failed)
                    continue
                try:
                    out.append(("OK",) + convert(response.get_data()))
                except DataLayerError as exc:
                    out.append((exc.result,) + failed)
            return out
