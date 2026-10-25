"""Whitelist/blacklist rating, change detection, history store and schedules."""

import gzip
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from app import rating, scheduler
from app.history import History


def host(ip, ports=(), mac="", names=()):
    return {"ip": ip, "mac": mac, "names": [{"name": n, "source": "dns"} for n in names],
            "ports": [{"proto": p.split("/")[0], "port": int(p.split("/")[1]), "state": "open"} for p in ports]}


def result(scan_id, hosts, started="2026-10-25T10:00:00Z", profile_id="quick", tcp="common", udp="", ranges=None,
           status="completed"):
    return {"id": scan_id, "status": status, "startedAt": started, "finishedAt": started, "durationS": 1.0,
            "trigger": "manual", "profile": {"id": profile_id, "name": profile_id, "tcpPorts": tcp, "udpPorts": udp},
            "targets": {"count": 254, "ranges": ranges or [["192.168.1.1", "192.168.1.254"]]},
            "hosts": hosts, "summary": {"targets": 254, "hostsUp": len(hosts), "openTcp": 0, "openUdp": 0}}


class RatingTest(unittest.TestCase):
    def test_rules(self):
        lists = rating.Lists(
            whitelist=[{"hosts": "00:60:34", "ports": "", "note": "Bosch Rexroth"},
                       {"hosts": "192.168.1.2", "ports": "tcp/22, tcp/443", "note": "PLC"},
                       {"hosts": "hmi-*", "ports": "", "note": "HMIs"}],
            blacklist=[{"hosts": "", "ports": "23", "note": "no telnet"},
                       {"hosts": "192.168.1.66", "ports": "", "note": "must be gone"},
                       {"hosts": "192.168.1.2", "ports": "tcp/443", "note": "blacklist wins"}])
        plc = host("192.168.1.2", ["tcp/22", "tcp/23", "tcp/443", "tcp/8080"], mac="00:60:34:85:75:a4")
        self.assertEqual(lists.rate_host(plc), ("ok", "Bosch Rexroth"))
        self.assertEqual(lists.rate_port(plc, "tcp", 22), ("ok", "PLC"))
        self.assertEqual(lists.rate_port(plc, "tcp", 23), ("forbidden", "no telnet"))
        self.assertEqual(lists.rate_port(plc, "udp", 23)[0], "forbidden")   # no protocol = both
        self.assertEqual(lists.rate_port(plc, "tcp", 443)[0], "forbidden")
        self.assertEqual(lists.rate_port(plc, "tcp", 8080), ("unlisted", ""))
        self.assertEqual(lists.rate_host(host("192.168.1.66"))[0], "forbidden")
        self.assertEqual(lists.rate_host(host("192.168.1.9", names=["HMI-line2.local"]))[0], "ok")
        self.assertEqual(lists.rate_host(host("192.168.1.10"))[0], "unlisted")

    def test_apply_counts_alarms(self):
        lists = rating.Lists([], [{"hosts": "", "ports": "tcp/23", "note": "telnet"}])
        rated = rating.apply(result("a", [host("192.168.1.2", ["tcp/22", "tcp/23"])]), lists)
        r = rated["summary"]["rating"]
        self.assertEqual((r["forbidden"], r["ok"], r["unlisted"]), (1, 0, 2))
        self.assertEqual(r["alarms"], [{"type": "port", "ip": "192.168.1.2", "proto": "tcp", "port": 23, "note": "telnet"}])
        self.assertEqual(rated["hosts"][0]["ports"][1]["rating"], "forbidden")

    def test_invalid_entries_are_ignored_not_fatal(self):
        lists = rating.Lists([{"hosts": "", "ports": ""}, {"hosts": "x$y"}, "junk"], [])
        self.assertEqual(lists.rate_host(host("1.2.3.4"))[0], "unlisted")
        self.assertEqual(rating.check_entry({"hosts": "10.0.0.0/8 aa:bb:cc:dd:ee:ff plc-?", "ports": "udp/161"}), [])
        self.assertTrue(rating.check_entry({"hosts": "", "ports": ""}))

    def test_compare(self):
        prev = result("p", [host("192.168.1.2", ["tcp/22", "tcp/80"], mac="00:60:34:00:00:01"), host("192.168.1.3"),
                            host("192.168.1.4", ["tcp/22"])])
        cur = result("c", [host("192.168.1.2", ["tcp/22", "tcp/23", "tcp/9999"], mac="00:60:34:00:00:02"),
                           host("192.168.1.5"), host("192.168.1.4", ["tcp/22"])], tcp="common, 9999")
        ch = rating.compare(prev, cur)
        self.assertEqual(ch["newHosts"], ["192.168.1.5"])
        self.assertEqual(ch["goneHosts"], ["192.168.1.3"])
        # 9999 was not scanned before -> not "opened"; 80 was scanned in both -> "closed"
        self.assertEqual(ch["openedPorts"], [{"ip": "192.168.1.2", "proto": "tcp", "port": 23}])
        self.assertEqual(ch["closedPorts"], [{"ip": "192.168.1.2", "proto": "tcp", "port": 80}])
        self.assertEqual(ch["macChanged"][0]["new"], "00:60:34:00:00:02")
        self.assertEqual(ch["total"], 5)

    def test_compare_respects_targets(self):
        prev = result("p", [host("192.168.1.2")], ranges=[["192.168.1.1", "192.168.1.10"]])
        cur = result("c", [host("192.168.1.50")], ranges=[["192.168.1.40", "192.168.1.60"]])
        ch = rating.compare(prev, cur)
        self.assertEqual((ch["newHosts"], ch["goneHosts"]), ([], []))   # neither was in the other's range


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "history")

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_get_list_delete(self):
        h = History(self.dir)
        h.load()
        h.add(result("20261025-100000-quick-aaaa", [host("192.168.1.2")], started="2026-10-25T10:00:00Z"))
        h.add(result("20261025-110000-deep-bbbb", [], started="2026-10-25T11:00:00Z", profile_id="deep"))
        self.assertEqual([e["id"] for e in h.list()], ["20261025-110000-deep-bbbb", "20261025-100000-quick-aaaa"])
        self.assertEqual(h.get("20261025-100000-quick-aaaa")["hosts"][0]["ip"], "192.168.1.2")
        self.assertEqual(h.latest("quick")["id"], "20261025-100000-quick-aaaa")
        self.assertIsNone(h.latest("quick", exclude_id="20261025-100000-quick-aaaa"))
        self.assertEqual(h.last_started("deep"), "2026-10-25T11:00:00Z")
        self.assertTrue(h.delete("20261025-110000-deep-bbbb"))
        self.assertFalse(h.delete("20261025-110000-deep-bbbb"))
        self.assertEqual(sorted(os.listdir(self.dir)), ["20261025-100000-quick-aaaa.json.gz", "index.json"])

    def test_ids_cannot_escape_the_folder(self):
        h = History(self.dir)
        h.load()
        for bad in ("../settings", "a/b", "", ".hidden"):
            self.assertIsNone(h.get(bad))
            self.assertFalse(h.delete(bad))
        with self.assertRaises(ValueError):
            h.add(result("../x", []))

    def test_limits_delete_oldest(self):
        h = History(self.dir, max_scans=3)
        h.load()
        for i in range(5):
            h.add(result("s{}".format(i), [], started="2026-10-25T1{}:00:00Z".format(i)))
        self.assertEqual([e["id"] for e in h.list()], ["s4", "s3", "s2"])
        self.assertFalse(os.path.exists(os.path.join(self.dir, "s0.json.gz")))
        h.set_limits(max_scans=100, max_megabytes=0.000001)   # size limit: keeps only the newest
        self.assertEqual([e["id"] for e in h.list()], ["s4"])

    def test_index_rebuilt_when_missing_or_stale(self):
        h = History(self.dir)
        h.load()
        h.add(result("s1", [host("10.0.0.1")]))
        os.remove(os.path.join(self.dir, "index.json"))
        with gzip.open(os.path.join(self.dir, "s2.json.gz"), "wt") as f:   # e.g. copied in by hand
            json.dump(result("s2", [], started="2026-10-26T00:00:00Z"), f)
        h2 = History(self.dir)
        self.assertEqual(h2.load(), 2)
        self.assertEqual([e["id"] for e in h2.list()], ["s2", "s1"])
        self.assertEqual(h2.list()[1]["hostsUp"], 1)


UTC = timezone.utc


class ScheduleTest(unittest.TestCase):
    T0 = datetime(2026, 10, 26, 8, 0, 0, tzinfo=UTC)   # a Monday

    def p(self, **kw):
        base = {"id": "x", "scheduleMode": "off", "scheduleIntervalMinutes": 60, "scheduleTimes": "02:00", "scheduleDays": ""}
        base.update(kw)
        return base

    def test_parse(self):
        self.assertEqual(scheduler.parse_times("14:30, 2:05"), [(2, 5), (14, 30)])
        self.assertEqual(scheduler.parse_days("Mon,wed"), {0, 2})
        self.assertEqual(scheduler.parse_days(""), set(range(7)))
        for bad in ("24:00", "7", "12:60"):
            with self.assertRaises(ValueError):
                scheduler.parse_times(bad)
        self.assertTrue(scheduler.check_profile(self.p(scheduleMode="daily", scheduleTimes="")))

    def test_interval(self):
        p = self.p(scheduleMode="interval", scheduleIntervalMinutes=60)
        start = self.T0
        # never ran: one minute after the app started
        self.assertEqual(scheduler.next_run(p, None, start, start), start + scheduler.STARTUP_DELAY)
        last = self.T0 - timedelta(minutes=30)
        self.assertEqual(scheduler.next_run(p, last, self.T0, start - timedelta(hours=5)), last + timedelta(minutes=60))
        # missed while off: runs right after the start delay
        self.assertEqual(scheduler.next_run(p, last - timedelta(days=1), self.T0, start), start + scheduler.STARTUP_DELAY)
        # clock went back (last start "in the future"): treated as never ran
        self.assertEqual(scheduler.next_run(p, self.T0 + timedelta(days=1), self.T0, start), start + scheduler.STARTUP_DELAY)

    def test_daily_with_days_no_catch_up(self):
        p = self.p(scheduleMode="daily", scheduleTimes="02:00, 20:00", scheduleDays="tue,thu")
        nxt = scheduler.next_run(p, None, self.T0, self.T0, tz=UTC)
        self.assertEqual(nxt, datetime(2026, 10, 27, 2, 0, tzinfo=UTC))   # Tuesday 02:00
        p2 = self.p(scheduleMode="daily", scheduleTimes="07:00, 20:00")
        # 07:00 today passed before the app started -> not caught up, next is 20:00
        self.assertEqual(scheduler.next_run(p2, None, self.T0, self.T0, tz=UTC), datetime(2026, 10, 26, 20, 0, tzinfo=UTC))
        self.assertIsNone(scheduler.next_run(self.p(), None, self.T0, self.T0))

    def test_scheduler_fires_once(self):
        now = [self.T0]
        s = scheduler.Scheduler(clock=lambda: now[0], tz=UTC)
        profiles = [self.p(id="a", scheduleMode="interval", scheduleIntervalMinutes=5), self.p(id="b")]
        last = {}
        self.assertEqual(s.due(profiles, last.get, set()), [])
        now[0] += timedelta(seconds=61)
        self.assertEqual(s.due(profiles, last.get, set()), ["a"])
        self.assertEqual(s.due(profiles, last.get, set()), [])            # fired: not again right away
        now[0] += timedelta(minutes=5)
        self.assertEqual(s.due(profiles, last.get, {"a"}), [])            # still running/queued
        self.assertEqual(s.due(profiles, last.get, set()), ["a"])
        self.assertIn("a", s.next_runs(profiles, last.get))
        self.assertNotIn("b", s.next_runs(profiles, last.get))


if __name__ == "__main__":
    unittest.main()
