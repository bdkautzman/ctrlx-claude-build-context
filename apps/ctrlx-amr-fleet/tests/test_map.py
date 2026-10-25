"""Occupancy map parsing, tiles (decoded PNGs) and the map file source."""

import os
import struct
import tempfile
import time
import unittest
import zlib

from app import config
from app.map_source import MapSource
from app.occupancy_map import TILE_SIZE, MapError, OccupancyMap, auto_resolution, parse_points


def decode_png_1bit(png):
    """Minimal decoder for our own tiles: returns (width, height, palette, rows of 0/1 lists)."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pos, chunks = 8, {}
    while pos < len(png):
        length, kind = struct.unpack(">I4s", png[pos:pos + 8])
        chunks.setdefault(kind, b"")
        chunks[kind] += png[pos + 8:pos + 8 + length]
        pos += 12 + length
    width, height, depth, ctype = struct.unpack(">IIBB", chunks[b"IHDR"][:10])
    assert (depth, ctype) == (1, 3)
    raw = zlib.decompress(chunks[b"IDAT"])
    stride = (width + 7) // 8
    rows = []
    for r in range(height):
        line = raw[r * (stride + 1):(r + 1) * (stride + 1)]
        assert line[0] == 0  # filter type none
        rows.append([(line[1 + c // 8] >> (7 - c % 8)) & 1 for c in range(width)])
    return width, height, chunks[b"PLTE"], chunks[b"tRNS"], rows


class ParseTest(unittest.TestCase):
    def test_formats(self):
        text = ("x\ty\theader\n-860\t39360\t\t\t\tMin X\t-860\n"
                "1,5;2,5\n3.5,4.5\n  7 8  \n\nnot a number\n1e400\t1\n")
        xs, ys = parse_points(text)
        self.assertEqual(list(zip(xs, ys)), [(-860, 39360), (1.5, 2.5), (3.5, 4.5), (7, 8)])

    def test_auto_resolution(self):
        self.assertEqual(auto_resolution([0, 40, 100], [10, 30]), 20)
        self.assertEqual(auto_resolution([0.05, 0.10, 0.25], [1.0]), 0.05)
        self.assertEqual(auto_resolution([5], [5]), 1)
        with self.assertRaises(MapError):
            auto_resolution([0, 0.123456], [0])


class OccupancyMapTest(unittest.TestCase):
    def test_geometry_and_tile_bits(self):
        # points (cell centres) on a 10-unit grid; y up -> row 0 is the largest y
        m = OccupancyMap.from_text("0\t0\n10\t0\n0\t20\n30\t20\n")
        self.assertEqual((m.resolution, m.width, m.height, m.levels), (10, 4, 3, 1))
        meta = m.meta()
        self.assertEqual((meta["left"], meta["top"], meta["right"], meta["bottom"]), (-5, 25, 35, -5))
        w, h, plte, trns, rows = decode_png_1bit(m.tile_png(0, 0, 0, (1, 2, 3)))
        self.assertEqual((w, h), (TILE_SIZE, TILE_SIZE))
        self.assertEqual((plte, trns), (b"\x00\x00\x00\x01\x02\x03", b"\x00"))
        self.assertEqual([row[:4] for row in rows[:3]], [[1, 0, 0, 1], [0, 0, 0, 0], [1, 1, 0, 0]])
        self.assertEqual(sum(map(sum, rows)), 4)
        self.assertIsNone(m.tile_png(0, 1, 0))
        self.assertIsNone(m.tile_png(1, 0, 0))

    def test_levels_combine_cells(self):
        m = OccupancyMap.from_text("0\t0\n600\t0\n1200\t0\n0\t300", resolution=1)
        # 1201 x 301 cells -> levels until it fits into one 256 tile: 1201 -> 601 -> 301 -> 151
        self.assertEqual((m.width, m.height, m.levels), (1201, 301, 4))
        self.assertEqual(m.level_size(3), (151, 38))
        _, _, _, _, rows = decode_png_1bit(m.tile_png(3, 0, 0))
        self.assertEqual([c for c, v in enumerate(rows[37]) if v], [0, 75, 150])  # y=0 is the bottom row
        self.assertEqual([c for c, v in enumerate(rows[0]) if v], [0])
        self.assertEqual(m.tile_count(0), (5, 2))
        _, _, _, _, rows = decode_png_1bit(m.tile_png(0, 4, 1))  # last tile: cols 1024.., rows 256..
        self.assertEqual([c for c, v in enumerate(rows[300 - 256]) if v], [1200 - 1024])

    def test_errors(self):
        with self.assertRaises(MapError):
            OccupancyMap.from_text("header only\n")
        with self.assertRaises(MapError):
            OccupancyMap.from_text("0\t0\n100000\t100000\n", resolution=1)  # 1e10 cells

    def test_resolution_setting_and_id(self):
        a = OccupancyMap.from_text("0\t0\n100\t100\n")
        b = OccupancyMap.from_text("0\t0\n100\t100\n", resolution=50)
        self.assertEqual((a.width, b.width), (2, 3))
        self.assertNotEqual(a.id, b.id)
        self.assertEqual(a.id, OccupancyMap.from_text("0\t0\n100\t100\n").id)

    def test_bundled_map(self):
        m = OccupancyMap.from_file(os.path.join(config.BUNDLED_MAPS_DIR, "HMI_Map.txt"))
        self.assertEqual((m.resolution, m.width, m.height, m.points, m.levels), (20, 520, 890, 17015, 3))

    def test_prepare_builds_the_same_bitmaps(self):
        """prepare() derives each level from the one below - must equal building from all points."""
        m = OccupancyMap.from_file(os.path.join(config.BUNDLED_MAPS_DIR, "HMI_Map.txt"))
        reference = OccupancyMap.from_file(os.path.join(config.BUNDLED_MAPS_DIR, "HMI_Map.txt"))
        m.prepare()
        self.assertTrue(m.prepared)
        for level in range(m.levels):
            self.assertEqual(m._bitmaps[level], reference._bitmap(level), level)

    def test_fast_and_careful_parsing_agree(self):
        text = "x\ty\n1\t2\t\t\tMin X\t1\n 3 4 \n5;6\n1.2.3\t9\n-\t1\n+1.5e2\t-.5\n1e400\t1\n"
        fast = parse_points(text)                                  # no commas: regex path (falls back here)
        careful = parse_points(text.replace("\n", "\n,\n"))         # a comma forces the line-by-line path
        self.assertEqual(list(zip(*fast)), list(zip(*careful)))
        self.assertEqual(list(zip(*parse_points("1\t2\n+1.5e2\t-.5\n"))), [(1.0, 2.0), (150.0, -0.5)])


class MapSourceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.appdata = os.path.join(self.tmp.name, "appdata")
        self.bundled = os.path.join(self.tmp.name, "bundled")
        os.makedirs(self.appdata)
        os.makedirs(self.bundled)
        self.write(self.bundled, "m.txt", "0\t0\n10\t10\n")
        self.src = MapSource([("app data", self.appdata), ("bundled", self.bundled)], background=False)
        self.events = []
        self.src.add_listener(self.events.append)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, directory, name, text, mtime=None):
        path = os.path.join(directory, name)
        with open(path, "w") as f:
            f.write(text)
        if mtime:
            os.utime(path, (mtime, mtime))

    def test_lookup_reload_and_errors(self):
        self.src.configure("m.txt", 0)
        info = self.src.info()
        self.assertEqual((info["loaded"], info["source"], info["width"]), (True, "bundled", 2))
        self.write(self.appdata, "m.txt", "0\t0\n10\t0\n20\t0\n")  # app data wins over bundled
        self.src.poll()
        self.assertEqual((self.src.info()["source"], self.src.current().width), ("app data", 3))
        first = self.src.current()
        self.src.poll()                                           # unchanged: same object
        self.assertIs(self.src.current(), first)
        self.write(self.appdata, "m.txt", "broken\n", mtime=1)   # broken: keep the last good map
        self.src.poll()
        info = self.src.info()
        self.assertTrue(info["loaded"])
        self.assertIn("no points", info["error"])
        self.src.configure("other.txt", 0)                        # unknown file: no map
        info = self.src.info()
        self.assertFalse(info["loaded"])
        self.assertIn("not found", info["error"])
        self.src.configure("../m.txt", 0)                         # no paths
        self.assertFalse(self.src.info()["loaded"])
        self.assertGreaterEqual(len(self.events), 4)
        self.src.configure("", 0)
        self.assertIn("map.file", self.src.info()["error"])

    def test_background_load(self):
        """configure() answers at once; the previous map stays until the new one is loaded and
        prepared (all tile levels built)."""
        import threading
        from unittest import mock
        from app import occupancy_map
        src = MapSource([("bundled", self.bundled)])
        src.add_listener(self.events.append)
        src.configure("m.txt", 0)
        self.assertTrue(src.wait_idle(10))
        first = src.current()
        self.assertTrue(src.info()["prepared"])
        self.write(self.bundled, "m.txt", "0\t0\n10\t0\n20\t0\n", mtime=12345)
        release = threading.Event()
        real = occupancy_map.OccupancyMap.from_file

        def slow(*args, **kwargs):
            release.wait(10)
            return real(*args, **kwargs)
        with mock.patch.object(occupancy_map.OccupancyMap, "from_file", side_effect=slow):
            started = time.monotonic()
            src.poll()
            self.assertLess(time.monotonic() - started, 1.0)       # did not wait for the load
            info = src.info()
            self.assertTrue(info["loading"])
            self.assertIs(src.current(), first)                    # old map still shown
            self.assertFalse(src.wait_idle(0.2))
            release.set()
            self.assertTrue(src.wait_idle(10))
        info = src.info()
        self.assertEqual((info["loading"], info["prepared"], info["width"]), (False, True, 3))
        self.assertTrue(any(e["loading"] for e in self.events) and not self.events[-1]["loading"])


if __name__ == "__main__":
    unittest.main()
