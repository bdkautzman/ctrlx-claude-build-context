"""Application entry point: wires settings, map, fleet state, simulation, Data Layer provider
and web server together.

Data flow: Data Layer write to <snap>/fleet/amrs (or the simulation) -> FleetState.receive
(validates) -> web UI polls GET /api/v1/fleet. The map file comes from MapSource (polled
every 2 s, reloaded on change) and reaches the UI as PNG tiles (GET /api/v1/map/tiles/...).

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
from app.fleet import FleetError, FleetState
from app.map_source import MapSource
from app.settings_rules import RULES
from app.settings_store import SettingsError, SettingsStore
from app.simulator import Simulator
from app.web_server import WebServer

log = logging.getLogger("main")

STORAGE_WAIT_S = 30.0
POLL_INTERVAL_S = 2.0


def _wait_for_storage(store, stop):
    deadline = time.monotonic() + STORAGE_WAIT_S
    while not store.storage_available() and time.monotonic() < deadline and not stop.is_set():
        stop.wait(1.0)
    return store.storage_available()


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
                          config.ACTIVE_CONFIGURATION_DIR, RULES)

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

    fleet = FleetState(settings["fleet"]["rotationUnit"])
    maps = MapSource(config.MAP_SEARCH_DIRS)

    # the Data Layer node settings/active gets redacted settings (`secret` rules masked)
    datalayer = DataLayerService(store.get_redacted(), config.app_version(), on_fleet_write=fleet.receive)
    store.add_listener(lambda _s: datalayer.on_settings_changed(store.get_redacted()))
    fleet.add_listener(datalayer.on_fleet_changed)
    maps.add_listener(datalayer.on_map_changed)
    maps.configure(settings["map"]["file"], settings["map"]["resolution"])
    datalayer.start()

    def publish_simulated(text):
        try:
            fleet.receive(text, source="simulation")
        except FleetError as exc:
            log.error("Simulation produced invalid fleet JSON: %s", exc)
            return
        datalayer.registry.set_internal("fleet/amrs", text)

    simulator = Simulator(maps, publish_simulated)
    simulator.configure(settings)
    simulator.start()

    def apply_settings(s):
        fleet.configure(s["fleet"]["rotationUnit"])
        maps.configure(s["map"]["file"], s["map"]["resolution"])
        simulator.configure(s)
    store.add_listener(apply_settings)

    web = WebServer(store, datalayer, fleet, maps)
    web.start()

    while not stop.wait(POLL_INTERVAL_S):
        store.poll_external_change()
        maps.poll()

    log.info("Shutting down")
    web.stop()
    simulator.stop()
    datalayer.stop()
    log.info("Stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
