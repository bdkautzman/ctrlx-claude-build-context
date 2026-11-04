"""imaging.py: pixel formats, Bayer patterns (colors must come out right), scaling, region; decoder.py."""

import unittest

from tests.helpers import HAVE_IMAGING

if HAVE_IMAGING:
    import numpy as np

    from app import imaging
    from app.decoder import FORMAT_NAMES, FORMATS, Decoder, zxing
    from app.simulator import Simulator


def mosaic(rgb, pattern):
    """H x W x 3 RGB -> Bayer mosaic of the PFNC pattern (top-left 2 x 2 block)."""
    h, w, _ = rgb.shape
    m = np.zeros((h, w), np.uint8)
    pos = {"BayerRG8": "RGGB", "BayerGR8": "GRBG", "BayerGB8": "GBRG", "BayerBG8": "BGGR"}[pattern]
    idx = {"R": 0, "G": 1, "B": 2}
    for k, (dy, dx) in enumerate(((0, 0), (0, 1), (1, 0), (1, 1))):
        m[dy::2, dx::2] = rgb[dy::2, dx::2, idx[pos[k]]]
    return m


@unittest.skipUnless(HAVE_IMAGING, "OpenCV/numpy/zxing-cpp not available (README: Local development)")
class ImagingTest(unittest.TestCase):
    def test_bayer_patterns_keep_colors(self):
        red = np.zeros((64, 64, 3), np.uint8)
        red[..., 0] = 200                      # pure red in RGB
        for pattern in imaging.BAYER:
            f = imaging.Frame(mosaic(red, pattern), pattern)
            bgr = imaging.to_bgr(f)
            self.assertGreater(int(bgr[32, 32, 2]), 150, pattern + ": red lost (full)")
            self.assertLess(int(bgr[32, 32, 0]), 50, pattern + ": blue appeared (full)")
            half = imaging.scaled(f, 32)
            self.assertEqual(half.shape, (32, 32, 3))
            self.assertGreater(int(half[16, 16, 2]), 150, pattern + ": red lost (small)")
            self.assertLess(int(half[16, 16, 0]), 50, pattern + ": blue appeared (small)")

    def test_gray_and_formats(self):
        gray = np.full((40, 60), 77, np.uint8)
        self.assertIs(imaging.to_gray(imaging.Frame(gray, "Mono8")), gray)
        rgb = np.zeros((40, 60, 3), np.uint8)
        rgb[..., 1] = 255
        g = imaging.to_gray(imaging.Frame(rgb, "RGB8"))
        self.assertEqual(g.shape, (40, 60))
        self.assertTrue(140 < int(g[0, 0]) < 160)     # 0.587 * 255
        f = imaging.frame_from_buffer(bytes(60 * 40), 60, 40, 0x01080001)
        self.assertEqual((f.width, f.height, f.pixel_format, f.color), (60, 40, "Mono8", False))
        f = imaging.frame_from_buffer(bytes(60 * 40 * 3), 60, 40, 0x02180014)
        self.assertEqual(f.data.shape, (40, 60, 3))
        with self.assertRaises(ValueError):
            imaging.frame_from_buffer(bytes(10), 60, 40, 0x01080001)          # too short
        with self.assertRaises(ValueError):
            imaging.frame_from_buffer(bytes(60 * 40 * 2), 60, 40, 0x01100003)  # Mono10

    def test_scaled_jpeg_png(self):
        f = imaging.Frame(np.zeros((100, 200), np.uint8), "Mono8")
        self.assertEqual(imaging.scaled(f, 50).shape, (25, 50))        # halved twice
        self.assertEqual(imaging.scaled(f, 80).shape, (40, 80))        # halved, then linear
        self.assertEqual(imaging.scaled(f, 400).shape, (100, 200))    # never larger
        self.assertEqual(imaging.shrink(np.zeros((2048, 2448, 3), np.uint8), 1280).shape, (1071, 1280, 3))
        self.assertTrue(imaging.jpeg(f.data, 80).startswith(b"\xff\xd8"))
        self.assertTrue(imaging.png(f.data).startswith(b"\x89PNG"))
        self.assertEqual(imaging.decode_image(imaging.png(f.data)).shape, (100, 200))

    def test_region(self):
        roi = {"enabled": True, "x": 150, "y": 10, "width": 100, "height": 100}
        self.assertEqual(imaging.clamp_roi(roi, 200, 100), (150, 10, 50, 90))
        self.assertIsNone(imaging.clamp_roi(dict(roi, enabled=False), 200, 100))
        self.assertIsNone(imaging.clamp_roi(dict(roi, x=195), 200, 100))   # 5 px wide: too small
        img = np.arange(200 * 100, dtype=np.uint32).reshape(100, 200)
        self.assertEqual(imaging.crop(img, (150, 10, 50, 90)).shape, (90, 50))


@unittest.skipUnless(HAVE_IMAGING, "OpenCV/numpy/zxing-cpp not available (README: Local development)")
class DecoderTest(unittest.TestCase):
    def test_format_names_exist_in_zxing(self):
        zx = zxing()
        for name, (attr, _label, kind) in FORMATS.items():
            self.assertTrue(hasattr(zx.BarcodeFormat, attr), name)
            self.assertIn(kind, ("1D", "2D"))
        self.assertEqual(FORMAT_NAMES, list(FORMATS))

    def code_image(self, text, fmt, pad=30):
        zx = zxing()
        img = np.asarray(zx.create_barcode(text, getattr(zx.BarcodeFormat, fmt)).to_image(scale=4, add_quiet_zones=True))
        return np.pad(img, pad, constant_values=255)

    def settings(self, **kw):
        s = {"formats": list(FORMAT_NAMES), "tryRotate": True, "tryInvert": False, "tryDownscale": True, "maxCodes": 10}
        s.update(kw)
        return s

    def test_symbologies(self):
        d = Decoder(self.settings())
        for fmt, text in (("QRCode", "https://example.com/1"), ("DataMatrix", "LOT 42"), ("Code128", "ABC-123"),
                          ("Code39", "PART42"), ("EAN13", "4006381333931"), ("PDF417", "PDF 1"), ("Aztec", "AZ 9"),
                          ("ITF", "12345678901231")):
            codes, ms = d.decode(self.code_image(text, fmt))
            self.assertEqual([(c["format"], c["text"]) for c in codes], [(fmt, text)], fmt)
            self.assertEqual(len(codes[0]["corners"]), 4)
            self.assertGreaterEqual(ms, 0.0)

    def test_format_filter_offset_and_limit(self):
        qr, dm = self.code_image("ONE", "QRCode"), self.code_image("TWO", "DataMatrix")
        h = max(qr.shape[0], dm.shape[0])
        img = np.hstack([np.pad(c, ((0, h - c.shape[0]), (0, 0)), constant_values=255) for c in (qr, dm)])
        only_qr = Decoder(self.settings(formats=["QRCode"]))
        codes, _ = only_qr.decode(img)
        self.assertEqual([c["text"] for c in codes], ["ONE"])
        both = Decoder(self.settings())
        self.assertEqual(sorted(c["text"] for c in both.decode(img)[0]), ["ONE", "TWO"])
        one = Decoder(self.settings(maxCodes=1))
        self.assertEqual(len(one.decode(img)[0]), 1)
        shifted, _ = only_qr.decode(img, offset=(1000, 500))
        plain, _ = only_qr.decode(img)
        self.assertEqual(shifted[0]["corners"][0], [plain[0]["corners"][0][0] + 1000, plain[0]["corners"][0][1] + 500])

    def test_simulated_scene_decodes_to_expected(self):
        sim = Simulator("/nonexistent", seed=7)
        d = Decoder(self.settings())
        for _ in range(3):
            scene = sim.generate(1224, 1024, 3)
            self.assertEqual(scene.frame.pixel_format, "BayerRG8")
            codes, _ = d.decode(imaging.to_gray(scene.frame))
            self.assertEqual(sorted((c["format"], c["text"]) for c in codes), sorted(scene.expected))
