"""Application entry point: wires settings, Data Layer provider and web server together.

Startup order matters on ctrlX OS:
  1. Settings defaults are loaded immediately (the app must work before app data is ready).
  2. The active-solution content interface may be connected AFTER the daemon starts,
     so we wait a bit for $SNAP_COMMON/solutions/activeConfiguration and keep polling.
  3. The Data Layer provider reconnects on its own.
  4. SIGTERM (snap stop/refresh) -> clean shutdown within the systemd stop timeout.
  5. The plugs system-observe and removable-media are connected by the ctrlX installer a few
     seconds AFTER the first start (the service is restarted then, gotchas #67): nothing here
     caches "no access" - every sample simply reads what it can.
"""

import json
import logging
import os
import signal
import threading
import time

from app import config, grouping, logsetup
from app.collector import Collector, EventLog
from app.datalayer_service import DataLayerService
from app.history import History
from app.medium import MediumRecorder
from app.queries import Queries
from app.settings_rules import RULES
from app.settings_store import SettingsError, SettingsStore
from app.web_server import WebServer

log = logging.getLogger("main")

STORAGE_WAIT_S = 30.0
POLL_INTERVAL_S = 2.0
GROWING_EVERY_S = 60.0


class Services:
    """Everything the web server and the Data Layer nodes need, built from the settings."""

    def __init__(self, store, proc_root=config.PROC_ROOT, media_root=config.MEDIA_ROOT):
        s = store.get()
        h = s["history"]
        self.history = History(h["rawMinutes"], h["minuteHours"], h["tenMinuteDays"],
                               s["collector"]["intervalSeconds"], h["maxMemoryMb"])
        self.events = EventLog()
        self.collector = Collector(self.history, self.events, proc_root)
        self.recorder = MediumRecorder(media_root, config.MEDIUM_FOLDER, self.collector.describe,
                                       config.MEDIA_REQUIRE_MOUNT)
        self.history.listeners.append(self.recorder.on_minute)
        self.events.listeners.append(self.recorder.on_event)
        self.queries = Queries(self.history, self.collector, self.events, self.recorder, store)
        self.apply(s)

    def apply(self, settings):
        """SettingsStore listener: intervals, history sizes and the medium apply at once."""
        h = settings["history"]
        self.history.configure(h["rawMinutes"], h["minuteHours"], h["tenMinuteDays"],
                               settings["collector"]["intervalSeconds"], h["maxMemoryMb"])
        self.collector.configure(settings)
        self.recorder.configure(settings)

    def growing(self, window_minutes):
        """Data Layer node analysis/growing: apps flagged "growing" over the last window."""
        now = time.time()
        result = self.queries.analysis(now - window_minutes * 60, now, "apps")
        return [{"key": r["app"], "name": r["name"], "growthMbPerHour": r["growthMbPerHour"], "fit": r["fit"],
                 "hoursUntilFull": r["hoursUntilFull"]} for r in result["results"] if r["verdict"] == "growing"]


def _update_nodes(registry, snap, services):
    registry.set_internal("collector/samples", services.collector.status()["samples"])
    registry.set_internal("collector/sample-cpu-ms", float(services.collector.status()["lastCpuMs"]))
    registry.set_internal("system/cpu-percent", float(snap["system"]["cpu"] or 0.0))
    summary = [{"key": g["key"], "name": g["name"], "cpu": g["cpu"], "anonMb": g["anonMb"], "rssMb": g["rssMb"],
                "pssMb": g["pssMb"], "threads": g["threads"], "processes": g["processes"]}
               for g in sorted(snap["groups"].values(), key=lambda g: g["key"])]
    registry.set_internal("apps/summary", json.dumps(summary, separators=(",", ":")))
    registry.set_internal("recording/state", services.recorder.status()["state"])


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

    services = Services(store)
    store.add_listener(services.apply)

    # the Data Layer node settings/active gets redacted settings (`secret` rules masked)
    datalayer = DataLayerService(store.get_redacted(), config.app_version(), commands=services.queries)
    store.add_listener(lambda _s: datalayer.on_settings_changed(store.get_redacted()))
    services.collector.on_sample.append(lambda snap: _update_nodes(datalayer.registry, snap, services))
    datalayer.start()

    # tied to the analyzer's own app: the leak fit of its row starts anew after it (RESTART_TYPES)
    services.events.add(time.time(), "analyzer", "System Analyzer {} started".format(config.app_version()),
                        grouping.Group("snap:" + config.SNAP_NAME, config.SNAP_NAME, "snap", "service",
                                       config.SNAP_NAME, True), "service")
    services.recorder.start()
    services.collector.start()

    web = WebServer(store, datalayer, services)
    web.start()

    next_growing = time.monotonic() + GROWING_EVERY_S
    while not stop.wait(POLL_INTERVAL_S):
        store.poll_external_change()
        if time.monotonic() >= next_growing:
            next_growing = time.monotonic() + GROWING_EVERY_S
            try:
                growing = services.growing(store.get()["leak"]["nodeWindowMinutes"])
                datalayer.registry.set_internal("analysis/growing", json.dumps(growing, separators=(",", ":")))
            except Exception:
                log.exception("leak analysis for the Data Layer failed")

    log.info("Shutting down")
    services.collector.stop()
    services.recorder.stop()
    web.stop()
    datalayer.stop()
    log.info("Stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
