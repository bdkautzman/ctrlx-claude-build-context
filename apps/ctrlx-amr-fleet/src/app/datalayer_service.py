"""ctrlX Data Layer provider: publishes the nodes of NodeRegistry.

API reference (exact signatures): reference/ctrlxdatalayer-api-3.6.0/*.py
Pattern taken from SDK 4.6 samples: datalayer.provider, datalayer.provider.all-data.

Lifecycle:
    System("") -> start(False) -> factory().create_provider(conn) -> provider.start()
    -> register_node(address, ProviderNode) for each node -> ... -> unregister_node()
    -> provider.stop()/close() -> system.stop(False)/close()
The thread reconnects automatically when the provider loses its connection
(e.g. Data Layer broker restart, app started before automationcore).

ctrlxdatalayer is imported lazily so the rest of the app (web UI, settings) can run on a
PC without the native libcomm_datalayer library.
"""

import json
import logging
import threading
import time

from app import config
from app.datalayer_nodes import NodeRegistry, NodeWriteError, build_node_specs

log = logging.getLogger("datalayer")

RECONNECT_DELAY_S = 5.0


class DataLayerService:
    def __init__(self, settings, version, on_fleet_write=None, amr_inputs=None, fleet_reader=None):
        self.registry = NodeRegistry(config.DATALAYER_ROOT,
                                     build_node_specs(settings, version, on_fleet_write, fleet_reader))
        self.registry.set_internal("settings/active", json.dumps(settings, separators=(",", ":")))
        self._heartbeat_s = settings["general"]["heartbeatIntervalMs"] / 1000.0
        self._amr_inputs = amr_inputs     # per-AMR input modes: fleet/amr/** (amr_inputs.py)
        self._wake = threading.Event()    # input mode changed -> (un)register fleet/amr/** in the provider thread
        self._stop = threading.Event()
        self._thread = None
        self._status_lock = threading.Lock()
        self._status = {"enabled": False, "connected": False, "error": None, "registered": 0}

    # -- public -------------------------------------------------------------
    def start(self):
        self._thread = threading.Thread(target=self._run, name="datalayer", daemon=True)
        self._thread.start()

    def stop(self, timeout=10.0):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)

    def status(self):
        with self._status_lock:
            status = dict(self._status)
        conn = config.datalayer_connection_string()
        status["connection"] = conn.split("@")[-1] if conn else ""  # never expose credentials
        return status

    def on_settings_changed(self, settings):
        """SettingsStore listener."""
        self.registry.set_internal("settings/active", json.dumps(settings, separators=(",", ":")))
        self._heartbeat_s = settings["general"]["heartbeatIntervalMs"] / 1000.0

    def on_fleet_changed(self, snapshot):
        """FleetState listener (runs in the writer's thread - only in-memory updates)."""
        self.registry.set_internal("fleet/count", snapshot["count"])
        self.registry.set_internal("fleet/updates", snapshot["revision"])
        self.registry.set_internal("fleet/rejected", snapshot["rejected"])
        self.registry.set_internal("fleet/last-error", snapshot["lastError"])

    def on_input_changed(self):
        """Input mode or AMR list changed: the provider thread (un)registers fleet/amr/** within a moment."""
        self._wake.set()

    def on_map_changed(self, info):
        """MapSource listener."""
        self.registry.set_internal("map/info", json.dumps(info, separators=(",", ":")))

    # -- thread -------------------------------------------------------------
    def _set_status(self, **kwargs):
        with self._status_lock:
            self._status.update(kwargs)

    def _run(self):
        conn = config.datalayer_connection_string()
        if not conn:
            self._set_status(enabled=False, error="Data Layer disabled (not in snap and CTRLX_HOST not set)")
            log.warning("Data Layer disabled: not running in a snap and CTRLX_HOST is not set")
            self._run_offline()
            return
        try:
            import ctrlxdatalayer  # noqa: F401  (native lib required)
        except Exception as exc:  # ImportError or OSError from the missing .so
            self._set_status(enabled=False, error="ctrlxdatalayer not available: {}".format(exc))
            log.error("ctrlxdatalayer not available: %s", exc)
            self._run_offline()
            return

        self._set_status(enabled=True)
        while not self._stop.is_set():
            try:
                self._run_connected(conn)
            except Exception as exc:
                log.exception("Data Layer provider failed")
                self._set_status(connected=False, error=str(exc))
            if not self._stop.is_set():
                self._stop.wait(RECONNECT_DELAY_S)

    def _run_offline(self):
        """Keep heartbeat/state alive for the web UI even without a Data Layer."""
        self.registry.set_internal("status/state", "running (no Data Layer)")
        while not self._stop.wait(self._heartbeat_s):
            self._tick()

    def _tick(self):
        hb = self.registry.get("status/heartbeat")
        self.registry.set_internal("status/heartbeat", (hb + 1) % (2 ** 64))

    def _run_connected(self, conn):
        import ctrlxdatalayer
        from ctrlxdatalayer.variant import Result

        with ctrlxdatalayer.system.System("") as system:
            system.start(False)
            provider = system.factory().create_provider(conn)
            with provider:  # provider.close() on exit
                result = provider.start()
                if result != Result.OK:
                    self._set_status(connected=False, error="provider.start() failed: {}".format(result))
                    log.error("Data Layer provider start failed: %s (%s)", result, conn.split("@")[-1])
                    return
                nodes = []
                for spec in self.registry.specs():
                    node = _ProviderNode(self.registry, spec)
                    res = provider.register_node(self.registry.address(spec.name), node.provider_node)
                    if res != Result.OK:
                        log.error("register_node %s failed: %s", self.registry.address(spec.name), res)
                        node.close()
                        continue
                    nodes.append(node)  # keep references: GC would destroy the callbacks
                self.registry.set_internal("status/state", "running")
                self._set_status(connected=True, error=None, registered=len(nodes))
                log.info("Data Layer provider running, %d nodes under %s/", len(nodes), self.registry.root)
                tree = _AmrTree(self._amr_inputs, self.registry.address("fleet/amr")) if self._amr_inputs else None

                next_tick = time.monotonic() + self._heartbeat_s
                while not self._stop.is_set() and provider.is_connected():
                    if tree and tree.sync(provider):
                        self._set_status(registered=len(nodes) + len(tree.nodes))
                    self._wake.wait(max(0.0, next_tick - time.monotonic()))
                    self._wake.clear()
                    if time.monotonic() >= next_tick:
                        next_tick = time.monotonic() + self._heartbeat_s
                        self._tick()

                if not self._stop.is_set():
                    log.warning("Data Layer provider disconnected - reconnecting")
                self._set_status(connected=False, registered=0)
                if tree:
                    tree.unregister(provider)
                for node in nodes:
                    provider.unregister_node(self.registry.address(node.spec.name))
                    node.close()
                provider.stop()
            system.stop(False)


class _ProviderNode:
    """Bridges one NodeSpec to a ctrlxdatalayer ProviderNode (callbacks run in a Data Layer thread)."""

    def __init__(self, registry, spec):
        import ctrlxdatalayer
        from comm.datalayer import NodeClass
        from ctrlxdatalayer.metadata_utils import MetadataBuilder
        from ctrlxdatalayer.provider_node import ProviderNode, ProviderNodeCallbacks

        self.registry = registry
        self.spec = spec
        self._ctrlxdatalayer = ctrlxdatalayer
        self.metadata = MetadataBuilder.create_metadata(
            spec.name.split("/")[-1], spec.description, spec.unit, "",
            NodeClass.NodeClass.Variable,
            read_allowed=True, write_allowed=spec.writable,
            create_allowed=False, delete_allowed=False, browse_allowed=True,
            type_path="types/datalayer/" + spec.vtype)
        self._cbs = ProviderNodeCallbacks(
            self._on_create, self._on_remove, self._on_browse,
            self._on_read, self._on_write, self._on_metadata)
        self.provider_node = ProviderNode(self._cbs)

    def close(self):
        self.provider_node.close()
        self.metadata.close()

    # Variant helpers -------------------------------------------------------
    def _to_variant(self, value):
        from ctrlxdatalayer.variant import Variant
        variant = Variant()
        getattr(variant, "set_" + self.spec.vtype)(value)
        return variant

    def _from_variant(self, variant):
        from ctrlxdatalayer.variant import VariantType
        expected = getattr(VariantType, self.spec.vtype.upper())
        if variant.get_type() != expected:
            raise NodeWriteError("type", "expected " + self.spec.vtype)
        return getattr(variant, "get_" + self.spec.vtype)()

    # Callbacks: signature (userdata, address, [data,] cb). ALWAYS call cb exactly once.
    def _on_create(self, userdata, address, data, cb):
        from ctrlxdatalayer.variant import Result
        cb(Result.UNSUPPORTED, None)

    def _on_remove(self, userdata, address, cb):
        from ctrlxdatalayer.variant import Result
        cb(Result.UNSUPPORTED, None)

    def _on_browse(self, userdata, address, cb):
        from ctrlxdatalayer.variant import Result
        cb(Result.OK, None)

    def _on_read(self, userdata, address, data, cb):
        from ctrlxdatalayer.variant import Result
        try:
            with self._to_variant(self.registry.get(self.spec.name)) as variant:
                cb(Result.OK, variant)
        except Exception:
            log.exception("on_read %s", address)
            cb(Result.FAILED, None)

    def _on_write(self, userdata, address, data, cb):
        from ctrlxdatalayer.variant import Result
        try:
            if not self.spec.writable:  # report read-only before a type mismatch
                raise NodeWriteError("readonly", self.spec.name + " is read-only")
            value = self.registry.write(self.spec.name, self._from_variant(data))
            with self._to_variant(value) as variant:
                cb(Result.OK, variant)
        except NodeWriteError as exc:
            log.info("Rejected write to %s: %s", address, exc)
            cb({"readonly": Result.PERMISSION_DENIED, "type": Result.TYPE_MISMATCH,
                "range": Result.INVALID_VALUE, "invalid": Result.INVALID_VALUE,
                "unknown": Result.INVALID_ADDRESS}.get(exc.reason, Result.FAILED), None)
        except Exception:
            log.exception("on_write %s", address)
            cb(Result.FAILED, None)

    def _on_metadata(self, userdata, address, cb):
        from ctrlxdatalayer.variant import Result
        cb(Result.OK, self.metadata)


class _AmrTree:
    """<root>/fleet/amr and everything below it (per-AMR input modes): two wildcard-capable registrations
    served by amr_inputs.AmrInputs - registered only while such a mode is active."""

    def __init__(self, inputs, base):
        self.inputs, self.base = inputs, base
        self.nodes = []                   # [(address, _AmrTreeNode)]

    def sync(self, provider):
        """Register / unregister to match the input mode; True when something changed."""
        from ctrlxdatalayer.variant import Result
        want = self.inputs.per_amr()
        if want and not self.nodes:
            for address in (self.base, self.base + "/**"):
                node = _AmrTreeNode(self.inputs, self.base)
                res = provider.register_node(address, node.provider_node)
                if res != Result.OK:
                    log.error("register_node %s failed: %s", address, res)
                    node.close()
                    continue
                self.nodes.append((address, node))
            log.info("Per-AMR nodes %s/... registered (%s)", self.base, self.inputs.mode())
            return True
        if not want and self.nodes:
            self.unregister(provider)
            log.info("Per-AMR nodes %s/... unregistered", self.base)
            return True
        return False

    def unregister(self, provider):
        for address, node in self.nodes:
            provider.unregister_node(address)
            node.close()
        self.nodes = []


class _AmrTreeNode:
    """Browse / read / write / metadata for any address below <root>/fleet/amr (amr_inputs.py decides
    what exists). Metadata variants are built once per name and kind and kept until close()."""

    def __init__(self, inputs, base):
        from ctrlxdatalayer.provider_node import ProviderNode, ProviderNodeCallbacks

        self.inputs, self.base = inputs, base
        self._metadata = {}
        self._lock = threading.Lock()
        self._cbs = ProviderNodeCallbacks(
            self._on_create, self._on_remove, self._on_browse,
            self._on_read, self._on_write, self._on_metadata)
        self.provider_node = ProviderNode(self._cbs)

    def close(self):
        self.provider_node.close()
        with self._lock:
            for metadata in self._metadata.values():
                metadata.close()
            self._metadata = {}

    def _rel(self, address):
        address = address.strip("/")
        if address == self.base:
            return ""
        if address.startswith(self.base + "/"):
            return address[len(self.base) + 1:]
        return None

    def _meta(self, rel, kind):
        from comm.datalayer import NodeClass
        from ctrlxdatalayer.metadata_utils import MetadataBuilder
        name = rel.split("/")[-1] if rel else "amr"
        key = (name,) + kind
        with self._lock:
            metadata = self._metadata.get(key)
            if metadata is None:
                if kind[0] == "folder":
                    metadata = MetadataBuilder.create_metadata(
                        name, "AMRs of the AMR list (input mode fleet.input)" if not rel else "AMR " + name,
                        "", "", NodeClass.NodeClass.Folder, read_allowed=False, write_allowed=False,
                        create_allowed=False, delete_allowed=False, browse_allowed=True, type_path="")
                else:
                    metadata = MetadataBuilder.create_metadata(
                        name, kind[2], "", "", NodeClass.NodeClass.Variable, read_allowed=True, write_allowed=True,
                        create_allowed=False, delete_allowed=False, browse_allowed=True,
                        type_path="types/datalayer/" + kind[1])
                self._metadata[key] = metadata
            return metadata

    @staticmethod
    def _value(data):
        """A written Variant as str or number (a PLC may write REAL / INT: any number type works)."""
        from ctrlxdatalayer.variant import VariantType
        vtype = data.get_type()
        if vtype == VariantType.STRING:
            return data.get_string()
        if vtype in (VariantType.FLOAT64, VariantType.FLOAT32, VariantType.INT8, VariantType.UINT8,
                     VariantType.INT16, VariantType.UINT16, VariantType.INT32, VariantType.UINT32,
                     VariantType.INT64, VariantType.UINT64):
            return getattr(data, "get_" + vtype.name.lower())()
        raise NodeWriteError("type", "expected a number or a string")

    # Callbacks: signature (userdata, address, [data,] cb). ALWAYS call cb exactly once.
    def _on_create(self, userdata, address, data, cb):
        from ctrlxdatalayer.variant import Result
        cb(Result.UNSUPPORTED, None)

    def _on_remove(self, userdata, address, cb):
        from ctrlxdatalayer.variant import Result
        cb(Result.UNSUPPORTED, None)

    def _on_browse(self, userdata, address, cb):
        from ctrlxdatalayer.variant import Result, Variant
        try:
            rel = self._rel(address)
            names = None if rel is None else self.inputs.browse(rel)
            if names is None:
                cb(Result.INVALID_ADDRESS, None)
                return
            with Variant() as variant:
                variant.set_array_string(names)
                cb(Result.OK, variant)
        except Exception:
            log.exception("on_browse %s", address)
            cb(Result.FAILED, None)

    def _on_read(self, userdata, address, data, cb):
        from ctrlxdatalayer.variant import Result, Variant
        try:
            rel = self._rel(address)
            value = self.inputs.read(rel) if rel is not None else None
            with Variant() as variant:
                if isinstance(value, str):
                    variant.set_string(value)
                else:
                    variant.set_float64(value)
                cb(Result.OK, variant)
        except NodeWriteError:
            cb(Result.INVALID_ADDRESS, None)
        except Exception:
            log.exception("on_read %s", address)
            cb(Result.FAILED, None)

    def _on_write(self, userdata, address, data, cb):
        from ctrlxdatalayer.variant import Result
        try:
            rel = self._rel(address)
            if rel is None:
                raise NodeWriteError("unknown", "not below " + self.base)
            self.inputs.write(rel, self._value(data))
            cb(Result.OK, data)
        except NodeWriteError as exc:   # the reason is in fleet/last-error; 1000s of writes/s: no log line each
            log.debug("Rejected write to %s: %s", address, exc)
            cb({"type": Result.TYPE_MISMATCH, "invalid": Result.INVALID_VALUE,
                "unknown": Result.INVALID_ADDRESS}.get(exc.reason, Result.FAILED), None)
        except Exception:
            log.exception("on_write %s", address)
            cb(Result.FAILED, None)

    def _on_metadata(self, userdata, address, cb):
        from ctrlxdatalayer.variant import Result
        try:
            rel = self._rel(address)
            kind = self.inputs.describe(rel) if rel is not None else None
            if kind is None:
                cb(Result.INVALID_ADDRESS, None)
                return
            cb(Result.OK, self._meta(rel, kind))
        except Exception:
            log.exception("on_metadata %s", address)
            cb(Result.FAILED, None)
