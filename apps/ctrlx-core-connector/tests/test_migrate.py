"""Import of the settings of the predecessor app ctrlx-core-monitor (no ctrlX needed)."""

import json
import os
import tempfile
import unittest
from unittest import mock

from app.migrate import import_legacy_settings
from app.settings_rules import RULES
from app.settings_store import SettingsStore

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULTS_FILE = os.path.join(HERE, "..", "defaults", "settings.json")
LEGACY = {"settingsVersion": 1, "general": {"logLevel": "DEBUG", "pollIntervalMs": 3000},
          "cores": [{"name": "real-core", "host": "192.168.1.7", "user": "boschrexroth", "password": "pw",
                     "sslPort": 443, "skipCertificateCheck": True}]}


class MigrateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.active = os.path.join(self.tmp.name, "activeConfiguration")
        os.makedirs(os.path.join(self.active, "ctrlx-core-monitor"))
        self.legacy = os.path.join(self.active, "ctrlx-core-monitor", "settings.json")
        self.own = os.path.join(self.active, "ctrlx-core-connector", "settings.json")
        self.store = SettingsStore(self.own, DEFAULTS_FILE, self.active, RULES)

    def tearDown(self):
        self.tmp.cleanup()

    def write_legacy(self, data):
        with open(self.legacy, "w", encoding="utf-8") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def test_imported_once(self):
        self.write_legacy(LEGACY)
        self.assertTrue(import_legacy_settings(self.store, self.legacy))

    def test_main_retries_instead_of_crashing(self):
        from app import main
        with mock.patch.object(self.store, "load_from_disk", side_effect=PermissionError(13, "Permission denied")):
            self.assertFalse(main._load_settings(self.store))
        self.assertTrue(main._load_settings(self.store))
        with open(self.own, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual((saved["cores"][0]["name"], saved["cores"][0]["password"], saved["general"]["pollIntervalMs"]),
                         ("real-core", "pw", 3000))
        self.assertFalse(import_legacy_settings(self.store, self.legacy))   # own settings exist now
        self.assertTrue(self.store.load_from_disk(create_if_missing=True))
        self.assertEqual(self.store.get()["cores"][0]["host"], "192.168.1.7")

    def test_nothing_to_import_or_invalid(self):
        self.assertFalse(import_legacy_settings(self.store, self.legacy))   # no Core Monitor
        self.write_legacy("{not json")
        self.assertFalse(import_legacy_settings(self.store, self.legacy))
        self.write_legacy(dict(LEGACY, cores=[{"name": "Bad Name"}]))
        self.assertFalse(import_legacy_settings(self.store, self.legacy))
        self.assertFalse(os.path.exists(self.own))

    def test_unreadable_app_data_is_retried_later(self):
        # first start after the install: folder visible, access denied (gotchas #82) - must not be "nothing to import"
        self.write_legacy(LEGACY)
        os.chmod(self.legacy, 0)
        try:
            if os.access(self.legacy, os.R_OK):
                self.skipTest("running as root: chmod does not block reading")
            with self.assertRaises(OSError):
                import_legacy_settings(self.store, self.legacy)
        finally:
            os.chmod(self.legacy, 0o600)
        self.assertTrue(import_legacy_settings(self.store, self.legacy))

    def test_main_retries_instead_of_crashing(self):
        from app import main
        with mock.patch.object(self.store, "load_from_disk", side_effect=PermissionError(13, "Permission denied")):
            self.assertFalse(main._load_settings(self.store))
        self.assertTrue(main._load_settings(self.store))


if __name__ == "__main__":
    unittest.main()
