"""AMR list file (amr-list.txt in the app data): parsing, default, saving, external changes."""

import os
import tempfile
import time
import unittest

from app.amr_list import FILE_NAME, MAX_IDS, AmrList, AmrListError, format_list, parse_list

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FILE = os.path.join(HERE, "..", "defaults", FILE_NAME)


class ParseTest(unittest.TestCase):
    def test_lines_comments_csv(self):
        text = "﻿# header\nAMR-01\n\n  AMR_02  \nAMR-03,Tugger,blue\n\"AMR-04\";x\nAMR-05\tfoo\n"
        self.assertEqual(parse_list(text), ["AMR-01", "AMR_02", "AMR-03", "AMR-04", "AMR-05"])
        self.assertEqual(parse_list(format_list(["a", "B-2"])), ["a", "B-2"])
        self.assertEqual(parse_list(""), [])

    def test_errors_name_the_lines(self):
        with self.assertRaises(AmrListError) as ctx:
            parse_list("AMR-01\nAMR 02\n-x\nAMR-01\n" + "a" * 65)
        self.assertEqual([e.split(":")[0] for e in ctx.exception.errors], ["line 2", "line 3", "line 4", "line 5"])
        self.assertIn("listed twice", ctx.exception.errors[2])
        with self.assertRaises(AmrListError):
            parse_list("\n".join("A{}".format(i) for i in range(MAX_IDS + 1)))

    def test_default_file(self):
        with open(DEFAULT_FILE, encoding="utf-8") as f:
            self.assertEqual(parse_list(f.read()), ["AMR-01", "AMR-02", "AMR-03", "AMR-04"])  # = the simulation's ids


class AmrListTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "app", FILE_NAME)
        self.amrs = AmrList(self.path, DEFAULT_FILE)
        self.seen = []
        self.amrs.add_listener(self.seen.append)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)
        st = os.stat(self.path)                          # a new mtime even within the same clock tick
        os.utime(self.path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))

    def test_default_until_storage_then_created(self):
        self.assertEqual(self.amrs.ids(), ["AMR-01", "AMR-02", "AMR-03", "AMR-04"])
        self.amrs.poll(storage_available=False)
        self.assertFalse(os.path.exists(self.path))
        self.amrs.poll()
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(parse_list(f.read()), self.amrs.ids())
        self.assertEqual(self.seen, [])                  # same ids: no change to report

    def test_save_and_external_change(self):
        self.amrs.poll()
        self.assertEqual(self.amrs.save(["X1", "X2"]), ["X1", "X2"])
        self.assertEqual(self.seen, [["X1", "X2"]])
        self.amrs.poll()                                 # our own write is not reloaded
        self.assertEqual(len(self.seen), 1)
        with self.assertRaises(AmrListError):
            self.amrs.save(["ok", "not ok"])
        self.assertEqual(self.amrs.ids(), ["X1", "X2"])  # nothing written
        self.write("Y1\nY2\nY3\n")                       # Manage app data / a loaded configuration
        self.amrs.poll(paused=True)
        self.assertEqual(self.amrs.ids(), ["X1", "X2"])  # not while a configuration is being loaded
        self.amrs.poll()
        self.assertEqual(self.amrs.ids(), ["Y1", "Y2", "Y3"])
        self.assertEqual(self.amrs.status(), {"file": FILE_NAME, "count": 3, "error": None})

    def test_invalid_external_change_keeps_the_list(self):
        self.amrs.poll()
        self.write("Y1\nbad id\n")
        with self.assertLogs("amr-list", "ERROR"):
            self.amrs.poll()
        self.assertEqual(self.amrs.ids(), ["AMR-01", "AMR-02", "AMR-03", "AMR-04"])
        self.assertIn("line 2", self.amrs.status()["error"])
        self.amrs.poll()                                 # logged once per bad version
        time.sleep(0.01)
        self.write("Y1\n")
        self.amrs.poll()
        self.assertEqual((self.amrs.ids(), self.amrs.status()["error"]), (["Y1"], None))

    def test_missing_file_recreated_from_the_default(self):
        self.amrs.poll()
        self.amrs.save(["X1"])
        os.unlink(self.path)                             # e.g. a loaded configuration without a list
        self.amrs.poll()
        self.assertEqual(self.amrs.ids(), ["AMR-01", "AMR-02", "AMR-03", "AMR-04"])
        self.assertTrue(os.path.exists(self.path))


if __name__ == "__main__":
    unittest.main()
