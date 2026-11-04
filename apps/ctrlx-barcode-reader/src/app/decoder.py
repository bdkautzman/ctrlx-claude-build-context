"""Barcode decoding with zxing-cpp (Apache-2.0) on a grayscale numpy image.

OpenCV prepares the image (imaging.py: debayer/gray, region); zxing-cpp finds and decodes the codes.
OpenCV's own decoders were not used: OpenCV 4.14 reads only QR codes and EAN/UPC.

zxingcpp is imported on first use, so settings and tests can import this module without it.
"""

import time

# Symbologies offered in the settings: setting name -> (zxingcpp.BarcodeFormat attribute, label, kind)
FORMATS = {
    "Code128": ("Code128", "Code 128", "1D"),
    "Code39": ("Code39", "Code 39", "1D"),
    "Code93": ("Code93", "Code 93", "1D"),
    "Codabar": ("Codabar", "Codabar", "1D"),
    "ITF": ("ITF", "ITF (Interleaved 2 of 5)", "1D"),
    "EAN13": ("EAN13", "EAN-13", "1D"),
    "EAN8": ("EAN8", "EAN-8", "1D"),
    "UPCA": ("UPCA", "UPC-A", "1D"),
    "UPCE": ("UPCE", "UPC-E", "1D"),
    "DataBar": ("DataBar", "GS1 DataBar", "1D"),
    "DataBarExpanded": ("DataBarExpanded", "GS1 DataBar Expanded", "1D"),
    "DataBarLimited": ("DataBarLimited", "GS1 DataBar Limited", "1D"),
    "QRCode": ("QRCode", "QR Code", "2D"),
    "MicroQRCode": ("MicroQRCode", "Micro QR Code", "2D"),
    "RMQRCode": ("RMQRCode", "rMQR Code", "2D"),
    "DataMatrix": ("DataMatrix", "Data Matrix", "2D"),
    "PDF417": ("PDF417", "PDF417", "2D"),
    "Aztec": ("Aztec", "Aztec", "2D"),
    "MaxiCode": ("MaxiCode", "MaxiCode", "2D"),
}
FORMAT_NAMES = list(FORMATS)

_zx = None


def zxing():
    global _zx
    if _zx is None:
        import zxingcpp
        _zx = zxingcpp
    return _zx


def library_versions():
    """{"zxing-cpp": "3.1.1"} or the import error text."""
    try:
        from importlib.metadata import version
        return {"zxing-cpp": version("zxing-cpp")}
    except Exception as exc:  # noqa: BLE001 - only for the status page
        return {"zxing-cpp": "unavailable: {}".format(exc)}


class Decoder:
    """Decodes codes in a grayscale image; settings come from the `decode` settings object."""

    def __init__(self, decode_settings):
        self.configure(decode_settings)

    def configure(self, s):
        self.format_names = [n for n in s["formats"] if n in FORMATS]
        self.try_rotate = bool(s["tryRotate"])
        self.try_invert = bool(s["tryInvert"])
        self.try_downscale = bool(s["tryDownscale"])
        self.max_codes = int(s["maxCodes"])
        self._formats = None   # zxingcpp.BarcodeFormats, built on first decode

    def _zx_formats(self):
        if self._formats is None:
            zx = zxing()
            fmts = [getattr(zx.BarcodeFormat, FORMATS[n][0]) for n in self.format_names]
            self._formats = zx.BarcodeFormats(fmts)
        return self._formats

    def decode(self, gray, offset=(0, 0)):
        """gray: 2-D uint8 numpy array. offset: (x, y) of gray in the full image (decode region).

        Returns (codes, milliseconds). Each code: {"format", "label", "text", "corners": [[x, y] x4],
        "orientation", "symbologyId", "contentType"} with corners in full-image pixels
        (top-left, top-right, bottom-right, bottom-left)."""
        zx = zxing()
        t0 = time.monotonic()
        found = zx.read_barcodes(gray, formats=self._zx_formats(), try_rotate=self.try_rotate,
                                 try_downscale=self.try_downscale, try_invert=self.try_invert)
        ms = (time.monotonic() - t0) * 1000.0
        ox, oy = offset
        codes = []
        for b in found:
            if not b.valid:
                continue
            p = b.position
            corners = [[pt.x + ox, pt.y + oy] for pt in (p.top_left, p.top_right, p.bottom_right, p.bottom_left)]
            name = b.format.name
            codes.append({
                "format": name,
                "label": FORMATS[name][1] if name in FORMATS else str(b.format),
                "text": b.text,
                "corners": corners,
                "orientation": int(b.orientation),
                "symbologyId": b.symbology_identifier,
                "contentType": b.content_type.name,
            })
            if len(codes) >= self.max_codes:
                break
        # reading order: top to bottom, then left to right (stable for PLCs reading result/text)
        codes.sort(key=lambda c: (min(pt[1] for pt in c["corners"]) // 32, min(pt[0] for pt in c["corners"])))
        return codes, ms
