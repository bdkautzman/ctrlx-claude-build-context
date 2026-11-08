import struct
from enum import Enum


class Result(Enum):
    OK = 0
    FAILED = 0x80000001
    INVALID_ADDRESS = 0x80010001
    UNSUPPORTED = 0x80010002
    TYPE_MISMATCH = 0x80010006
    INVALID_VALUE = 0x8001000D
    PERMISSION_DENIED = 0x80010014


class VariantType(Enum):   # same names and numbers as reference/ctrlxdatalayer-api-3.6.0/variant.py
    UNKNON = 0
    BOOL8 = 1
    INT8 = 2
    UINT8 = 3
    INT16 = 4
    UINT16 = 5
    INT32 = 6
    UINT32 = 7
    INT64 = 8
    UINT64 = 9
    FLOAT32 = 10
    FLOAT64 = 11
    STRING = 12
    ARRAY_BOOL8 = 13
    ARRAY_INT8 = 14
    ARRAY_UINT8 = 15
    ARRAY_INT16 = 16
    ARRAY_UINT16 = 17
    ARRAY_INT32 = 18
    ARRAY_UINT32 = 19
    ARRAY_INT64 = 20
    ARRAY_UINT64 = 21
    ARRAY_FLOAT32 = 22
    ARRAY_FLOAT64 = 23
    ARRAY_STRING = 24
    RAW = 25
    FLATBUFFERS = 26
    TIMESTAMP = 27
    ARRAY_OF_TIMESTAMP = 28


def _f32(x):
    """What the real library keeps of a float32 (ctypes c_float)."""
    return struct.unpack("<f", struct.pack("<f", x))[0]


_CONVERT = {"BOOL8": bool, "STRING": str, "FLOAT32": _f32, "FLOAT64": float}


class Variant:
    def __init__(self):
        self._type, self._value, self.closed = VariantType.UNKNON, None, False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.closed = True

    def get_type(self):
        return self._type

    def clone(self):
        v = Variant()
        v.__dict__.update(self.__dict__)
        v.closed = False
        return Result.OK, v

    def __getattr__(self, name):
        """set_<type>(value) / get_<type>() for every scalar and array type, like the real Variant."""
        kind, _, tname = name.partition("_")
        upper = tname.upper()
        if kind not in ("set", "get") or upper not in VariantType.__members__:
            raise AttributeError(name)
        if kind == "get":
            return lambda: self._value
        elem = upper[len("ARRAY_"):] if upper.startswith("ARRAY_") else None
        convert = _CONVERT.get(elem or upper, int)

        def setter(data):
            self._type = VariantType[upper]
            self._value = [convert(x) for x in data] if elem else convert(data)
            return Result.OK
        return setter
