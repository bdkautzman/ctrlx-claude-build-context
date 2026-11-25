"""The TwinCAT controllers: one thread per configured controller (settings["controllers"]).

Each thread
* connects (ads_link: route + AMS port; the AMS Net ID from the settings or TwinCAT's UDP server info),
* reads every second the state of the TwinCAT system (port 10000: RUN / CONFIG) and of the PLC runtime (amsPort),
* uploads the symbol and data type tables when needed (first use, new symbol version after a download or an
  online change) - the browser and the Data Layer nodes share them,
* reads the controller's Data Layer nodes (plc_nodes.NodeMap) every pollIntervalMs with ADS sum reads (one request
  for up to 500 variables, the symbol version as the first item: a change throws the values of that answer away
  and loads the tables again), and decodes only values whose bytes changed.
A lost connection (ads_link.AdsError.transport) closes the link; the thread opens it again after 1, 2, 5, 10 s.

Writes (browser: operate role; Data Layer nodes: the writer's Data Layer permission) go through a symbol handle
(get handle, write, release) and wait for the PLC's answer (timeout: settings ads.timeoutMs per request).
"""

import logging
import threading
import time

from app import ads_types as at
from app.ads_link import AdsError, local_ip_for, make_discover, make_link, set_local_net_id
from app.plc_nodes import NodeMap, default_address, valid_address

log = logging.getLogger("controllers")

BACKOFF_S = (1.0, 2.0, 5.0, 10.0, 10.0, 30.0)   # then every 30 s (AdsLib logs each attempt; X7, 2026-11-24)
STATE_INTERVAL_S = 1.0
VERSION_CHECK_S = 2.0              # browser: the symbol version is checked at most this often
MAX_TABLE_BYTES = 24 * 1024 * 1024 # symbol + data type tables of one controller (RAM, CLAUDE.md)
TREE_VALUE_BYTES = 4096            # values shown in the tree (bigger ones: in the details)
TREE_ARRAY_ITEMS = 20              # array elements shown in a tree row
MAX_ADD = 500                      # Data Layer nodes one "add" may create (a struct with all its members)
MAX_PATHS = 500                    # values one browser request may read


class ControllerUnavailable(Exception):
    """The controller (or its PLC runtime) can't answer now - web API: 503."""


def _restart_keys(cfg):
    return cfg["host"], cfg["amsNetId"], cfg["amsPort"]


class _Read:
    __slots__ = ("address", "res", "raw")

    def __init__(self, address, res):
        self.address, self.res, self.raw = address, res, None


class Controller:
    def __init__(self, cfg, ads_cfg, nodes: NodeMap, link_factory=make_link, discover=make_discover,
                 clock=time.monotonic):
        self.cfg = dict(cfg)
        self.name = cfg["name"]
        self.ads_cfg = dict(ads_cfg)
        self.nodes = nodes
        self._link_factory, self._discover, self._clock = link_factory, discover, clock
        self._link = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._table = None
        self._table_lock = threading.RLock()
        self._reads = None
        self._last_state = -1e9
        self._last_version_check = -1e9
        self._status_lock = threading.Lock()
        self._status = {"state": "connecting", "error": None, "netId": cfg["amsNetId"] or None,
                        "localNetId": None, "localIp": None, "hostname": None, "twincat": None, "os": None,
                        "systemState": None, "plcState": None, "plcName": None, "plcVersion": None,
                        "plcError": None, "symbols": None, "symbolVersion": None, "tableBytes": None,
                        "cycleMs": None, "since": None}
        self._plc_info_read = False
        self._answered = False

    # -- life cycle ---------------------------------------------------------------------------------------------
    def start(self):
        self._thread = threading.Thread(target=self._run, name="ads-" + self.name, daemon=True)
        self._thread.start()

    def stop(self, timeout=5.0):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)
        self._close()

    def update(self, cfg, ads_cfg):
        """Settings that need no new connection: poll interval, enabled, timeout. Nodes: re-resolved."""
        enabled_before = self.cfg["enabled"]
        self.cfg, self.ads_cfg = dict(cfg), dict(ads_cfg)
        link = self._link
        if link is not None and link.timeout_ms != ads_cfg["timeoutMs"]:
            try:
                link.set_timeout(ads_cfg["timeoutMs"])
            except AdsError as exc:
                log.debug("set_timeout: %s", exc)
        if enabled_before != cfg["enabled"]:
            log.info("Controller %s %s", self.name, "enabled" if cfg["enabled"] else "disabled")
        self.nodes_changed()

    def nodes_changed(self):
        self._reads = None
        self._wake.set()

    # -- status ---------------------------------------------------------------------------------------------
    def _set(self, **kw):
        with self._status_lock:
            before = (self._status["state"], self._status["error"])
            self._status.update(kw)
            after = (self._status["state"], self._status["error"])
            if "state" in kw and kw["state"] != before[0]:
                self._status["since"] = time.time()
        if after != before:
            state, error = after
            if state == "connected" and not error:
                log.info("Controller %s (%s) connected", self.name, self.cfg["host"])
            elif error and error != before[1]:
                (log.warning if state in ("offline", "error") else log.info)(
                    "Controller %s (%s): %s - %s", self.name, self.cfg["host"], state, error)

    def status(self):
        with self._status_lock:
            s = dict(self._status)
        entries = self.nodes.entries(self.name)
        s.update({"name": self.name, "host": self.cfg["host"], "amsNetId": self.cfg["amsNetId"],
                  "amsPort": self.cfg["amsPort"], "enabled": self.cfg["enabled"],
                  "pollIntervalMs": self.cfg["pollIntervalMs"], "nodes": len(entries),
                  "nodesOk": sum(1 for e in entries if e.ok)})
        s["systemStateName"] = at.state_name(s["systemState"])
        s["plcStateName"] = at.state_name(s["plcState"])
        return s

    # -- thread ---------------------------------------------------------------------------------------------
    def _wait(self, seconds):
        self._wake.wait(max(0.0, seconds))
        self._wake.clear()

    def _run(self):
        failures = 0
        while not self._stop.is_set():
            if not self.cfg["enabled"]:
                self._close()
                self._set(state="disabled", error=None)
                self.nodes.set_all(self.name, "controller disabled", offline=True)
                self._wait(5.0)
                continue
            started = self._clock()
            try:
                if self._link is None or not self._link.is_open:
                    self._connect()
                self._cycle()
                failures = 0
                self._set(cycleMs=round((self._clock() - started) * 1000, 1))
                self._wait(self.cfg["pollIntervalMs"] / 1000.0 - (self._clock() - started))
            except AdsError as exc:
                if exc.transport:
                    failures += 1
                    self._lost(exc)
                    self._wait(BACKOFF_S[min(failures, len(BACKOFF_S)) - 1])
                else:
                    self._set(state="connected", error=str(exc))
                    self.nodes.set_all(self.name, str(exc), offline=False)
                    self._wait(max(1.0, self.cfg["pollIntervalMs"] / 1000.0))
            except Exception as exc:
                log.exception("Controller %s: unexpected error", self.name)
                self._set(state="error", error="internal error: {}".format(exc))
                self._close()
                self._wait(10.0)
        self._close()

    def _close(self):
        link, self._link = self._link, None
        if link is not None:
            link.close()
        with self._table_lock:
            self._table, self._reads = None, None
        self._plc_info_read = False

    def _lost(self, exc):
        self._close()
        state = "offline" if exc.code is None else "error"
        error = str(exc)
        st = self._status
        if exc.code == 0x745 and not self._answered and st.get("localNetId") and st.get("localIp"):
            # TCP opened, but no ADS answer: TwinCAT resets connections from AMS Net IDs it has no route for
            # (X7 -> CX without a route, 2026-11-24: "Connection reset by peer", then 0x745)
            error = ("no ADS answer (timeout) - probably no route on the controller for AMS Net ID {} at {}"
                     .format(st["localNetId"], st["localIp"]))
        self.nodes.set_all(self.name, "not connected: {}".format(error), offline=True)   # nodes first, then the state
        self._set(state=state, error=error, systemState=None, plcState=None)

    def _connect(self):
        host = self.cfg["host"]
        if self._status["state"] not in ("offline", "error"):
            self._set(state="connecting", error=None)
        info = None
        try:
            info = self._discover(host)
        except AdsError as exc:
            if not self.cfg["amsNetId"]:
                raise AdsError(None, "AMS Net ID unknown - {} (enter it in the controller's settings)".format(exc),
                               transport=True)
        net_id = self.cfg["amsNetId"] or info["amsNetId"]
        link = self._link_factory(host, net_id, self.ads_cfg["timeoutMs"])
        link.open()
        self._link = link
        self._answered = False          # until the controller answers an ADS request on this connection
        self._last_state = -1e9
        extra = {"netId": net_id, "localNetId": link.local_net_id() or None,
                 "localIp": local_ip_for(host) or None}
        if info:
            extra.update(hostname=info.get("hostname") or None, twincat=info.get("twincat") or None,
                         os=info.get("os") or None)
        self._set(**extra)

    def _read_states(self, link):
        port = self.cfg["amsPort"]
        system, _ = link.read_state(at.PORT_SYSTEM)
        self._answered = True
        try:
            plc, _ = link.read_state(port)
            plc_error = None
        except AdsError as exc:
            if exc.transport:
                raise
            plc, plc_error = None, "PLC runtime {}: {}".format(port, exc)
        info = {}
        if plc is not None and not self._plc_info_read:
            try:
                info["plcName"], info["plcVersion"] = link.read_device_info(port)
                self._plc_info_read = True
            except AdsError as exc:
                if exc.transport:
                    raise
        if plc is None:
            self._plc_info_read = False
            with self._table_lock:
                self._table, self._reads = None, None
        self._set(state="connected", error=plc_error, systemState=system, plcState=plc, plcError=plc_error, **info)
        return plc_error

    def _cycle(self):
        link = self._link
        if self._clock() - self._last_state >= STATE_INTERVAL_S:
            self._last_state = self._clock()
            if self._read_states(link):
                self.nodes.set_all(self.name, self._status["plcError"], offline=False)
                return
        elif self._status["plcError"]:
            return
        entries = self.nodes.entries(self.name)
        if not entries:
            return
        with self._table_lock:
            table = self._table or self._load_table()
            if self._reads is None:
                self._reads = self._resolve(table, entries)
            reads = self._reads
        port = self.cfg["amsPort"]
        items = [(at.IG_SYM_VERSION, 0, 1)] + [(r.res.ig, r.res.io, r.res.size) for r in reads]
        results = []
        for start, end in at.chunk_reads(items):
            results.extend(link.sum_read(port, items[start:end]))
        err, raw = results[0]
        if err == 0 and raw and raw[0] != table.version:
            log.info("Controller %s: symbol version %s -> %s (download or online change) - loading the symbols "
                     "again", self.name, table.version, raw[0])
            with self._table_lock:
                self._table, self._reads = None, None
            self._wake.set()
            return
        for r, (err, raw) in zip(reads, results[1:]):
            if err:
                r.raw = None
                self.nodes.update(self.name, r.address, error=at.error_text(err))
            elif raw != r.raw:
                r.raw = raw
                value = at.decode(r.res.type, raw, 0, r.res.bit)
                self.nodes.update(self.name, r.address, tuple(value) if isinstance(value, list) else value)

    def _load_table(self):
        """Upload the symbol + data type tables (called with _table_lock held)."""
        link, port = self._link, self.cfg["amsPort"]
        if link is None:
            raise ControllerUnavailable("not connected")
        started = self._clock()
        try:
            info = link.read(port, at.IG_SYM_UPLOADINFO2, 0, 24)
            sym_bytes, dt_bytes = struct_unpack_info2(info)
        except AdsError as exc:
            if exc.transport or exc.code not in (0x701, 0x702, 0x703, 0x705):
                raise
            info = link.read(port, at.IG_SYM_UPLOADINFO, 0, 8)
            sym_bytes, dt_bytes = int.from_bytes(info[4:8], "little"), 0
        if sym_bytes + dt_bytes > MAX_TABLE_BYTES:
            raise AdsError(None, "symbol tables too big ({:.1f} MB, at most {} MB)".format(
                (sym_bytes + dt_bytes) / 1e6, MAX_TABLE_BYTES // (1024 * 1024)))
        version = link.read(port, at.IG_SYM_VERSION, 0, 1)[0]
        symbols = link.read(port, at.IG_SYM_UPLOAD, 0, sym_bytes) if sym_bytes else b""
        datatypes = link.read(port, at.IG_SYM_DT_UPLOAD, 0, dt_bytes) if dt_bytes else b""
        try:
            table = at.SymbolTable(symbols, datatypes, version)
        except at.AdsTypeError as exc:
            raise AdsError(None, "symbol table not readable: {}".format(exc))
        self._table, self._reads = table, None
        self._last_version_check = self._clock()
        self._set(symbols=table.symbol_count, symbolVersion=version, tableBytes=table.size_bytes)
        log.info("Controller %s: %d symbols (%d + %d bytes, version %d) loaded in %.0f ms", self.name,
                 table.symbol_count, len(symbols), len(datatypes), version, (self._clock() - started) * 1000)
        return table

    def _resolve(self, table, entries):
        reads = []
        for e in entries:
            try:
                res = table.resolve(e.symbol)
            except at.AdsTypeError as exc:
                self.nodes.update(self.name, e.address, error="not in the PLC: {}".format(exc))
                continue
            dl = res.type.dl_type()
            if dl != e.vtype:
                now = "{} ({})".format(dl, res.type.name) if dl else res.type.name
                self.nodes.update(self.name, e.address, error="the PLC variable is now {}, the node {} - add it "
                                  "again".format(now, e.vtype))
                continue
            if not res.readable:
                self.nodes.update(self.name, e.address, error="not readable ({} bytes)".format(res.size))
                continue
            # a node keeps its last value until the next poll (an online change doesn't interrupt readers)
            self.nodes.set_limits(self.name, e.address, res.type.max_chars,
                                  res.type.count if res.type.kind == "array" else None)
            reads.append(_Read(e.address, res))
        return reads

    # -- browser (web threads) ----------------------------------------------------------------------------------
    def _require_link(self):
        link = self._link
        st = self._status
        if link is None or not link.is_open or st["state"] != "connected":
            raise ControllerUnavailable("{} is not connected{}".format(
                self.name, ": " + st["error"] if st["error"] else ""))
        if st["plcError"]:
            raise ControllerUnavailable(st["plcError"])
        return link

    def table(self):
        """The symbol tables for the browser (loaded or checked against the symbol version first)."""
        link = self._require_link()
        try:
            with self._table_lock:
                now = self._clock()
                if self._table is not None and now - self._last_version_check >= VERSION_CHECK_S:
                    self._last_version_check = now
                    if link.read(self.cfg["amsPort"], at.IG_SYM_VERSION, 0, 1)[0] != self._table.version:
                        self._table, self._reads = None, None
                return self._table or self._load_table()
        except AdsError as exc:
            if exc.transport:
                self._wake.set()
            raise ControllerUnavailable(str(exc))

    def _mapped(self):
        out = {}
        for e in self.nodes.entries(self.name):
            out.setdefault(e.symbol.lower(), []).append(e.address)
        return out

    def _describe(self, res, mapped):
        t = res.type
        row = {"enum": [list(x) for x in t.enum]} if t.kind == "enum" else {}
        return {**row, "kind": t.kind, "type": t.name, "dlType": t.dl_type(), "size": res.size,
                "hasChildren": t.has_children and res.bit is None,
                "readonly": res.readonly, "comment": res.comment or t.comment, "unit": t.unit,
                "mapped": mapped.get(res.path.lower(), [])}

    def _read_values(self, link, rows):
        """rows: [(dict, Resolved)] -> fills dict["value"] / dict["valueError"] with one sum read per chunk."""
        if not rows:
            return
        items = [(res.ig, res.io, res.size) for _, res in rows]
        results = []
        try:
            for start, end in at.chunk_reads(items):
                results.extend(link.sum_read(self.cfg["amsPort"], items[start:end]))
        except AdsError as exc:
            if exc.transport:
                self._wake.set()
            for row, _ in rows:
                row["valueError"] = str(exc)
            return
        for (row, res), (err, raw) in zip(rows, results):
            if err:
                row["valueError"] = at.error_text(err)
                continue
            value = at.decode(res.type, raw, 0, res.bit, display=True)
            if isinstance(value, list) and len(value) > TREE_ARRAY_ITEMS:   # a tree row shows the first ones
                value, row["valueCut"] = value[:TREE_ARRAY_ITEMS], True
            row["value"] = value

    def browse(self, path):
        table = self.table()
        try:
            children, truncated = table.children(path)
        except at.AdsTypeError as exc:
            raise KeyError(str(exc))
        mapped = self._mapped()
        rows, reads = [], []
        for name, child, res in children:
            row = {"name": name, "path": child}
            if res is None:
                row.update(kind="folder", hasChildren=True)
            else:
                row.update(self._describe(res, mapped))
                if res.readable and res.size <= TREE_VALUE_BYTES and (res.type.leaf or row["dlType"]):
                    reads.append((row, res))
            rows.append(row)
        self._read_values(self._require_link(), reads)
        return {"path": path, "children": rows, "truncated": truncated, "symbolVersion": table.version}

    def values(self, paths):
        table = self.table()
        rows, reads = [], []
        for p in paths[:MAX_PATHS]:
            row = {"path": p}
            rows.append(row)
            try:
                res = table.resolve(p)
            except at.AdsTypeError as exc:
                row["valueError"] = str(exc)
                continue
            if res.readable and res.size <= TREE_VALUE_BYTES and (res.type.leaf or res.type.dl_type()):
                reads.append((row, res))
        self._read_values(self._require_link(), reads)
        return rows

    def details(self, path):
        table = self.table()
        if table.is_folder(path):
            path = table.folder_path(path)
            children, _ = table.children(path)
            return {"path": path, "kind": "folder", "name": path.split(".")[-1] if path else "(root)",
                    "childCount": len(children), "leafCount": _leaf_count(table, path)}
        try:
            res = table.resolve(path)
        except at.AdsTypeError as exc:
            raise KeyError(str(exc))
        t = res.type
        d = {"path": res.path, "name": res.path.split(".")[-1], "symbol": res.symbol, "ig": res.ig, "io": res.io,
             "bit": res.bit, "attributes": [list(a) for a in res.attributes], **self._describe(res, self._mapped())}
        d["writable"] = not res.readonly and res.bit is None and (t.leaf or d["dlType"] is not None)
        if t.kind in ("string", "wstring"):
            d["maxChars"] = t.max_chars
        if t.kind == "array":
            d["dims"] = [list(x) for x in t.dims]
            d["count"] = t.count
            d["elemType"] = t.elem.name if t.elem else None
        if t.kind == "struct":
            d["members"] = len(t.members)
        if not d["dlType"]:
            d["leafCount"] = _leaf_count(table, res.path)
        if res.readable:
            link = self._require_link()
            try:
                raw = link.read(self.cfg["amsPort"], res.ig, res.io, res.size)
                d["value"] = at.decode_value(table, res, raw, display=True)
            except AdsError as exc:
                if exc.transport:
                    self._wake.set()
                d["valueError"] = str(exc)
        elif t.kind == "pointer":
            d["valueError"] = "pointers and references are not read"
        elif res.size > at.MAX_VALUE_BYTES:
            d["valueError"] = "too large to show ({} bytes)".format(res.size)
        else:
            d["valueError"] = "this type can't be read ({})".format(t.comment or t.name)
        return d

    def value(self, path):
        """Value of the selected node (details pane refresh)."""
        table = self.table()
        res = table.resolve(path)
        if not res.readable:
            return {"path": path, "valueError": "not readable"}
        try:
            raw = self._require_link().read(self.cfg["amsPort"], res.ig, res.io, res.size)
        except AdsError as exc:
            return {"path": path, "valueError": str(exc)}
        return {"path": path, "value": at.decode_value(table, res, raw, display=True)}

    def write_symbol(self, path, value, from_input=False, vtype=None):
        """Write a value into the PLC. from_input: value from the web UI (text, JSON). vtype: the type the writer
        expects (a Data Layer node) - refused when the PLC variable has another one now."""
        table = self.table()
        res = table.resolve(path)
        if res.readonly:
            raise at.AdsTypeError("{} is read-only in the PLC".format(res.path))
        if res.bit is not None:
            raise at.AdsTypeError("bit members can't be written")
        if vtype is not None and res.type.dl_type() != vtype:
            raise at.AdsTypeError("the PLC variable is {} now, not {}".format(res.type.name, vtype))
        if from_input:
            value = at.parse_input(res.type, value)
        data = at.encode(res.type, value, res.path)
        link = self._require_link()
        try:
            link.write_by_name(self.cfg["amsPort"], res.path, data)
        except AdsError as exc:
            if exc.transport:
                self._wake.set()
            raise
        self._wake.set()      # the poll loop shows the new value at once
        return {"path": res.path, "value": at.decode(res.type, data, 0, None, display=True)}

    def plan_nodes(self, path, address=None, writable=False):
        """The Data Layer nodes for a browser selection: one for a value / an array of values, one per member for
        a struct or a namespace (at most MAX_ADD). address: the node's (or the base) address."""
        table = self.table()
        is_folder = table.is_folder(path)
        try:
            leaves = table.leaves(path, MAX_ADD)
        except at.AdsTypeError as exc:
            raise ValueError(str(exc))
        if not leaves:
            raise ValueError("{} has no values that a Data Layer node can hold".format(path or "the root"))
        base_path = path if is_folder else table.resolve(path).path
        base = address if address else default_address(base_path)
        if not valid_address(base):
            raise ValueError("address: letters, digits, '_' and '-' per level, levels separated by '/'")
        out = []
        for res in leaves:
            rest = res.path[len(base_path):] if res.path.lower().startswith(base_path.lower()) else res.path
            sub = default_address(rest) if rest else ""
            out.append({"controller": self.name, "symbol": res.path,
                        "address": base + ("/" + sub if sub else ""), "type": res.type.dl_type(),
                        "writable": bool(writable and not res.readonly and res.bit is None),
                        "description": (res.comment or "")[:200], "unit": res.type.unit})
        return out


def struct_unpack_info2(info):
    """UPLOADINFO2: symbols, symbol bytes, data types, data type bytes, ... -> (symbol bytes, data type bytes)."""
    if len(info) < 16:
        raise AdsError(None, "upload info too short")
    return int.from_bytes(info[4:8], "little"), int.from_bytes(info[12:16], "little")


def _leaf_count(table, path):
    try:
        return len(table.leaves(path, MAX_ADD))
    except at.AdsTypeError:
        return MAX_ADD + 1


class ControllerManager:
    """Controllers from the settings; the writer for plc_nodes.NodeMap."""

    def __init__(self, nodes: NodeMap, link_factory=make_link, discover=make_discover):
        self.nodes = nodes
        self.nodes.writer = self.write_node
        self._link_factory, self._discover = link_factory, discover
        self._controllers = {}
        self._order = []
        self._lock = threading.Lock()

    def apply(self, settings):
        """SettingsStore listener (also called once at the start)."""
        ads_cfg = settings["ads"]
        try:
            set_local_net_id(ads_cfg["localNetId"])
        except AdsError as exc:
            log.error("Local AMS Net ID %s not set: %s", ads_cfg["localNetId"], exc)
        self.nodes.configure(settings["nodes"])
        wanted = {c["name"]: c for c in settings["controllers"]}
        with self._lock:
            for name in list(self._controllers):
                c = self._controllers[name]
                if name not in wanted or _restart_keys(wanted[name]) != _restart_keys(c.cfg):
                    # wait for a running ADS request (up to the timeout): its late close() would delete the AdsLib
                    # route of the same Net ID under the new connection (that one recovers with one reconnect)
                    c.stop(timeout=2.0)
                    del self._controllers[name]
            for name, cfg in wanted.items():
                c = self._controllers.get(name)
                if c is None:
                    c = Controller(cfg, ads_cfg, self.nodes, self._link_factory, self._discover)
                    self._controllers[name] = c
                    c.start()
                else:
                    c.update(cfg, ads_cfg)
            self._order = list(wanted)

    def get(self, name):
        with self._lock:
            return self._controllers.get(name)

    def snapshot(self):
        with self._lock:
            controllers = [self._controllers[n] for n in self._order if n in self._controllers]
        return [c.status() for c in controllers]

    def summary(self):
        """For the Data Layer node controllers/summary."""
        keys = ("name", "host", "netId", "state", "systemStateName", "plcStateName", "nodes", "nodesOk", "error")
        return [{k: s.get(k) for k in keys} for s in self.snapshot()]

    def write_node(self, controller, symbol, vtype, value):
        c = self.get(controller)
        if c is None:
            raise ControllerUnavailable("no controller " + controller)
        return c.write_symbol(symbol, value, vtype=vtype)

    def stop(self):
        with self._lock:
            controllers = list(self._controllers.values())
            self._controllers = {}
        for c in controllers:
            c.stop(timeout=2.0)
