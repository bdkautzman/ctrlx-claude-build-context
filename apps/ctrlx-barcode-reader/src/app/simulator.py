"""Test images for the simulated camera (camera.source = simulated).

Aravis' built-in fake camera delivers the frames (so trigger, stream and buffers run through Aravis like
with a real camera), but its test pattern holds no codes. Each fake frame's content is replaced by
  * the next image file from <app data>/test-images/ (PNG/JPEG/BMP, sorted by name), if there are any, or
  * a generated scene: simulation.codes random codes (zxing-cpp's barcode writer), slightly rotated, on a
    noisy gray background, as a BayerRG8 mosaic - the STC-MCS500POE's format, so the debayer path runs too.
The texts of a generated scene are known (Scene.expected), the unit tests compare them with the decoder.
"""

import logging
import math
import os
import random
from dataclasses import dataclass

from app import imaging
from app.decoder import zxing

log = logging.getLogger("simulator")

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".bmp")
# (setting name, zxingcpp format, text generator) - creatable formats with a readable size at scale 4-6
_KINDS = [
    ("QRCode", "QRCode", lambda r: "SIM-QR-{:05d}".format(r.randrange(100000))),
    ("DataMatrix", "DataMatrix", lambda r: "LOT{:06d}".format(r.randrange(1000000))),
    ("Code128", "Code128", lambda r: "C128-{:04d}".format(r.randrange(10000))),
    ("EAN13", "EAN13", lambda r: _ean13(r)),
    ("PDF417", "PDF417", lambda r: "PDF417 {:04d}".format(r.randrange(10000))),
    ("Aztec", "Aztec", lambda r: "AZ{:04d}".format(r.randrange(10000))),
]


def _ean13(r):
    digits = [r.randrange(10) for _ in range(12)]
    check = (10 - sum(d * (3 if i % 2 else 1) for i, d in enumerate(digits)) % 10) % 10
    return "".join(map(str, digits)) + str(check)


@dataclass
class Scene:
    frame: object            # imaging.Frame
    expected: list           # [(format, text)] of the generated codes; [] for image files
    source: str              # "generated" or the file name


class Simulator:
    def __init__(self, images_dir, seed=None):
        self.images_dir = images_dir
        self._rng = random.Random(seed)
        self._file_index = 0

    def image_files(self):
        try:
            names = sorted(n for n in os.listdir(self.images_dir) if n.lower().endswith(IMAGE_EXT))
        except OSError:
            return []
        return [os.path.join(self.images_dir, n) for n in names]

    def next_scene(self, width, height, codes, frame_id=0):
        files = self.image_files()
        if files:
            path = files[self._file_index % len(files)]
            self._file_index += 1
            try:
                with open(path, "rb") as f:
                    img = imaging.decode_image(f.read())
            except OSError as exc:
                img = None
                log.warning("Cannot read test image %s: %s", path, exc)
            if img is not None:
                fmt = "Mono8" if img.ndim == 2 else "BGR8"
                return Scene(imaging.Frame(img, fmt, frame_id=frame_id), [], os.path.basename(path))
            log.warning("Not an image: %s", path)
        return self.generate(width, height, codes, frame_id)

    def generate(self, width, height, codes, frame_id=0):
        cv2, np = imaging.cv()
        zx = zxing()
        r = self._rng
        gray = np.full((height, width), 0, np.uint8)
        gray[:] = r.randrange(150, 200)
        expected = []
        cols = max(1, math.ceil(math.sqrt(codes)))
        rows = max(1, -(-codes // cols))
        cell_w, cell_h = width // cols, height // rows
        kinds = r.sample(_KINDS, k=min(codes, len(_KINDS)))
        for i, (name, fmt, text_of) in enumerate(kinds):
            text = text_of(r)
            barcode = zx.create_barcode(text, getattr(zx.BarcodeFormat, fmt))
            h1, w1 = np.asarray(barcode.to_image(scale=1, add_quiet_zones=True)).shape
            # whole pixels per module, as large as fits the cell (also rotated), at most 6; never scaled
            # down afterwards - a resized code may not be readable any more
            fit = int(min((cell_w - 16) / (w1 * 1.25), (cell_h - 16) / (h1 * 1.25 + w1 * 0.2)))
            if fit < 3:
                continue
            code = np.asarray(barcode.to_image(scale=min(6, fit, r.choice((4, 5, 6))), add_quiet_zones=True))
            # 1D and stacked codes only slightly tilted: a noisy, tilted EAN-13 can give a second, wrong read with a
            # valid check digit (zxing-cpp 3.1.1, seen in this generator at 6 degrees) - test scenes must be
            # unambiguous
            angle = r.uniform(-3, 3) if name in ("Code128", "EAN13", "PDF417") else r.uniform(-8, 8)
            h, w = code.shape
            m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
            cos, sin = abs(m[0, 0]), abs(m[0, 1])
            nw, nh = int(w * cos + h * sin), int(w * sin + h * cos)
            m[0, 2] += nw / 2 - w / 2
            m[1, 2] += nh / 2 - h / 2
            rot = cv2.warpAffine(code, m, (nw, nh), borderValue=255)[: cell_h - 8, : cell_w - 8]
            rh, rw = rot.shape
            cx, cy = (i % cols) * cell_w, (i // cols) * cell_h
            x = cx + r.randrange(0, max(1, cell_w - rw))
            y = cy + r.randrange(0, max(1, cell_h - rh))
            gray[y:y + rh, x:x + rw] = rot
            expected.append((name, text))
        noise = np.random.default_rng(r.randrange(2 ** 32)).normal(0, 6, gray.shape)
        gray = np.clip(gray + noise, 0, 255).astype(np.uint8)
        # BayerRG8 mosaic of a gray scene: every pixel keeps the gray value, like a white-balanced sensor
        mosaic = gray[: height // 2 * 2, : width // 2 * 2].copy()
        return Scene(imaging.Frame(mosaic, "BayerRG8", frame_id=frame_id), expected, "generated")
