"""Application entry point: wires settings, Data Layer provider and web server together.

Startup order matters on ctrlX OS:
  1. Settings defaults are loaded immediately (the app must work before app data is ready).
  2. The active-solution content interface may be connected AFTER the daemon starts,
     so we wait a bit for $SNAP_COMMON/solutions/activeConfiguration and keep polling.
  3. The Data Layer provider reconnects on its own.
  4. SIGTERM (snap stop/refresh) -> clean shutdown within the systemd stop timeout.
The controllers (controllers.py, one thread each) follow the settings; their Data Layer nodes (plc_nodes.py)
are served by the provider; the summary nodes are refreshed by the main loop.
"""

import logging
import os
import signal
import threading
import time

from app import ads_link, config, logsetup
from app.controllers import ControllerManager
from app.datalayer_service import DataLayerService
from app.plc_nodes import NodeMap
from app.settings_rules import RULES, validate_settings
from app.settings_store import SettingsError, SettingsStore
from app.web_server import WebServer

log = logging.getLogger("main")

STORAGE_WAIT_S = 30.0
POLL_INTERVAL_S = 2.0


def _wait_for_storage(store, stop):
    deadline = time.monotonic() + STORAGE_WAIT_S
    while not store.storage_available() and time.monotonic() < deadline and not stop.is_set():
        stop.wait(1.0)
    return store.storage_available()


def _load_settings(store, stop):
    """Right after an install the app data folder can be there but not writable for a few seconds
    (gotchas #82): retry instead of crashing."""
    deadline = time.monotonic() + STORAGE_WAIT_S
    while True:
        try:
            store.load_from_disk(create_if_missing=True)
            return
        except SettingsError as exc:
            log.error("settings.json is invalid, running with defaults until it is fixed: %s", exc)
            return
        except OSError as exc:
            if time.monotonic() > deadline or stop.is_set():
                log.error("App data not usable, running with defaults: %s", exc)
                return
            log.info("App data not writable yet (%s) - trying again", exc.strerror or exc)
            stop.wait(POLL_INTERVAL_S)


def publish_summary(registry, manager, nodes):
    """controllers/... and nodes/... of the app's Data Layer nodes."""
    summary = manager.summary()
    registry.set_internal("controllers/summary", summary)
    registry.set_internal("controllers/configured", len(summary))
    registry.set_internal("controllers/connected",
                          sum(1 for s in summary if s["state"] == "connected" and s["plcStateName"]))
    total, bad = nodes.counts()
    registry.set_internal("nodes/configured", total)
    registry.set_internal("nodes/bad", bad)


def main():
    logsetup.setup("INFO")
    stop = threading.Event()

    def _on_signal(signum, _frame):
        log.info("Signal %s received - stopping", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    log.info("%s %s starting (snap=%s)", config.SNAP_NAME, config.app_version(), config.IN_SNAP)

    if not config.IN_SNAP:
        # local development: emulate the Solutions mount
        os.makedirs(config.ACTIVE_CONFIGURATION_DIR, exist_ok=True)

    store = SettingsStore(config.SETTINGS_FILE, config.DEFAULT_SETTINGS_FILE,
                          config.ACTIVE_CONFIGURATION_DIR, RULES, validator=validate_settings)

    if _wait_for_storage(store, stop):
        _load_settings(store, stop)
    else:
        log.warning("App data storage not available after %.0fs - running with defaults "
                    "(is the active-solution plug connected?)", STORAGE_WAIT_S)

    settings = store.get()
    logsetup.set_level(settings["general"]["logLevel"])
    store.add_listener(lambda s: logsetup.set_level(s["general"]["logLevel"]))
    ads_link.set_adslib_log_level(settings["general"]["logLevel"])        # AdsLib's own stderr lines
    store.add_listener(lambda s: ads_link.set_adslib_log_level(s["general"]["logLevel"]))

    # the Data Layer node settings/active gets redacted settings (`secret` rules masked).
    # Command nodes that start something in the app: commands = datalayer_nodes.Commands(), pass it here
    # (DataLayerService(..., commands)), create the worker with datalayer.registry, then commands.bind(worker).
    # Reading/writing other nodes (PLC variables, system/info): DataLayerService(..., on_client=worker.attach),
    # see datalayer_client.py.
    plc_nodes = NodeMap()
    manager = ControllerManager(plc_nodes)
    datalayer = DataLayerService(store.get_redacted(), config.app_version(), plc_nodes=plc_nodes)
    store.add_listener(lambda _s: datalayer.on_settings_changed(store.get_redacted()))
    manager.apply(settings)
    store.add_listener(manager.apply)
    datalayer.start()

    web = WebServer(store, datalayer, manager)
    web.start()

    while not stop.wait(POLL_INTERVAL_S):
        store.poll_external_change()
        publish_summary(datalayer.registry, manager, plc_nodes)

    log.info("Shutting down")
    web.stop()
    manager.stop()
    datalayer.stop()
    log.info("Stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
