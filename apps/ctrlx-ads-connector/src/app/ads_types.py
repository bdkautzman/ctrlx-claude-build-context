"""TwinCAT ADS: symbol and data type tables, symbol paths, PLC values <-> bytes - pure Python, no pyads.

The transport (pyads) is in ads_link.py; this module only works on bytes, so everything here is unit-tested with
the tables of a real controller (tests/fixtures/cx_*.bin: TwinCAT 3.1.4024 on a CX with Windows CE 7, 2026-11-23).

Tables (Beckhoff ADS specification, TwinCAT 3 layout checked against that CX):
* symbol upload (index group 0xF00B): AdsSymbolEntry back to back - entryLength, iGroup, iOffs, size, dataType
  (ADST_*), flags (u32 each), nameLength, typeLength, commentLength (u16), name\\0 type\\0 comment\\0, then optional
  parts (GUID, attributes, ...) up to entryLength.
* data type upload (0xF00E): AdsDatatypeEntry back to back - entryLength, version, hashValue, typeHashValue, size,
  offs, dataType, flags (u32), nameLength, typeLength, commentLength, arrayDim, subItems (u16), name\\0 type\\0
  comment\\0, arrayDim x (lBound i32, elements u32), subItems x AdsDatatypeEntry (struct members, offs = byte offset
  in the struct), then by flags: GUID (0x80), copy mask (0x200), method infos (0x800), attributes (0x1000), enum
  infos (0x2000), ... up to entryLength.
Only the top-level variables are symbols ("POU_Counter.iCounter", "GVL.aValues"); struct members and array elements
are found through the data types. Symbol paths use the TwinCAT syntax: MAIN.stMotor.fSpeed, MAIN.aValues[3],
MAIN.aGrid[1,2].x. TwinCAT names are case-insensitive.

The raw tables are kept as bytes with an index (name -> offset) and entries are parsed when needed: a large
project has 10-100k symbols, and Python objects for all of them would cost tens of MB (CLAUDE.md "RAM").
"""

import math
import re
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ---- index groups ---------------------------------------------------------------------------------------------
IG_SYM_HNDBYNAME = 0xF003      # ReadWrite: name -> handle (u32)
IG_SYM_VALBYHND = 0xF005       # Read/Write by handle (offset = handle)
IG_SYM_RELEASEHND = 0xF006     # Write: release a handle
IG_SYM_VERSION = 0xF008        # Read 1 byte: changes with every download / online change
IG_SYM_UPLOAD = 0xF00B
IG_SYM_UPLOADINFO = 0xF00C     # 8 bytes (older TwinCAT): symbols, symbol bytes
IG_SYM_DT_UPLOAD = 0xF00E
IG_SYM_UPLOADINFO2 = 0xF00F    # 24 bytes: symbols, symbol bytes, data types, data type bytes, ...
IG_SUMUP_READ = 0xF080         # sum command: many reads in one request (offset = number of reads)

PORT_SYSTEM = 10000            # TwinCAT system service (its state: RUN / CONFIG)
MAX_SUM_ITEMS = 500            # Beckhoff: at most 500 sub-commands per sum command
MAX_SUM_BYTES = 60000          # keep one sum read well below the usual AMS frame sizes

ADS_STATES = {
    0: "INVALID", 1: "IDLE", 2: "RESET", 3: "INIT", 4: "START", 5: "RUN", 6: "STOP", 7: "SAVECFG", 8: "LOADCFG",
    9: "POWERFAILURE", 10: "POWERGOOD", 11: "ERROR", 12: "SHUTDOWN", 13: "SUSPEND", 14: "RESUME", 15: "CONFIG",
    16: "RECONFIG", 17: "STOPPING", 18: "INCOMPATIBLE", 19: "EXCEPTION",
}
ADS_STATE_RUN, ADS_STATE_STOP, ADS_STATE_CONFIG = 5, 6, 15

# ADS return codes the app explains in its own words (the rest: pyads' text or the number)
ADS_ERRORS = {
    0x1: "internal error", 0x6: "target port not found (no PLC runtime on this port)",
    0x7: "target machine not found (no route on the controller for this device?)",
    0x700: "device error", 0x701: "service not supported", 0x702: "invalid index group",
    0x703: "invalid index offset", 0x704: "access denied", 0x705: "invalid size", 0x706: "invalid data",
    0x707: "device not ready", 0x708: "device busy", 0x70A: "out of memory", 0x70B: "invalid parameter",
    0x70C: "not found", 0x70E: "incompatible", 0x710: "symbol not found", 0x711: "symbol version invalid",
    0x712: "invalid state", 0x745: "timeout", 0x748: "port not open", 0x749: "no AMS address",
}

# ---- types ----------------------------------------------------------------------------------------------------
ADST_INT16, ADST_INT32, ADST_REAL32, ADST_REAL64 = 2, 3, 4, 5
ADST_STRING, ADST_WSTRING, ADST_BIT, ADST_BIGTYPE = 30, 31, 33, 65
ADST_SCALARS = {2: "int16", 3: "int32", 4: "float32", 5: "float64", 16: "int8", 17: "uint8", 18: "uint16",
                19: "uint32", 20: "int64", 21: "uint64"}
FMT = {"bool8": "B", "int8": "b", "uint8": "B", "int16": "h", "uint16": "H", "int32": "i", "uint32": "I",
       "int64": "q", "uint64": "Q", "float32": "f", "float64": "d"}
SIZE = {t: struct.calcsize("<" + f) for t, f in FMT.items()}
INT_RANGES = {
    "int8": (-2 ** 7, 2 ** 7 - 1), "uint8": (0, 2 ** 8 - 1), "int16": (-2 ** 15, 2 ** 15 - 1),
    "uint16": (0, 2 ** 16 - 1), "int32": (-2 ** 31, 2 ** 31 - 1), "uint32": (0, 2 ** 32 - 1),
    "int64": (-2 ** 63, 2 ** 63 - 1), "uint64": (0, 2 ** 64 - 1),
}
# IEC 61131-3 names -> Data Layer type (the PLC's own representation, nothing converted; time types as raw counts)
IEC_TYPES = {
    "BOOL": "bool8", "BIT": "bool8", "BYTE": "uint8", "USINT": "uint8", "SINT": "int8", "WORD": "uint16",
    "UINT": "uint16", "INT": "int16", "DWORD": "uint32", "UDINT": "uint32", "DINT": "int32", "LWORD": "uint64",
    "ULINT": "uint64", "LINT": "int64", "REAL": "float32", "LREAL": "float64", "TIME": "uint32", "TOD": "uint32",
    "TIME_OF_DAY": "uint32", "DATE": "uint32", "DT": "uint32", "DATE_AND_TIME": "uint32", "LTIME": "uint64",
    "LDATE": "uint64", "LDT": "uint64", "LTOD": "uint64", "LDATE_AND_TIME": "uint64", "LTIME_OF_DAY": "uint64",
}
TIME_UNITS = {"TIME": "ms", "TOD": "ms", "TIME_OF_DAY": "ms", "DATE": "s", "DT": "s", "DATE_AND_TIME": "s",
              "LTIME": "ns", "LDATE": "ns", "LDT": "ns", "LTOD": "ns", "LDATE_AND_TIME": "ns", "LTIME_OF_DAY": "ns"}
# size depends on the controller (32 or 64 bit)
PLATFORM_INTS = {"XINT": "int", "__XINT": "int", "UXINT": "uint", "__UXINT": "uint", "XWORD": "uint",
                 "__XWORD": "uint"}
POINTER_NAMES = ("POINTER TO ", "REFERENCE TO ")
POINTER_TYPES = {"PVOID"}

# AdsSymbolEntry flags
SYMFLAG_BITVALUE, SYMFLAG_REFERENCETO, SYMFLAG_TYPEGUID, SYMFLAG_READONLY = 0x2, 0x4, 0x8, 0x20
SYMFLAG_ATTRIBUTES = 0x1000
# AdsDatatypeEntry flags
DTFLAG_BITVALUES, DTFLAG_TYPEGUID, DTFLAG_COPYMASK = 0x20, 0x80, 0x200
DTFLAG_METHODINFOS, DTFLAG_ATTRIBUTES, DTFLAG_ENUMINFOS = 0x800, 0x1000, 0x2000

MAX_DEPTH = 12                 # nesting of structs/arrays we follow (protects against odd tables)
MAX_TREE_ELEMENTS = 1000       # array elements shown in the browser per array
MAX_NODE_ELEMENTS = 10000      # elements of an array node in the Data Layer
MAX_VALUE_BYTES = 256 * 1024   # bigger values are not read for display
JS_SAFE_INT = 2 ** 53 - 1      # display=True: 64-bit integers beyond it as text (JavaScript numbers)


class AdsTypeError(Exception):
    """A path that doesn't exist, a type that can't be read or written, a value that doesn't fit."""


def error_text(code):
    """'symbol not found (0x710)' for an ADS return code."""
    text = ADS_ERRORS.get(code)
    return "{} (0x{:X})".format(text, code) if text else "ADS error 0x{:X}".format(code)


def state_name(code):
    return ADS_STATES.get(code, str(code)) if code is not None else None


def _text(raw):
    """Names and comments: ASCII identifiers; comments may be UTF-8 (TwinCAT 3.1.4026+) or Windows-1252."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace")


# ---- type descriptions ----------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Member:
    name: str
    type_name: str
    offset: int          # bytes (bit members: bits)
    size: int            # bytes (bit members: bits)
    comment: str = ""
    bit: bool = False
    attributes: Tuple[Tuple[str, str], ...] = ()


@dataclass(frozen=True)
class TypeInfo:
    """kind: scalar | enum | string | wstring | array | struct | pointer | unsupported
    vtype: the Data Layer scalar type (scalar, enum: its base type, string/wstring: "string")."""
    name: str
    kind: str
    size: int
    vtype: Optional[str] = None
    unit: str = ""
    dims: Tuple[Tuple[int, int], ...] = ()          # arrays: (lower bound, count) per dimension
    elem: Optional["TypeInfo"] = None               # arrays: element type
    members: Tuple[Member, ...] = ()                # structs, function blocks
    enum: Tuple[Tuple[str, int], ...] = ()          # enums: (name, value)
    encoding: str = "cp1252"                        # STRING: Windows-1252, or UTF-8 ({attribute 'TcEncoding'})
    comment: str = ""

    @property
    def count(self):
        """Elements of an array (all dimensions)."""
        n = 1
        for _, c in self.dims:
            n *= c
        return n

    @property
    def max_chars(self):
        """STRING(n) / WSTRING(n): n."""
        return self.size - 1 if self.kind == "string" else self.size // 2 - 1 if self.kind == "wstring" else None

    @property
    def leaf(self):
        """A single value the browser shows in the tree and a Data Layer node can hold."""
        return self.kind in ("scalar", "enum", "string", "wstring")

    @property
    def has_children(self):
        return (self.kind == "array" and self.count > 0) or (self.kind == "struct" and bool(self.members))

    def dl_type(self):
        """The Data Layer node type for this PLC type, None when it can't be one node (structs, pointers, ...)."""
        if self.leaf:
            return self.vtype
        if self.kind == "array" and self.elem is not None and self.elem.leaf and 0 < self.count <= MAX_NODE_ELEMENTS:
            return "array-of-" + self.elem.vtype
        return None

    def enum_name(self, value):
        for name, v in self.enum:
            if v == value:
                return name
        return None


UNSUPPORTED = TypeInfo("?", "unsupported", 0)


def _scalar(name, vtype, size=None, comment=""):
    base = name.upper()
    return TypeInfo(name, "scalar", SIZE[vtype] if size is None else size, vtype, TIME_UNITS.get(base, ""),
                    comment=comment)


_STRING_RE = re.compile(r"(W?STRING)(?:\s*[(\[]\s*(\d+)\s*[)\]])?", re.I)
_ARRAY_RE = re.compile(r"ARRAY\s*\[(.+?)\]\s*OF\s+(.+)", re.I | re.S)
_RANGE_RE = re.compile(r"\s*(-?\d+)\s*\.\.\s*(-?\d+)\s*")


def parse_dims(text):
    """'0..9, 1..2' -> ((0, 10), (1, 2)); None when the bounds are not numbers (constants we can't see)."""
    dims = []
    for part in text.split(","):
        m = _RANGE_RE.fullmatch(part)
        if not m:
            return None
        low, high = int(m.group(1)), int(m.group(2))
        if high < low:
            return None
        dims.append((low, high - low + 1))
    return tuple(dims)


# ---- table entries --------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Symbol:
    name: str
    ig: int
    io: int
    size: int
    adst: int
    flags: int
    type_name: str
    comment: str = ""
    attributes: Tuple[Tuple[str, str], ...] = ()

    @property
    def readonly(self):
        return bool(self.flags & SYMFLAG_READONLY)


@dataclass(frozen=True)
class _DtEntry:
    name: str
    type_name: str
    comment: str
    size: int
    offs: int
    adst: int
    flags: int
    dims: Tuple[Tuple[int, int], ...]
    subitems: Tuple["_DtEntry", ...]
    enum: Tuple[Tuple[str, int], ...]
    attributes: Tuple[Tuple[str, str], ...]


def _strings(blob, pos, lengths, end):
    out = []
    for n in lengths:
        if pos + n + 1 > end:
            raise AdsTypeError("table entry too short")
        out.append(_text(blob[pos:pos + n]))
        pos += n + 1
    return out, pos


def _attributes(blob, pos, end):
    """u16 count, then (u8 name length, u8 value length, name\\0, value\\0) each."""
    count, = struct.unpack_from("<H", blob, pos)
    pos += 2
    attrs = []
    for _ in range(count):
        if pos + 2 > end:
            raise AdsTypeError("attributes cut off")
        nl, vl = blob[pos], blob[pos + 1]
        pos += 2
        (name, value), pos = _strings(blob, pos, (nl, vl), end)
        attrs.append((name, value))
    return tuple(attrs), pos


def parse_symbol(blob, pos):
    """The AdsSymbolEntry at pos -> (Symbol, next position)."""
    if pos + 30 > len(blob):
        raise AdsTypeError("symbol table cut off")
    length, ig, io, size, adst, flags, nl, tl, cl = struct.unpack_from("<6I3H", blob, pos)
    end = pos + length
    if length < 30 or end > len(blob):
        raise AdsTypeError("invalid symbol entry length {}".format(length))
    (name, type_name, comment), q = _strings(blob, pos + 30, (nl, tl, cl), end)
    attrs = ()
    if flags & SYMFLAG_TYPEGUID:
        q += 16
    if flags & SYMFLAG_ATTRIBUTES and q + 2 <= end:
        attrs, q = _attributes(blob, q, end)
    return Symbol(name, ig, io, size, adst, flags, type_name, comment, attrs), end


def parse_datatype(blob, pos, depth=0):
    """The AdsDatatypeEntry at pos (with its members) -> (_DtEntry, next position)."""
    if depth > MAX_DEPTH:
        raise AdsTypeError("data types nested too deep")
    if pos + 42 > len(blob):
        raise AdsTypeError("data type table cut off")
    (length, _version, _hash, _type_hash, size, offs, adst, flags,
     nl, tl, cl, array_dim, sub_items) = struct.unpack_from("<8I5H", blob, pos)
    end = pos + length
    if length < 42 or end > len(blob):
        raise AdsTypeError("invalid data type entry length {}".format(length))
    (name, type_name, comment), q = _strings(blob, pos + 42, (nl, tl, cl), end)
    dims = []
    for _ in range(array_dim):
        if q + 8 > end:
            raise AdsTypeError("array info cut off")
        low, count = struct.unpack_from("<iI", blob, q)
        dims.append((low, count))
        q += 8
    subs = []
    for _ in range(sub_items):
        sub, q = parse_datatype(blob, q, depth + 1)
        if q > end:
            raise AdsTypeError("member past the end of its type")
        subs.append(sub)
    attrs, enum = (), ()
    try:
        if flags & DTFLAG_TYPEGUID:
            q += 16
        if flags & DTFLAG_COPYMASK:
            q += size
        if flags & DTFLAG_METHODINFOS:
            count, = struct.unpack_from("<H", blob, q)
            q += 2
            for _ in range(count):
                mlen, = struct.unpack_from("<I", blob, q)
                if mlen < 4:
                    raise AdsTypeError("invalid method entry")
                q += mlen
        if flags & DTFLAG_ATTRIBUTES:
            attrs, q = _attributes(blob, q, end)
        if flags & DTFLAG_ENUMINFOS and 0 < size <= 8:
            count, = struct.unpack_from("<H", blob, q)
            q += 2
            values = []
            for _ in range(count):
                n = blob[q]
                (ename,), q = _strings(blob, q + 1, (n,), end)
                values.append((ename, int.from_bytes(blob[q:q + size], "little", signed=adst in (2, 3, 16, 20))))
                q += size
            enum = tuple(values)
    except (struct.error, IndexError):
        pass   # the optional parts are only extras (attributes, enum names); the entry itself is complete
    return _DtEntry(name, type_name, comment, size, offs, adst, flags, tuple(dims), tuple(subs), enum, attrs), end


def _attr(attrs, key):
    for k, v in attrs:
        if k.lower() == key.lower():
            return v
    return None


# ---- paths ----------------------------------------------------------------------------------------------------
_TOKEN_RE = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[([^\]]*)\]")


def join(parent, child):
    """Child path: members with '.', array elements ('[3]') directly."""
    if not parent:
        return child
    return parent + child if child.startswith("[") else parent + "." + child


def parse_index(text):
    """'3' -> (3,), '1, -2' -> (1, -2)."""
    try:
        return tuple(int(p) for p in text.split(","))
    except ValueError:
        raise AdsTypeError("invalid array index [{}]".format(text))


@dataclass
class Resolved:
    """A symbol path resolved to its place in the PLC memory."""
    path: str                 # the path with the PLC's spelling of every name
    ig: int
    io: int
    size: int
    type: TypeInfo
    readonly: bool = False
    comment: str = ""
    bit: Optional[int] = None  # bit members: the bit number in the byte at io
    symbol: str = ""          # the top-level symbol
    attributes: Tuple[Tuple[str, str], ...] = ()

    @property
    def readable(self):
        return self.type.kind not in ("pointer", "unsupported") and 0 < self.size <= MAX_VALUE_BYTES


def element_names(t, limit=MAX_TREE_ELEMENTS):
    """'[0]', '[1]', ... ('[1,1]', '[1,2]', ... for more dimensions), at most `limit`."""
    names = []

    def walk(level, prefix):
        low, count = t.dims[level]
        for i in range(low, low + count):
            if len(names) >= limit:
                return
            idx = prefix + (i,)
            if level + 1 < len(t.dims):
                walk(level + 1, idx)
            else:
                names.append("[" + ",".join(str(x) for x in idx) + "]")
    if t.dims:
        walk(0, ())
    return names


# ---- the tables of one PLC runtime ----------------------------------------------------------------------------
class SymbolTable:
    """Symbols + data types of one PLC runtime (one symbol version). Thread-safe for readers (no mutation after
    __init__ except the caches, which are filled idempotently)."""

    def __init__(self, symbols, datatypes, version=None, pointer_size=None):
        self.version = version
        self._sym = bytes(symbols)
        self._dt = bytes(datatypes)
        self._sym_index: Dict[str, int] = {}
        pos = 0
        while pos < len(self._sym):
            length, = struct.unpack_from("<I", self._sym, pos)
            nl, = struct.unpack_from("<H", self._sym, pos + 24)
            if length < 30 or pos + length > len(self._sym):
                raise AdsTypeError("invalid symbol entry at {}".format(pos))
            name = _text(self._sym[pos + 30:pos + 30 + nl])
            self._sym_index[name.lower()] = pos
            pos += length
        self._dt_index: Dict[str, int] = {}
        pos = 0
        while pos < len(self._dt):
            length, = struct.unpack_from("<I", self._dt, pos)
            nl, = struct.unpack_from("<H", self._dt, pos + 32)
            if length < 42 or pos + length > len(self._dt):
                raise AdsTypeError("invalid data type entry at {}".format(pos))
            self._dt_index[_text(self._dt[pos + 42:pos + 42 + nl]).lower()] = pos
            pos += length
        self._types: Dict[str, TypeInfo] = {}
        self._folders = None
        self.pointer_size = pointer_size or self._guess_pointer_size()

    def _guess_pointer_size(self):
        for name in ("PVOID", "UXINT", "XINT"):
            entry = self._entry(name)
            if entry is not None and entry.size in (4, 8):
                return entry.size
        return 8

    # -- symbols --------------------------------------------------------------------------------------------
    @property
    def symbol_count(self):
        return len(self._sym_index)

    @property
    def size_bytes(self):
        return len(self._sym) + len(self._dt)

    def symbol(self, name):
        pos = self._sym_index.get(name.lower())
        return None if pos is None else parse_symbol(self._sym, pos)[0]

    def symbol_names(self):
        return [self.symbol(n).name for n in self._sym_index]

    def _folder_tree(self):
        """'' -> {lower child: display name} for the namespace levels of the symbol names (MAIN, GVL, ...)."""
        if self._folders is None:
            folders: Dict[str, Dict[str, str]] = {"": {}}
            for lower in self._sym_index:
                name = parse_symbol(self._sym, self._sym_index[lower])[0].name
                parts = name.split(".")
                for i in range(len(parts)):
                    parent = ".".join(parts[:i]).lower()
                    folders.setdefault(parent, {}).setdefault(parts[i].lower(), parts[i])
            self._folders = folders
        return self._folders

    # -- data types -----------------------------------------------------------------------------------------
    def _entry(self, name):
        pos = self._dt_index.get(name.lower())
        return None if pos is None else parse_datatype(self._dt, pos)[0]

    def type_info(self, name, size=None, adst=None, depth=0):
        key = name.lower()
        cached = self._types.get(key)
        if cached is not None and (size is None or cached.size == size or cached.kind == "unsupported"):
            return cached
        if depth > MAX_DEPTH:
            return TypeInfo(name, "unsupported", size or 0)
        entry = self._entry(name)
        info = self._from_entry(entry, depth) if entry is not None else self._from_name(name, size, adst, depth)
        if size is not None and info.size != size and info.kind != "unsupported":
            info = TypeInfo(name, "unsupported", size, comment="size {} does not match the type ({})".format(
                size, info.size))
        self._types[key] = info
        return info

    def _from_entry(self, e, depth):
        upper = e.name.upper()
        if upper.startswith(POINTER_NAMES) or upper in POINTER_TYPES:
            return TypeInfo(e.name, "pointer", e.size, comment=e.comment)
        if e.dims:
            count = 1
            for _, c in e.dims:
                count *= c
            elem_size = e.size // count if count else 0
            elem = self.type_info(e.type_name, elem_size, e.adst, depth + 1) if e.type_name else UNSUPPORTED
            return TypeInfo(e.name, "array", e.size, dims=e.dims, elem=elem, comment=e.comment)
        if e.subitems:
            members = tuple(Member(s.name, s.type_name, s.offs, s.size, s.comment, bool(s.flags & DTFLAG_BITVALUES),
                                   s.attributes) for s in e.subitems)
            return TypeInfo(e.name, "struct", e.size, members=members, comment=e.comment)
        if e.enum and e.adst in ADST_SCALARS and SIZE[ADST_SCALARS[e.adst]] == e.size:
            return TypeInfo(e.name, "enum", e.size, ADST_SCALARS[e.adst], enum=e.enum, comment=e.comment)
        if e.adst == ADST_STRING:
            enc = "utf-8" if (_attr(e.attributes, "TcEncoding") or "").upper() == "UTF-8" else "cp1252"
            return TypeInfo(e.name, "string", e.size, "string", encoding=enc, comment=e.comment)
        if e.adst == ADST_WSTRING:
            return TypeInfo(e.name, "wstring", e.size, "string", comment=e.comment)
        if e.adst == ADST_BIT and e.size == 1:
            return _scalar(e.name, "bool8", comment=e.comment)
        base = upper if upper in IEC_TYPES else (e.type_name.upper() if e.type_name.upper() in IEC_TYPES else None)
        if base and SIZE[IEC_TYPES[base]] == e.size:
            info = _scalar(e.name, IEC_TYPES[base], comment=e.comment)
            unit = TIME_UNITS.get(upper) or TIME_UNITS.get(e.type_name.upper(), "")
            return TypeInfo(info.name, "scalar", info.size, info.vtype, unit, comment=e.comment)
        if e.adst in ADST_SCALARS and SIZE[ADST_SCALARS[e.adst]] == e.size:
            return _scalar(e.name, ADST_SCALARS[e.adst], comment=e.comment)
        if upper in PLATFORM_INTS and e.size in (4, 8):
            return _scalar(e.name, "{}{}".format(PLATFORM_INTS[upper], e.size * 8), comment=e.comment)
        if e.type_name and e.type_name.lower() != e.name.lower():   # an alias: HRESULT = DINT, CLSID = GUID
            alias = self.type_info(e.type_name, e.size, e.adst, depth + 1)
            if alias.kind != "unsupported":
                return TypeInfo(e.name, alias.kind, alias.size, alias.vtype, alias.unit, alias.dims, alias.elem,
                                alias.members, alias.enum, alias.encoding, e.comment or alias.comment)
        return TypeInfo(e.name, "unsupported", e.size, comment=e.comment)

    def _from_name(self, name, size, adst, depth):
        """A type the data type table doesn't list: base types, STRING(n), ARRAY [..] OF ..."""
        upper = name.strip().upper()
        if upper.startswith(POINTER_NAMES) or upper in POINTER_TYPES:
            return TypeInfo(name, "pointer", size or self.pointer_size)
        m = _STRING_RE.fullmatch(upper)
        if m:
            n = int(m.group(2)) if m.group(2) else 80
            if m.group(1) == "WSTRING":
                return TypeInfo(name, "wstring", 2 * (n + 1), "string")
            return TypeInfo(name, "string", n + 1, "string")
        m = _ARRAY_RE.fullmatch(name.strip())
        if m:
            dims = parse_dims(m.group(1))
            if dims:
                elem = self.type_info(m.group(2).strip(), None, None, depth + 1)
                count = 1
                for _, c in dims:
                    count *= c
                return TypeInfo(name, "array", elem.size * count, dims=dims, elem=elem)
            return TypeInfo(name, "unsupported", size or 0)
        if upper in IEC_TYPES:
            return _scalar(name, IEC_TYPES[upper])
        if upper in PLATFORM_INTS:
            return _scalar(name, "{}{}".format(PLATFORM_INTS[upper], self.pointer_size * 8))
        if adst == ADST_BIT and size == 1:
            return _scalar(name, "bool8")
        if adst in ADST_SCALARS and size == SIZE[ADST_SCALARS[adst]]:
            return _scalar(name, ADST_SCALARS[adst])
        if adst == ADST_STRING and size:
            return TypeInfo(name, "string", size, "string")
        return TypeInfo(name, "unsupported", size or 0)

    # -- paths ------------------------------------------------------------------------------------------------
    def resolve(self, path):
        """A symbol path -> Resolved (memory place + type). AdsTypeError when it doesn't exist."""
        path = (path or "").strip()
        sym, rest = None, ""
        # the longest prefix that is a symbol (symbol names may hold characters the token syntax doesn't)
        cut = len(path)
        while cut > 0:
            sym = self.symbol(path[:cut])
            if sym is not None:
                rest = path[cut:]
                break
            cut = max(path.rfind(".", 0, cut), path.rfind("[", 0, cut))
        if sym is None:
            raise AdsTypeError("no symbol {}".format(path or "(empty)"))
        cur = Resolved(sym.name, sym.ig, sym.io, sym.size,
                       TypeInfo(sym.type_name, "pointer", sym.size) if sym.flags & SYMFLAG_REFERENCETO
                       else self.type_info(sym.type_name, sym.size, sym.adst),
                       sym.readonly, sym.comment, symbol=sym.name, attributes=sym.attributes)
        if sym.flags & SYMFLAG_BITVALUE:
            cur.type = TypeInfo(sym.type_name, "unsupported", sym.size, comment="bit-addressed symbol")
        pos = 0
        while pos < len(rest):
            m = _TOKEN_RE.match(rest, pos)
            if not m:
                raise AdsTypeError("invalid path {} (at {!r})".format(path, rest[pos:]))
            pos = m.end()
            if cur.bit is not None:
                raise AdsTypeError("{} has no members".format(cur.path))
            if m.group(1) is not None:
                cur = self._member(cur, m.group(1))
            else:
                cur = self._element(cur, parse_index(m.group(2)))
        return cur

    def _member(self, cur, name):
        t = cur.type
        if t.kind != "struct":
            raise AdsTypeError("{} ({}) has no members".format(cur.path, t.name))
        for mem in t.members:
            if mem.name.lower() == name.lower():
                path = cur.path + "." + mem.name
                if mem.bit:
                    bit_type = _scalar(mem.type_name, "bool8") if mem.size == 1 else TypeInfo(
                        mem.type_name, "unsupported", 0, comment="bit field of {} bits".format(mem.size))
                    return Resolved(path, cur.ig, cur.io + mem.offset // 8, 1, bit_type, cur.readonly, mem.comment,
                                    bit=mem.offset % 8, symbol=cur.symbol, attributes=mem.attributes)
                mt = self.type_info(mem.type_name, mem.size)
                return Resolved(path, cur.ig, cur.io + mem.offset, mem.size, mt, cur.readonly, mem.comment,
                                symbol=cur.symbol, attributes=mem.attributes)
        raise AdsTypeError("{} has no member {}".format(cur.path, name))

    def _element(self, cur, index):
        t = cur.type
        if t.kind != "array":
            raise AdsTypeError("{} ({}) is not an array".format(cur.path, t.name))
        if len(index) != len(t.dims):
            raise AdsTypeError("{} needs {} index value(s)".format(cur.path, len(t.dims)))
        linear = 0
        for i, (low, count) in zip(index, t.dims):
            if not low <= i < low + count:
                raise AdsTypeError("index {} out of range {}..{}".format(i, low, low + count - 1))
            linear = linear * count + (i - low)
        size = t.elem.size
        path = cur.path + "[" + ",".join(str(i) for i in index) + "]"
        return Resolved(path, cur.ig, cur.io + linear * size, size, t.elem, cur.readonly, cur.comment,
                        symbol=cur.symbol)

    def children(self, path, limit=MAX_TREE_ELEMENTS):
        """Browser: [(name, child path, Resolved or None for a folder)], truncated flag."""
        path = (path or "").strip()
        folders = self._folder_tree()
        out = []
        if self.is_folder(path):
            for lower, display in sorted(folders[path.lower()].items()):
                child = join(path, display)
                sym = self.symbol(child)
                out.append((display, child, self.resolve(child) if sym is not None else None))
            return out, False
        cur = self.resolve(path)
        t = cur.type
        if t.kind == "struct":
            for mem in t.members:
                try:
                    out.append((mem.name, cur.path + "." + mem.name, self._member(cur, mem.name)))
                except AdsTypeError:
                    continue
            return out, False
        if t.kind == "array":
            names = element_names(t, limit)
            for name in names:
                out.append((name, cur.path + name, self._element(cur, parse_index(name[1:-1]))))
            return out, len(names) < t.count
        return out, False

    def leaves(self, path, limit):
        """Every member a Data Layer node can hold below path (structs, arrays of structs), depth first:
        [Resolved]. AdsTypeError when there are more than `limit`."""
        out = []

        def walk(res, depth):
            if depth > MAX_DEPTH:
                return
            if res.type.dl_type() is not None:
                out.append(res)
                if len(out) > limit:
                    raise AdsTypeError("more than {} variables".format(limit))
                return
            if res.type.kind == "struct" and res.bit is None:
                for mem in res.type.members:
                    walk(self._member(res, mem.name), depth + 1)
            elif res.type.kind == "array" and res.type.count <= MAX_NODE_ELEMENTS:
                for name in element_names(res.type, MAX_NODE_ELEMENTS):
                    walk(self._element(res, parse_index(name[1:-1])), depth + 1)

        def walk_folder(folder):
            for _, child, res in self.children(folder)[0]:
                if res is None:
                    walk_folder(child)
                else:
                    walk(res, 0)
        path = (path or "").strip()
        if self.is_folder(path):
            walk_folder(path)
        else:
            walk(self.resolve(path), 0)
        return out

    def folder_path(self, path):
        """A namespace path in the PLC's spelling (gvl_demo -> GVL_Demo); unchanged when it isn't one."""
        out, parent = [], ""
        for part in (path or "").split(".") if path else []:
            name = self._folder_tree().get(parent, {}).get(part.lower())
            if name is None:
                return path
            out.append(name)
            parent = ".".join(out).lower()
        return ".".join(out)

    def is_folder(self, path):
        """A namespace level of the symbol names (MAIN, GVL, the root ''), not a variable."""
        return (path or "").lower() in self._folder_tree() and self.symbol(path or "") is None


# ---- values ---------------------------------------------------------------------------------------------------
def shortest_float32(x):
    """The shortest decimal that is the same float32 (0.1, not 0.10000000149011612) - as PLC tools show REALs."""
    if not math.isfinite(x):
        return x
    packed = struct.pack("<f", x)
    for digits in range(6, 10):
        y = float("{:.{}g}".format(x, digits))
        if struct.pack("<f", y) == packed:
            return y
    return x


def decode(t, data, pos=0, bit=None, depth=0, display=False):
    """bytes -> Python value: bool, int, float, str, list (arrays, flattened over all dimensions). Structs need the
    member types of their table: decode_value(). display=True: float32 as its shortest decimal; pointers and
    unsupported parts as None."""
    if bit is not None:
        return bool((data[pos] >> bit) & 1)
    kind = t.kind
    if kind in ("scalar", "enum"):
        value, = struct.unpack_from("<" + FMT[t.vtype], data, pos)
        if t.vtype == "bool8":
            return value != 0
        if t.vtype == "float32" and display:
            return shortest_float32(value)
        if display and t.vtype in ("int64", "uint64") and abs(value) > JS_SAFE_INT:
            return str(value)        # a web page's numbers keep only 53 bits: the exact digits as text
        return value
    if kind == "string":
        raw = bytes(data[pos:pos + t.size])
        return raw.split(b"\0", 1)[0].decode(t.encoding, "replace")
    if kind == "wstring":
        raw = bytes(data[pos:pos + t.size])
        end = len(raw) - len(raw) % 2
        for i in range(0, end, 2):
            if raw[i] == 0 and raw[i + 1] == 0:
                end = i
                break
        return raw[:end].decode("utf-16-le", "replace")
    if depth >= MAX_DEPTH:
        return None
    if kind == "array" and t.elem is not None and t.elem.size > 0:
        return [decode(t.elem, data, pos + i * t.elem.size, None, depth + 1, display) for i in range(t.count)]
    return None


def decode_value(table, res, data, pos=0, display=False):
    """Decode a resolved value, structs included (their member types come from `table`)."""
    return _decode_tree(table, res.type, data, pos, res.bit, 0, display)


def _decode_tree(table, t, data, pos, bit, depth, display):
    if bit is not None or t.kind not in ("struct", "array"):
        return decode(t, data, pos, bit, depth, display)
    if depth >= MAX_DEPTH:
        return None
    if t.kind == "array":
        if t.elem is None or t.elem.size <= 0:
            return None
        return [_decode_tree(table, t.elem, data, pos + i * t.elem.size, None, depth + 1, display)
                for i in range(t.count)]
    out = {}
    for mem in t.members:
        if mem.bit:
            out[mem.name] = bool((data[pos + mem.offset // 8] >> (mem.offset % 8)) & 1) if mem.size == 1 else None
            continue
        mt = table.type_info(mem.type_name, mem.size)
        out[mem.name] = _decode_tree(table, mt, data, pos + mem.offset, None, depth + 1, display) \
            if mt.kind not in ("pointer", "unsupported") else None
    return out


def _scalar_bytes(vtype, value, what):
    if vtype == "bool8":
        if not isinstance(value, bool):
            raise AdsTypeError("{}: expected true or false".format(what))
        return b"\x01" if value else b"\x00"
    if vtype in INT_RANGES:
        if isinstance(value, bool) or not isinstance(value, int):
            raise AdsTypeError("{}: expected an integer".format(what))
        low, high = INT_RANGES[vtype]
        if not low <= value <= high:
            raise AdsTypeError("{}: {} out of range {}..{}".format(what, value, low, high))
        return struct.pack("<" + FMT[vtype], value)
    if vtype in ("float32", "float64"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise AdsTypeError("{}: expected a number".format(what))
        if not math.isfinite(value):
            raise AdsTypeError("{}: must be finite".format(what))
        try:
            return struct.pack("<" + FMT[vtype], value)
        except OverflowError:
            raise AdsTypeError("{}: out of the {} range".format(what, vtype))
    raise AdsTypeError("{}: unsupported type {}".format(what, vtype))


def _leaf_bytes(t, value, what):
    if t.kind in ("scalar", "enum"):
        return _scalar_bytes(t.vtype, value, what)
    if t.kind in ("string", "wstring"):
        if not isinstance(value, str):
            raise AdsTypeError("{}: expected text".format(what))
        if "\0" in value:
            raise AdsTypeError("{}: text must not contain NUL".format(what))
        try:
            raw = value.encode(t.encoding if t.kind == "string" else "utf-16-le")
        except UnicodeEncodeError:
            raise AdsTypeError("{}: character not possible in a {} ({})".format(what, t.name, t.encoding))
        room = t.size - (1 if t.kind == "string" else 2)
        if len(raw) > room:
            raise AdsTypeError("{}: longer than {} ({} of {} bytes)".format(what, t.name, len(raw), room))
        return raw + bytes(t.size - len(raw))
    raise AdsTypeError("{}: {} values can't be written".format(what, t.name))


def encode(t, value, what="value"):
    """Python value -> bytes of exactly t.size. Leaf types and arrays of them (a list with exactly t.count
    elements, flattened over all dimensions). AdsTypeError for everything else."""
    if t.leaf:
        return _leaf_bytes(t, value, what)
    if t.kind == "array" and t.elem is not None and t.elem.leaf:
        if not isinstance(value, (list, tuple)):
            raise AdsTypeError("{}: expected an array of {} elements".format(what, t.count))
        if len(value) != t.count:
            raise AdsTypeError("{}: expected {} elements, got {}".format(what, t.count, len(value)))
        return b"".join(_leaf_bytes(t.elem, v, "{}[{}]".format(what, i)) for i, v in enumerate(value))
    raise AdsTypeError("{}: a {} can't be written as a whole - write its members".format(what, t.name))


def parse_input(t, value):
    """A value from the web UI (JSON, or text from an input field) -> the Python value encode() takes.
    Numbers may come as text (64-bit integers exactly), booleans as "true"/"false", arrays as JSON text."""
    import json
    if t.kind == "array" and isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = [v.strip() for v in value.split(",")] if value.strip() else []
    if t.kind == "array" and isinstance(value, list) and t.elem is not None:
        return [parse_input(t.elem, v) for v in value]
    if not isinstance(value, str) or t.kind in ("string", "wstring"):
        if t.vtype in ("float32", "float64") and isinstance(value, int) and not isinstance(value, bool):
            return float(value)
        return value
    text = value.strip()
    if t.vtype == "bool8":
        if text.lower() in ("true", "1"):
            return True
        if text.lower() in ("false", "0"):
            return False
        raise AdsTypeError("expected true or false")
    if t.vtype in INT_RANGES:
        if t.kind == "enum":
            for name, v in t.enum:
                if name.lower() == text.lower():
                    return v
        try:
            return int(text, 0) if text.lower().startswith(("0x", "-0x")) else int(text)
        except ValueError:
            raise AdsTypeError("expected an integer")
    if t.vtype in ("float32", "float64"):
        try:
            return float(text)
        except ValueError:
            raise AdsTypeError("expected a number")
    return value


def from_dl(t, value):
    """A value from the Data Layer (registry style: tuples for arrays) for encode()."""
    return list(value) if isinstance(value, tuple) else value


# ---- sum read -------------------------------------------------------------------------------------------------
def chunk_reads(items):
    """Split [(ig, io, size), ...] into sum reads of at most MAX_SUM_ITEMS / about MAX_SUM_BYTES each:
    [(start, end)] index ranges."""
    chunks, start, total = [], 0, 0
    for i, (_, _, size) in enumerate(items):
        if i > start and (i - start >= MAX_SUM_ITEMS or total + size + 4 > MAX_SUM_BYTES):
            chunks.append((start, i))
            start, total = i, 0
        total += size + 4
    if start < len(items):
        chunks.append((start, len(items)))
    return chunks


def split_sum_read(sizes, data):
    """The answer of a sum read: N error codes (u32), then the data of every read (its requested size, also when
    it failed) -> [(error, bytes)]."""
    n = len(sizes)
    if len(data) < 4 * n:
        raise AdsTypeError("sum read answer too short")
    errors = struct.unpack_from("<{}I".format(n), data, 0)
    out, pos = [], 4 * n
    for err, size in zip(errors, sizes):
        out.append((err, bytes(data[pos:pos + size])))
        pos += size
    return out


# ---- UDP discovery (port 48899) -------------------------------------------------------------------------------
UDP_MAGIC = 0x71146603
UDP_PORT = 48899


def server_info_request(invoke_id=0):
    """The 'who are you' request TwinCAT's broadcast search sends (service 1); changes nothing."""
    return struct.pack("<III6sHI", UDP_MAGIC, invoke_id, 1, bytes([1] * 6), PORT_SYSTEM, 0)


def parse_server_info(data):
    """The answer to server_info_request -> {amsNetId, hostname, twincat, os}."""
    if len(data) < 24:
        raise AdsTypeError("answer too short")
    magic, _, service = struct.unpack_from("<III", data, 0)
    if magic != UDP_MAGIC or service != 0x80000001:
        raise AdsTypeError("not an ADS server info answer")
    info = {"amsNetId": ".".join(str(b) for b in data[12:18]), "hostname": "", "twincat": "", "os": ""}
    count, = struct.unpack_from("<I", data, 20)
    pos = 24
    for _ in range(count):
        if pos + 4 > len(data):
            break
        tag, length = struct.unpack_from("<HH", data, pos)
        value = data[pos + 4:pos + 4 + length]
        pos += 4 + length
        if tag == 0x5:
            info["hostname"] = _text(value.split(b"\0", 1)[0])
        elif tag == 0x3 and length >= 4:
            info["twincat"] = "{}.{}.{}".format(value[0], value[1], struct.unpack_from("<H", value, 2)[0])
        elif tag == 0x4 and length >= 20:
            _, major, minor, build, platform = struct.unpack_from("<5I", value, 0)
            name = {2: "Windows", 3: "Windows CE"}.get(platform, "OS (platform {})".format(platform))
            info["os"] = "{} {}.{} (build {})".format(name, major, minor, build)
    return info


# ---- AMS Net IDs ----------------------------------------------------------------------------------------------
NET_ID_RE = re.compile(r"(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})")


def valid_net_id(text):
    m = NET_ID_RE.fullmatch(text or "")
    return bool(m) and all(int(g) <= 255 for g in m.groups())


@dataclass
class Read:
    """One value the poll loop reads: where, and how to decode it."""
    key: str
    ig: int
    io: int
    size: int
    type: TypeInfo
    bit: Optional[int] = None
    raw: Optional[bytes] = field(default=None, repr=False)
