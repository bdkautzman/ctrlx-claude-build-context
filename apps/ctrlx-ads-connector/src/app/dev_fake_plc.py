"""DEV ONLY - not in the snap (snap/snapcraft.yaml primes it out): a fake TwinCAT PLC for the unit tests, the UI
check (tools/ui_check.py) and run-local.sh. A controller with the host "fake-plc" uses it when the app runs outside
the snap (ads_link.make_link). It answers like a TwinCAT 3 runtime on the bytes level: symbol and data type
uploads in the TwinCAT 3 layout (the same parser reads a real CX's tables, tests/fixtures), reads by index
group/offset, sum reads, handles, states.

The demo project: MAIN (values that change over time), GVL_Demo (every IEC type, an enum, a struct, arrays,
a constant), a function block.
"""

import math
import struct
import threading
import time

from app import ads_types as at

IG_PLC = 0x4040
LOCAL_NET_ID = "127.0.0.1.1.1"
FAKE_INFO = {"amsNetId": "5.1.2.3.1.1", "hostname": "CX-FAKE", "twincat": "3.1.4024", "os": "fake (local tests)"}


# ---- table builder (TwinCAT 3 layout, see ads_types.py) --------------------------------------------------------
def dt_entry(name, size, adst, type_name="", comment="", offs=0, dims=(), subs=(), enum=(), flags=0x1, attrs=()):
    nb, tb, cb = name.encode(), type_name.encode(), comment.encode()
    if enum:
        flags |= at.DTFLAG_ENUMINFOS
    if attrs:
        flags |= at.DTFLAG_ATTRIBUTES
    body = nb + b"\0" + tb + b"\0" + cb + b"\0"
    body += b"".join(struct.pack("<iI", low, count) for low, count in dims)
    body += b"".join(subs)
    if attrs:
        body += struct.pack("<H", len(attrs))
        for k, v in attrs:
            body += bytes([len(k), len(v)]) + k.encode() + b"\0" + v.encode() + b"\0"
    if enum:
        body += struct.pack("<H", len(enum))
        for ename, value in enum:
            body += bytes([len(ename)]) + ename.encode() + b"\0" + value.to_bytes(size, "little", signed=True)
    head = struct.pack("<8I5H", 42 + len(body), 1, 0, 0, size, offs, adst, flags,
                       len(nb), len(tb), len(cb), len(dims), len(subs))
    return head + body


def member(name, type_name, offs, size, adst, comment=""):
    return dt_entry(name, size, adst, type_name, comment, offs=offs, flags=0x2)


def sym_entry(name, io, size, adst, type_name, comment="", flags=0, ig=IG_PLC):
    nb, tb, cb = name.encode(), type_name.encode(), comment.encode()
    body = nb + b"\0" + tb + b"\0" + cb + b"\0"
    return struct.pack("<6I3H", 30 + len(body), ig, io, size, adst, flags, len(nb), len(tb), len(cb)) + body


BASE = [("BOOL", 1, 33), ("BYTE", 1, 17), ("WORD", 2, 18), ("DWORD", 4, 19), ("LWORD", 8, 21), ("SINT", 1, 16),
        ("USINT", 1, 17), ("INT", 2, 2), ("UINT", 2, 18), ("DINT", 4, 3), ("UDINT", 4, 19), ("LINT", 8, 20),
        ("ULINT", 8, 21), ("REAL", 4, 4), ("LREAL", 8, 5), ("TIME", 4, 19), ("LTIME", 8, 21), ("DT", 4, 19),
        ("STRING(80)", 81, 30), ("STRING(20)", 21, 30), ("WSTRING(20)", 42, 31)]


def demo_tables():
    """(symbols bytes, data types bytes, {symbol: (io, type name)}) of the demo project."""
    dts = [dt_entry(n, s, a) for n, s, a in BASE]
    dts.append(dt_entry("E_State", 2, 2, "INT", "machine state", enum=[("Idle", 0), ("Running", 1), ("Error", 2)]))
    dts.append(dt_entry("ST_Axis", 40, 65, comment="one axis", subs=[
        member("fPos", "LREAL", 0, 8, 5, "position [mm]"), member("fVel", "REAL", 8, 4, 4),
        member("bEnabled", "BOOL", 12, 1, 33), member("eState", "E_State", 14, 2, 2),
        member("sName", "STRING(20)", 16, 21, 30)]))
    dts.append(dt_entry("FB_Counter", 12, 65, subs=[
        member("nCount", "UDINT", 0, 4, 19), member("bUp", "BOOL", 4, 1, 33), member("tCycle", "TIME", 8, 4, 19)]))
    dts.append(dt_entry("ARRAY [0..9] OF INT", 20, 2, "INT", dims=[(0, 10)]))
    dts.append(dt_entry("ARRAY [1..3] OF ST_Axis", 120, 65, "ST_Axis", dims=[(1, 3)]))
    dts.append(dt_entry("ARRAY [1..2,1..3] OF REAL", 24, 4, "REAL", dims=[(1, 2), (1, 3)]))
    dts.append(dt_entry("POINTER TO INT", 8, 65, "INT"))
    syms, layout, io = [], {}, 0x1000
    for name, type_name, size, adst, comment, flags in [
            ("MAIN.nCounter", "UDINT", 4, 19, "counts up every cycle", 0),
            ("MAIN.fSine", "LREAL", 8, 5, "sine, period 10 s", 0),
            ("MAIN.bToggle", "BOOL", 1, 33, "toggles every second", 0),
            ("MAIN.fbCounter", "FB_Counter", 12, 65, "", 0),
            ("MAIN.pValue", "POINTER TO INT", 8, 65, "", 0),
            ("GVL_Demo.bValue", "BOOL", 1, 33, "", 0), ("GVL_Demo.nByte", "BYTE", 1, 17, "", 0),
            ("GVL_Demo.nWord", "WORD", 2, 18, "", 0), ("GVL_Demo.nDword", "DWORD", 4, 19, "", 0),
            ("GVL_Demo.nLword", "LWORD", 8, 21, "", 0), ("GVL_Demo.nSint", "SINT", 1, 16, "", 0),
            ("GVL_Demo.nUsint", "USINT", 1, 17, "", 0), ("GVL_Demo.nInt", "INT", 2, 2, "", 0),
            ("GVL_Demo.nUint", "UINT", 2, 18, "", 0), ("GVL_Demo.nDint", "DINT", 4, 3, "", 0),
            ("GVL_Demo.nUdint", "UDINT", 4, 19, "", 0), ("GVL_Demo.nLint", "LINT", 8, 20, "", 0),
            ("GVL_Demo.nUlint", "ULINT", 8, 21, "", 0), ("GVL_Demo.fReal", "REAL", 4, 4, "setpoint", 0),
            ("GVL_Demo.fLreal", "LREAL", 8, 5, "", 0), ("GVL_Demo.sText", "STRING(80)", 81, 30, "", 0),
            ("GVL_Demo.wsText", "WSTRING(20)", 42, 31, "", 0), ("GVL_Demo.tTime", "TIME", 4, 19, "", 0),
            ("GVL_Demo.tLtime", "LTIME", 8, 21, "", 0), ("GVL_Demo.dtStamp", "DT", 4, 19, "", 0),
            ("GVL_Demo.eState", "E_State", 2, 2, "", 0),
            ("GVL_Demo.aInt", "ARRAY [0..9] OF INT", 20, 2, "", 0),
            ("GVL_Demo.aAxis", "ARRAY [1..3] OF ST_Axis", 120, 65, "", 0),
            ("GVL_Demo.aGrid", "ARRAY [1..2,1..3] OF REAL", 24, 4, "", 0),
            ("GVL_Demo.stAxis", "ST_Axis", 40, 65, "", 0),
            ("GVL_Demo.nConst", "INT", 2, 2, "a constant", at.SYMFLAG_READONLY)]:
        io = (io + 7) // 8 * 8
        syms.append(sym_entry(name, io, size, adst, type_name, comment, flags))
        layout[name] = (io, type_name)
        io += size
    return b"".join(syms), b"".join(dts), layout


class FakePlc:
    """The memory and state of the demo PLC (shared by every FakeLink of a process, like one real controller)."""

    def __init__(self):
        self.symbols, self.datatypes, self.layout = demo_tables()
        self.table = at.SymbolTable(self.symbols, self.datatypes)
        self.mem = bytearray(0x2000)
        self.version = 1
        self.system_state, self.plc_state = at.ADS_STATE_RUN, at.ADS_STATE_RUN
        self.plc_port = 851
        self.down = False             # True: unreachable (open and every request fail)
        self.no_route = False         # True: TCP opens, every ADS request times out (TwinCAT without a route)
        self.lock = threading.Lock()
        self.handles, self._next_handle = {}, 0x1000
        self.writes = []              # (name or (ig, io), bytes) - for tests
        self._t0 = time.monotonic()
        self._init_values()

    def _put(self, name, value):
        res = self.table.resolve(name)
        self.mem[res.io:res.io + res.size] = at.encode(res.type, value)

    def _init_values(self):
        for name, value in [("GVL_Demo.bValue", True), ("GVL_Demo.nByte", 200), ("GVL_Demo.nWord", 60000),
                            ("GVL_Demo.nDword", 4000000000), ("GVL_Demo.nLword", 2 ** 63 + 5),
                            ("GVL_Demo.nSint", -100), ("GVL_Demo.nUsint", 100), ("GVL_Demo.nInt", -1234),
                            ("GVL_Demo.nUint", 1234), ("GVL_Demo.nDint", -123456), ("GVL_Demo.nUdint", 123456),
                            ("GVL_Demo.nLint", -(2 ** 40)), ("GVL_Demo.nUlint", 2 ** 40),
                            ("GVL_Demo.fReal", 0.1), ("GVL_Demo.fLreal", 3.141592653589793),
                            ("GVL_Demo.sText", "Hello ADS"), ("GVL_Demo.wsText", "Grüße"),
                            ("GVL_Demo.tTime", 1500), ("GVL_Demo.tLtime", 2500000000),
                            ("GVL_Demo.dtStamp", 1795000000), ("GVL_Demo.eState", 1),
                            ("GVL_Demo.aInt", list(range(10, 110, 10))),
                            ("GVL_Demo.aGrid", [1.5, 2.5, 3.5, 4.5, 5.5, 6.5]), ("GVL_Demo.nConst", 42),
                            ("GVL_Demo.stAxis.fPos", 120.5), ("GVL_Demo.stAxis.fVel", 10.0),
                            ("GVL_Demo.stAxis.bEnabled", True), ("GVL_Demo.stAxis.sName", "X"),
                            ("MAIN.fbCounter.tCycle", 10)]:
            self._put(name, value)
        for i in (1, 2, 3):
            self._put("GVL_Demo.aAxis[{}].fPos".format(i), 100.0 * i)
            self._put("GVL_Demo.aAxis[{}].sName".format(i), "Axis {}".format(i))

    def tick(self):
        """MAIN's values follow the clock (only while RUN, like a PLC task)."""
        if self.plc_state != at.ADS_STATE_RUN:
            return
        t = time.monotonic() - self._t0
        self._put("MAIN.nCounter", int(t * 100) % 2 ** 32)
        self._put("MAIN.fSine", math.sin(2 * math.pi * t / 10.0))
        self._put("MAIN.bToggle", int(t) % 2 == 1)
        self._put("MAIN.fbCounter.nCount", int(t) % 2 ** 32)

    def online_change(self, symbols=None, datatypes=None):
        """New tables (or the same ones) and a new symbol version - as after a download / online change."""
        with self.lock:
            if symbols is not None:
                self.symbols, self.datatypes = symbols, datatypes
                self.table = at.SymbolTable(symbols, datatypes)
            self.version = (self.version + 1) % 256
            self.handles.clear()

    def info2(self):
        return struct.pack("<6I", self.table.symbol_count, len(self.symbols), 0, len(self.datatypes), 0, 0)

    def _readonly(self, io, size):
        for name, (sio, _) in self.layout.items():
            sym = self.table.symbol(name)
            if sym is not None and sym.readonly and sio < io + size and io < sio + sym.size:
                return True
        return False


PLC = None


def shared_plc():
    global PLC
    if PLC is None:
        PLC = FakePlc()
    return PLC


class FakeLink:
    """The AdsLink interface (ads_link.py) on a FakePlc."""

    def __init__(self, plc=None, local_net_id=LOCAL_NET_ID):
        self.plc = plc or shared_plc()
        self._open = False
        self._local = local_net_id
        self.timeout_ms = 1000
        self.requests = 0

    @property
    def is_open(self):
        return self._open

    def _check(self, port=None):
        from app.ads_link import AdsError
        if self.plc.down:
            self._open = False
            raise AdsError(0x745, transport=True)
        if not self._open:
            raise AdsError(None, "not connected", transport=True)
        if self.plc.no_route:
            raise AdsError(0x745)
        if port is not None and port not in (self.plc.plc_port, at.PORT_SYSTEM):
            raise AdsError(0x6)
        if port == self.plc.plc_port and self.plc.system_state != at.ADS_STATE_RUN:
            raise AdsError(0x6)
        self.requests += 1

    def open(self):
        from app.ads_link import AdsError
        if self.plc.down:
            raise AdsError(None, "fake-plc:48898 not reachable", transport=True)
        self._open = True

    def close(self):
        self._open = False

    def set_timeout(self, timeout_ms):
        self.timeout_ms = timeout_ms

    def local_net_id(self):
        return self._local

    def read_state(self, port):
        self._check(port)
        return (self.plc.system_state if port == at.PORT_SYSTEM else self.plc.plc_state), 0

    def read_device_info(self, port):
        self._check(port)
        return ("TwinCAT System" if port == at.PORT_SYSTEM else "Demo PLC"), "3.1.4024"

    def _read(self, ig, io, size):
        from app.ads_link import AdsError
        plc = self.plc
        if ig == at.IG_SYM_UPLOADINFO2:
            return plc.info2()[:size]
        if ig == at.IG_SYM_UPLOAD:
            return plc.symbols[:size]
        if ig == at.IG_SYM_DT_UPLOAD:
            return plc.datatypes[:size]
        if ig == at.IG_SYM_VERSION:
            return bytes([plc.version])
        if ig == IG_PLC:
            if io + size > len(plc.mem):
                raise AdsError(0x703)
            return bytes(plc.mem[io:io + size])
        if ig == at.IG_SYM_VALBYHND:
            if io not in plc.handles:
                raise AdsError(0x710)
            res = plc.handles[io]
            return bytes(plc.mem[res.io:res.io + min(size, res.size)])
        raise AdsError(0x702)

    def read(self, port, ig, io, size):
        self._check(port)
        with self.plc.lock:
            self.plc.tick()
            return self._read(ig, io, size)

    def sum_read(self, port, items):
        from app.ads_link import AdsError
        self._check(port)
        out = []
        with self.plc.lock:
            self.plc.tick()
            for ig, io, size in items:
                try:
                    out.append((0, self._read(ig, io, size).ljust(size, b"\0")))
                except AdsError as exc:
                    out.append((exc.code, bytes(size)))
        return out

    def read_write(self, port, ig, io, size, data):
        from app.ads_link import AdsError
        self._check(port)
        if ig != at.IG_SYM_HNDBYNAME:
            raise AdsError(0x701)
        name = data.rstrip(b"\0").decode("cp1252")
        with self.plc.lock:
            try:
                res = self.plc.table.resolve(name)
            except at.AdsTypeError:
                raise AdsError(0x710)
            self.plc._next_handle += 1
            self.plc.handles[self.plc._next_handle] = res
            return struct.pack("<I", self.plc._next_handle)

    def write(self, port, ig, io, data):
        from app.ads_link import AdsError
        self._check(port)
        plc = self.plc
        with plc.lock:
            if ig == at.IG_SYM_RELEASEHND:
                plc.handles.pop(struct.unpack("<I", data)[0], None)
                return
            if ig == at.IG_SYM_VALBYHND:
                res = plc.handles.get(io)
                if res is None:
                    raise AdsError(0x710)
                if len(data) != res.size:
                    raise AdsError(0x705)
                if res.readonly:
                    raise AdsError(0x704)
                plc.mem[res.io:res.io + res.size] = data
                plc.writes.append((res.path, bytes(data)))
                return
            if ig == IG_PLC:
                if plc._readonly(io, len(data)):
                    raise AdsError(0x704)
                plc.mem[io:io + len(data)] = data
                plc.writes.append(((ig, io), bytes(data)))
                return
            raise AdsError(0x702)

    def write_by_name(self, port, name, data):
        handle, = struct.unpack("<I", self.read_write(port, at.IG_SYM_HNDBYNAME, 0, 4, name.encode() + b"\0"))
        try:
            self.write(port, at.IG_SYM_VALBYHND, handle, data)
        finally:
            self.write(port, at.IG_SYM_RELEASEHND, 0, struct.pack("<I", handle))
