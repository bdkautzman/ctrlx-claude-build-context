"""ads_types: symbol/data type tables, paths, values <-> bytes (no pyads, no controller needed).

tests/fixtures/cx_*.bin are the raw uploads of a real controller: a Beckhoff CX with TwinCAT 3.1.4024 on Windows CE 7
(read-only, 2026-11-23): symbols (0xF00B), data types (0xF00E), upload info (0xF00F), UDP server info (port 48899).
The synthetic tables come from app/dev_fake_plc.py (every IEC type, enum, struct, arrays).
"""

import math
import os
import struct
import unittest

from app import ads_types as at
from app.dev_fake_plc import demo_tables, dt_entry, member, sym_entry

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name):
    with open(os.path.join(FIX, name), "rb") as f:
        return f.read()


class RealControllerTablesTest(unittest.TestCase):
    """The tables of the CX (TwinCAT 3.1.4024) - the layout the parser was written for."""

    @classmethod
    def setUpClass(cls):
        cls.t = at.SymbolTable(fixture("cx_symbols.bin"), fixture("cx_datatypes.bin"), version=1)

    def test_counts_match_the_upload_info(self):
        n_sym, sym_bytes, n_dt, dt_bytes = struct.unpack_from("<4I", fixture("cx_info2.bin"))
        self.assertEqual((self.t.symbol_count, n_sym), (15, 15))
        self.assertEqual(self.t.size_bytes, sym_bytes + dt_bytes)
        self.assertEqual(self.t.pointer_size, 4)          # a 32-bit CX

    def test_namespaces_and_variables(self):
        names = [n for n, _, _ in self.t.children("")[0]]
        self.assertEqual(names, ["Global_Version", "POU_Counter", "TwinCAT_SystemInfoVarList"])
        rows = {n: r for n, _, r in self.t.children("POU_Counter")[0]}
        self.assertEqual({n: (r.type.kind, r.type.dl_type()) for n, r in rows.items()},
                         {"bTest": ("scalar", "bool8"), "iCounter": ("scalar", "int16"),
                          "sTest": ("string", "string")})
        self.assertTrue(self.t.is_folder("POU_Counter") and not self.t.is_folder("POU_Counter.iCounter"))
        self.assertEqual(self.t.folder_path("pou_counter"), "POU_Counter")
        self.assertEqual(self.t.folder_path("nope"), "nope")

    def test_resolve(self):
        r = self.t.resolve("pou_counter.ICOUNTER")                    # TwinCAT names are case-insensitive
        self.assertEqual((r.path, r.ig, r.io, r.size, r.type.name), ("POU_Counter.iCounter", 0x4040, 0x5df10, 2, "INT"))
        r = self.t.resolve("POU_Counter.sTest")
        self.assertEqual((r.type.kind, r.size, r.type.max_chars), ("string", 81, 80))
        r = self.t.resolve("TwinCAT_SystemInfoVarList._TaskInfo[1].CycleTime")
        self.assertEqual((r.io, r.type.vtype), (0x5e08c, "uint32"))
        with self.assertRaises(at.AdsTypeError):
            self.t.resolve("TwinCAT_SystemInfoVarList._TaskInfo[2]")   # ARRAY [1..1]
        with self.assertRaises(at.AdsTypeError):
            self.t.resolve("POU_Counter.nope")

    def test_struct_with_enum_pointers_and_aliases(self):
        rows = {n: r for n, _, r in self.t.children("TwinCAT_SystemInfoVarList.__PlcTask")[0]}
        self.assertEqual(rows["KindOf"].type.kind, "enum")
        self.assertEqual(rows["KindOf"].type.enum[0], ("_implicit_cyclic", 0))
        self.assertEqual(rows["pszName"].type.kind, "pointer")
        self.assertFalse(rows["pszName"].readable)
        r = self.t.resolve("TwinCAT_SystemInfoVarList._TaskOid_PlcTask")   # OTCID = UDINT
        self.assertEqual(r.type.vtype, "uint32")
        info = self.t.type_info("PLC.EPlcPersistentStatus")
        self.assertEqual((info.kind, info.vtype, info.enum_name(2)), ("enum", "uint8", "PS_Partial"))

    def test_leaves_of_a_namespace(self):
        leaves = self.t.leaves("POU_Counter", 500)
        self.assertEqual([r.path for r in leaves], ["POU_Counter.bTest", "POU_Counter.iCounter", "POU_Counter.sTest"])
        with self.assertRaises(at.AdsTypeError):
            self.t.leaves("TwinCAT_SystemInfoVarList", 3)                # more than the limit

    def test_server_info(self):
        info = at.parse_server_info(fixture("cx_udp_info.bin"))
        self.assertEqual(info, {"amsNetId": "5.57.166.80.1.1", "hostname": "CX-39A650", "twincat": "3.1.4024",
                                "os": "Windows CE 7.0 (build 2882)"})
        with self.assertRaises(at.AdsTypeError):
            at.parse_server_info(b"\x00" * 30)

    def test_broken_tables(self):
        with self.assertRaises(at.AdsTypeError):
            at.SymbolTable(fixture("cx_symbols.bin")[:-5], b"")
        with self.assertRaises(at.AdsTypeError):
            at.SymbolTable(b"", fixture("cx_datatypes.bin")[:100])


class DemoTypesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        syms, dts, cls.layout = demo_tables()
        cls.t = at.SymbolTable(syms, dts)

    def roundtrip(self, path, value, expect=None):
        r = self.t.resolve(path)
        data = at.encode(r.type, value)
        self.assertEqual(len(data), r.size)
        self.assertEqual(at.decode(r.type, data), value if expect is None else expect)
        return r

    def test_every_iec_type(self):
        cases = [("GVL_Demo.bValue", True, "bool8"), ("GVL_Demo.nByte", 255, "uint8"), ("GVL_Demo.nWord", 65535, "uint16"),
                 ("GVL_Demo.nDword", 2 ** 32 - 1, "uint32"), ("GVL_Demo.nLword", 2 ** 64 - 1, "uint64"),
                 ("GVL_Demo.nSint", -128, "int8"), ("GVL_Demo.nUsint", 0, "uint8"), ("GVL_Demo.nInt", -32768, "int16"),
                 ("GVL_Demo.nUint", 65535, "uint16"), ("GVL_Demo.nDint", -2 ** 31, "int32"),
                 ("GVL_Demo.nUdint", 2 ** 32 - 1, "uint32"), ("GVL_Demo.nLint", -2 ** 63, "int64"),
                 ("GVL_Demo.nUlint", 2 ** 64 - 1, "uint64"), ("GVL_Demo.fReal", 1.5, "float32"),
                 ("GVL_Demo.fLreal", math.pi, "float64"), ("GVL_Demo.sText", "Grüße €", "string"),
                 ("GVL_Demo.wsText", "Ω ✓ 日本", "string"), ("GVL_Demo.tTime", 1500, "uint32"),
                 ("GVL_Demo.tLtime", 2 ** 40, "uint64"), ("GVL_Demo.dtStamp", 1795000000, "uint32"),
                 ("GVL_Demo.eState", 2, "int16")]
        for path, value, dl in cases:
            r = self.roundtrip(path, value)
            self.assertEqual(r.type.dl_type(), dl, path)
        self.assertEqual(self.t.resolve("GVL_Demo.tTime").type.unit, "ms")
        self.assertEqual(self.t.resolve("GVL_Demo.tLtime").type.unit, "ns")
        self.assertEqual(self.t.resolve("GVL_Demo.dtStamp").type.unit, "s")

    def test_float32(self):
        r = self.t.resolve("GVL_Demo.fReal")
        raw = at.encode(r.type, 0.1)
        self.assertEqual(at.decode(r.type, raw), 0.10000000149011612)     # what the REAL holds
        self.assertEqual(at.decode(r.type, raw, display=True), 0.1)       # how PLC tools show it
        self.assertEqual(at.shortest_float32(at.decode(r.type, at.encode(r.type, 123.456))), 123.456)
        big = self.t.resolve("GVL_Demo.nLword").type
        self.assertEqual(at.decode(big, at.encode(big, 2 ** 63 + 5), display=True), "9223372036854775813")
        self.assertEqual(at.decode(big, at.encode(big, 2 ** 53), display=True), "9007199254740992")
        self.assertEqual(at.decode(big, at.encode(big, 5), display=True), 5)
        with self.assertRaises(at.AdsTypeError):
            at.encode(r.type, 1e39)
        with self.assertRaises(at.AdsTypeError):
            at.encode(r.type, float("nan"))

    def test_arrays(self):
        r = self.roundtrip("GVL_Demo.aInt", list(range(10)))
        self.assertEqual((r.type.dl_type(), r.type.count), ("array-of-int16", 10))
        with self.assertRaises(at.AdsTypeError):
            at.encode(r.type, [1, 2])                                    # exactly 10
        g = self.t.resolve("GVL_Demo.aGrid")
        self.assertEqual((g.type.dims, g.type.count), (((1, 2), (1, 3)), 6))
        base = g.io
        self.assertEqual(self.t.resolve("GVL_Demo.aGrid[1,1]").io, base)
        self.assertEqual(self.t.resolve("GVL_Demo.aGrid[1, 3]").io, base + 2 * 4)   # last index fastest
        self.assertEqual(self.t.resolve("GVL_Demo.aGrid[2,1]").io, base + 3 * 4)
        self.assertEqual([n for n, _, _ in self.t.children("GVL_Demo.aGrid")[0]][:4],
                         ["[1,1]", "[1,2]", "[1,3]", "[2,1]"])
        for bad in ("GVL_Demo.aGrid[3,1]", "GVL_Demo.aGrid[1]", "GVL_Demo.aInt[x]", "GVL_Demo.nInt[0]"):
            with self.assertRaises(at.AdsTypeError, msg=bad):
                self.t.resolve(bad)
        axis = self.t.resolve("GVL_Demo.aAxis")
        self.assertIsNone(axis.type.dl_type())                           # array of structs: members, not one node
        self.assertEqual(self.t.resolve("GVL_Demo.aAxis[2].sName").io, axis.io + 40 + 16)

    def test_structs(self):
        r = self.t.resolve("GVL_Demo.stAxis")
        self.assertEqual((r.type.kind, r.type.size, len(r.type.members)), ("struct", 40, 5))
        self.assertIsNone(r.type.dl_type())
        self.assertEqual(self.t.resolve("gvl_demo.staxis.FPOS").path, "GVL_Demo.stAxis.fPos")
        data = bytearray(40)
        data[0:8] = struct.pack("<d", 1.25)
        data[16:21] = b"Ax\0\0\0"
        data[14:16] = struct.pack("<h", 1)
        value = at.decode_value(self.t, r, bytes(data), display=True)
        self.assertEqual(value, {"fPos": 1.25, "fVel": 0.0, "bEnabled": False, "eState": 1, "sName": "Ax"})
        with self.assertRaises(at.AdsTypeError):
            at.encode(r.type, value)                                     # structs are written member by member
        leaves = self.t.leaves("GVL_Demo.aAxis", 100)
        self.assertEqual(len(leaves), 15)
        self.assertEqual(leaves[5].path, "GVL_Demo.aAxis[2].fPos")

    def test_pointers_and_readonly(self):
        self.assertEqual(self.t.resolve("MAIN.pValue").type.kind, "pointer")
        self.assertTrue(self.t.resolve("GVL_Demo.nConst").readonly)
        self.assertTrue(self.t.resolve("MAIN.fbCounter.nCount").path, "MAIN.fbCounter.nCount")

    def test_encode_errors(self):
        res = {p: self.t.resolve(p).type for p in ("GVL_Demo.nUsint", "GVL_Demo.bValue", "GVL_Demo.sText",
                                                    "GVL_Demo.wsText", "GVL_Demo.fLreal")}
        for path, value in (("GVL_Demo.nUsint", 256), ("GVL_Demo.nUsint", -1), ("GVL_Demo.nUsint", True),
                            ("GVL_Demo.nUsint", 1.5), ("GVL_Demo.bValue", 1), ("GVL_Demo.sText", "x" * 81),
                            ("GVL_Demo.sText", "✓"), ("GVL_Demo.sText", "a\0b"), ("GVL_Demo.sText", 5),
                            ("GVL_Demo.wsText", "x" * 21), ("GVL_Demo.fLreal", "1")):
            with self.assertRaises(at.AdsTypeError, msg=(path, value)):
                at.encode(res[path], value)
        self.assertEqual(len(at.encode(res["GVL_Demo.sText"], "x" * 80)), 81)   # STRING(80): 80 + NUL

    def test_parse_input(self):
        t = {p: self.t.resolve(p).type for p in ("GVL_Demo.bValue", "GVL_Demo.nLword", "GVL_Demo.eState",
                                                  "GVL_Demo.fReal", "GVL_Demo.aInt", "GVL_Demo.sText")}
        self.assertEqual(at.parse_input(t["GVL_Demo.bValue"], "TRUE"), True)
        self.assertEqual(at.parse_input(t["GVL_Demo.bValue"], False), False)
        self.assertEqual(at.parse_input(t["GVL_Demo.nLword"], "18446744073709551615"), 2 ** 64 - 1)
        self.assertEqual(at.parse_input(t["GVL_Demo.nLword"], "0x10"), 16)
        self.assertEqual(at.parse_input(t["GVL_Demo.eState"], "running"), 1)
        self.assertEqual(at.parse_input(t["GVL_Demo.fReal"], " 2.5 "), 2.5)
        self.assertEqual(at.parse_input(t["GVL_Demo.fReal"], 3), 3.0)
        self.assertEqual(at.parse_input(t["GVL_Demo.aInt"], "1, 2, 3"), [1, 2, 3])
        self.assertEqual(at.parse_input(t["GVL_Demo.aInt"], "[4, 5]"), [4, 5])
        self.assertEqual(at.parse_input(t["GVL_Demo.sText"], " keep spaces "), " keep spaces ")
        for path, text in (("GVL_Demo.bValue", "yes"), ("GVL_Demo.nLword", "1.5"), ("GVL_Demo.fReal", "x")):
            with self.assertRaises(at.AdsTypeError):
                at.parse_input(t[path], text)

    def test_types_missing_from_the_table(self):
        syms = sym_entry("G.s", 0, 11, 30, "STRING(10)") + sym_entry("G.a", 16, 12, 3, "ARRAY [0..2] OF DINT") \
            + sym_entry("G.w", 32, 8, 31, "WSTRING(3)") + sym_entry("G.x", 40, 4, 65, "MYSTERY")
        t = at.SymbolTable(syms, b"")
        self.assertEqual((t.resolve("G.s").type.kind, t.resolve("G.s").type.max_chars), ("string", 10))
        self.assertEqual(t.resolve("G.a").type.dl_type(), "array-of-int32")
        self.assertEqual(t.resolve("G.a[2]").io, 16 + 8)
        self.assertEqual(t.resolve("G.w").type.kind, "wstring")
        self.assertEqual(t.resolve("G.x").type.kind, "unsupported")

    def test_bit_members(self):
        bits = dt_entry("ST_Bits", 1, 65, subs=[
            dt_entry("b0", 1, 33, "BIT", offs=0, flags=0x2 | at.DTFLAG_BITVALUES),
            dt_entry("b3", 1, 33, "BIT", offs=3, flags=0x2 | at.DTFLAG_BITVALUES)])
        t = at.SymbolTable(sym_entry("G.st", 100, 1, 65, "ST_Bits"), bits)
        r = t.resolve("G.st.b3")
        self.assertEqual((r.io, r.bit, r.type.dl_type()), (100, 3, "bool8"))
        self.assertTrue(at.decode(r.type, b"\x08", 0, r.bit))
        self.assertFalse(at.decode(r.type, b"\x07", 0, r.bit))
        self.assertEqual(at.decode_value(t, t.resolve("G.st"), b"\x09"), {"b0": True, "b3": True})


class HelpersTest(unittest.TestCase):
    def test_chunk_reads(self):
        items = [(1, i, 4) for i in range(1200)]
        self.assertEqual(at.chunk_reads(items), [(0, 500), (500, 1000), (1000, 1200)])
        big = [(1, 0, 40000), (1, 0, 40000), (1, 0, 10)]
        self.assertEqual(at.chunk_reads(big), [(0, 1), (1, 3)])
        self.assertEqual(at.chunk_reads([(1, 0, 100000)]), [(0, 1)])     # one big value still gets read
        self.assertEqual(at.chunk_reads([]), [])

    def test_split_sum_read(self):
        data = struct.pack("<2I", 0, 0x710) + b"\x01\x02" + b"\x00\x00\x00\x00"
        self.assertEqual(at.split_sum_read([2, 4], data), [(0, b"\x01\x02"), (0x710, b"\x00" * 4)])
        with self.assertRaises(at.AdsTypeError):
            at.split_sum_read([2, 4], b"\x00" * 4)

    def test_texts(self):
        self.assertEqual(at.error_text(0x710), "symbol not found (0x710)")
        self.assertEqual(at.error_text(0x1234), "ADS error 0x1234")
        self.assertEqual((at.state_name(5), at.state_name(15), at.state_name(None)), ("RUN", "CONFIG", None))
        self.assertTrue(at.valid_net_id("5.57.166.80.1.1"))
        self.assertFalse(at.valid_net_id("5.57.166.80.1"))
        self.assertFalse(at.valid_net_id("256.1.1.1.1.1"))

    def test_request(self):
        req = at.server_info_request()
        self.assertEqual(struct.unpack_from("<III", req), (at.UDP_MAGIC, 0, 1))
        self.assertEqual(len(req), 24)

    def test_member_layout_helper(self):
        self.assertEqual(len(member("x", "INT", 0, 2, 2)), struct.unpack_from("<I", member("x", "INT", 0, 2, 2))[0])


if __name__ == "__main__":
    unittest.main()
