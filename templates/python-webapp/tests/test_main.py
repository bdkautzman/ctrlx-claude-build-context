"""main.py: the first start after an install (app data folder not writable yet, gotchas #82)."""

import threading
import unittest
from unittest import mock

from app import main
from app.settings_store import SettingsError


class FlakyStore:
    def __init__(self, failures, error=None):
        self.failures = failures
        self.error = error or PermissionError(13, "Permission denied")
        self.calls = 0

    def load_from_disk(self, create_if_missing=False):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error


class LoadSettingsTest(unittest.TestCase):
    def test_retries_until_writable(self):
        store = FlakyStore(2)
        with mock.patch.object(main, "POLL_INTERVAL_S", 0.01):
            main._load_settings(store, threading.Event())
        self.assertEqual(store.calls, 3)

    def test_gives_up_after_the_wait(self):
        store = FlakyStore(10 ** 6)
        with mock.patch.object(main, "POLL_INTERVAL_S", 0.01), mock.patch.object(main, "STORAGE_WAIT_S", 0.05):
            main._load_settings(store, threading.Event())     # returns, runs with defaults
        self.assertGreater(store.calls, 1)

    def test_invalid_settings_are_not_retried(self):
        store = FlakyStore(5, SettingsError(["general.logLevel: must be one of ..."]))
        main._load_settings(store, threading.Event())
        self.assertEqual(store.calls, 1)


if __name__ == "__main__":
    unittest.main()
