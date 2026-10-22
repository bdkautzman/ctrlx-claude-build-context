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
        self.assertEqual(normalized["general"]["pollIntervalMs"], defaults["general"]["pollIntervalMs"])
        self.assertEqual(normalized["future"], 1)

    def test_type_and_rule_errors(self):
        defaults = load_defaults()
        _, errors = validate({"general": {"logLevel": "LOUD", "pollIntervalMs": True}}, defaults, RULES)
        self.assertTrue(any(e.startswith("general.logLevel") for e in errors))
        self.assertTrue(any(e.startswith("general.pollIntervalMs") for e in errors))

    def test_integral_float_accepted_for_int(self):
        defaults = load_defaults()
        normalized, errors = validate({"general": {"pollIntervalMs": 1500.0}}, defaults, RULES)
        self.assertEqual(errors, [])
        self.assertIsInstance(normalized["general"]["pollIntervalMs"], int)

    def test_root_must_be_object(self):
        _, errors = validate([1, 2], load_defaults(), RULES)
        self.assertTrue(errors)


class ItemsAndSecretsTest(unittest.TestCase):
    def core(self, **kw):
        entry = {"name": "a", "host": "10.0.0.1", "user": "admin", "password": "pw"}
        entry.update(kw)
        return entry

    def test_items_normalized_against_template(self):
        normalized, errors = validate({"cores": [self.core()]}, load_defaults(), RULES)
        self.assertEqual(errors, [])
        self.assertEqual(normalized["cores"][0]["user"], "admin")
        self.assertEqual(normalized["cores"][0]["sslPort"], 443)
        self.assertIs(normalized["cores"][0]["skipCertificateCheck"], True)

    def test_item_rules(self):
        cores = [self.core(name="UPPER"), self.core(name="b", sslPort=0), self.core(name="c", sslPort="443"),
                 self.core(name="a", host="")]
        _, errors = validate({"cores": cores}, load_defaults(), RULES)
        for expected in ("cores[0].name: invalid format", "cores[1].sslPort: must be >= 1",
                         "cores[2].sslPort: expected integer, got string", "cores[3].host: invalid format"):
            self.assertIn(expected, errors)
        _, errors = validate({"cores": [self.core(), self.core()]}, load_defaults(), RULES)
        self.assertIn("cores[1].name: duplicate value 'a'", errors)
        _, errors = validate({"cores": [self.core(name="n%d" % i) for i in range(33)]}, load_defaults(), RULES)
        self.assertIn("cores: at most 32 entries", errors)
        _, errors = validate({"cores": [{"name": "x", "host": "h", "password": "p"}]}, load_defaults(), RULES)
        self.assertIn("cores[0].user: invalid format", errors)  # no default user: must be given

    def test_redact_and_restore(self):
        current, _ = validate({"cores": [self.core(), self.core(name="b", password="pb")]}, load_defaults(), RULES)
        shown = redact(current, RULES)
        self.assertEqual([c["password"] for c in shown["cores"]], [SECRET_MASK, SECRET_MASK])
        self.assertEqual(current["cores"][0]["password"], "pw")  # redact works on a copy
        shown["cores"].reverse()  # order changes do not matter: matched by name
        restored = restore_secrets(shown, current, RULES)
        self.assertEqual({c["name"]: c["password"] for c in restored["cores"]}, {"a": "pw", "b": "pb"})
        # a mask for an unknown entry must not become the password
        unknown = restore_secrets({"cores": [self.core(name="new", password=SECRET_MASK)]}, current, RULES)
        self.assertEqual(unknown["cores"][0]["password"], "")


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
        store.update({"general": {"logLevel": "DEBUG", "pollIntervalMs": 1500}})
        with open(self.file, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["general"]["pollIntervalMs"], 1500)
        self.assertEqual(seen[-1]["general"]["logLevel"], "DEBUG")
        self.assertFalse(os.path.exists(self.file + ".tmp"))
        self.assertEqual(os.stat(self.file).st_mode & 0o777, 0o600)

    def test_invalid_update_rejected_and_not_written(self):
        os.makedirs(self.active)
        store = self.store()
        store.load_from_disk(create_if_missing=True)
        before = read_text(self.file)
        with self.assertRaises(SettingsError):
            store.update({"general": {"pollIntervalMs": 1}})
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
        store.update({"general": {"logLevel": "WARNING"}})

        def call(phase, kind="load"):
            body = json.dumps({"configurationPath": "x", "id": "op1", "phase": phase}).encode()
            fn = appdata_commands.handle_load if kind == "load" else appdata_commands.handle_save
            return fn(body, store)

        self.assertEqual(call("query")[0], 204)
        self.assertEqual(call("prepare")[0], 204)
        # Solutions copies the archived configuration (copyOnLoad) ...
        with open(self.file, "w", encoding="utf-8") as f:
            json.dump({"general": {"logLevel": "ERROR"}}, f)
        store.poll_external_change()  # paused during load: must not apply yet
        self.assertEqual(store.get()["general"]["logLevel"], "WARNING")
        self.assertEqual(call("load")[0], 204)
        self.assertEqual(store.get()["general"]["logLevel"], "ERROR")
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
        store.update({"general": {"logLevel": "DEBUG"}})
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
