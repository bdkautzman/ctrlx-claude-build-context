#!/usr/bin/env python3
"""Generate a large synthetic occupancy map for performance tests, in the format of HMI_Map.txt.

    python3 scripts/make_large_map.py OUT.txt [--seed 7] [--preview PREFIX]

Format (like the lidar export HMI_Map.txt): one occupied 20 mm cell per line, "x<TAB>y" in mm plus five
more tab fields (empty, empty, empty, label, value; lines 2-9 carry the Min/Max/Range/Center statistics of
the spreadsheet export, including its quirks), CRLF, sorted by x then y. Only outlines are occupied, as a
2D lidar sees them (faces of racks, walls, machines), with a little dropout, jitter and noise.

Layout, 104 m x 178 m (5200 x 8900 cells = 10 x 10 the HMI map), y up:
  y   0 -  89 m  warehouse shelving: 16 double rack rows (2 x 1.1 m deep, 3.5 m aisles), bays of 2.7 m with
                 uprights and pallet fronts (some bays empty), cross aisles at the ends and in the middle
  y  89 - 133 m  production: semi-irregular obstacles - rotated machines, L-shapes, irregular polygons,
                 tanks, pallet clusters, work bench rows; partition wall with openings to the racks
  y 133 - 178 m  open area: a few large obstacles (office block, silo, conveyor, trailers), building
                 columns, dock doors in the top wall
Deterministic for a given seed.
"""

import argparse
import math
import random
import struct
import zlib

CELL_MM = 20
W_CELLS, H_CELLS = 5200, 8900
X0_MM, Y0_MM = -1000, 20000          # map origin like a real export (not at 0, 0)
M = 1000 // CELL_MM                  # cells per meter


class Grid:
    def __init__(self, rng):
        self.bits = bytearray(W_CELLS * H_CELLS)
        self.rng = rng

    def set(self, c, r):
        if 0 <= c < W_CELLS and 0 <= r < H_CELLS:
            self.bits[r * W_CELLS + c] = 1

    def occupied(self):
        """Indexes of the occupied cells (bytearray.find runs in C - 46 M cells are too many for a loop)."""
        out, i, find = [], self.bits.find(1), self.bits.find
        while i >= 0:
            out.append(i)
            i = find(1, i + 1)
        return out

    def point(self, x, y):
        """x, y in meters."""
        self.set(int(round(x * M)), int(round(y * M)))

    def line(self, x1, y1, x2, y2, dropout=0.01, jitter=0.15):
        """Lidar-like line in meters: 1 cell wide, a few missing cells, an occasional 1-cell offset."""
        c1, r1, c2, r2 = (int(round(v * M)) for v in (x1, y1, x2, y2))
        n = max(abs(c2 - c1), abs(r2 - r1), 1)
        rng = self.rng
        for i in range(n + 1):
            if rng.random() < dropout:
                continue
            c = c1 + round((c2 - c1) * i / n)
            r = r1 + round((r2 - r1) * i / n)
            if rng.random() < jitter:
                if abs(c2 - c1) >= abs(r2 - r1):
                    r += rng.choice((-1, 1))
                else:
                    c += rng.choice((-1, 1))
            self.set(c, r)

    def polygon(self, pts, **kw):
        for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
            self.line(x1, y1, x2, y2, **kw)

    def rect(self, cx, cy, w, h, angle=0.0, gaps=(), **kw):
        """Outline of a w x h rectangle centred at cx, cy, rotated by angle (rad). gaps: edge indexes to skip."""
        ca, sa = math.cos(angle), math.sin(angle)
        corners = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
        pts = [(cx + x * ca - y * sa, cy + x * sa + y * ca) for x, y in corners]
        for i, ((x1, y1), (x2, y2)) in enumerate(zip(pts, pts[1:] + pts[:1])):
            if i not in gaps:
                self.line(x1, y1, x2, y2, **kw)

    def square(self, x, y, size_cells=3):
        c, r = int(round(x * M)), int(round(y * M))
        for dc in range(size_cells):
            for dr in range(size_cells):
                self.set(c + dc - size_cells // 2, r + dr - size_cells // 2)

    def circle(self, cx, cy, radius, dropout=0.01):
        steps = max(16, int(2 * math.pi * radius * M))
        for i in range(steps):
            if self.rng.random() < dropout:
                continue
            a = 2 * math.pi * i / steps
            self.point(cx + radius * math.cos(a), cy + radius * math.sin(a))

    def column(self, x, y, size=0.4):
        self.rect(x, y, size, size, jitter=0.0, dropout=0.0)


def building(g):
    """Outer walls with a few jogs and doors."""
    w = g.line
    # bottom wall with two personnel doors
    w(0.5, 0.5, 30, 0.5); w(31.2, 0.5, 70, 0.5); w(71.2, 0.5, 103.5, 0.5)
    # right wall with a jog (stair core) and a door
    w(103.5, 0.5, 103.5, 60); w(103.5, 60, 101.0, 60); w(101.0, 60, 101.0, 66); w(101.0, 66, 103.5, 66)
    w(103.5, 66, 103.5, 110); w(103.5, 111.2, 103.5, 177.5)
    # top wall: dock doors (3 m openings every 7 m) with bumper blobs
    x = 0.5
    for dock in range(11):
        start = 8 + dock * 7.5
        w(x, 177.5, start, 177.5)
        g.square(start - 0.2, 177.3, 4)
        g.square(start + 3.2, 177.3, 4)
        x = start + 3.0
    w(x, 177.5, 103.5, 177.5)
    # left wall with a jog (old annex) and a fire exit
    w(0.5, 177.5, 0.5, 140); w(0.5, 140, 3.0, 140); w(3.0, 140, 3.0, 128); w(3.0, 128, 0.5, 128)
    w(0.5, 128, 0.5, 45); w(0.5, 43.8, 0.5, 0.5)


def shelving(g, rng):
    """y 0.5 - 89 m: double rack rows along y."""
    depth, flue, aisle, bay = 1.1, 0.3, 3.5, 2.7
    pitch = 2 * depth + flue + aisle
    runs = [(5.0, 42.0), (48.0, 84.0)]            # rack runs between cross aisles
    x = 4.0
    while x + 2 * depth + flue < 100.0:
        faces = [(x, -1), (x + 2 * depth + flue, +1)]   # aisle-facing faces of the pair (sign: outward)
        for y_start, y_end in runs:
            n_bays = int((y_end - y_start) / bay)
            y_end_real = y_start + n_bays * bay
            for fx, out in faces:
                for b in range(n_bays + 1):          # uprights
                    g.square(fx, y_start + b * bay, 3)
                for b in range(n_bays):              # pallet fronts, 3 per bay, some missing
                    for p in range(3):
                        if rng.random() < 0.18:
                            continue
                        py = y_start + b * bay + 0.1 + p * 0.85
                        inset = -out * rng.uniform(0.02, 0.12)   # pallets don't sit exactly on the face
                        g.line(fx + inset, py, fx + inset, py + 0.78, dropout=0.03, jitter=0.1)
            for ye in (y_start, y_end_real):         # end frames + guard rails at both ends of the run
                g.line(x, ye, x + 2 * depth + flue, ye, jitter=0.0)
                guard = -0.25 if ye == y_start else 0.25
                g.line(x - 0.1, ye + guard, x + 2 * depth + flue + 0.1, ye + guard, dropout=0.05)
        x += pitch
    # a few things standing in the cross aisles: fire hose cabinets, a parked pallet jack, bins
    for _ in range(14):
        cx = rng.uniform(5, 99)
        cy = rng.choice((2.5, 45.0, 86.5)) + rng.uniform(-0.6, 0.6)
        g.rect(cx, cy, rng.uniform(0.5, 1.4), rng.uniform(0.4, 0.8), rng.uniform(0, math.pi))


def far_enough(placed, cx, cy, r, spacing):
    return all(math.hypot(cx - px, cy - py) >= r + pr + spacing for px, py, pr in placed)


def production(g, rng):
    """y 89 - 133 m: semi-irregular obstacles, kept apart enough to drive between them."""
    # partition to the racks: wall with three wide openings
    for x1, x2 in ((0.5, 18), (28, 48), (58, 80), (90, 103.5)):
        g.line(x1, 89.0, x2, 89.0, jitter=0.05)
    placed = []
    for col_x in range(12, 100, 12):                 # building columns
        for col_y in (100.0, 122.0):
            g.column(col_x, col_y)
            placed.append((col_x, col_y, 0.4))
    kinds = ["machine"] * 22 + ["lshape"] * 8 + ["blob"] * 14 + ["tank"] * 8 + ["pallets"] * 12 + ["benches"] * 6
    rng.shuffle(kinds)
    for kind in kinds:
        for _ in range(200):                         # rejection sampling: keep >= 2.2 m between objects
            size = {"machine": rng.uniform(1.5, 4.5), "lshape": rng.uniform(2.5, 4.5), "blob": rng.uniform(1.0, 3.0),
                    "tank": rng.uniform(0.6, 1.8), "pallets": 2.0, "benches": 3.5}[kind]
            cx, cy = rng.uniform(3 + size, 101 - size), rng.uniform(91.5 + size, 131 - size)
            if far_enough(placed, cx, cy, size, 2.2):
                break
        else:
            continue
        placed.append((cx, cy, size))
        angle = rng.uniform(-0.35, 0.35) if rng.random() < 0.6 else rng.uniform(0, math.pi)
        if kind == "machine":
            w, h = size * rng.uniform(1.0, 1.6), size * rng.uniform(0.5, 1.0)
            g.rect(cx, cy, w, h, angle)
            if rng.random() < 0.5:                   # control cabinet next to it
                g.rect(cx + math.cos(angle) * (w / 2 + 0.5), cy + math.sin(angle) * (w / 2 + 0.5), 0.6, 0.8, angle)
        elif kind == "lshape":
            a, b = size, size * 0.45
            pts = [(0, 0), (a, 0), (a, b), (b, b), (b, a), (0, a)]
            ca, sa = math.cos(angle), math.sin(angle)
            g.polygon([(cx + (x - a / 2) * ca - (y - a / 2) * sa, cy + (x - a / 2) * sa + (y - a / 2) * ca) for x, y in pts])
        elif kind == "blob":                         # irregular convex-ish polygon (e.g. material heap)
            n = rng.randint(6, 11)
            pts = []
            for i in range(n):
                a = 2 * math.pi * i / n + rng.uniform(-0.2, 0.2)
                rr = size * rng.uniform(0.55, 1.0)
                pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
            g.polygon(pts, jitter=0.3, dropout=0.04)
        elif kind == "tank":
            g.circle(cx, cy, size)
            if rng.random() < 0.5:
                g.circle(cx, cy, size * 0.3)
        elif kind == "pallets":                      # a few pallets dropped at odd angles
            for _ in range(rng.randint(3, 7)):
                g.rect(cx + rng.uniform(-1.5, 1.5), cy + rng.uniform(-1.5, 1.5), 1.2, 0.8, rng.uniform(0, math.pi),
                       dropout=0.05)
        elif kind == "benches":                      # a row of work benches with legs only
            for i in range(4):
                bx = cx - 2.4 + i * 1.6
                for lx, ly in ((-0.6, -0.35), (0.6, -0.35), (-0.6, 0.35), (0.6, 0.35)):
                    g.square(bx + lx, cy + ly, 2)
    # a meandering safety fence piece
    x, y = rng.uniform(20, 30), rng.uniform(110, 115)
    for _ in range(6):
        nx, ny = x + rng.uniform(2, 4), y + rng.uniform(-2, 2)
        g.line(x, y, nx, ny, dropout=0.15)
        x, y = nx, ny


def open_area(g, rng):
    """y 133 - 178 m: open floor with a few large obstacles and sparse columns."""
    for col_x in range(12, 100, 24):
        for col_y in (145.0, 167.0):
            g.column(col_x, col_y, 0.5)
    g.rect(16.0, 160.0, 14.0, 8.0, 0.0, gaps=(0,))                 # office block, door in the bottom wall
    g.line(9.0, 156.0, 14.0, 156.0)
    g.line(15.2, 156.0, 23.0, 156.0)
    g.line(16.0, 156.0, 16.0, 160.5)                                 # inner walls
    g.line(16.0, 161.7, 16.0, 164.0)
    g.circle(52.0, 152.0, 4.0)                                       # silo
    g.circle(52.0, 152.0, 3.6, dropout=0.3)
    g.rect(78.0, 141.0, 30.0, 1.0, 0.0)                              # conveyor
    for i in range(10):                                              # its legs
        g.square(64.0 + i * 3.2, 140.3, 2)
    g.rect(84.0, 165.0, 13.6, 2.6, math.pi / 2 - 0.05)              # two parked trailers
    g.rect(90.0, 165.0, 13.6, 2.6, math.pi / 2 + 0.03)
    g.rect(36.0, 170.0, 6.0, 4.0, 0.3, gaps=(2,))                   # charging station enclosure, open side
    keepout = [(9, 23, 156, 164), (47, 57, 147, 157), (62, 94, 139.5, 142.5), (82, 92.5, 157.5, 172.5),
               (32, 40, 166.5, 173.5)]                               # the obstacles above, x0 x1 y0 y1
    placed = 0
    while placed < 25:                                               # a few small things standing around
        x, y = rng.uniform(5, 99), rng.uniform(136, 175)
        if any(x0 - 1.5 < x < x1 + 1.5 and y0 - 1.5 < y < y1 + 1.5 for x0, x1, y0, y1 in keepout):
            continue
        g.rect(x, y, 1.2, 0.8, rng.uniform(0, math.pi), dropout=0.05)
        placed += 1


def noise(g, rng, count):
    for _ in range(count):
        g.point(rng.uniform(1, 103), rng.uniform(1, 177))


def write_map(g, path):
    pts = sorted((X0_MM + (i % W_CELLS) * CELL_MM, Y0_MM + (i // W_CELLS) * CELL_MM) for i in g.occupied())
    min_x, max_x = min(p[0] for p in pts), max(p[0] for p in pts)
    min_y, max_y = min(p[1] for p in pts), max(p[1] for p in pts)
    stats = [("Min X", min_x), ("Max X", max_x), ("Range X", min_x - max_x), ("Center X ", (min_x + max_x) // 2),
             ("Min Y", min_y), ("Max Y", max_y), ("Range Y", max_y - min_y), ("Center Y ", (min_y + max_y) // 2)]
    with open(path, "w", newline="") as f:
        for n, (x, y) in enumerate(pts):
            label, value = stats[n - 1] if 1 <= n <= len(stats) else ("", "")
            f.write("{}\t{}\t\t\t\t{}\t{}\r\n".format(x, y, label, value))
    return len(pts), (min_x, max_x, min_y, max_y)


def write_png(path, width, height, rows):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"".join(b"\x00" + bytes(r) for r in rows)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def previews(g, prefix):
    """Overview (1 px = 4 x 4 cells, occupied if any is) and full-resolution close-ups of each zone."""
    k = 4
    w, h = W_CELLS // k, H_CELLS // k
    rows = [bytearray([255]) * w for _ in range(h)]
    bits = g.bits
    for i in g.occupied():
        c, r = i % W_CELLS, i // W_CELLS
        rows[h - 1 - min(h - 1, r // k)][min(w - 1, c // k)] = 0
    write_png(prefix + "-overview.png", w, h, rows)
    crops = {"shelving": (20, 30, 30, 22), "production": (30, 100, 30, 22), "open": (40, 140, 30, 22)}
    for name, (x, y, cw, ch) in crops.items():   # meters: lower-left corner, width, height
        c0, r0, cw, ch = int(x * M), int(y * M), int(cw * M), int(ch * M)
        rows = []
        for r in range(r0 + ch - 1, r0 - 1, -1):
            row = bytearray([255]) * cw
            base = r * W_CELLS + c0
            for c in range(cw):
                if bits[base + c]:
                    row[c] = 0
            rows.append(row)
        write_png("{}-{}.png".format(prefix, name), cw, ch, rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--preview", help="also write PNG previews: PREFIX-overview.png, PREFIX-<zone>.png")
    args = ap.parse_args()
    rng = random.Random(args.seed)
    g = Grid(rng)
    building(g)
    shelving(g, rng)
    production(g, rng)
    open_area(g, rng)
    noise(g, rng, 3000)
    n, (min_x, max_x, min_y, max_y) = write_map(g, args.out)
    print("{}: {} points, x {}..{} mm, y {}..{} mm ({:.1f} x {:.1f} m)".format(
        args.out, n, min_x, max_x, min_y, max_y, (max_x - min_x) / 1000, (max_y - min_y) / 1000))
    if args.preview:
        previews(g, args.preview)


if __name__ == "__main__":
    main()
