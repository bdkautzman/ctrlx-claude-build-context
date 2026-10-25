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
import os
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
# fast path for files without commas: the first two number-like fields of every line in one C-level
# regex pass; float() validates them (anything odd -> the line-by-line path)
_NUMBERISH = r"[-+.\d][-+.\deE]*"
_FAST_LINE = re.compile(r"^[ \t]*(" + _NUMBERISH + r")[\t; ]+(" + _NUMBERISH + r")", re.M)
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# prepare(): one byte of a level = 8 pixels -> 4 pixels (pairs OR-ed) of the next level, as a nibble
_PAIRS = [(((b & 0xC0) != 0) << 3) | (((b & 0x30) != 0) << 2) | (((b & 0x0C) != 0) << 1) | ((b & 0x03) != 0)
          for b in range(256)]
_PAIRS_HIGH = bytes(v << 4 for v in _PAIRS)   # for the even bytes of a row
_PAIRS_LOW = bytes(_PAIRS)                     # for the odd bytes


class MapError(Exception):
    """The map file can't be used (message is shown in the UI and the Data Layer)."""


def parse_points(text):
    """Return (xs, ys) as array('d') from the point-list text."""
    if "," not in text:   # no decimal commas possible: regex passes (several times faster)
        try:
            xs, ys = _parse_fast(text)
        except ValueError:
            pass              # e.g. "1.2.3" or a lone "-": the careful path below skips such lines
        else:
            if all(map(math.isfinite, xs)) and all(map(math.isfinite, ys)):
                return xs, ys
            keep = [i for i in range(len(xs)) if math.isfinite(xs[i]) and math.isfinite(ys[i])]
            return array("d", (xs[i] for i in keep)), array("d", (ys[i] for i in keep))
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


def _parse_fast(text, chunk_chars=256 * 1024):
    """The regex pass in chunks of whole lines: all 184k lines at once as Python tuples/strings/floats
    peaked at 39 MB (Python keeps that memory afterwards, gotchas #59); 256 KB chunks: 7 MB."""
    xs, ys = array("d"), array("d")
    pos, end_of_text = 0, len(text)
    while pos < end_of_text:
        end = text.find("\n", pos + chunk_chars)
        end = end_of_text if end < 0 else end + 1
        pairs = _FAST_LINE.findall(text, pos, end)
        xs.extend([float(p[0]) for p in pairs])
        ys.extend([float(p[1]) for p in pairs])
        pos = end
    return xs, ys


def _halve(stride, bits, height, width2, height2):
    """Bitmap of the next level (half size, a pixel is set if any of its 2 x 2 pixels is) from the bytes
    of this one - row by row with C-level operations, no Python object per pixel."""
    stride2 = (width2 + 7) >> 3      # == ceil(stride / 2): the even bytes of a row
    out = bytearray(stride2 * height2)
    for r in range(height2):
        top = bits[2 * r * stride:(2 * r + 1) * stride]
        if 2 * r + 1 < height:       # OR of the two rows
            bottom = bits[(2 * r + 1) * stride:(2 * r + 2) * stride]
            top = (int.from_bytes(top, "big") | int.from_bytes(bottom, "big")).to_bytes(stride, "big")
        high = bytes(top[0::2]).translate(_PAIRS_HIGH)
        low = bytes(top[1::2]).translate(_PAIRS_LOW).ljust(stride2, b"\0")
        out[r * stride2:(r + 1) * stride2] = (int.from_bytes(high, "big") | int.from_bytes(low, "big")).to_bytes(stride2, "big")
    return stride2, out


def _scaled_offset_gcd(xs, ys, scale):
    """gcd of all offsets from the minimum, after multiplying by scale; None if not integers.
    xs, ys: the distinct values are enough (a big map has ~100x fewer distinct values than points)."""
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
    xs, ys = set(xs), set(ys)
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
        # cell of every point via lookup tables of the distinct values (far fewer than points)
        col_of = {x: int(round((x - min_x) / res)) for x in set(xs)}
        row_of = {y: int(round((max_y - y) / res)) for y in set(ys)}
        cols = array("I", map(col_of.__getitem__, xs))
        rows = array("I", map(row_of.__getitem__, ys))
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
        self._build_lock = threading.Lock()   # one bitmap build at a time (prepare() vs. tile requests)
        self.prepared = False

    @classmethod
    def from_text(cls, text, resolution=0.0, name="", digest=None):
        xs, ys = parse_points(text)
        if digest is None:
            digest = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()
        return cls(xs, ys, resolution, name, digest)

    @classmethod
    def from_file(cls, path, resolution=0.0, name=""):
        with open(path, "rb") as f:
            # check the size first: f.read(limit) reserves `limit` bytes up front (64 MB for a 3 MB map)
            if os.fstat(f.fileno()).st_size > MAX_FILE_BYTES:
                raise MapError("map file larger than {} MB".format(MAX_FILE_BYTES // (1024 * 1024)))
            data = f.read()
        digest = hashlib.sha1(data).hexdigest()   # of the bytes: no extra copy of the text for it
        text = data.decode("utf-8-sig", "replace")
        del data
        return cls.from_text(text, resolution, name, digest)

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
        with self._build_lock:
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
                self._bitmaps[level] = (stride, bits)
                return self._bitmaps[level]

    def prepare(self):
        """Build the bitmaps of all levels ahead of the first tile request (MapSource runs it in the
        background after loading): level 0 from the points, every further level from the bytes of the one
        below (_halve) - fast and without a Python object per point (memory)."""
        stride, bits = self._bitmap(0)
        for level in range(1, self.levels):
            with self._lock:
                done = self._bitmaps.get(level)
            if done:
                stride, bits = done
                continue
            w1, h1 = self.level_size(level - 1)
            w2, h2 = self.level_size(level)
            with self._build_lock:
                stride, bits = _halve(stride, bits, h1, w2, h2)
                with self._lock:
                    self._bitmaps.setdefault(level, (stride, bits))
        self.prepared = True

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
