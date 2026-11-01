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
    ARRAY_STRING = 24


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
    def set_int32(self, d): return self._set(VariantType.INT32, int(d))
    def set_float32(self, d): return self._set(VariantType.FLOAT32, float(d))
    def set_array_string(self, d): return self._set(VariantType.ARRAY_STRING, list(d))
    def get_int8(self): return self._value
    def get_uint8(self): return self._value
    def get_int16(self): return self._value
    def get_uint16(self): return self._value
    def get_int32(self): return self._value
    def get_uint32(self): return self._value
    def get_float32(self): return self._value
    def get_array_string(self): return list(self._value)
    def get_bool8(self): return self._value
    def get_int64(self): return self._value
    def get_uint64(self): return self._value
    def get_float64(self): return self._value
    def get_string(self): return self._value
