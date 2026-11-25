"""The ADS transport to one controller: pyads 3.6.0 (MIT) with Beckhoff's AdsLib (in-process AMS router).

Only bytes go through here (read, write, read-write, sum read, state, device info); what they mean is
ads_types.py. pyads is imported lazily, so the web UI and the tests run without it (local runs: .dev/pylib,
README "Develop").

AdsLib facts that shape this module (pyads 3.6.0 on Linux):
* Routes are process-wide: AdsAddRoute(net id, ip) opens ONE TCP connection to <ip>:48898 per controller; the
  AMS port from AdsPortOpenEx() is this app's own local port. pyads' Connection.close() deletes the route of
  its net id - so the app keeps one AdsLink (route + local port) per controller and never two for a net id.
* The local AMS Net ID is process-wide too: without AdsSetLocalAddress, AdsLib takes the IP of the first
  connection + ".1.1". The controller only answers it with a route for that Net ID (TwinCAT: routes; the "Add
  route" dialog of this app sends TwinCAT's UDP add-route request, add_route()).
* AdsLib connects with a blocking connect (Linux: up to ~2 min for a host that is gone): open() first tries a
  plain TCP connect with a short timeout.
* A connection the controller closed (restart, route deleted) is not reopened by AdsLib: requests time out
  (0x745). Such errors are `transport` errors - the controller thread closes the link and opens it again.
"""

import ctypes
import logging
import socket
import struct
import threading

from app import ads_types as at

log = logging.getLogger("ads")

ADS_TCP_PORT = 48898
CONNECT_TIMEOUT_S = 2.0
# ADS return codes after which the link is closed and opened again (the TCP connection is gone or useless)
TRANSPORT_ERRORS = {0x7, 0x745, 0x746, 0x748, 0x749, 0x750, 0x751, 0x752, 0x753, 0x754, 0x755, 0x756}

_pyads = None
_lock = threading.Lock()          # AdsLib's router is process-wide: route changes one at a time
_local_net_id = ""


class AdsError(Exception):
    """code: the ADS return code (None: no ADS answer at all). transport: the link must be opened again."""

    def __init__(self, code, message=None, transport=False):
        self.code = code
        self.transport = transport or code in TRANSPORT_ERRORS
        super().__init__(message or (at.error_text(code) if code is not None else "ADS error"))


def available():
    """pyads + AdsLib.so importable? -> None, or the reason why not."""
    try:
        _load()
        return None
    except AdsError as exc:
        return str(exc)


def _load():
    global _pyads
    if _pyads is None:
        try:
            import pyads
            from pyads import pyads_ex, structs   # noqa: F401
        except (ImportError, OSError) as exc:     # OSError: AdsLib.so missing
            raise AdsError(None, "pyads not available: {}".format(exc), transport=True)
        _pyads = pyads
    return _pyads


# AdsLib writes its own lines to stderr (-> journal / Logbook): 0 verbose, 1 info, 2 warning, 3 error. Its info lines
# ("connection closed by remote") come at every reconnect to a controller that refuses this device (no route).
ADSLIB_LEVELS = {"DEBUG": 0, "INFO": 2, "WARNING": 2, "ERROR": 3}


def set_adslib_log_level(app_level):
    """AdsLib's Logger::logLevel (an exported C++ variable of AdsLib.so) for the app's log level. False when pyads or
    the symbol is not there (the app works the same, AdsLib just logs more)."""
    try:
        lib = _load().pyads_ex._adsDLL
        ctypes.c_size_t.in_dll(lib, "_ZN6Logger8logLevelE").value = ADSLIB_LEVELS.get(app_level, 2)
        return True
    except (AdsError, ValueError, AttributeError) as exc:
        log.debug("AdsLib log level not set: %s", exc)
        return False


def set_local_net_id(net_id):
    """Use this local AMS Net ID from now on. '' = AdsLib's default (IP of the first connection + .1.1); going
    back from a set one to '' needs an app restart (AdsLib keeps the last one)."""
    global _local_net_id
    if not net_id or net_id == _local_net_id:
        return
    pyads = _load()
    with _lock:
        pyads.set_local_address(net_id)
    _local_net_id = net_id
    log.info("Local AMS Net ID set to %s", net_id)


def local_ip_for(host):
    """The IP address of this device toward `host` (the address a route on the controller must name)."""
    if _dev_fake(host):
        return "127.0.0.1"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((host, at.UDP_PORT))       # no packet is sent; the kernel picks the route
            return s.getsockname()[0]
    except OSError:
        return ""


def discover(host, timeout_s=2.0):
    """TwinCAT's UDP server info (port 48899, read-only): {amsNetId, hostname, twincat, os}. No route needed."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout_s)
        try:
            s.sendto(at.server_info_request(), (host, at.UDP_PORT))
            while True:
                data, addr = s.recvfrom(2048)
                if addr[0] == socket.gethostbyname(host):
                    return at.parse_server_info(data)
        except socket.timeout:
            raise AdsError(None, "no answer on UDP port {} (no TwinCAT there, or a firewall)".format(at.UDP_PORT))
        except OSError as exc:
            raise AdsError(None, "{}: {}".format(host, exc.strerror or exc))


def add_route(host, local_net_id, local_ip, user, password, route_name):
    """TwinCAT's UDP add-route request (the "Add route" of TwinCAT XAE): the controller adds a route to this
    device's AMS Net ID at local_ip, if user/password are an administrator of the controller. True: added,
    False: credentials refused. Changes the controller's route table - only on request of a user."""
    pyads = _load()
    try:
        return bool(pyads.pyads_ex.adsAddRouteToPLC(local_net_id, local_ip, host, user, password,
                                                   route_name=route_name, added_net_id=local_net_id))
    except socket.timeout:
        raise AdsError(None, "no answer from {} on UDP port {}".format(host, at.UDP_PORT))
    except (RuntimeError, OSError) as exc:
        raise AdsError(None, str(exc))


class AdsLink:
    """Route + local AMS port to one controller. Every call is serialized (one request at a time per controller:
    the poll loop, the browser and Data Layer writes share it) and raises AdsError."""

    def __init__(self, host, net_id, timeout_ms=1000):
        self.host, self.net_id, self.timeout_ms = host, net_id, timeout_ms
        self._port = None
        self._call_lock = threading.Lock()

    @property
    def is_open(self):
        return self._port is not None

    def open(self):
        pyads = _load()
        try:
            socket.create_connection((self.host, ADS_TCP_PORT), timeout=CONNECT_TIMEOUT_S).close()
        except OSError as exc:
            raise AdsError(None, "{}:{} not reachable ({})".format(self.host, ADS_TCP_PORT, exc.strerror or exc),
                           transport=True)
        ex = pyads.pyads_ex
        with _lock:
            try:
                ex.adsAddRoute(pyads.structs.AmsAddr(self.net_id, 0).netIdStruct(), self.host)
            except pyads.ADSError as exc:
                raise AdsError(getattr(exc, "err_code", None), "route to {} not possible: {}".format(
                    self.host, _msg(exc)), transport=True)
            try:
                self._port = ex.adsPortOpenEx()
                ex.adsSyncSetTimeoutEx(self._port, int(self.timeout_ms))
            except (pyads.ADSError, RuntimeError) as exc:
                self._close_locked(pyads)
                raise AdsError(getattr(exc, "err_code", None), _msg(exc), transport=True)

    def close(self):
        if self._port is None or _pyads is None:
            return
        with _lock:
            self._close_locked(_pyads)

    def _close_locked(self, pyads):
        ex = pyads.pyads_ex
        if self._port is not None:
            try:
                ex.adsPortCloseEx(self._port)
            except Exception as exc:   # closing a dead port: nothing to do
                log.debug("adsPortCloseEx: %s", exc)
            self._port = None
        try:
            ex.adsDelRoute(pyads.structs.AmsAddr(self.net_id, 0).netIdStruct())
        except Exception as exc:
            log.debug("adsDelRoute: %s", exc)

    def set_timeout(self, timeout_ms):
        self.timeout_ms = timeout_ms
        if self._port is not None:
            self._call(lambda ex, port, addr: ex.adsSyncSetTimeoutEx(port, int(timeout_ms)), 0)

    def local_net_id(self):
        if self._port is None:
            return ""
        return self._call(lambda ex, port, addr: ex.adsGetLocalAddressEx(port).netid, 0)

    # -- requests ---------------------------------------------------------------------------------------------
    def _call(self, fn, ams_port):
        pyads = _load()
        with self._call_lock:
            if self._port is None:
                raise AdsError(None, "not connected", transport=True)
            try:
                return fn(pyads.pyads_ex, self._port, pyads.structs.AmsAddr(self.net_id, ams_port))
            except pyads.ADSError as exc:
                code = getattr(exc, "err_code", None)
                raise AdsError(code, at.error_text(code) if code in at.ADS_ERRORS else _msg(exc))
            except RuntimeError as exc:   # "Insufficient data": the answer was shorter than asked for
                raise AdsError(None, str(exc))

    def read_state(self, ams_port):
        """(ADS state, device state)."""
        return self._call(lambda ex, port, addr: tuple(ex.adsSyncReadStateReqEx(port, addr)), ams_port)

    def read_device_info(self, ams_port):
        """(name, 'major.minor.build')."""
        def fn(ex, port, addr):
            name, version = ex.adsSyncReadDeviceInfoReqEx(port, addr)
            return name, "{}.{}.{}".format(version.version, version.revision, version.build)
        return self._call(fn, ams_port)

    def read(self, ams_port, ig, io, size):
        def fn(ex, port, addr):
            return bytes(ex.adsSyncReadReqEx2(port, addr, ig, io, ctypes.c_ubyte * size, return_ctypes=True))
        return self._call(fn, ams_port)

    def read_write(self, ams_port, ig, io, size, data):
        def fn(ex, port, addr):
            out = ex.adsSyncReadWriteReqEx2(port, addr, ig, io, ctypes.c_ubyte * size, data,
                                            ctypes.c_ubyte * len(data), return_ctypes=True, check_length=False)
            return bytes(out)
        return self._call(fn, ams_port)

    def write(self, ams_port, ig, io, data):
        def fn(ex, port, addr):
            ex.adsSyncWriteReqEx(port, addr, ig, io, data, ctypes.c_ubyte * len(data))
        return self._call(fn, ams_port)

    def sum_read(self, ams_port, items):
        """[(ig, io, size)] in ONE request (0xF080) -> [(ADS error, bytes)] (split in chunks by the caller)."""
        if not items:
            return []

        def fn(ex, port, addr):
            req = (_pyads.structs.SAdsSumRequest * len(items))()
            for i, (ig, io, size) in enumerate(items):
                req[i].iGroup, req[i].iOffset, req[i].size = ig, io, size
            out = ex.adsSyncReadWriteReqEx2(port, addr, at.IG_SUMUP_READ, len(items), None, req, None,
                                            return_ctypes=True, check_length=False)
            return bytes(out)
        return at.split_sum_read([s for _, _, s in items], self._call(fn, ams_port))

    def write_by_name(self, ams_port, name, data):
        """Write through a symbol handle (get handle, write, release): safe across online changes."""
        raw = name.encode("cp1252", "replace") + b"\0"
        handle, = struct.unpack("<I", self.read_write(ams_port, at.IG_SYM_HNDBYNAME, 0, 4, raw))
        try:
            self.write(ams_port, at.IG_SYM_VALBYHND, handle, data)
        finally:
            try:
                self.write(ams_port, at.IG_SYM_RELEASEHND, 0, struct.pack("<I", handle))
            except AdsError as exc:
                log.debug("release handle %s: %s", handle, exc)


def _msg(exc):
    return getattr(exc, "msg", "") or str(exc)


def _dev_fake(host):
    """Local development only (not in the snap): host "fake-plc" is the demo PLC of app/dev_fake_plc.py - the UI check
    and run-local.sh use it; the module is primed out of the snap."""
    if host != "fake-plc":
        return None
    from app import config
    if config.IN_SNAP:
        return None
    try:
        from app import dev_fake_plc
        return dev_fake_plc
    except ImportError:
        return None


def make_link(host, net_id, timeout_ms):
    """The link for a controller (pyads; the demo PLC for "fake-plc" in local runs)."""
    fake = _dev_fake(host)
    return fake.FakeLink() if fake else AdsLink(host, net_id, timeout_ms)


def make_discover(host):
    fake = _dev_fake(host)
    return dict(fake.FAKE_INFO) if fake else discover(host)
