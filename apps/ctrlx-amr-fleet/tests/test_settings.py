"""Unit tests that run on any PC (no ctrlX, no ctrlxdatalayer):  python3 -m unittest discover -s tests"""

import json
import os
import tempfile
import time
import unittest
from unittest import mock

from app import appdata_commands
from app.settings_rules import RULES
from app.settings_store import (LOAD_QUERY_TIMEOUT_S, SECRET_MASK, SettingsError, SettingsStore, redact,
                                restore_secrets, validate)

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULTS_FILE = os.path.join(HERE, "..", "defaults", "settings.json")


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def load_defaults():
    with open(DEFAULTS_FILE, encoding="utf-8") as f:
        return json.load(f)


class ValidateTest(unittest.TestCase):
    def test_defaults_are_valid(self):
        defaults = load_defaults()
        normalized, errors = validate(defaults, defaults, RULES)
        self.assertEqual(errors, [])

    def test_every_rule_points_to_an_existing_setting(self):
        defaults = load_defaults()
        for dotted in RULES:
            node = defaults
            for part in dotted.split("."):
                self.assertIn(part, node, "rule {} has no default".format(dotted))
                node = node[part]

    def test_missing_keys_filled_unknown_kept(self):
        defaults = load_defaults()
        normalized, errors = validate({"general": {"logLevel": "DEBUG"}, "future": 1}, defaults, RULES)
        self.assertEqual(errors, [])
        self.assertEqual(normalized["general"]["logLevel"], "DEBUG")
        self.assertEqual(normalized["general"]["heartbeatIntervalMs"], defaults["general"]["heartbeatIntervalMs"])
        self.assertEqual(normalized["future"], 1)

    def test_type_and_rule_errors(self):
        defaults = load_defaults()
        _, errors = validate({"general": {"logLevel": "LOUD", "heartbeatIntervalMs": True}}, defaults, RULES)
        self.assertTrue(any(e.startswith("general.logLevel") for e in errors))
        self.assertTrue(any(e.startswith("general.heartbeatIntervalMs") for e in errors))

    def test_int_accepted_for_float(self):
        defaults = load_defaults()
        normalized, errors = validate({"simulation": {"speedMps": 3}}, defaults, RULES)
        self.assertEqual(errors, [])
        self.assertIsInstance(normalized["simulation"]["speedMps"], float)

    def test_path_history_minutes(self):
        defaults = load_defaults()
        self.assertEqual(defaults["fleet"]["pathHistoryMinutes"], 5)
        normalized, errors = validate({"fleet": {"pathHistoryMinutes": 0}}, defaults, RULES)
        self.assertEqual((errors, normalized["fleet"]["pathHistoryMinutes"]), ([], 0))   # 0 = off
        for bad in (-1, 61, 2.5, "5"):
            _, errors = validate({"fleet": {"pathHistoryMinutes": bad}}, defaults, RULES)
            self.assertTrue(any(e.startswith("fleet.pathHistoryMinutes") for e in errors), bad)

    def test_root_must_be_object(self):
        _, errors = validate([1, 2], load_defaults(), RULES)
        self.assertTrue(errors)


class ItemsPatternSecretRulesTest(unittest.TestCase):
    """Generic rule keys (used e.g. by apps/ctrlx-core-monitor for its list of connections)."""
    DEFAULTS = {"token": "", "peers": []}
    RULES = {
        "token": {"secret": True},
        "peers": {"maxItems": 2, "unique": "name", "items": {
            "template": {"name": "", "port": 443, "password": ""},
            "rules": {"name": {"pattern": r"[a-z]+"}, "port": {"min": 1, "max": 65535},
                      "password": {"secret": True, "minLength": 1}}}},
    }

    def test_items_pattern_unique_max(self):
        ok, errors = validate({"peers": [{"name": "a", "password": "x"}]}, self.DEFAULTS, self.RULES)
        self.assertEqual(errors, [])
        self.assertEqual(ok["peers"][0]["port"], 443)  # filled from the item template
        _, errors = validate({"peers": [{"name": "A1", "password": "x", "port": 0}, {"name": "A1"}, {"name": "c"}]},
                             self.DEFAULTS, self.RULES)
        for expected in ("peers: at most 2 entries", "peers[0].name: invalid format", "peers[0].port: must be >= 1",
                         "peers[1].name: duplicate value 'A1'", "peers[1].password: must have at least 1 characters"):
            self.assertIn(expected, errors)

    def test_secrets_masked_and_restored(self):
        current = {"token": "t0p", "peers": [{"name": "a", "port": 443, "password": "pa"}]}
        shown = redact(current, self.RULES)
        self.assertEqual((shown["token"], shown["peers"][0]["password"]), (SECRET_MASK, SECRET_MASK))
        back = restore_secrets(shown, current, self.RULES)
        self.assertEqual((back["token"], back["peers"][0]["password"]), ("t0p", "pa"))
        fresh = restore_secrets({"peers": [{"name": "new", "password": SECRET_MASK}]}, current, self.RULES)
        self.assertEqual(fresh["peers"][0]["password"], "")  # never store the mask itself


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.active = os.path.join(self.tmp.name, "solutions", "activeConfiguration")
        self.file = os.path.join(self.active, "my-app", "settings.json")

    def tearDown(self):
        self.tmp.cleanup()

    def store(self):
        return SettingsStore(self.file, DEFAULTS_FILE, self.active, RULES)

    def test_no_storage_runs_with_defaults(self):
        store = self.store()
        self.assertFalse(store.load_from_disk(create_if_missing=True))
        self.assertEqual(store.get(), load_defaults())
        self.assertFalse(os.path.exists(self.file))

    def test_creates_file_and_update_persists(self):
        os.makedirs(self.active)
        store = self.store()
        self.assertTrue(store.load_from_disk(create_if_missing=True))
        self.assertTrue(os.path.isfile(self.file))
        seen = []
        store.add_listener(seen.append)
        store.update({"general": {"logLevel": "DEBUG", "heartbeatIntervalMs": 250}})
        with open(self.file, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["general"]["heartbeatIntervalMs"], 250)
        self.assertEqual(seen[-1]["general"]["logLevel"], "DEBUG")
        self.assertFalse(os.path.exists(self.file + ".tmp"))
        self.assertEqual(os.stat(self.file).st_mode & 0o777, 0o600)

    def test_invalid_update_rejected_and_not_written(self):
        os.makedirs(self.active)
        store = self.store()
        store.load_from_disk(create_if_missing=True)
        before = read_text(self.file)
        with self.assertRaises(SettingsError):
            store.update({"general": {"heartbeatIntervalMs": 1}})
        self.assertEqual(read_text(self.file), before)

    def test_external_change_is_picked_up_and_invalid_ignored(self):
        os.makedirs(os.path.dirname(self.file))
        store = self.store()
        store.load_from_disk(create_if_missing=True)
        with open(self.file, "w", encoding="utf-8") as f:
            json.dump({"general": {"logLevel": "WARNING"}}, f)
        os.utime(self.file, ns=(1, 1))  # force a different mtime
        store.poll_external_change()
        self.assertEqual(store.get()["general"]["logLevel"], "WARNING")
        with open(self.file, "w", encoding="utf-8") as f:
            f.write("{broken")
        os.utime(self.file, ns=(2, 2))
        store.poll_external_change()  # logged, ignored
        self.assertEqual(store.get()["general"]["logLevel"], "WARNING")
        self.assertIn("cannot read", store.status()["error"])

    def test_solutions_load_cycle(self):
        os.makedirs(os.path.dirname(self.file))
        store = self.store()
        store.load_from_disk(create_if_missing=True)
        store.update({"map": {"file": "before.txt"}})

        def call(phase, kind="load"):
            body = json.dumps({"configurationPath": "x", "id": "op1", "phase": phase}).encode()
            fn = appdata_commands.handle_load if kind == "load" else appdata_commands.handle_save
            return fn(body, store)

        self.assertEqual(call("query")[0], 204)
        self.assertEqual(call("prepare")[0], 204)
        # Solutions copies the archived configuration (copyOnLoad) ...
        with open(self.file, "w", encoding="utf-8") as f:
            json.dump({"map": {"file": "archive.txt"}}, f)
        store.poll_external_change()  # paused during load: must not apply yet
        self.assertEqual(store.get()["map"]["file"], "before.txt")
        self.assertEqual(call("load")[0], 204)
        self.assertEqual(store.get()["map"]["file"], "archive.txt")
        self.assertEqual(call("validate")[0], 204)
        self.assertEqual(call("activate")[0], 204)
        self.assertEqual(call("save", kind="save")[0], 204)
        self.assertEqual(call("some-future-phase")[0], 204)

    def test_refused_query_pauses_polling_only_briefly(self):
        # Seen on ctrlX OS 4.6: when another app refuses the load in "query", Solutions cancels the
        # operation without sending "abort" - polling must resume after LOAD_QUERY_TIMEOUT_S.
        os.makedirs(os.path.dirname(self.file))
        store = self.store()
        store.load_from_disk(create_if_missing=True)
        appdata_commands.handle_load(b'{"phase":"query","id":"q1"}', store)
        self.assertTrue(store._load_in_progress())
        now = time.monotonic()
        with mock.patch("app.settings_store.time.monotonic", return_value=now + LOAD_QUERY_TIMEOUT_S + 1):
            self.assertFalse(store._load_in_progress())
        # a load that goes ahead extends the pause to the full timeout
        appdata_commands.handle_load(b'{"phase":"query","id":"q2"}', store)
        appdata_commands.handle_load(b'{"phase":"prepare","id":"q2"}', store)
        with mock.patch("app.settings_store.time.monotonic", return_value=now + LOAD_QUERY_TIMEOUT_S + 1):
            self.assertTrue(store._load_in_progress())

    def test_load_without_data_resets_to_defaults(self):
        os.makedirs(os.path.dirname(self.file))
        store = self.store()
        store.load_from_disk(create_if_missing=True)
        store.update({"map": {"file": "custom.txt"}})
        os.remove(self.file)
        status, _ = appdata_commands.handle_load(b'{"phase":"load","id":"x"}', store)
        self.assertEqual(status, 204)
        self.assertEqual(store.get(), load_defaults())

    def test_load_invalid_returns_problem(self):
        os.makedirs(os.path.dirname(self.file))
        store = self.store()
        with open(self.file, "w", encoding="utf-8") as f:
            json.dump({"general": {"logLevel": 42}}, f)
        status, body = appdata_commands.handle_load(b'{"phase":"load","id":"op7"}', store)
        self.assertEqual(status, 400)
        self.assertIn("[op7]", body["dynamicDescription"])


if __name__ == "__main__":
    unittest.main()
