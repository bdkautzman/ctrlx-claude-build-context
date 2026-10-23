"""Occupancy map of the AMR environment, loaded from a point-list file - pure Python, no ctrlX.

File format (e.g. defaults/maps/HMI_Map.txt, an export of a lidar map):
  one occupied cell per line, "<x><sep><y>[<sep>anything]"; sep = tab, ';', ',' or spaces.
  Decimal commas work with tab/';' separators ("1,25;3,5"). Lines that don't start with two
  numbers (headers, comments) are skipped, extra columns are ignored. Coordinates are in map
  units - the same units as the AMR positions - and every point is the CENTER of a cell.

Grid: `resolution` = cell size in map units (0 = automatic: greatest common divisor of the
coordinate offsets). Column i = round((x - min_x) / res), row j = round((max_y - y) / res), so
row 0 is the top of the picture (largest y). Edges: left = min_x - res/2, top = max_y + res/2.

Tiles for the web UI: level 0 has one pixel per cell, every further level halves the size
(a pixel is occupied when any of its cells is). The last level fits into one tile.
Tiles are TILE_SIZE x TILE_SIZE PNGs, 1 bit/pixel, palette (0 = transparent, 1 = color).
Levels are built on first use from the cell list, tiles are cached (LRU).
"""

import hashlib
import math
import re
import struct
import threading
import zlib
from array import array
from collections import OrderedDict

TILE_SIZE = 256
MAX_CELLS = 100_000_000           # 10000 x 10000 cells -> 12.5 MB bitmap for level 0
MAX_FILE_BYTES = 64 * 1024 * 1024
TILE_CACHE_ENTRIES = 512
_FIELD_SPLIT = re.compile(r"[\t; ]+")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class MapError(Exception):
    """The map file can't be used (message is shown in the UI and the Data Layer)."""


def parse_points(text):
    """Return (xs, ys) as array('d') from the point-list text."""
    xs, ys = array("d"), array("d")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        fields = _FIELD_SPLIT.split(line)
        if len(fields) < 2:
            fields = line.split(",")
            if len(fields) < 2:
                continue
        try:
            x = float(fields[0].replace(",", "."))
            y = float(fields[1].replace(",", "."))
        except ValueError:
            continue
        if math.isfinite(x) and math.isfinite(y):
            xs.append(x)
            ys.append(y)
    return xs, ys


def _scaled_offset_gcd(xs, ys, scale):
    """gcd of all offsets from the minimum, after multiplying by scale; None if not integers."""
    g = 0
    for values in (xs, ys):
        base = round(min(values) * scale)
        for v in values:
            scaled = v * scale
            k = round(scaled)
            if abs(scaled - k) > 1e-6 * max(1.0, abs(scaled)):
                return None
            g = math.gcd(g, k - base)
    return g


def auto_resolution(xs, ys):
    """Cell size = gcd of all offsets from the minimum, for coordinates with <= 4 decimals."""
    for scale in (1, 10, 100, 1000, 10000):
        g = _scaled_offset_gcd(xs, ys, scale)
        if g is not None:
            return g / scale if g else 1.0 / scale
    raise MapError("cannot determine the cell size automatically - set map.resolution")


def _png(width, height, rows, rgb):
    """PNG, 1 bit per pixel, palette [transparent, rgb]; rows = bytes of ceil(width/8) each."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"".join(b"\x00" + row for row in rows)
    return (_PNG_SIGNATURE
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 1, 3, 0, 0, 0))
            + chunk(b"PLTE", b"\x00\x00\x00" + bytes(rgb))
            + chunk(b"tRNS", b"\x00")
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b""))


class OccupancyMap:
    def __init__(self, xs, ys, resolution=0.0, name="", digest=""):
        if not xs:
            raise MapError("the map file contains no points")
        res = float(resolution) if resolution and resolution > 0 else auto_resolution(xs, ys)
        min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
        width = int(round((max_x - min_x) / res)) + 1
        height = int(round((max_y - min_y) / res)) + 1
        if width * height > MAX_CELLS:
            raise MapError("map has {} x {} cells at resolution {:g} (max. {} cells) - set a larger "
                           "map.resolution".format(width, height, res, MAX_CELLS))
        cols, rows = array("I"), array("I")
        for x, y in zip(xs, ys):
            cols.append(int(round((x - min_x) / res)))
            rows.append(int(round((max_y - y) / res)))
        self.name = name
        self.resolution = res
        self.min_x, self.max_x, self.min_y, self.max_y = min_x, max_x, min_y, max_y
        self.width, self.height = width, height
        self.points = len(xs)
        self._cols, self._rows = cols, rows
        levels = 1
        while (width - 1) >> (levels - 1) >= TILE_SIZE or (height - 1) >> (levels - 1) >= TILE_SIZE:
            levels += 1
        self.levels = levels
        self.id = hashlib.sha1("{}|{!r}".format(digest, res).encode()).hexdigest()[:12]
        self._bitmaps = {}
        self._tiles = OrderedDict()
        self._lock = threading.Lock()

    @classmethod
    def from_text(cls, text, resolution=0.0, name=""):
        xs, ys = parse_points(text)
        digest = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()
        return cls(xs, ys, resolution, name, digest)

    @classmethod
    def from_file(cls, path, resolution=0.0, name=""):
        with open(path, "rb") as f:
            data = f.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise MapError("map file larger than {} MB".format(MAX_FILE_BYTES // (1024 * 1024)))
        return cls.from_text(data.decode("utf-8-sig", "replace"), resolution, name)

    # -- geometry ---------------------------------------------------------------------
    def level_size(self, level):
        """(width, height) in pixels of a level."""
        return ((self.width - 1) >> level) + 1, ((self.height - 1) >> level) + 1

    def tile_count(self, level):
        w, h = self.level_size(level)
        return (w + TILE_SIZE - 1) // TILE_SIZE, (h + TILE_SIZE - 1) // TILE_SIZE

    def meta(self):
        res = self.resolution
        return {
            "id": self.id, "name": self.name, "resolution": res,
            "left": self.min_x - res / 2, "top": self.max_y + res / 2,
            "right": self.max_x + res / 2, "bottom": self.min_y - res / 2,
            "width": self.width, "height": self.height, "points": self.points,
            "tileSize": TILE_SIZE, "levels": self.levels,
        }

    def cells(self):
        """Iterate the (col, row) of every point (duplicates possible)."""
        return zip(self._cols, self._rows)

    # -- bitmaps and tiles ----------------------------------------------------------------
    def _bitmap(self, level):
        with self._lock:
            found = self._bitmaps.get(level)
        if found:
            return found
        w, h = self.level_size(level)
        stride = (w + 7) >> 3
        bits = bytearray(stride * h)
        for c, r in zip(self._cols, self._rows):
            c >>= level
            bits[(r >> level) * stride + (c >> 3)] |= 0x80 >> (c & 7)
        with self._lock:
            self._bitmaps.setdefault(level, (stride, bits))
            return self._bitmaps[level]

    def tile_png(self, level, tx, ty, rgb=(0x33, 0x3d, 0x4a)):
        """PNG bytes of one tile, or None if level/tile is outside the map."""
        if not 0 <= level < self.levels:
            return None
        ntx, nty = self.tile_count(level)
        if not (0 <= tx < ntx and 0 <= ty < nty):
            return None
        key = (level, tx, ty, tuple(rgb))
        with self._lock:
            png = self._tiles.get(key)
            if png is not None:
                self._tiles.move_to_end(key)
                return png
        stride, bits = self._bitmap(level)
        _, h = self.level_size(level)
        row_bytes = TILE_SIZE // 8
        empty = bytes(row_bytes)
        b0 = tx * row_bytes
        rows = []
        for r in range(ty * TILE_SIZE, (ty + 1) * TILE_SIZE):
            if r < h:
                row = bytes(bits[r * stride + b0: r * stride + min(stride, b0 + row_bytes)])
                rows.append(row + empty[len(row):])
            else:
                rows.append(empty)
        png = _png(TILE_SIZE, TILE_SIZE, rows, rgb)
        with self._lock:
            self._tiles[key] = png
            while len(self._tiles) > TILE_CACHE_ENTRIES:
                self._tiles.popitem(last=False)
        return png
