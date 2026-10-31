"""One-time import of the settings of the app's predecessor ctrlx-core-monitor (Core Monitor 0.1.x).

Core Connector is the same app under a new snap name, so its app data folder is new. On the first
start (no ctrlx-core-connector/settings.json yet) the core connections and general settings are
taken from ctrlx-core-monitor/settings.json of the active configuration, if that exists. The
remotes it created (devices/remotes/cm-*) are taken over because the prefix stayed the same.
"""

import json
import logging
import os

from app.settings_store import SettingsError

log = logging.getLogger("migrate")


def import_legacy_settings(store, legacy_file):
    """Returns True when settings were imported. Call before store.load_from_disk().
    Raises OSError when the app data can't be read/written yet (the caller tries again later)."""
    if not store.storage_available() or os.path.isfile(store.status()["file"]) or not os.path.isfile(legacy_file):
        return False
    try:
        with open(legacy_file, "r", encoding="utf-8") as f:
            candidate = json.load(f)
        store.update(candidate)   # validated like every change, written to our own app data
    except (ValueError, SettingsError) as exc:
        log.warning("Core Monitor settings not imported (%s): %s", legacy_file, exc)
        return False
    log.info("Imported %d core connection(s) from %s", len(store.get()["cores"]), legacy_file)
    return True
