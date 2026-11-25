"""ctrlX Data Layer provider: publishes the nodes of NodeRegistry.

API reference (exact signatures): reference/ctrlxdatalayer-api-3.6.0/*.py
Pattern taken from SDK 4.6 samples: datalayer.provider, datalayer.provider.all-data.

Lifecycle:
    System("") -> start(False) -> factory().create_provider(conn) -> provider.start()
    -> register_node(address, ProviderNode) for each node -> ... -> unregister_node()
    -> provider.stop()/close() -> system.stop(False)/close()
The thread reconnects automatically when the provider loses its connection
(e.g. Data Layer broker restart, app started before automationcore).
Reading/writing OTHER nodes: pass on_client - the thread opens a client on the same connection and hands a
datalayer_client.DataLayerClient to on_client(client), on_client(None) when it is gone (datalayer_client.py).
PLC variables (plc_nodes.NodeMap): <root>/connections and <root>/connections/** - two registrations of one wildcard-capable
provider node (gotchas #88) whose callbacks ask the NodeMap; registered for as long as the provider runs.

ctrlxdatalayer is imported lazily so the rest of the app (web UI, settings) can run on a
PC without the native libcomm_datalayer library.
"""

import logging
import threading
import time

from app import config
from app.datalayer_nodes import ARRAY_OF, NodeRegistry, NodeWriteError, build_node_specs
from app.plc_nodes import BASE as PLC_BASE
from app.plc_nodes import NodeUnavailable

log = logging.getLogger("datalayer")

RECONNECT_DELAY_S = 5.0
DOWN_GRACE_S = 60.0      # no Data Layer for this long is expected (first start, SERVICE <-> OPERATING): INFO, not ERROR
DOWN_REPEAT_S = 600.0    # a longer outage: ERROR again every 10 min, not at every retry


class DownTimeLog:
    """Log lines while the provider can't start or lost its connection. Right after the app starts (the content
    interfaces are connected after the daemon starts) and at every SERVICE <-> OPERATING switch the Data Layer is
    gone for a few seconds (gotchas #28): INFO then - every kit app wrote an ERROR into the Logbook at every boot.
    ERROR only when it lasts longer than grace_s, then again every repeat_s."""

    def __init__(self, grace_s=DOWN_GRACE_S, repeat_s=DOWN_REPEAT_S, clock=time.monotonic):
        self.grace_s, self.repeat_s, self.clock = grace_s, repeat_s, clock
        self.since = clock()          # the app starts without a connection
        self.last_error = None

    def down(self):
        """The connection was lost."""
        if self.since is None:
            self.since = self.clock()

    def failed(self, text):
        """The provider could not start (it retries)."""
        now = self.clock()
        if self.since is None:
            self.since = now
        lasting = now - self.since
        if lasting < self.grace_s:
            log.info("%s - waiting for the Data Layer, retry in %.0f s", text, RECONNECT_DELAY_S)
        elif self.last_error is None or now - self.last_error >= self.repeat_s:
            self.last_error = now
            log.error("%s - no Data Layer for %.0f s, still retrying", text, lasting)
        else:
            log.debug(text)

    def up(self):
        """Connected (again)."""
        if self.since is not None and self.last_error is not None:
            log.info("Data Layer connected again after %.0f s", self.clock() - self.since)
        self.since, self.last_error = None, None


class DataLayerService:
    def __init__(self, settings, version, commands=None, on_client=None, plc_nodes=None):
        """commands: the app's actions behind command nodes (datalayer_nodes.Commands), passed to build_node_specs.
        on_client: called with a DataLayerClient while connected and with None when not (no client without it).
        plc_nodes: the NodeMap of the PLC variables (connections/...)."""
        self.registry = NodeRegistry(config.DATALAYER_ROOT, build_node_specs(settings, version, commands))
        self._on_client = on_client
        self._plc_nodes = plc_nodes
        self.registry.set_internal("settings/active", settings)   # a JSON node (json=True)
        self._heartbeat_s = settings["general"]["heartbeatIntervalMs"] / 1000.0
        self._stop = threading.Event()
        self._thread = None
        self._status_lock = threading.Lock()
        self._status = {"enabled": False, "connected": False, "error": None, "registered": 0}
        self._downtime = DownTimeLog()

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
        self.registry.set_internal("settings/active", settings)
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
                    self._downtime.failed("Data Layer provider start failed: {} ({})".format(result, conn.split("@")[-1]))
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
                tree = []
                if self._plc_nodes is not None:
                    base = self.registry.address(PLC_BASE)
                    for address in (base, base + "/**"):
                        node = _PlcTreeNode(self._plc_nodes, base)
                        res = provider.register_node(address, node.provider_node)
                        if res != Result.OK:
                            log.error("register_node %s failed: %s", address, res)
                            node.close()
                            continue
                        tree.append((address, node))
                self.registry.set_internal("status/state", "running")
                self._set_status(connected=True, error=None, registered=len(nodes) + len(tree))
                self._downtime.up()
                log.info("Data Layer provider running, %d nodes under %s/", len(nodes), self.registry.root)

                client = dl_client = None
                if self._on_client is not None:
                    from app.datalayer_client import DataLayerClient
                    client = system.factory().create_client(conn)
                    dl_client = DataLayerClient(system, client)
                    self._on_client(dl_client)
                try:
                    while not self._stop.is_set() and provider.is_connected():
                        self._stop.wait(self._heartbeat_s)
                        self._tick()
                finally:
                    if dl_client is not None:
                        self._on_client(None)
                        dl_client.close()
                        client.close()

                if not self._stop.is_set():
                    log.info("Data Layer provider disconnected - reconnecting")   # e.g. SERVICE <-> OPERATING (#28)
                    self._downtime.down()
                self._set_status(connected=False, registered=0)
                for address, node in tree:
                    provider.unregister_node(address)
                    node.close()
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
    # vtype -> Variant method / VariantType: "float32" -> set_float32 / FLOAT32, "array-of-int16" -> set_array_int16 /
    # ARRAY_INT16. The writer's type must be exactly the node's: the get_* methods would convert ("auto convert if
    # possible", e.g. a float64 into an int8), so the type is checked first and nothing is converted.
    def _variant_name(self):
        return self.spec.vtype.replace(ARRAY_OF, "array_")

    def _to_variant(self, value):
        from ctrlxdatalayer.variant import Result, Variant
        variant = Variant()
        result = getattr(variant, "set_" + self._variant_name())(value)
        if result != Result.OK:
            variant.close()
            raise RuntimeError("Variant.set_{} failed: {}".format(self._variant_name(), result))
        return variant

    def _from_variant(self, variant):
        from ctrlxdatalayer.variant import VariantType
        expected = getattr(VariantType, self._variant_name().upper())
        if variant.get_type() != expected:
            raise NodeWriteError("type", "expected " + self.spec.vtype)
        return getattr(variant, "get_" + self._variant_name())()

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



def _variant_name(vtype):
    """"float32" -> "float32", "array-of-int16" -> "array_int16" (Variant set_/get_ method, VariantType name)."""
    return vtype.replace(ARRAY_OF, "array_")


class _PlcTreeNode:
    """Browse / read / write / metadata for <root>/plc and every address below it (plc_nodes.NodeMap decides what
    exists). Metadata variants are built once per address and kind and kept until close()."""

    def __init__(self, nodes, base):
        from ctrlxdatalayer.provider_node import ProviderNode, ProviderNodeCallbacks

        self.nodes, self.base = nodes, base
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
        key = (rel,) + kind
        with self._lock:
            metadata = self._metadata.get(key)
            if metadata is None:
                name = rel.split("/")[-1] if rel else PLC_BASE
                if kind[0] == "folder":
                    description = "PLC variables of the TwinCAT controllers" if not rel else ""
                    metadata = MetadataBuilder.create_metadata(
                        name, description, "", "", NodeClass.NodeClass.Folder, read_allowed=False,
                        write_allowed=False, create_allowed=False, delete_allowed=False, browse_allowed=True,
                        type_path="")
                else:
                    _, vtype, writable, description, unit = kind
                    metadata = MetadataBuilder.create_metadata(
                        name, description, unit, "", NodeClass.NodeClass.Variable, read_allowed=True,
                        write_allowed=writable, create_allowed=False, delete_allowed=False, browse_allowed=True,
                        type_path="types/datalayer/" + vtype)
                if len(self._metadata) > 5000:      # nodes added and removed many times: start over
                    for old in self._metadata.values():
                        old.close()
                    self._metadata = {}
                self._metadata[key] = metadata
            return metadata

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
            names = None if rel is None else self.nodes.browse(rel)
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
            if rel is None:
                cb(Result.INVALID_ADDRESS, None)
                return
            vtype, value = self.nodes.read(rel)
            with Variant() as variant:
                result = getattr(variant, "set_" + _variant_name(vtype))(value)
                if result != Result.OK:
                    log.warning("on_read %s: Variant.set_%s failed: %s", address, _variant_name(vtype), result)
                    cb(Result.FAILED, None)
                    return
                cb(Result.OK, variant)
        except NodeWriteError:
            cb(Result.INVALID_ADDRESS, None)
        except NodeUnavailable as exc:
            cb(Result.COMMUNICATION_ERROR if exc.reason == "offline" else Result.RESOURCE_UNAVAILABLE, None)
        except Exception:
            log.exception("on_read %s", address)
            cb(Result.FAILED, None)

    def _on_write(self, userdata, address, data, cb):
        from ctrlxdatalayer.variant import Result, VariantType
        from app.ads_link import AdsError
        from app.ads_types import AdsTypeError
        from app.controllers import ControllerUnavailable
        try:
            rel = self._rel(address)
            kind = self.nodes.describe(rel) if rel is not None else None
            if kind is None or kind[0] != "variable":
                raise NodeWriteError("unknown", "no variable " + address)
            if not kind[2]:      # read-only is reported before a type mismatch (gotchas #43)
                raise NodeWriteError("readonly", address + " is read-only")
            name = _variant_name(kind[1])
            if data.get_type() != getattr(VariantType, name.upper()):     # exactly the node's type (#111)
                raise NodeWriteError("type", "expected " + kind[1])
            value = self.nodes.write(rel, getattr(data, "get_" + name)())
            with _variant_of(name, value) as variant:
                cb(Result.OK, variant)
        except NodeWriteError as exc:
            log.info("Rejected write to %s: %s", address, exc)
            cb({"readonly": Result.PERMISSION_DENIED, "type": Result.TYPE_MISMATCH,
                "range": Result.INVALID_VALUE, "invalid": Result.INVALID_VALUE,
                "unknown": Result.INVALID_ADDRESS}.get(exc.reason, Result.FAILED), None)
        except AdsTypeError as exc:
            log.info("Rejected write to %s: %s", address, exc)
            cb(Result.INVALID_VALUE, None)
        except (NodeUnavailable, ControllerUnavailable) as exc:
            log.info("Write to %s not possible: %s", address, exc)
            cb(Result.COMMUNICATION_ERROR, None)
        except AdsError as exc:
            log.warning("Write to %s failed in the PLC: %s", address, exc)
            cb(Result.TIMEOUT if exc.code == 0x745 else Result.COMMUNICATION_ERROR if exc.transport
               else Result.PERMISSION_DENIED if exc.code == 0x704 else Result.FAILED, None)
        except Exception:
            log.exception("on_write %s", address)
            cb(Result.FAILED, None)

    def _on_metadata(self, userdata, address, cb):
        from ctrlxdatalayer.variant import Result
        try:
            rel = self._rel(address)
            kind = self.nodes.describe(rel) if rel is not None else None
            if kind is None:
                cb(Result.INVALID_ADDRESS, None)
                return
            cb(Result.OK, self._meta(rel, kind))
        except Exception:
            log.exception("on_metadata %s", address)
            cb(Result.FAILED, None)


def _variant_of(name, value):
    """A Variant holding value (set_<name>), for the write answer."""
    from ctrlxdatalayer.variant import Result, Variant
    variant = Variant()
    if getattr(variant, "set_" + name)(value) != Result.OK:
        variant.close()
        raise RuntimeError("Variant.set_{} failed".format(name))
    return variant
