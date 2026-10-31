"""Nodes of the Key Value Database app (rexroth-kvd) of a core: add/remove entries of its nodes.json.

KVD keeps its node definitions in its app data (<active configuration>/kvd/nodes.json, schema
nodes.v1.0) and watches that file. On every change it restarts its Data Layer provider (~1 s in which
ALL its nodes are gone), then the new entry exists / the removed one is gone; the values of the other
nodes are kept (rexroth-kvd 4.6.3 and 4.6.5, gotchas #80). It misses a change made during such a restart,
and an entry it can't convert (e.g. a uint64 initial value above 2^53: it reads numbers as float64)
makes the whole KVD process exit - systemd starts it again 10 s later. Therefore: one change at a time
per core, wait until KVD runs again and shows the change, save once more if it runs but missed it,
and take an entry out again at once when KVD does not take it.

    this device:   the file itself, through the app's active-solution plug (whole solutions folder)
    other cores:   Solutions WebDAV of that core (GET/PUT /solutions/webdav/appdata/kvd/nodes.json)
                   with the core's connection user; WebDAV ignores If-Match, so edits of one core are
                   serialized here and the file is re-read right before each change.
A node KVD does not create within APPLY_TIMEOUT_S is taken out of the file again (no broken entry stays).
"""

import json
import logging
import os
import re
import threading
import time

from app import dltypes
from app.monitor import DataLayerError
from app.rest_client import CoreRestClient, RestError

log = logging.getLogger("kvd")

WEBDAV_NODES = "/solutions/webdav/appdata/kvd/nodes.json"
KVD_ROOT_NODE = "kvd"            # KVD's own nodes (kvd/db, kvd/value-count): KVD runs on the core
KVD_APP_NODES = ("system/apps/installed/rexroth-kvd", "system/apps/installed/snap_rexroth-kvd")  # 4.x / 3.6
APPLY_TIMEOUT_S = 6.0
# KVD reads initial values as float64: larger 64-bit integers lose digits or crash KVD (2^64-1 did)
MAX_EXACT_INT = 2 ** 53 - 1
APPLY_POLL_S = 0.25
SEGMENT = r"[A-Za-z0-9_.\-]{1,128}"
ADDRESS_RE = re.compile(r"{0}(/{0})*".format(SEGMENT))
NODE_CLASSES = ("Variable", "Folder")
# what the add dialog offers: KVD's scalar and array types (timestamp, flatbuffers and models are not offered)
TYPES = ("bool8", "int8", "uint8", "int16", "uint16", "int32", "uint32", "int64", "uint64", "float32", "float64",
         "string", "arbool8", "arint8", "aruint8", "arint16", "aruint16", "arint32", "aruint32", "arint64",
         "aruint64", "arfloat32", "arfloat64", "arstring")


class KvdError(Exception):
    def __init__(self, status, title, errors=None):
        super().__init__(title)
        self.status = status
        self.title = title
        self.errors = list(errors or [])


def empty_doc():
    return {"$schema": "./schemas/nodes.v1.0.schema.json", "types": [], "nodes": []}


def parse_doc(raw):
    try:
        doc = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except (ValueError, UnicodeError):
        raise KvdError(502, "The core's kvd/nodes.json is not valid JSON - fix it in Manage app data")
    if not isinstance(doc, dict) or not isinstance(doc.get("nodes", []), list):
        raise KvdError(502, "The core's kvd/nodes.json has no node list")
    doc.setdefault("nodes", [])
    doc.setdefault("types", [])   # required by the schema
    return doc


def dump_doc(doc):
    return (json.dumps(doc, indent=4, ensure_ascii=False) + "\n").encode("utf-8")   # KVD's own layout


def defined_addresses(doc):
    return [str(n.get("address", "")) for n in doc.get("nodes", []) if isinstance(n, dict)]


def build_entry(req):
    """nodes.json entry from the add dialog. Raises KvdError(400) with 'field: problem' errors."""
    if not isinstance(req, dict):
        raise KvdError(400, "Body must be a JSON object")
    errors = []
    address = str(req.get("address", "")).strip().strip("/")
    if not ADDRESS_RE.fullmatch(address) or len(address) > 512:
        errors.append("address: letters, digits, '_', '-', '.' and '/' between the parts")
    node_class = req.get("nodeClass", "Variable")
    if node_class not in NODE_CLASSES:
        errors.append("nodeClass: Variable or Folder")
    description = req.get("description", "") or ""
    unit = req.get("unit", "") or ""
    if not isinstance(description, str) or len(description) > 1000:
        errors.append("description: text, max. 1000 characters")
    if not isinstance(unit, str) or len(unit) > 32:
        errors.append("unit: text, max. 32 characters")
    entry = {"address": address}
    metadata = {"nodeClass": node_class}
    if node_class == "Variable":
        vtype = req.get("type")
        if vtype not in TYPES:
            errors.append("type: one of " + ", ".join(TYPES))
        else:
            entry["type"] = vtype
            raw = req.get("value")
            if raw is not None and raw != "":
                try:
                    entry["value"] = dltypes.coerce(vtype, raw)
                except ValueError as exc:
                    errors.append("value: {}".format(exc))
                else:
                    values = entry["value"] if isinstance(entry["value"], list) else [entry["value"]]
                    scalar = vtype[2:] if vtype.startswith("ar") else vtype
                    if scalar in ("int64", "uint64") and any(abs(v) > MAX_EXACT_INT for v in values):
                        errors.append("value: KVD takes initial values up to {} - write larger ones after "
                                      "the node exists".format(MAX_EXACT_INT))
        if isinstance(unit, str) and unit.strip():
            metadata["unit"] = unit.strip()
        if req.get("readOnly"):
            metadata["operations"] = {"write": False}
    if isinstance(description, str) and description.strip():
        metadata["description"] = description.strip()
    if errors:
        raise KvdError(400, "Invalid node", errors)
    entry["metadata"] = metadata
    return entry


class LocalNodesFile:
    """nodes.json of KVD on this device (active configuration)."""

    def __init__(self, path):
        self.path = path

    def load(self):
        try:
            with open(self.path, "rb") as f:
                return parse_doc(f.read())
        except FileNotFoundError:
            if not os.path.isdir(os.path.dirname(self.path)):
                raise KvdError(503, "KVD app data folder not found (active configuration not available?)")
            return empty_doc()
        except OSError as exc:
            raise KvdError(502, "Cannot read {}: {}".format(self.path, exc))

    def save(self, doc):
        folder = os.path.dirname(self.path)
        tmp = os.path.join(folder, ".nodes.json.ctrlx-core-connector.tmp")
        try:
            mode = os.stat(self.path).st_mode & 0o777
        except OSError:
            mode = 0o600
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
            with os.fdopen(fd, "wb") as f:
                f.write(dump_doc(doc))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except OSError as exc:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise KvdError(502, "Cannot write {}: {}".format(self.path, exc))


class RemoteNodesFile:
    """nodes.json of KVD on another core, through its Solutions WebDAV."""

    def __init__(self, client):
        self.client = client

    def _call(self, method, data=None):
        try:
            return self.client.request(method, WEBDAV_NODES, data, "application/json" if data else None)
        except RestError as exc:
            raise KvdError(502, "REST API of the core: {}".format(exc))

    def load(self):
        status, body = self._call("GET")
        if status == 404:
            return empty_doc()
        if status in (401, 403):
            raise KvdError(403, "The connection user may not read the core's app data (HTTP {})".format(status))
        if status != 200:
            raise KvdError(502, "Reading kvd/nodes.json from the core failed (HTTP {})".format(status))
        return parse_doc(body)

    def save(self, doc):
        status, _ = self._call("PUT", dump_doc(doc))
        if status in (401, 403):
            raise KvdError(403, "The connection user may not change the core's app data (HTTP {})".format(status))
        if status not in (200, 201, 204):
            raise KvdError(502, "Writing kvd/nodes.json on the core failed (HTTP {})".format(status))


class KvdService:
    def __init__(self, get_client, local_nodes_file, apply_timeout_s=APPLY_TIMEOUT_S):
        """get_client() -> Data Layer client adapter or None (to check KVD and wait for nodes)."""
        self._get_client = get_client
        self._local = LocalNodesFile(local_nodes_file)
        self._apply_timeout_s = apply_timeout_s
        self._rest = {}      # core name -> (connection key, CoreRestClient)
        self._locks = {}
        self._guard = threading.Lock()

    def _lock(self, key):
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def _store(self, core):
        """core: None for this device, else the core's settings (incl. password)."""
        if core is None:
            return self._local
        key = (core["host"], core["sslPort"], core["user"], core["password"], core["skipCertificateCheck"])
        with self._guard:
            cached = self._rest.get(core["name"])
            if cached is None or cached[0] != key:
                cached = (key, CoreRestClient(core["host"], core["sslPort"], core["user"], core["password"],
                                              core["skipCertificateCheck"]))
                self._rest[core["name"]] = cached
        return RemoteNodesFile(cached[1])

    def _client(self):
        client = self._get_client()
        if client is None:
            raise DataLayerError("CLIENT_NOT_CONNECTED", "Data Layer not connected")
        return client

    @staticmethod
    def _exists(client, address):
        try:
            client.metadata(address)
            return True
        except DataLayerError as exc:
            if exc.result == "INVALID_ADDRESS":
                return False
            raise

    @staticmethod
    def _running(client, base):
        """KVD's own root node is there: KVD runs (it is missing for ~1 s while KVD restarts)."""
        try:
            return KVD_ROOT_NODE in client.browse(base)
        except DataLayerError:
            return False

    def _installed(self, client, base):
        if self._running(client, base):
            return True
        return any(self._exists(client, base + node) for node in KVD_APP_NODES)

    def _wait(self, client, base, address, present):
        """Until KVD runs and shows the node present/absent, or the timeout."""
        deadline = time.monotonic() + self._apply_timeout_s
        while True:
            if self._exists(client, base + address) == present and (present or self._running(client, base)):
                return True
            if time.monotonic() > deadline:
                return False
            time.sleep(APPLY_POLL_S)

    def _await_running(self, client, base):
        """KVD running, possibly after waiting for its restart. Raises KvdError(409) if it isn't there."""
        if self._running(client, base):
            return
        if not self._installed(client, base):
            raise KvdError(409, "The Key Value Database app is not installed on this core")
        deadline = time.monotonic() + self._apply_timeout_s
        while not self._running(client, base):
            if time.monotonic() > deadline:
                raise KvdError(503, "The Key Value Database app is installed but not running on this core")
            time.sleep(APPLY_POLL_S)

    def _apply(self, client, base, store, doc, address, present):
        """Save and wait until KVD shows the change; save once more if KVD runs but missed it."""
        store.save(doc)
        if self._wait(client, base, address, present):
            return True
        if not self._running(client, base):   # KVD exited (an entry it can't take): don't feed it again
            return False
        log.info("KVD did not apply the change of %s - saving nodes.json again", address)
        store.save(doc)
        return self._wait(client, base, address, present)

    def status(self, base, core):
        """{"available", "running", "nodes": [addresses in nodes.json], "error"} for the browser page."""
        client = self._client()
        if not self._installed(client, base):
            return {"available": False, "running": False, "nodes": [], "error": None}
        running = self._running(client, base)
        try:
            doc = self._store(core).load()
        except KvdError as exc:
            return {"available": True, "running": running, "nodes": [], "error": exc.title}
        return {"available": True, "running": running, "nodes": defined_addresses(doc), "error": None}

    def add(self, base, core, req, key):
        entry = build_entry(req)
        with self._lock(key):
            client = self._client()
            self._await_running(client, base)
            if self._exists(client, base + entry["address"]):
                raise KvdError(409, "A node with this address exists", ["address: already exists"])
            store = self._store(core)
            doc = store.load()
            if entry["address"] in defined_addresses(doc):
                raise KvdError(409, "This address is already in kvd/nodes.json", ["address: already defined"])
            doc["nodes"].append(entry)
            if self._apply(client, base, store, doc, entry["address"], True):
                log.info("KVD node %s added on %s", entry["address"], key)
                return entry
            doc = store.load()   # KVD did not take it: don't leave the entry behind
            doc["nodes"] = [n for n in doc["nodes"] if not (isinstance(n, dict) and n.get("address") == entry["address"])]
            store.save(doc)
            log.warning("KVD did not create %s on %s - entry removed again", entry["address"], key)
            raise KvdError(502, "KVD did not create the node, so the entry was removed again. "
                                "The core's Logbook shows KVD's reason.")

    def remove(self, base, core, address, key):
        address = str(address or "").strip().strip("/")
        with self._lock(key):
            client = self._client()
            self._await_running(client, base)
            store = self._store(core)
            doc = store.load()
            if address not in defined_addresses(doc):
                raise KvdError(404, "This node is not defined in the core's kvd/nodes.json")
            doc["nodes"] = [n for n in doc["nodes"] if not (isinstance(n, dict) and n.get("address") == address)]
            if any(a.startswith(address + "/") for a in defined_addresses(doc)):
                store.save(doc)   # the folder stays (implicitly): nothing to wait for
                log.info("KVD node %s removed on %s (nodes below it stay)", address, key)
                return {"removed": address, "gone": False,
                        "note": "Nodes below it are still defined, so the folder stays (without its own settings)."}
            gone = self._apply(client, base, store, doc, address, False)
            log.info("KVD node %s removed on %s", address, key)
            return {"removed": address, "gone": gone,
                    "note": None if gone else "KVD has not removed the node yet."}
