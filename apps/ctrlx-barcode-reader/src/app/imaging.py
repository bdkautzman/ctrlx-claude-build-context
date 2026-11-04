"""Image processing with OpenCV + numpy: camera frames -> grayscale for decoding, small JPEGs for the UI.

A Frame holds the camera's raw 8-bit data: Mono8 (H x W), Bayer8 (H x W mosaic) or RGB8/BGR8 (H x W x 3).
The camera's pattern names (GenICam PFNC) say which color the top-left pixel has: BayerRG8 = R G / G B.

cv2 and numpy are imported on first use, so the settings and the web server load without them.
"""

import time
from dataclasses import dataclass, field

# GenICam PFNC codes of the supported 8-bit formats (Aravis buffer pixel format)
PFNC = {
    0x01080001: "Mono8",
    0x01080008: "BayerGR8",
    0x01080009: "BayerRG8",
    0x0108000A: "BayerGB8",
    0x0108000B: "BayerBG8",
    0x02180014: "RGB8",
    0x02180015: "BGR8",
}
PFNC_BY_NAME = {v: k for k, v in PFNC.items()}
BAYER = ("BayerRG8", "BayerGR8", "BayerGB8", "BayerBG8")

_cv2 = None
_np = None


def cv():
    global _cv2, _np
    if _cv2 is None:
        import cv2
        import numpy
        cv2.setNumThreads(2)   # a ctrlX CORE gives apps 2 of its 4 cores (gotchas #73)
        _cv2, _np = cv2, numpy
    return _cv2, _np


def library_versions():
    try:
        cv2, np = cv()
        return {"opencv": cv2.__version__, "numpy": np.__version__}
    except Exception as exc:  # noqa: BLE001 - only for the status page
        return {"opencv": "unavailable: {}".format(exc)}


@dataclass
class Frame:
    data: object                 # numpy uint8 array
    pixel_format: str            # one of PFNC's names
    frame_id: int = 0
    timestamp: float = field(default_factory=time.time)   # epoch seconds when the image arrived
    grab_ms: float = 0.0

    @property
    def width(self):
        return int(self.data.shape[1])

    @property
    def height(self):
        return int(self.data.shape[0])

    @property
    def color(self):
        return self.pixel_format != "Mono8"


def frame_from_buffer(raw, width, height, pfnc, **kw):
    """Aravis buffer bytes -> Frame; ValueError for formats this app doesn't handle."""
    _, np = cv()
    name = PFNC.get(pfnc)
    if name is None:
        raise ValueError("pixel format 0x{:08x} is not supported (use an 8-bit Mono, Bayer or RGB format)".format(pfnc))
    channels = 3 if name in ("RGB8", "BGR8") else 1
    need = width * height * channels
    if len(raw) < need:
        raise ValueError("image data too short: {} of {} bytes".format(len(raw), need))
    arr = np.frombuffer(raw, np.uint8, count=need)
    arr = arr.reshape((height, width, 3) if channels == 3 else (height, width))
    return Frame(arr, name, **kw)


def _bayer_code(cv2, name, target):
    # OpenCV names Bayer patterns by the 2nd row's 2nd and 3rd pixel, the camera (PFNC) by the
    # top-left 2 x 2 block: PFNC BayerRG = OpenCV BayerBG (tests/test_imaging.py checks the colors)
    opencv = {"BayerRG8": "BG", "BayerGR8": "GB", "BayerGB8": "GR", "BayerBG8": "RG"}[name]
    return getattr(cv2, "COLOR_Bayer{}2{}".format(opencv, target))


def to_gray(frame):
    """Full-resolution grayscale image for the decoder."""
    cv2, _ = cv()
    if frame.pixel_format == "Mono8":
        return frame.data
    if frame.pixel_format in BAYER:
        return cv2.cvtColor(frame.data, _bayer_code(cv2, frame.pixel_format, "GRAY"))
    return cv2.cvtColor(frame.data, cv2.COLOR_RGB2GRAY if frame.pixel_format == "RGB8" else cv2.COLOR_BGR2GRAY)


def to_bgr(frame):
    """Full-resolution BGR (or gray for Mono8) image, e.g. for saving."""
    cv2, _ = cv()
    if frame.pixel_format == "Mono8":
        return frame.data
    if frame.pixel_format in BAYER:
        return cv2.cvtColor(frame.data, _bayer_code(cv2, frame.pixel_format, "BGR"))
    if frame.pixel_format == "RGB8":
        return cv2.cvtColor(frame.data, cv2.COLOR_RGB2BGR)
    return frame.data


def shrink(img, width):
    """Image -> at most `width` pixels wide: halve with INTER_AREA while possible (OpenCV's fast integer path),
    then one INTER_LINEAR step. INTER_AREA with a non-integer factor took ~170 ms for 5 MP on a ctrlX CORE."""
    cv2, _ = cv()
    h, w = img.shape[:2]
    while w >= 2 * width:
        w, h = w // 2, h // 2
        img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
    if w > width:
        img = cv2.resize(img, (width, max(1, round(h * width / w))), interpolation=cv2.INTER_LINEAR)
    return img


def scaled(frame, width):
    """Frame -> BGR/gray image at most `width` wide (OpenCV debayer: ~15 ms for 5 MP on a ctrlX CORE)."""
    return shrink(to_bgr(frame), width)


def jpeg(img, quality):
    cv2, _ = cv()
    ok, data = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    return data.tobytes()


def png(img):
    cv2, _ = cv()
    ok, data = cv2.imencode(".png", img, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    if not ok:
        raise RuntimeError("PNG encoding failed")
    return data.tobytes()


def clamp_roi(roi, width, height):
    """Settings decode.roi -> (x, y, w, h) inside the image, or None (whole image)."""
    if not roi.get("enabled"):
        return None
    x = max(0, min(int(roi["x"]), width - 1))
    y = max(0, min(int(roi["y"]), height - 1))
    w = min(int(roi["width"]), width - x)
    h = min(int(roi["height"]), height - y)
    if w < 16 or h < 16:
        return None
    return x, y, w, h


def crop(img, region):
    if region is None:
        return img
    x, y, w, h = region
    return img[y:y + h, x:x + w]


def decode_image(img):
    """Image file bytes (PNG/JPEG/BMP) -> BGR or gray numpy image, or None."""
    cv2, np = cv()
    arr = np.frombuffer(img, np.uint8)
    out = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
    if out is not None and out.ndim == 3 and out.shape[2] == 4:
        out = cv2.cvtColor(out, cv2.COLOR_BGRA2BGR)
    return out
