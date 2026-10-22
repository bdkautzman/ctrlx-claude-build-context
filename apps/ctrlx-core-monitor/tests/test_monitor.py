"""CoreMonitor tests against a fake Data Layer client adapter (no ctrlX needed)."""

import json
import unittest
from unittest import mock

from app import monitor
from app.monitor import CoreMonitor, DataLayerError, connection_address, reconcile_plan, summary_json

CORE_INFO = {
    "framework/metrics/system/cpu-utilisation-percent": 5.3,
    "framework/metrics/system/memused-percent": 16.4,
    "framework/metrics/system/memused-mb": 327.0,
    "framework/metrics/system/memtotal-mb": 1993.1,
    "system/info/uptime": 643,
    "system/info/hostname": "ctrlX-OS",
    "system/info/ctrlxOsVersion": "4.6",
    "system/info/architecture": "arm64",
    "system/apps/installed/rexroth-deviceadmin": {"name": "rexroth-deviceadmin", "release": {"version": "4.6.6"}},
}


class FakeClient:
    """Mimics ctrlX OS: devices/remotes lists remotes with masked addresses; a remote's Data Layer
    is mounted below devices/remotes/<name>/ once `online` contains it."""

    def __init__(self, remotes=None):
        self.remotes = dict(remotes or {})   # name -> address
        self.trees = {}                       # name -> {node: value}
        self.online = set()
        self.log = []

    def read(self, address):
        if address == "devices/remotes":
            return {"remotes": [{"name": n, "address": "tcp://***:***@x"} for n in self.remotes]}
        parts = address.split("/", 3)
        name, node = parts[2], parts[3]
        if name not in self.remotes or name not in self.online:
            raise DataLayerError("DL_INVALID_ADDRESS")
        if node not in self.trees.get(name, {}):
            raise DataLayerError("DL_INVALID_ADDRESS")
        return self.trees[name][node]

    def create_remote(self, name, address):
        self.log.append(("create", name))
        self.remotes[name] = address

    def remove(self, address):
        name = address.rsplit("/", 1)[-1]
        self.log.append(("remove", name))
        if name not in self.remotes:
            raise DataLayerError("DL_INVALID_ADDRESS")
        del self.remotes[name]


def settings(*cores, poll_ms=2000):
    full = []
    for c in cores:
        entry = {"name": "", "host": "192.168.1.7", "user": "boschrexroth", "password": "pw", "sslPort": 443,
                 "skipCertificateCheck": True}
        entry.update(c)
        full.append(entry)
    return {"settingsVersion": 1, "general": {"logLevel": "INFO", "pollIntervalMs": poll_ms}, "cores": full}


class AddressTest(unittest.TestCase):
    def test_connection_address(self):
        core = settings({"name": "a", "user": "admin", "password": "p@ss:w/rd"})["cores"][0]
        self.assertEqual(connection_address(core),
                         "tcp://admin:p%40ss%3Aw%2Frd@192.168.1.7?sslport=443&NoCheckCertificate=true")
        core.update(skipCertificateCheck=False, sslPort=8443)
        self.assertEqual(connection_address(core), "tcp://admin:p%40ss%3Aw%2Frd@192.168.1.7?sslport=8443")

    def test_reconcile_plan(self):
        desired = {"cm-a": "A", "cm-b": "B"}
        # first run after start: existing remotes are recreated once, missing ones created, stale removed
        self.assertEqual(reconcile_plan({"cm-a", "cm-old"}, desired, {}), (["cm-old", "cm-a"], ["cm-a", "cm-b"]))
        # nothing to do when everything was created by us with the same address
        self.assertEqual(reconcile_plan({"cm-a", "cm-b"}, desired, dict(desired)), ([], []))
        # changed address (e.g. new password) -> remove + create
        self.assertEqual(reconcile_plan({"cm-a", "cm-b"}, desired, {"cm-a": "A", "cm-b": "OLD"}), (["cm-b"], ["cm-b"]))


class MonitorTest(unittest.TestCase):
    def make(self, cfg, client):
        mon = CoreMonitor(cfg)
        mon.attach(client)
        return mon

    def test_only_own_remotes_are_managed(self):
        client = FakeClient({"by-hand": "tcp://x", "cm-old": "tcp://y"})
        mon = self.make(settings({"name": "line-1"}), client)
        mon.run_once()
        self.assertIn("by-hand", client.remotes)          # never touched
        self.assertNotIn("cm-old", client.remotes)        # not configured anymore
        self.assertIn("cm-line-1", client.remotes)
        self.assertIn("NoCheckCertificate=true", client.remotes["cm-line-1"])

    def test_recreated_once_after_restart_then_stable(self):
        client = FakeClient({"cm-line-1": "tcp://from-before-restart"})
        mon = self.make(settings({"name": "line-1"}), client)
        mon.run_once()
        self.assertEqual(client.log, [("remove", "cm-line-1"), ("create", "cm-line-1")])
        client.log.clear()
        mon.on_settings_changed(settings({"name": "line-1"}))  # same content -> no churn
        mon.run_once()
        self.assertEqual(client.log, [])
        mon.on_settings_changed(settings({"name": "line-1", "password": "new"}))
        mon.run_once()
        self.assertEqual(client.log, [("remove", "cm-line-1"), ("create", "cm-line-1")])

    def test_status_connected_with_values(self):
        client = FakeClient()
        mon = self.make(settings({"name": "line-1"}), client)
        mon.run_once()
        client.online.add("cm-line-1")
        client.trees["cm-line-1"] = dict(CORE_INFO)
        mon.run_once()
        core = mon.snapshot()[0]
        self.assertEqual(core["state"], "connected")
        self.assertEqual((core["hostname"], core["osVersion"], core["architecture"]), ("ctrlX-OS", "4.6", "arm64"))
        self.assertNotIn("firmware", core)
        self.assertEqual((core["cpuPercent"], core["memUsedPercent"], core["uptimeS"]), (5.3, 16.4, 643))
        self.assertEqual(core["remote"], "devices/remotes/cm-line-1")

    def connected_core(self, tree):
        client = FakeClient()
        mon = self.make(settings({"name": "a"}), client)
        mon.run_once()
        client.online.add("cm-a")
        client.trees["cm-a"] = tree
        mon.run_once()
        return mon.snapshot()[0]

    def test_os_version_of_ctrlx_os_3_6(self):
        # seen on a ctrlX CORE with ctrlX OS 3.6: no system/info/ctrlxOsVersion, app nodes named snap_<name>
        tree = {k: v for k, v in CORE_INFO.items() if "ctrlxOsVersion" not in k and "deviceadmin" not in k}
        tree["system/apps/installed/snap_rexroth-deviceadmin"] = {"name": "rexroth-deviceadmin",
                                                                  "release": {"version": "3.6.8"}}
        core = self.connected_core(tree)
        self.assertEqual((core["state"], core["osVersion"]), ("connected", "3.6"))

    def test_missing_version_info_still_connected(self):
        tree = {k: v for k, v in CORE_INFO.items() if "ctrlxOsVersion" not in k and "deviceadmin" not in k}
        core = self.connected_core(tree)
        self.assertEqual((core["state"], core["osVersion"], core["hostname"]), ("connected", None, "ctrlX-OS"))

    def test_errors(self):
        client = FakeClient()
        mon = self.make(settings({"name": "a"}), client)
        mon.run_once()
        self.assertEqual(mon.snapshot()[0]["state"], "connecting")  # just created: grace period
        with mock.patch.object(monitor, "CONNECT_GRACE_S", -1):
            mon.run_once()
        core = mon.snapshot()[0]
        self.assertEqual((core["state"], core["result"], core["error"]), ("error", "DL_INVALID_ADDRESS", "not connected"))
        self.assertIn("login rejected", monitor._explain("DL_SEC_UNAUTHORIZED"))

    def test_offline_without_client(self):
        mon = CoreMonitor(settings({"name": "a"}))
        mon.run_once()
        self.assertEqual(mon.snapshot()[0]["state"], "offline")

    def test_removed_core_disappears(self):
        client = FakeClient()
        mon = self.make(settings({"name": "a"}, {"name": "b"}), client)
        mon.run_once()
        mon.on_settings_changed(settings({"name": "b"}))
        mon.run_once()
        self.assertEqual([c["name"] for c in mon.snapshot()], ["b"])
        self.assertNotIn("cm-a", client.remotes)

    def test_no_credentials_in_output(self):
        client = FakeClient()
        mon = self.make(settings({"name": "a", "password": "TopSecret1"}), client)
        published = []
        mon._on_update = published.append
        mon.run_once()
        text = json.dumps(mon.snapshot()) + summary_json(mon.snapshot()) + json.dumps(published)
        self.assertNotIn("TopSecret1", text)
        self.assertNotIn("password", text)

    def test_summary_json(self):
        data = json.loads(summary_json([{"name": "a", "host": "h", "state": "connected", "cpuPercent": 1.0, "x": 1}]))
        self.assertEqual(data[0]["name"], "a")
        self.assertNotIn("x", data[0])
        self.assertIn("memUsedPercent", data[0])


if __name__ == "__main__":
    unittest.main()
