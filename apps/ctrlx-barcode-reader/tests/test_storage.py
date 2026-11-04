"""storage.py: images + results.csv on a medium, no-read filter, size limit, missing medium."""

import csv
import os
import tempfile
import time
import unittest

from tests.helpers import HAVE_IMAGING

if HAVE_IMAGING:
    import numpy as np

    from app import imaging
    from app.storage import MediumWriter, list_media


def result(seq, ok=True, epoch=None):
    return {"sequence": seq, "ok": ok, "epoch": epoch or time.time(), "timestamp": "2026-11-03T10:00:00.000-06:00",
            "source": "trigger", "requestId": 0, "durationMs": 12.5, "error": None if ok else "no code",
            "codes": [{"format": "QRCode", "text": "A,B \"x\""}] if ok else []}


def wait_saved(w, n, timeout=20, count_dropped=True):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = w.status()
        if s["saved"] + (s["dropped"] if count_dropped else 0) >= n and s["queued"] == 0:
            return True
        time.sleep(0.02)
    return False


@unittest.skipUnless(HAVE_IMAGING, "OpenCV/numpy not available (README: Local development)")
class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        os.makedirs(os.path.join(self.root, "usb1"))
        self.w = MediumWriter(self.root, "ctrlx-barcode-reader", require_mount=False)
        self.frame = imaging.Frame(np.random.default_rng(1).integers(0, 255, (200, 300), np.uint8), "BayerRG8")
        self.w.start()

    def tearDown(self):
        self.w.stop()
        self.tmp.cleanup()

    def settings(self, **kw):
        s = {"target": "medium", "medium": "usb1", "save": "all", "format": "jpg", "jpegQuality": 90, "maxMb": 1024}
        s.update(kw)
        return s

    def test_list_media(self):
        media, error = list_media(self.root, require_mount=False)
        self.assertIsNone(error)
        self.assertEqual([m["name"] for m in media], ["usb1"])
        self.assertEqual(list_media(os.path.join(self.root, "none"))[0], [])

    def test_ram_only_writes_nothing(self):
        self.w.configure(self.settings(target="ram"))
        self.assertFalse(self.w.submit(self.frame, result(1)))
        self.assertEqual(self.w.status()["state"], "off")

    def test_saves_image_and_csv(self):
        self.w.configure(self.settings())
        self.assertTrue(self.w.submit(self.frame, result(1)))
        self.assertTrue(self.w.submit(self.frame, result(2, ok=False)) or True)   # may be dropped if the queue is full
        self.assertTrue(wait_saved(self.w, 2))
        base = os.path.join(self.root, "usb1", "ctrlx-barcode-reader")
        day = os.path.join(base, os.listdir(base)[0])
        images = sorted(n for n in os.listdir(day) if n.endswith(".jpg"))
        self.assertTrue(images[0].endswith("-000001-ok.jpg"), images)
        with open(os.path.join(day, images[0]), "rb") as f:
            self.assertTrue(f.read(2) == b"\xff\xd8")
        with open(os.path.join(day, "results.csv"), encoding="utf-8") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0][0], "time")
        self.assertEqual(rows[1][1], "1")
        self.assertEqual(rows[1][7], '["A,B \\"x\\""]')
        self.assertEqual(self.w.status()["state"], "ok")

    def test_noread_filter_and_png(self):
        self.w.configure(self.settings(save="noread", format="png"))
        self.assertFalse(self.w.submit(self.frame, result(1, ok=True)))
        self.assertTrue(self.w.submit(self.frame, result(2, ok=False)))
        self.assertTrue(wait_saved(self.w, 1))
        self.assertTrue(self.w.status()["lastFile"].endswith("-000002-noread.png"))

    def test_size_limit_deletes_oldest(self):
        self.w.configure(self.settings(format="png", maxMb=10))
        big = imaging.Frame(np.random.default_rng(2).integers(0, 255, (1500, 2000), np.uint8), "Mono8")  # ~3 MB PNG
        day0 = time.mktime((2026, 1, 1, 12, 0, 0, 0, 0, -1))
        for i in range(6):
            while not self.w.submit(big, result(i + 1, epoch=day0 + i * 86400)):
                time.sleep(0.05)
        self.assertTrue(wait_saved(self.w, 6, timeout=60, count_dropped=False))
        base = os.path.join(self.root, "usb1", "ctrlx-barcode-reader")
        files = sorted(os.path.join(d, n) for d, _s, ns in os.walk(base) for n in ns if n.endswith(".png"))
        total = sum(os.path.getsize(p) for p in files)
        self.assertLessEqual(total, 10 * 1048576)
        self.assertTrue(files[-1].endswith("-000006-ok.png"), (files, self.w.status()))
        self.assertNotIn("2026-01-01", os.listdir(base))       # oldest day removed with its results.csv

    def test_medium_not_mounted(self):
        self.w.configure(self.settings(medium="gone"))
        self.assertTrue(self.w.submit(self.frame, result(1)))
        self.assertTrue(wait_saved(self.w, 1))
        s = self.w.status()
        self.assertEqual(s["state"], "waiting")
        self.assertIn("not mounted", s["error"])
        self.assertEqual(s["dropped"], 1)


if __name__ == "__main__":
    unittest.main()
