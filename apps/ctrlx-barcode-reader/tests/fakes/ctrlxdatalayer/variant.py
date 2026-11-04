from enum import Enum


class Result(Enum):
    OK = 0
    FAILED = 0x80000001
    INVALID_ADDRESS = 0x80010001
    UNSUPPORTED = 0x80010002
    TYPE_MISMATCH = 0x80010006
    INVALID_VALUE = 0x8001000D
    PERMISSION_DENIED = 0x80010014


class VariantType(Enum):
    UNKNOWN = 0
    BOOL8 = 1
    INT64 = 8
    UINT64 = 9
    FLOAT64 = 11
    STRING = 12


class Variant:
    def __init__(self):
        self._type, self._value, self.closed = VariantType.UNKNOWN, None, False

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

    def _set(self, t, value):
        self._type, self._value = t, value
        return Result.OK

    def set_bool8(self, d): return self._set(VariantType.BOOL8, bool(d))
    def set_int64(self, d): return self._set(VariantType.INT64, int(d))
    def set_uint64(self, d): return self._set(VariantType.UINT64, int(d))
    def set_float64(self, d): return self._set(VariantType.FLOAT64, float(d))
    def set_string(self, d): return self._set(VariantType.STRING, str(d))
    def get_bool8(self): return self._value
    def get_int64(self): return self._value
    def get_uint64(self): return self._value
    def get_float64(self): return self._value
    def get_string(self): return self._value
