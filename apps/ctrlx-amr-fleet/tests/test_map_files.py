"""Map files for the settings drop-down: listing and uploads into the app data (no ctrlX needed)."""

import io
import os
import tempfile
import unittest
from unittest import mock

from app import map_files
from app.map_files import MapFileError, MapFiles

MAP = b"x\ty\n0\t0\n20\t0\n40\t0\n0\t20\n"


class MapFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.appdata = os.path.join(self.tmp.name, "appdata")
        self.bundled = os.path.join(self.tmp.name, "bundled")
        os.makedirs(self.bundled)
        for name in ("HMI_Map.txt", "Shared.txt", "notes.md"):
            with open(os.path.join(self.bundled, name), "wb") as f:
                f.write(MAP)
        self.files = MapFiles([("app data", self.appdata), ("bundled", self.bundled)], self.appdata,
                              not_maps=("amr-list.txt",))

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, name, data, replace=False):
        return self.files.save(name, io.BytesIO(data).read, len(data), replace)

    def test_list_without_app_data(self):
        """No app data folder yet: the bundled maps only, no other file types."""
        self.assertEqual([(m["name"], m["source"]) for m in self.files.list_maps()],
                         [("HMI_Map.txt", "bundled"), ("Shared.txt", "bundled")])

    def test_upload_lists_once_with_the_file_mapsource_loads(self):
        entry = self.save("Shared.txt", MAP + b"60\t0\n")
        self.assertEqual((entry["name"], entry["source"], entry["size"]), ("Shared.txt", "app data", len(MAP) + 5))
        self.save("big-hall_2.TXT", MAP)
        with open(os.path.join(self.appdata, "amr-list.txt"), "w") as f:
            f.write("AMR-01\n")                      # not a map
        listed = [(m["name"], m["source"]) for m in self.files.list_maps()]
        self.assertEqual(listed, [("big-hall_2.TXT", "app data"), ("HMI_Map.txt", "bundled"), ("Shared.txt", "app data")])
        self.assertEqual(sorted(os.listdir(self.appdata)), ["Shared.txt", "amr-list.txt", "big-hall_2.TXT"])  # no .tmp

    def test_replace_needs_the_flag(self):
        self.save("a.txt", MAP)
        with self.assertRaises(MapFileError) as cm:
            self.save("a.txt", MAP + b"60\t0\n")
        self.assertEqual(cm.exception.status, 409)
        self.save("a.txt", MAP + b"60\t0\n", replace=True)
        with open(os.path.join(self.appdata, "a.txt"), "rb") as f:
            self.assertTrue(f.read().endswith(b"60\t0\n"))

    def test_rejected_uploads_store_nothing(self):
        cases = [("../evil.txt", MAP, 400), ("map.csv", MAP, 400), (".hidden.txt", MAP, 400),
                 ("amr-list.txt", MAP, 400), ("with space.txt", MAP, 400), ("empty.txt", b"", 400),
                 ("binary.txt", b"0\t0\n\0\0\0", 400), ("words.txt", b"hello\nworld\n", 400)]
        for name, data, status in cases:
            with self.subTest(name=name), self.assertRaises(MapFileError) as cm:
                self.save(name, data)
            self.assertEqual(cm.exception.status, status)
        self.assertFalse(os.path.exists(self.appdata) and os.listdir(self.appdata))

    def test_too_large_is_refused_before_reading(self):
        read = mock.Mock(side_effect=AssertionError("must not read"))
        with mock.patch.object(map_files, "MAX_FILE_BYTES", 100):
            with self.assertRaises(MapFileError) as cm:
                self.files.save("big.txt", read, 101)
        self.assertEqual(cm.exception.status, 413)

    def test_incomplete_upload_is_dropped(self):
        with self.assertRaises(MapFileError) as cm:
            self.files.save("cut.txt", io.BytesIO(MAP).read, len(MAP) + 50)
        self.assertIn("incomplete", str(cm.exception))
        self.assertEqual(os.listdir(self.appdata), [])

    def test_big_file_streams_in_chunks_and_checks_the_head(self):
        """Chunks of CHUNK_BYTES; only the first HEAD_BYTES are parsed (a cut last line does not count)."""
        lines = b"".join(b"%d\t%d\n" % (i * 20, (i % 50) * 20) for i in range(30000))
        reads = []
        stream = io.BytesIO(lines)

        def read(n):
            reads.append(n)
            return stream.read(n)
        with mock.patch.object(map_files, "CHUNK_BYTES", 64 * 1024):
            entry = self.files.save("long.txt", read, len(lines))
        self.assertEqual(entry["size"], len(lines))
        self.assertGreater(len(reads), 3)
        self.assertTrue(all(n <= 64 * 1024 for n in reads))


if __name__ == "__main__":
    unittest.main()
