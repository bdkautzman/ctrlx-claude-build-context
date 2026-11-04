"""Application entry point: wires settings, Data Layer provider and web server together.

Startup order matters on ctrlX OS:
  1. Settings defaults are loaded immediately (the app must work before app data is ready).
  2. The active-solution content interface may be connected AFTER the daemon starts,
     so we wait a bit for $SNAP_COMMON/solutions/activeConfiguration and keep polling.
  3. The Data Layer provider reconnects on its own.
  4. SIGTERM (snap stop/refresh) -> clean shutdown within the systemd stop timeout.
"""

import importlib
import logging
import os
import signal
import sys
import threading
import time

from app import config, logsetup
from app.datalayer_service import DataLayerService
from app.reader import Reader
from app.settings_rules import RULES, validate_settings
from app.settings_store import SettingsError, SettingsStore
from app.simulator import Simulator
from app.storage import MediumWriter
from app.web_server import WebServer

log = logging.getLogger("main")

STORAGE_WAIT_S = 30.0
POLL_INTERVAL_S = 2.0


class ReaderCommands:
    """Hands Data Layer writes to the reader, which is created after the Data Layer registry."""

    reader = None

    def can_queue(self):
        return self.reader is not None and self.reader.can_queue()

    def request_read(self, source, request_id=0):
        return self.reader.request_read(source, request_id)

    def set_continuous(self, enabled):
        self.reader.set_continuous(enabled)


def _wait_for_storage(store, stop):
    deadline = time.monotonic() + STORAGE_WAIT_S
    while not store.storage_available() and time.monotonic() < deadline and not stop.is_set():
        stop.wait(1.0)
    return store.storage_available()


def _check_numpy():
    """numpy that can't run on this CPU raises RuntimeError on import, and flatbuffers (used by ctrlxdatalayer)
    only catches ImportError (gotchas #98): block numpy then, so the Data Layer still works and every read
    reports the error. Returns the error text or None."""
    try:
        importlib.import_module("numpy")
    except ImportError:
        return None
    except Exception as exc:  # noqa: BLE001 - RuntimeError for a CPU without the wheel's baseline
        log.error("numpy can't run on this device - image processing is off: %s", exc)
        sys.modules["numpy"] = None      # `import numpy` now raises ImportError
        return str(exc)
    return None


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

    # the Data Layer node settings/active gets redacted settings (`secret` rules masked)
    _check_numpy()
    commands = ReaderCommands()
    datalayer = DataLayerService(store.get_redacted(), config.app_version(), commands)
    store.add_listener(lambda _s: datalayer.on_settings_changed(store.get_redacted()))

    medium = MediumWriter(config.MEDIA_ROOT, config.SNAP_NAME, require_mount=config.MEDIA_REQUIRE_MOUNT)
    medium.configure(settings["storage"])
    medium.start()
    reader = Reader(store, registry=datalayer.registry, storage=medium,
                    simulator=Simulator(config.TEST_IMAGES_DIR))
    store.add_listener(reader.on_settings)
    commands.reader = reader
    reader.start()
    datalayer.start()

    web = WebServer(store, datalayer, reader)
    web.start()

    while not stop.wait(POLL_INTERVAL_S):
        store.poll_external_change()

    log.info("Shutting down")
    web.stop()
    datalayer.stop()
    reader.stop()
    medium.stop()
    log.info("Stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
