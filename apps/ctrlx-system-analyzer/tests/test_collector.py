"""Collector against a fake /proc: grouping, CPU %, memory sums, threads, events."""

import os
import tempfile
import unittest

from app import procfs
from app.collector import Collector, EventLog
from app.history import History
from tests.helpers import BTIME, FakeProc, load_defaults

TICK = procfs.CLK_TCK


class CollectorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fake = FakeProc(self.tmp.name)
        f = self.fake
        f.add(2, "kthreadd", "/", kernel=True)
        f.add(100, "rexroth-automat", "/system.slice/snap.alpha.control.service",
              "/snap/alpha/2302/frame /var/x", anon=100000, file=50000, shmem=200, pss=120000,
              threads={101: {"comm": "ctrlXAutomation", "policy": 1, "rt_priority": 76, "processor": 3},
                       102: {"comm": "dl_client"}})
        f.add(200, "python3", "/system.slice/snap.beta.service.service", "/snap/beta/x8/bin/python3 -m app",
              anon=20000, file=10000, pss=25000)
        f.add(300, "snapd", "/system.slice/snapd.service", "/usr/lib/snapd/snapd", anon=13000, file=28000)
        self.history = History(interval_s=10)
        self.events = EventLog()
        self.c = Collector(self.history, self.events, self.tmp.name)
        self.c.configure(load_defaults())

    def tearDown(self):
        self.tmp.cleanup()

    def step(self, now, ticks=0, thread_ticks=0):
        """Advance: alpha's main thread uses ticks, thread 101 thread_ticks, cores half busy."""
        p = self.fake.procs[100]
        self.fake.update(100, utime=p["utime"] + ticks + thread_ticks)
        p["threads"][101]["utime"] = p["threads"][101].get("utime", 0) + thread_ticks
        self.fake.write_proc(100)
        self.fake.busy(500)
        return self.c.sample(now)

    def test_groups_cpu_memory_threads(self):
        self.c.sample(BTIME + 1000)
        snap = self.step(BTIME + 1010, ticks=2 * TICK, thread_ticks=3 * TICK)   # 5 s CPU in 10 s
        g = snap["groups"]
        self.assertEqual(set(g), {"kernel", "snap:alpha", "snap:beta", "system:snapd"})
        a = g["snap:alpha"]
        self.assertAlmostEqual(a["cpu"], 50.0, places=1)
        self.assertEqual((a["processes"], a["threads"], a["services"], a["revision"]), (1, 3, ["control"], "2302"))
        self.assertAlmostEqual(a["anonMb"], 100000 / 1024, places=1)
        self.assertAlmostEqual(a["pssMb"], 120000 / 1024, places=1)
        self.assertIsNone(g["system:snapd"]["pssMb"])        # no smaps_rollup readable for it
        self.assertIsNone(g["kernel"]["anonMb"])
        proc = a["processList"][0]
        threads = {t["tid"]: t for t in proc["threadList"]}
        self.assertAlmostEqual(threads[101]["cpu"], 30.0, places=1)
        self.assertEqual((threads[101]["policy"], threads[101]["rtPriority"], threads[101]["core"]), ("FIFO", 76, 3))
        self.assertEqual(snap["system"]["cpu"], 50.0)
        self.assertEqual(snap["system"]["coreCount"], 2)
        self.assertEqual(snap["system"]["pressure"]["memory"], 1.5)
        # series: app always, process/thread from their second sample, idle threads none
        h = self.history
        self.assertTrue(h.has("app.snap:alpha.anon") and h.has("app.snap:alpha.cpu"))
        pkey = "100-{}".format(BTIME + 1)
        self.assertTrue(h.has("proc.snap:alpha.{}.anon".format(pkey)))
        self.assertTrue(h.has("thr.snap:alpha.100-101-{}.cpu".format(BTIME + 1)))
        self.assertFalse(h.has("thr.snap:alpha.100-102-{}.cpu".format(BTIME + 1)))
        self.assertFalse(h.has("app.kernel.anon"))
        self.assertEqual(self.c.describe("proc.snap:alpha." + pkey)["name"], "rexroth-automat")
        st = self.c.status()
        self.assertEqual((st["samples"], st["pssAvailable"], st["pressureAvailable"]), (2, True, True))

    def test_new_process_counts_from_its_start(self):
        self.c.sample(BTIME + 1000)
        # started 5 s after the first sample, used 1 s CPU since
        self.fake.add(400, "helper", "/system.slice/snap.beta.service.service", starttime=1005 * TICK,
                      utime=TICK, anon=10, file=10)
        snap = self.step(BTIME + 1010)
        helper = [p for p in snap["groups"]["snap:beta"]["processList"] if p["pid"] == 400][0]
        self.assertAlmostEqual(helper["cpu"], 10.0, places=1)

    def test_short_lived_helpers_get_no_own_series(self):
        self.c.sample(BTIME + 1000)
        self.fake.add(400, "sleep", "/system.slice/snap.beta.service.service", starttime=1001 * TICK, anon=10, file=10)
        self.c.sample(BTIME + 1010)
        self.c.sample(BTIME + 1020)                   # 2 samples, but 19 s old: app only
        self.assertFalse(self.history.has("proc.snap:beta.400-{}.anon".format(BTIME + 1001)))
        self.assertEqual(self.c.snapshot()["groups"]["snap:beta"]["processes"], 2)
        self.c.sample(BTIME + 1070)                   # a minute old: own series
        self.assertTrue(self.history.has("proc.snap:beta.400-{}.anon".format(BTIME + 1001)))

    def test_events_restart_update_stop_start(self):
        self.c.sample(BTIME + 1000)
        self.c.sample(BTIME + 1010)
        self.assertEqual(self.events.between(0, 2 ** 40), [])
        # beta restarts from a new revision
        self.fake.remove(200)
        self.fake.add(201, "python3", "/system.slice/snap.beta.service.service", "/snap/beta/x9/bin/python3 -m app",
                      starttime=1015 * TICK)
        self.c.sample(BTIME + 1020)
        types = sorted(e["type"] for e in self.events.between(0, 2 ** 40))
        self.assertEqual(types, ["restarted", "updated"])
        upd = [e for e in self.events.between(0, 2 ** 40) if e["type"] == "updated"][0]
        self.assertEqual((upd["app"], upd["text"]), ("snap:beta", "revision x8 -> x9"))
        # snapd stops, a new service starts
        self.fake.remove(300)
        self.fake.add(500, "web", "/system.slice/snap.gamma.web.service", starttime=1025 * TICK)
        self.c.sample(BTIME + 1030)
        last = {e["type"]: e for e in self.events.between(BTIME + 1030, BTIME + 1030)}
        self.assertEqual(last["stopped"]["app"], "system:snapd")
        self.assertEqual(last["started"]["app"], "snap:gamma")

    def test_threads_off_and_unreadable_files(self):
        s = load_defaults()
        s["collector"]["threads"] = False
        s["collector"]["pss"] = False
        self.c.configure(s)
        self.c.sample(BTIME + 1000)
        self.fake.update(100, utime=TICK)
        os.remove(os.path.join(self.tmp.name, "pressure", "memory"))   # what a snap sees without system-observe
        snap = self.c.sample(BTIME + 1010)
        proc = snap["groups"]["snap:alpha"]["processList"][0]
        self.assertEqual(proc["threadList"], [])
        self.assertIsNone(proc["pssMb"])
        self.assertFalse(self.c.status()["pressureAvailable"])
        self.assertIsNone(self.c.status()["pssAvailable"])

    def test_ended_process_is_dropped_and_clock_jump_back(self):
        self.c.sample(BTIME + 1000)
        self.fake.remove(300)
        snap = self.c.sample(BTIME + 990)          # clock went back: no CPU values, no crash
        self.assertNotIn("system:snapd", snap["groups"])
        self.assertIsNone(snap["groups"]["snap:alpha"]["cpu"])


if __name__ == "__main__":
    unittest.main()
