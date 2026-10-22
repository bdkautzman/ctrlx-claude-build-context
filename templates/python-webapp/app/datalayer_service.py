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

from app import config
from app.datalayer_nodes import NodeRegistry, NodeWriteError, build_node_specs

log = logging.getLogger("datalayer")

RECONNECT_DELAY_S = 5.0


class DataLayerService:
    def __init__(self, settings, version):
        self.registry = NodeRegistry(config.DATALAYER_ROOT, build_node_specs(settings, version))
        self.registry.set_internal("settings/active", json.dumps(settings, separators=(",", ":")))
        self._heartbeat_s = settings["general"]["heartbeatIntervalMs"] / 1000.0
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
        self.registry.set_internal("values/label", settings["example"]["label"])
        self._heartbeat_s = settings["general"]["heartbeatIntervalMs"] / 1000.0

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

                while not self._stop.is_set() and provider.is_connected():
                    self._stop.wait(self._heartbeat_s)
                    self._tick()

                if not self._stop.is_set():
                    log.warning("Data Layer provider disconnected - reconnecting")
                self._set_status(connected=False, registered=0)
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

