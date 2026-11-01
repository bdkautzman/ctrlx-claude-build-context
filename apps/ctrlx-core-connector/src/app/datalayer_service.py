"""ctrlX Data Layer provider (publishes the nodes of NodeRegistry) and client (for app/monitor.py).

API reference (exact signatures): reference/ctrlxdatalayer-api-3.6.0/*.py
Pattern taken from SDK 4.6 samples: datalayer.provider, datalayer.provider.all-data.

Lifecycle:
    System("") -> start(False) -> factory().create_provider(conn) -> provider.start()
    -> register_node(address, ProviderNode) for each node -> ... -> unregister_node()
    -> provider.stop()/close() -> system.stop(False)/close()
The thread reconnects automatically when the provider loses its connection
(e.g. Data Layer broker restart, app started before automationcore).
On the same connection it opens a client and hands a ClientAdapter to on_client(adapter)
(None when disconnected); the core monitor uses it to manage devices/remotes and read them,
the Data Layer browser (app/browser.py) and the KVD editor (app/kvd.py) to browse/read/write.

ctrlxdatalayer is imported lazily so the rest of the app (web UI, settings) can run on a
PC without the native libcomm_datalayer library.
"""

import json
import logging
import threading

from app import config, dltypes
from app.datalayer_nodes import NodeRegistry, NodeWriteError, build_node_specs
from app.monitor import DataLayerError, normalize_result, summary_json

log = logging.getLogger("datalayer")

RECONNECT_DELAY_S = 5.0


class DataLayerService:
    def __init__(self, settings, version, on_client=None):
        """settings: REDACTED settings (they end up in the Data Layer node settings/active)."""
        self.registry = NodeRegistry(config.DATALAYER_ROOT, build_node_specs(settings, version))
        self.registry.set_internal("settings/active", json.dumps(settings, separators=(",", ":")))
        self._heartbeat_s = settings["general"]["pollIntervalMs"] / 1000.0
        self._on_client = on_client or (lambda adapter: None)
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
        """SettingsStore listener - pass REDACTED settings."""
        self.registry.set_internal("settings/active", json.dumps(settings, separators=(",", ":")))
        self.registry.set_internal("cores/configured", len(settings.get("cores", [])) + 1)   # + this device
        self._heartbeat_s = settings["general"]["pollIntervalMs"] / 1000.0

    def publish_cores(self, snapshot, local=None):
        """CoreMonitor on_update callback: status of all cores into the Data Layer nodes. cores/summary lists
        the configured connections, cores/connected also counts this device (local = monitor.local_status())."""
        self.registry.set_internal("cores/summary", summary_json(snapshot))
        connected = [c for c in snapshot + [local or {}] if c.get("state") == "connected"]
        self.registry.set_internal("cores/connected", len(connected))

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

                client = system.factory().create_client(conn)
                adapter = ClientAdapter(system, client)
                self._on_client(adapter)
                try:
                    while not self._stop.is_set() and provider.is_connected():  # client errors: see monitor
                        self._stop.wait(self._heartbeat_s)
                        self._tick()
                finally:
                    self._on_client(None)
                    adapter.close()
                    client.close()

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



class ClientAdapter:
    """The Data Layer operations of app/monitor.py, app/browser.py and app/kvd.py on a ctrlxdatalayer
    Client. Single-address methods raise monitor.DataLayerError(<result name>); the *_many methods
    (one bulk request each) return a (result name, ...) tuple per address instead.
    One lock: the monitor thread and the web server threads share the client."""

    def __init__(self, system, client):
        self._system = system
        self._client = client
        self._lock = threading.RLock()
        self._schema = None   # metadata schema for the JSON converter, fetched once

    def close(self):
        with self._lock:
            if self._schema is not None:
                self._schema.close()
                self._schema = None

    def read(self, address):
        """Value of a node as Python object (flatbuffers objects via the JSON converter)."""
        from ctrlxdatalayer.variant import Result, VariantType
        with self._lock:
            result, data = self._client.read_sync(address)
            with data:
                if result != Result.OK:
                    raise DataLayerError(result.name)
                vtype = data.get_type()
                if vtype != VariantType.FLATBUFFERS:
                    getter = getattr(data, "get_" + vtype.name.lower(), None)
                    if getter is None:
                        raise DataLayerError("TYPE_MISMATCH", "unsupported type " + vtype.name)
                    return getter()
        value = self.read_json(address)
        # the converter wraps simple values like the REST API does: {"type": ..., "value": ...}
        if isinstance(value, dict) and set(value) >= {"type", "value"}:
            value = value["value"]
        return value

    def read_json(self, address):
        """Value as the JSON converter writes it: {"type": t, "value": v} for simple types,
        the plain object for flatbuffers (gotchas #79); None for a node without a value (a KVD
        collection reads OK with an empty string, gotchas #87)."""
        from ctrlxdatalayer.variant import Result
        with self._lock:
            result, data = self._client.read_json_sync(self._system.json_converter(), address, -1)
            with data:
                if result != Result.OK:
                    raise DataLayerError(result.name)
                text = data.get_string()
        return json.loads(text) if text and text.strip() else None

    def write_json(self, address, text):
        """Write a JSON value: '{"type": "int32", "value": 5}' or a flatbuffers object '{...}'."""
        from ctrlxdatalayer.variant import Result, VariantType
        with self._lock:
            result, error = self._client.write_json_sync(self._system.json_converter(), address, text)
            with error:
                detail = error.get_string() if error.get_type() == VariantType.STRING else ""
        if result != Result.OK:
            raise DataLayerError(result.name, detail or result.name)

    def browse(self, address):
        from ctrlxdatalayer.variant import Result
        with self._lock:
            result, data = self._client.browse_sync(address)
            with data:
                if result != Result.OK:
                    raise DataLayerError(result.name)
                return list(data.get_array_string() or [])

    def _metadata_json(self, data):
        """Metadata flatbuffers variant -> dict (converter + metadata schema). Call with the lock held."""
        from ctrlxdatalayer.converter import C_DLR_SCHEMA
        from ctrlxdatalayer.variant import Result
        conv = self._system.json_converter()
        if self._schema is None:
            result, schema = conv.get_schema(C_DLR_SCHEMA.METADATA)
            if result != Result.OK:
                schema.close()
                raise DataLayerError(result.name, "metadata schema")
            self._schema = schema
        result, js = conv.converter_generate_json_complex(data, self._schema, -1)
        with js:
            if result != Result.OK:
                raise DataLayerError(result.name, "metadata conversion")
            return json.loads(js.get_string())

    def metadata(self, address):
        from ctrlxdatalayer.variant import Result
        with self._lock:
            result, data = self._client.metadata_sync(address)
            with data:
                if result != Result.OK:
                    raise DataLayerError(result.name)
                return self._metadata_json(data)

    def _bulk(self, operation, request, convert, failed):
        """One bulk request; per address (result name,) + convert(data), or + failed when it failed."""
        from ctrlxdatalayer.bulk import Bulk
        from ctrlxdatalayer.variant import Result
        if not request:
            return []
        with self._lock, Bulk(self._client) as bulk:
            result = getattr(bulk, operation)(request)
            if result != Result.OK:
                return [(normalize_result(result.name),) + failed] * len(request)
            out = []
            for response in bulk.get_response():
                res = response.get_result()
                if res not in (Result.OK, Result.OK_NO_CONTENT):
                    out.append((normalize_result(res.name),) + failed)
                    continue
                try:
                    out.append(("OK",) + convert(response.get_data()))
                except DataLayerError as exc:
                    out.append((exc.result,) + failed)
            return out

    def metadata_many(self, addresses):
        return self._bulk("metadata", list(addresses), lambda d: (self._metadata_json(d),), (None,))

    def browse_many(self, addresses):
        return self._bulk("browse", list(addresses), lambda d: (list(d.get_array_string() or []),), (None,))

    def read_many(self, addresses):
        from ctrlxdatalayer.bulk import BulkReadRequest
        return self._bulk("read", [BulkReadRequest(a) for a in addresses], variant_value, (None, None))

    def create_remote(self, name, address):
        """Create devices/remotes/<name> (type types/datalayer/remoteconfig)."""
        import flatbuffers
        from comm.datalayer.remote import ConfigItem
        from ctrlxdatalayer.variant import Result, Variant
        builder = flatbuffers.Builder(256)
        b_name, b_address = builder.CreateString(name), builder.CreateString(address)
        ConfigItem.ConfigItemStart(builder)
        ConfigItem.ConfigItemAddName(builder, b_name)
        ConfigItem.ConfigItemAddAddress(builder, b_address)
        builder.Finish(ConfigItem.ConfigItemEnd(builder))
        with self._lock, Variant() as data:
            data.set_flatbuffers(builder.Output())
            result, _ = self._client.create_sync("devices/remotes", data)
        if result != Result.OK:
            raise DataLayerError(result.name)

    def remove(self, address):
        from ctrlxdatalayer.variant import Result
        with self._lock:
            result = self._client.remove_sync(address)
        if result != Result.OK:
            raise DataLayerError(result.name)


def variant_value(data):
    """(type name, JSON-able value) of a Variant; flatbuffers/raw: value None (read them as JSON)."""
    from ctrlxdatalayer.variant import VariantType
    vtype = data.get_type()
    name = dltypes.VARIANT_TYPES.get(vtype.name, "unknown")
    if vtype in (VariantType.FLATBUFFERS, VariantType.RAW, VariantType.UNKNON):
        return name, None
    if vtype == VariantType.TIMESTAMP:
        return name, data.get_datetime().isoformat()
    if vtype == VariantType.ARRAY_OF_TIMESTAMP:
        return name, [d.isoformat() for d in data.get_array_datetime()]
    getter = getattr(data, "get_" + vtype.name.lower(), None)
    return (name, getter()) if getter else ("unknown", None)
