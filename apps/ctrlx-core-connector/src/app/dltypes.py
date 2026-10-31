"""Data Layer type names and value conversion - pure Python, no ctrlxdatalayer import.

Type names are the ones of types/datalayer/<name> (and of KVD's nodes.json): bool8, int32, arstring ...
Values come from the web UI as JSON, numbers often as strings (int64/uint64 don't fit a JS number).
coerce() turns them into the Python value for that type or raises ValueError with a short reason.
"""

import json
import math

INT_RANGES = {
    "int8": (-2 ** 7, 2 ** 7 - 1), "uint8": (0, 2 ** 8 - 1),
    "int16": (-2 ** 15, 2 ** 15 - 1), "uint16": (0, 2 ** 16 - 1),
    "int32": (-2 ** 31, 2 ** 31 - 1), "uint32": (0, 2 ** 32 - 1),
    "int64": (-2 ** 63, 2 ** 63 - 1), "uint64": (0, 2 ** 64 - 1),
    "timestamp": (0, 2 ** 64 - 1),
}
FLOAT32_MAX = 3.4028234663852886e38
SCALAR_TYPES = ("bool8", "int8", "uint8", "int16", "uint16", "int32", "uint32", "int64", "uint64",
                "float32", "float64", "string", "timestamp")
ARRAY_TYPES = tuple("ar" + t for t in SCALAR_TYPES)
SIMPLE_TYPES = frozenset(SCALAR_TYPES + ARRAY_TYPES)

# ctrlxdatalayer.variant.VariantType name -> Data Layer type name
VARIANT_TYPES = {
    "BOOL8": "bool8", "INT8": "int8", "UINT8": "uint8", "INT16": "int16", "UINT16": "uint16",
    "INT32": "int32", "UINT32": "uint32", "INT64": "int64", "UINT64": "uint64",
    "FLOAT32": "float32", "FLOAT64": "float64", "STRING": "string", "TIMESTAMP": "timestamp",
    "ARRAY_BOOL8": "arbool8", "ARRAY_INT8": "arint8", "ARRAY_UINT8": "aruint8", "ARRAY_INT16": "arint16",
    "ARRAY_UINT16": "aruint16", "ARRAY_INT32": "arint32", "ARRAY_UINT32": "aruint32", "ARRAY_INT64": "arint64",
    "ARRAY_UINT64": "aruint64", "ARRAY_FLOAT32": "arfloat32", "ARRAY_FLOAT64": "arfloat64",
    "ARRAY_STRING": "arstring", "ARRAY_OF_TIMESTAMP": "artimestamp",
    "RAW": "raw", "FLATBUFFERS": "flatbuffers", "UNKNON": "unknown",
}
# KVD (nodes.json) also accepts these aliases
ALIASES = {"float": "float32", "double": "float64", "arfloat": "arfloat32", "ardouble": "arfloat64"}


def type_from_reference(target):
    """'types/datalayer/int32' -> 'int32', 'types/datalayer/array-of-int16' -> 'arint16' (the names of
    array types in the type tree, ctrlX OS 4.6); other type paths (flatbuffers) are returned unchanged.
    Seen from this device's root, a remote's nodes reference devices/remotes/<name>/types/datalayer/<type>."""
    simple = target
    if simple.startswith("devices/remotes/") and "/types/datalayer/" in simple:
        simple = simple[simple.index("/types/datalayer/") + 1:]
    if simple.startswith("types/datalayer/"):
        name = simple[len("types/datalayer/"):]
        if name.startswith("array-of-"):
            name = "ar" + name[len("array-of-"):]
        name = ALIASES.get(name, name)
        if name in SIMPLE_TYPES:
            return name
    return target


def coerce(vtype, raw):
    """Value of type vtype from a UI value (JSON value or text). Raises ValueError."""
    vtype = ALIASES.get(vtype, vtype)
    if vtype in ARRAY_TYPES:
        items = _array_items(vtype, raw)
        return [coerce(vtype[2:], item) for item in items]
    if vtype == "bool8":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in ("true", "1"):
            return True
        if text in ("false", "0"):
            return False
        raise ValueError("expected true or false")
    if vtype in INT_RANGES:
        if isinstance(raw, bool):
            raise ValueError("expected an integer")
        if isinstance(raw, float):
            if not raw.is_integer():
                raise ValueError("expected an integer")
            raw = int(raw)
        if not isinstance(raw, int):
            text = str(raw).strip()
            try:  # hex as 0x.. (int(text, 0) would reject decimal numbers with leading zeros)
                raw = int(text, 16) if text.lower().lstrip("-").startswith("0x") else int(text)
            except ValueError:
                raise ValueError("expected an integer")
        low, high = INT_RANGES[vtype]
        if not low <= raw <= high:
            raise ValueError("out of range {}..{}".format(low, high))
        return raw
    if vtype in ("float32", "float64"):
        if isinstance(raw, bool):
            raise ValueError("expected a number")
        try:
            value = float(raw) if not isinstance(raw, str) else float(raw.strip())
        except (TypeError, ValueError):
            raise ValueError("expected a number")
        if not math.isfinite(value):
            raise ValueError("must be a finite number")
        if vtype == "float32" and abs(value) > FLOAT32_MAX:
            raise ValueError("out of the float32 range")
        return value
    if vtype == "string":
        if isinstance(raw, (dict, list)) or raw is None:
            raise ValueError("expected text")
        return raw if isinstance(raw, str) else json.dumps(raw)
    raise ValueError("type {} can't be entered here".format(vtype))


def _array_items(vtype, raw):
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, str):
        raise ValueError("expected a list")
    text = raw.strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            items = json.loads(text)
        except ValueError:
            raise ValueError("not a valid JSON array")
        if not isinstance(items, list):
            raise ValueError("expected a list")
        return items
    if vtype == "arstring":
        raise ValueError('string arrays as JSON, e.g. ["a", "b"]')
    return [part for part in (p.strip() for p in text.split(",")) if part != ""]


def json_safe(value):
    """Value that json.dumps writes as standard JSON (no NaN/Infinity, bytes as hex)."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    return value
