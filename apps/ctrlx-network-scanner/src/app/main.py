"""Application entry point: wires settings, Data Layer provider and web server together.

Startup order matters on ctrlX OS:
  1. Settings defaults are loaded immediately (the app must work before app data is ready).
  2. The active-solution content interface may be connected AFTER the daemon starts,
     so we wait a bit for $SNAP_COMMON/solutions/activeConfiguration and keep polling.
  3. The Data Layer provider reconnects on its own.
  4. SIGTERM (snap stop/refresh) -> clean shutdown within the systemd stop timeout.
"""

import logging
import os
import signal
import threading
import time

from app import config, logsetup
from app.datalayer_service import DataLayerService
from app.history import History
from app.scan_manager import ScanManager
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


class DataLayerCommands:
    """scan/start and scan/abort node writes -> scan manager (set once the manager exists)."""
    manager = None

    def start(self, profile_id):
        return self.manager.start(profile_id, trigger="datalayer")

    def abort(self):
        return self.manager.abort()


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
        try:
            store.load_from_disk(create_if_missing=True)
        except SettingsError as exc:
            log.error("settings.json is invalid, running with defaults until it is fixed: %s", exc)
    else:
        log.warning("App data storage not available after %.0fs - running with defaults "
                    "(is the active-solution plug connected?)", STORAGE_WAIT_S)

    settings = store.get()
    logsetup.set_level(settings["general"]["logLevel"])
    store.add_listener(lambda s: logsetup.set_level(s["general"]["logLevel"]))

    # the Data Layer node settings/active gets redacted settings (`secret` rules masked)
    commands = DataLayerCommands()
    datalayer = DataLayerService(store.get_redacted(), config.app_version(), commands)
    store.add_listener(lambda _s: datalayer.on_settings_changed(store.get_redacted()))

    history = History(config.HISTORY_DIR)
    manager = ScanManager(store, history, registry=datalayer.registry)
    commands.manager = manager
    manager.load()
    store.add_listener(manager.on_settings_changed)
    datalayer.start()

    web = WebServer(store, datalayer, manager)
    web.start()

    while not stop.wait(POLL_INTERVAL_S):
        store.poll_external_change()
        manager.tick()

    log.info("Shutting down")
    if manager.abort():
        manager.wait(10.0)   # the aborted scan is stored before the app ends
    web.stop()
    datalayer.stop()
    log.info("Stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
