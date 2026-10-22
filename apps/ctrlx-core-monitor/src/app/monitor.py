"""Monitoring of other ctrlX COREs via Data Layer remote connections - no ctrlxdatalayer import.

ctrlX OS feature "Data Layer remote connection" (verified on ctrlX OS 4.6, docs/ctrlx/gotchas.md):
creating {"name": N, "address": "tcp://<user>:<pw>@<ip>?sslport=443&NoCheckCertificate=true"}
under devices/remotes mounts the whole Data Layer of the other device at devices/remotes/N/...
for every app on this device. The Data Layer persists these remotes itself.

This module only manages remotes named REMOTE_PREFIX + <core name>; remotes created by hand
(Settings > Data Layer) are never touched. Reconcile = make devices/remotes/cm-* match
settings["cores"]: delete what is not configured, (re)create what is missing or changed.

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
# ctrlX OS version: ctrlX OS 4.x has system/info/ctrlxOsVersion ("4.6"). ctrlX OS 3.6 has not;
# there the major.minor of the Device Admin app is the OS release, and the installed-app nodes
# are named snap_<name> (4.x: <name>).
OS_VERSION_NODE = "system/info/ctrlxOsVersion"
DEVICE_ADMIN_NODES = ("system/apps/installed/rexroth-deviceadmin",
                      "system/apps/installed/snap_rexroth-deviceadmin")


class DataLayerError(Exception):
    """result is the Data Layer result name, e.g. DL_INVALID_ADDRESS."""

    def __init__(self, result, message=""):
        super().__init__(message or result)
        self.result = result


def remote_name(core_name):
    return REMOTE_PREFIX + core_name


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
    if result == "DL_SEC_UNAUTHORIZED":
        return "login rejected - check user/password, or enable 'Skip certificate check'"
    if result in ("DL_INVALID_ADDRESS", "DL_TIMEOUT", "DL_CONNECTION_REFUSED"):
        return "not connected"
    return result


class CoreMonitor:
    def __init__(self, settings, on_update=None):
        self._lock = threading.Lock()
        self._client = None
        self._applied = {}      # remote name -> address created by this process
        self._created_at = {}   # remote name -> monotonic time of creation
        self._status = {}       # core name -> status dict
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
                self._set_status(core, state="offline", error="Data Layer not connected")
        else:
            if reconcile:
                self._reconcile(client, cores)
            slow = self._polls % SLOW_POLL_EVERY == 0
            self._polls += 1
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
        base = REMOTES_NODE + "/" + remote_name(core["name"]) + "/"
        values = {}
        nodes = dict(FAST_NODES, **(SLOW_NODES if slow or not self._has_info(core) else {}))
        for key, node in nodes.items():
            try:
                values[key] = client.read(base + node)
            except DataLayerError as exc:
                if key in FAST_NODES:  # the metrics must be there: the remote is not usable
                    young = time.monotonic() - self._created_at.get(remote_name(core["name"]), -1e9) < CONNECT_GRACE_S
                    self._set_status(core, state="connecting" if young else "error",
                                     error=None if young else _explain(exc.result), result=exc.result)
                    return
                values[key] = None   # optional info (e.g. an app missing on the other device)
        if "hostname" in values:  # slow poll
            values["osVersion"] = read_os_version(client, base)
        self._set_status(core, state="connected", error=None, result=None, updated=time.time(), **values)

    def _has_info(self, core):
        with self._lock:
            return "hostname" in self._status.get(core["name"], {})

    def _set_status(self, core, **fields):
        with self._lock:
            status = self._status.setdefault(core["name"], {})
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
                     "remote": REMOTES_NODE + "/" + remote_name(core["name"]), "state": "connecting"}
            entry.update(status.get(core["name"], {}))
            result.append(entry)
        return result


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
    keys = ("name", "host", "state", "hostname", "osVersion", "cpuPercent", "memUsedPercent")
    return json.dumps([{k: c.get(k) for k in keys} for c in snapshot], separators=(",", ":"))
