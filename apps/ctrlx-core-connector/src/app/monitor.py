"""Status of this device and of other ctrlX COREs via Data Layer remote connections - no ctrlxdatalayer import.

ctrlX OS feature "Data Layer remote connection" (verified on ctrlX OS 4.6, docs/ctrlx/gotchas.md):
creating {"name": N, "address": "tcp://<user>:<pw>@<ip>?sslport=443&NoCheckCertificate=true"}
under devices/remotes mounts the whole Data Layer of the other device at devices/remotes/N/...
for every app on this device. The Data Layer persists these remotes itself.

This module only manages remotes named REMOTE_PREFIX + <core name>; remotes created by hand
(Settings > Data Layer) are never touched. Reconcile = make devices/remotes/cm-* match
settings["cores"]: delete what is not configured, (re)create what is missing or changed.
The prefix "cm-" comes from the app's former name Core Monitor; it was kept so that Core Connector
takes over the remotes Core Monitor created.

A remote whose session the other device no longer accepts (SEC_INVALID_SESSION, e.g. after its
clock was set forward: gotchas #77) does not recover by itself: it is recreated (fresh login).

The device the app runs on is polled with the same nodes, without the remote prefix (LOCAL_ID).

The Data Layer is reached through a small client adapter (datalayer_service.ClientAdapter on the
device, a fake in the tests) with: read(address) -> value, create_remote(name, address),
remove(address). Each raises DataLayerError on failure.
"""

import json
import logging
import threading
import time
import urllib.parse

log = logging.getLogger("monitor")

REMOTES_NODE = "devices/remotes"
REMOTE_PREFIX = "cm-"
RECONCILE_INTERVAL_S = 30.0   # also re-check remotes periodically (someone may delete them by hand)
RECREATE_BACKOFF_S = 30.0     # a remote with an invalid session is recreated at most this often
SLOW_POLL_EVERY = 15          # read rarely changing values (versions) every n-th poll
CONNECT_GRACE_S = 20.0        # errors right after creating a remote are shown as "connecting"

# value key -> node below devices/remotes/<remote>/
FAST_NODES = {
    "cpuPercent": "framework/metrics/system/cpu-utilisation-percent",
    "memUsedPercent": "framework/metrics/system/memused-percent",
    "memUsedMb": "framework/metrics/system/memused-mb",
    "memTotalMb": "framework/metrics/system/memtotal-mb",
    "uptimeS": "system/info/uptime",
}
SLOW_NODES = {
    "hostname": "system/info/hostname",
    "architecture": "system/info/architecture",
}
# ctrlX operating state, {"state": "OPERATING" | "SETUP" | "SERVICE"} (read every poll, optional)
STATE_NODE = "scheduler/admin/state"
# ctrlX OS version: ctrlX OS 4.x has system/info/ctrlxOsVersion ("4.6"). ctrlX OS 3.6 has not;
# there the major.minor of the Device Admin app is the OS release, and the installed-app nodes
# are named snap_<name> (4.x: <name>).
OS_VERSION_NODE = "system/info/ctrlxOsVersion"
DEVICE_ADMIN_NODES = ("system/apps/installed/rexroth-deviceadmin",
                      "system/apps/installed/snap_rexroth-deviceadmin")
# Results after which only a new login helps (Python names have no "DL_" prefix: gotchas #78)
SESSION_ERRORS = {"SEC_INVALID_SESSION", "SEC_INVALID_TOKEN_CONTENT", "SEC_NO_TOKEN"}
# Results that concern the connection, not a single node
CONNECTION_ERRORS = {"SEC_UNAUTHORIZED", "TIMEOUT", "CONNECTION_REFUSED", "COMMUNICATION_ERROR",
                     "CLIENT_NOT_CONNECTED"}
LOCAL_ID = "_local"   # = config.LOCAL_ID (this module does not import config)


class DataLayerError(Exception):
    """result is the Data Layer result name, e.g. INVALID_ADDRESS (normalized: no "DL_" prefix)."""

    def __init__(self, result, message=""):
        self.result = normalize_result(result)
        super().__init__(message or self.result)


def normalize_result(result):
    """Result names as the Python library spells them: REST/C use DL_INVALID_ADDRESS,
    ctrlxdatalayer.variant.Result INVALID_ADDRESS; SEC_NO_TOKEN has a garbled enum name there."""
    result = str(result or "")
    if result.startswith("DL_"):
        result = result[3:]
    return {"sec_noSEC_NO_TOKEN_token": "SEC_NO_TOKEN", "SEC_INVALIDSESSION": "SEC_INVALID_SESSION"}.get(result, result)


def remote_name(core_name):
    return REMOTE_PREFIX + core_name


def remote_base(core_name):
    """Data Layer address prefix of a configured core's tree, with trailing '/'."""
    return REMOTES_NODE + "/" + remote_name(core_name) + "/"


def web_url(core):
    """Start page of the core's own ctrlX web UI."""
    port = int(core["sslPort"])
    return "https://{}{}/".format(core["host"], "" if port == 443 else ":{}".format(port))


def connection_address(core):
    """Connect string of a Data Layer remote. NoCheckCertificate=true is required for devices
    with the default self-signed certificate (else: DL_SEC_UNAUTHORIZED)."""
    query = "sslport={}".format(int(core["sslPort"]))
    if core.get("skipCertificateCheck"):
        query += "&NoCheckCertificate=true"
    return "tcp://{}:{}@{}?{}".format(urllib.parse.quote(core["user"], safe=""),
                                     urllib.parse.quote(core["password"], safe=""), core["host"], query)


def managed_remote_names(remotes_value):
    """Names of our remotes from the value of devices/remotes ({"remotes": [{"name", "address"}]})."""
    remotes = remotes_value.get("remotes", []) if isinstance(remotes_value, dict) else []
    return {r.get("name", "") for r in remotes if str(r.get("name", "")).startswith(REMOTE_PREFIX)}


def reconcile_plan(existing, desired, applied):
    """existing: set of our remote names on the device; desired: {remote: address} from the
    settings; applied: {remote: address} this process created. Returns (remove, create) lists.
    A remote whose address we did not create in this process is recreated once (its address
    is masked when read back, so a changed password could not be detected otherwise)."""
    remove = sorted(n for n in existing if n not in desired)
    create = []
    for name, address in sorted(desired.items()):
        if name in existing and applied.get(name) != address:
            remove.append(name)
        if name not in existing or applied.get(name) != address:
            create.append(name)
    return remove, create


def _explain(result):
    result = normalize_result(result)
    if result == "SEC_UNAUTHORIZED":
        return "login rejected - check user/password, or enable 'Skip certificate check'"
    if result in SESSION_ERRORS:
        return "session expired - reconnecting"
    if result in ("INVALID_ADDRESS", "TIMEOUT", "CONNECTION_REFUSED", "COMMUNICATION_ERROR", "CLIENT_NOT_CONNECTED"):
        return "not connected"
    return result


def read_core_status(client, base, slow):
    """Status values of the device whose Data Layer starts at base ("" = this device).
    Every value is optional - devices differ: a ctrlX OS 3.6 CORE (2026-10-31) had the metrics and the
    operating state, but no system/info/hostname, uptime or architecture. Raises DataLayerError when none
    of FAST_NODES can be read (not connected), or at once on a connection/session error."""
    values, first_error = {}, None
    for key, node in FAST_NODES.items():
        try:
            values[key] = client.read(base + node)
        except DataLayerError as exc:
            if exc.result in CONNECTION_ERRORS or exc.result in SESSION_ERRORS:
                raise   # the next node would fail the same way
            values[key] = None   # this device has no such node (INVALID_ADDRESS) or may not read it
            first_error = first_error or exc
    if first_error is not None and all(v is None for v in values.values()):
        raise first_error   # nothing readable: e.g. the remote is not mounted (INVALID_ADDRESS)
    try:
        state = client.read(base + STATE_NODE)
        values["operatingState"] = (state.get("state") or None) if isinstance(state, dict) else None
    except DataLayerError:
        values["operatingState"] = None   # e.g. no automationcore on that device
    if slow:
        for key, node in SLOW_NODES.items():
            try:
                values[key] = client.read(base + node)
            except DataLayerError:
                values[key] = None   # optional info
        values["osVersion"] = read_os_version(client, base)
    return values


class CoreMonitor:
    def __init__(self, settings, on_update=None):
        self._lock = threading.Lock()
        self._client = None
        self._applied = {}      # remote name -> address created by this process
        self._created_at = {}   # remote name -> monotonic time of creation
        self._status = {}       # core name -> status dict
        self._local = {}        # status of this device
        self._dirty = True
        self._last_reconcile = 0.0
        self._polls = 0
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._on_update = on_update or (lambda snapshot: None)
        self.on_settings_changed(settings)

    # -- configuration -----------------------------------------------------
    def on_settings_changed(self, settings):
        """SettingsStore listener (full settings incl. passwords - never log them)."""
        with self._lock:
            self._cores = [dict(c) for c in settings.get("cores", [])]
            self._interval_s = settings["general"]["pollIntervalMs"] / 1000.0
            names = {c["name"] for c in self._cores}
            self._status = {n: s for n, s in self._status.items() if n in names}
            self._dirty = True
        self._wake.set()

    def attach(self, client):
        """Called by the Data Layer service with a connected client adapter, or None."""
        with self._lock:
            self._client = client
            self._applied = {} if client is None else self._applied
            self._dirty = True
        self._wake.set()

    def client(self):
        """The connected client adapter or None (also used by the browser and the KVD editor)."""
        with self._lock:
            return self._client

    # -- thread ------------------------------------------------------------
    def start(self):
        self._thread = threading.Thread(target=self._run, name="monitor", daemon=True)
        self._thread.start()

    def stop(self, timeout=10.0):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)

    def _run(self):
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                log.exception("monitor cycle failed")
            self._wake.wait(self._interval_s)
            self._wake.clear()

    def run_once(self):
        """One cycle: reconcile remotes if needed, then poll every core (also used by tests)."""
        with self._lock:
            client, cores = self._client, list(self._cores)
            reconcile = self._dirty or time.monotonic() - self._last_reconcile > RECONCILE_INTERVAL_S
        if client is None:
            for core in cores:
                self._set_status(core["name"], state="offline", error="Data Layer not connected")
            with self._lock:
                self._local.update(state="offline", error="Data Layer not connected")
        else:
            if reconcile:
                self._reconcile(client, cores)
            slow = self._polls % SLOW_POLL_EVERY == 0
            self._polls += 1
            self._poll_local(client, slow)
            for core in cores:
                self._poll(client, core, slow)
        self._on_update(self.snapshot())

    def _reconcile(self, client, cores):
        desired = {remote_name(c["name"]): connection_address(c) for c in cores}
        try:
            existing = managed_remote_names(client.read(REMOTES_NODE))
        except DataLayerError as exc:
            log.warning("Cannot read %s: %s", REMOTES_NODE, exc.result)
            return
        with self._lock:
            applied = dict(self._applied)
        remove, create = reconcile_plan(existing, desired, applied)
        for name in remove:
            try:
                client.remove(REMOTES_NODE + "/" + name)
                log.info("Removed Data Layer remote %s", name)
            except DataLayerError as exc:
                log.warning("Cannot remove remote %s: %s", name, exc.result)
            applied.pop(name, None)
        for name in create:
            try:
                client.create_remote(name, desired[name])
                applied[name] = desired[name]
                self._created_at[name] = time.monotonic()
                log.info("Created Data Layer remote %s", name)  # never log the address (password)
            except DataLayerError as exc:
                log.warning("Cannot create remote %s: %s", name, exc.result)
        with self._lock:
            self._applied = applied
            self._dirty = False
            self._last_reconcile = time.monotonic()

    def _poll(self, client, core, slow):
        remote = remote_name(core["name"])
        with self._lock:
            slow = slow or "hostname" not in self._status.get(core["name"], {})
        try:
            values = read_core_status(client, remote_base(core["name"]), slow)
        except DataLayerError as exc:
            if exc.result in SESSION_ERRORS:
                self._request_recreate(remote, exc.result)
            young = time.monotonic() - self._created_at.get(remote, -1e9) < CONNECT_GRACE_S
            self._set_status(core["name"], state="connecting" if young else "error",
                             error=None if young else _explain(exc.result), result=exc.result)
            return
        self._set_status(core["name"], state="connected", error=None, result=None, updated=time.time(), **values)

    def _poll_local(self, client, slow):
        with self._lock:
            slow = slow or "hostname" not in self._local
        try:
            values = read_core_status(client, "", slow)
        except DataLayerError as exc:
            with self._lock:
                self._local.update(state="error", error=_explain(exc.result), result=exc.result)
            return
        with self._lock:
            self._local.update(state="connected", error=None, result=None, updated=time.time(), **values)

    def _request_recreate(self, remote, result):
        """Next reconcile removes and recreates the remote (= new login), at most every RECREATE_BACKOFF_S."""
        with self._lock:
            if time.monotonic() - self._created_at.get(remote, -1e9) < RECREATE_BACKOFF_S:
                return
            self._applied.pop(remote, None)
            self._dirty = True
        log.warning("Data Layer remote %s: %s - recreating it", remote, result)
        self._wake.set()

    def _set_status(self, name, **fields):
        with self._lock:
            status = self._status.setdefault(name, {})
            status.update(fields)

    # -- output ------------------------------------------------------------
    def snapshot(self):
        """Status of every configured core, in configuration order - without credentials."""
        with self._lock:
            cores, status = list(self._cores), {n: dict(s) for n, s in self._status.items()}
        result = []
        for core in cores:
            entry = {"name": core["name"], "host": core["host"], "sslPort": core["sslPort"],
                     "user": core["user"], "skipCertificateCheck": core["skipCertificateCheck"],
                     "remote": REMOTES_NODE + "/" + remote_name(core["name"]), "webUrl": web_url(core),
                     "state": "connecting"}
            entry.update(status.get(core["name"], {}))
            result.append(entry)
        return result

    def local_status(self):
        """Status of the device the app runs on (its web UI is the page's own origin)."""
        with self._lock:
            entry = {"name": LOCAL_ID, "local": True, "remote": "", "state": "connecting"}
            entry.update(self._local)
        return entry

    def core(self, name):
        """Connection settings of a configured core incl. the password (for app-internal use), or None."""
        with self._lock:
            for core in self._cores:
                if core["name"] == name:
                    return dict(core)
        return None


def read_os_version(client, base):
    """ctrlX OS version ("4.6", "3.6") of the device mounted at base, or None."""
    try:
        return str(client.read(base + OS_VERSION_NODE))
    except DataLayerError:
        pass
    for node in DEVICE_ADMIN_NODES:
        try:
            app = client.read(base + node)
        except DataLayerError:
            continue
        version = ((app.get("release") or {}).get("version") or "") if isinstance(app, dict) else ""
        parts = version.split(".")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            return parts[0] + "." + parts[1]
    return None


def summary_json(snapshot):
    """Compact JSON for the Data Layer node cores/summary."""
    keys = ("name", "host", "state", "operatingState", "hostname", "osVersion", "cpuPercent", "memUsedPercent")
    return json.dumps([{k: c.get(k) for k in keys} for c in snapshot], separators=(",", ":"))
