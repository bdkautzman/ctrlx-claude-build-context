"""External medium: listing, CSV recording per day/app, reading back, a medium that comes later."""

import csv
import os
import tempfile
import unittest

from app import medium
from app.medium import MediumRecorder

T0 = 1_793_100_000.0   # a whole minute
INFO = {
    "app.snap:alpha": {"name": "alpha"},
    "proc.snap:alpha.100-1700": {"pid": 100, "name": "frame", "service": "control"},
    "thr.snap:alpha.100-101-1700": {"pid": 100, "tid": 101, "name": "ctrlXAutomation"},
}


def buckets(anon_kb=2048):
    return {
        "sys.cpu": (1000, 1500, 2000), "sys.core0": (500, 600, 700), "sys.memused": (1024, 2048, 3072),
        "app.snap:alpha.anon": (anon_kb, anon_kb, anon_kb), "app.snap:alpha.cpu": (100, 250, 400),
        "app.snap:alpha.procs": (1, 1, 1),
        "proc.snap:alpha.100-1700.anon": (1024, 1024, 1024),
        "thr.snap:alpha.100-101-1700.cpu": (0, 50, 100),
    }


class MediumTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "media")
        os.makedirs(os.path.join(self.root, "USB1"))
        self.rec = MediumRecorder(self.root, "ctrlx-system-analyzer", INFO.get, require_mount=False)
        self.rec.configure({"recording": {"medium": "USB1", "threads": True}})

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, item):
        """What the writer thread does with one queue item."""
        self.rec._pending.append(self.rec._rows_for(item))
        self.rec._write_pending()

    def test_list_media(self):
        media, err = medium.list_media(self.root, require_mount=False)
        self.assertIsNone(err)
        self.assertEqual([m["name"] for m in media], ["USB1"])
        self.assertTrue(media[0]["writable"])
        self.assertEqual(medium.list_media(self.root, require_mount=True)[0], [])   # a folder is no mount
        self.assertIsNotNone(medium.list_media(os.path.join(self.tmp.name, "nope"))[1])

    def test_files_and_read_back(self):
        for i in range(3):
            self.write(("minute", T0 + 60 * i, buckets(2048 + 1024 * i)))
        self.write(("event", {"time": T0 + 30, "type": "marker", "text": "test, started", "app": "", "service": ""}))
        base = os.path.join(self.root, "USB1", "ctrlx-system-analyzer", medium.day_of(T0))
        self.assertEqual(sorted(os.listdir(base)), ["apps", "events.csv", "processes", "system.csv", "threads"])
        with open(os.path.join(base, "apps", "alpha.csv"), newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 3)
        self.assertEqual((rows[0]["app"], rows[0]["name"], rows[0]["anon_mb_avg"], rows[0]["cpu_pct_max"]),
                         ("snap:alpha", "alpha", "2.0", "4.0"))
        with open(os.path.join(base, "processes", "alpha.csv"), newline="") as f:
            prow = next(csv.DictReader(f))
        self.assertEqual((prow["process"], prow["pid"], prow["name"], prow["service"]), ("100-1700", "100", "frame", "control"))
        with open(os.path.join(base, "system.csv"), newline="") as f:
            header = next(csv.reader(f))
        self.assertEqual(header[:8], ["epoch", "time", "cpu_pct_min", "cpu_pct_avg", "cpu_pct_max",
                                      "core0_pct_min", "core0_pct_avg", "core0_pct_max"])
        times, series = self.rec.read(["app.snap:alpha.anon", "sys.cpu", "proc.snap:alpha.100-1700.anon",
                                       "thr.snap:alpha.100-101-1700.cpu", "app.snap:nope.anon"], T0 - 1, T0 + 600)
        self.assertEqual(times, [T0, T0 + 60, T0 + 120])
        self.assertEqual(series["app.snap:alpha.anon"][1], [2.0, 3.0, 4.0])
        self.assertEqual(series["sys.cpu"][2], [20.0, 20.0, 20.0])
        self.assertEqual(series["proc.snap:alpha.100-1700.anon"][0], [1.0, 1.0, 1.0])
        self.assertEqual(series["thr.snap:alpha.100-101-1700.cpu"][1], [0.5, 0.5, 0.5])
        self.assertNotIn("app.snap:nope.anon", series)
        self.assertEqual(self.rec.oldest(), T0)
        self.assertEqual(list(self.rec.entities("apps", T0, T0 + 600)), ["app.snap:alpha"])
        self.assertIn("proc.snap:alpha.100-1700", self.rec.entities("processes", T0, T0 + 600))
        events = self.rec.events(T0, T0 + 600)
        self.assertEqual((events[0]["type"], events[0]["text"]), ("marker", "test, started"))
        self.assertEqual(self.rec.status()["state"], "recording")

    def test_medium_mounted_later_gets_the_waiting_minutes(self):
        self.rec.configure({"recording": {"medium": "STICK", "threads": False}})
        self.write(("minute", T0, buckets()))
        st = self.rec.status()
        self.assertEqual((st["state"], st["pending"]), ("waiting", 1))
        os.makedirs(os.path.join(self.root, "STICK"))
        self.write(("minute", T0 + 60, buckets()))
        st = self.rec.status()
        self.assertEqual((st["state"], st["pending"]), ("recording", 0))
        times, _ = self.rec.read(["sys.cpu"], T0 - 1, T0 + 600)
        self.assertEqual(times, [T0, T0 + 60])
        base = os.path.join(self.root, "STICK", "ctrlx-system-analyzer", medium.day_of(T0))
        self.assertNotIn("threads", os.listdir(base))   # recording.threads off

    def test_off_drops_pending(self):
        self.rec.configure({"recording": {"medium": "STICK", "threads": False}})
        self.write(("minute", T0, buckets()))
        self.rec.configure({"recording": {"medium": "", "threads": False}})
        self.rec.on_minute(T0 + 60, buckets())           # ignored while off
        self.assertEqual(self.rec.status()["state"], "off")
        self.assertEqual(self.rec._pending, [])
        self.assertTrue(self.rec._queue.empty())


if __name__ == "__main__":
    unittest.main()
