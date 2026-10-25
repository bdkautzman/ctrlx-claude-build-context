"""A small PDF 1.4 writer - standard library only (zlib), no fonts embedded.

Enough for reports: pages, text in Helvetica / Helvetica-Bold / Courier (the PDF standard fonts every
viewer has, WinAnsi = cp1252 characters), lines, filled rectangles, text measuring and wrapping.
Coordinates in the API are TOP-LEFT based (y grows downwards, points = 1/72 inch); the writer
converts them to PDF's bottom-left system.

    doc = PdfDocument(title="Report")
    page = doc.new_page()
    page.text(40, 60, "Hello", font="bold", size=14)
    data = doc.output()        # bytes

Reusable in other apps: no dependencies besides app.fontmetrics (generated widths).
"""

import zlib
from datetime import datetime, timezone

from app import fontmetrics

A4 = (595.28, 841.89)
FONTS = {"regular": ("F1", "Helvetica"), "bold": ("F2", "Helvetica-Bold"), "mono": ("F3", "Courier")}


def to_cp1252(text):
    """Text -> bytes in WinAnsi; characters it can't show are replaced by close ASCII or '?'."""
    text = str(text)
    replacements = {"\u2192": "->", "\u2190": "<-", "\u2264": "<=", "\u2265": ">=", "\u2713": "v",
                    "\u2717": "x", "\u00a0": " ", "\u2009": " ", "\u202f": " "}
    out = bytearray()
    for ch in text:
        ch = replacements.get(ch, ch)
        try:
            out += ch.encode("cp1252")
        except UnicodeEncodeError:
            out += b"?"
    return bytes(out)


def _escape(data):
    return data.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)").replace(b"\r", b"\\r")


def text_width(text, font="regular", size=10.0):
    data = to_cp1252(text)
    if font == "mono":
        return len(data) * 600 * size / 1000.0
    table = fontmetrics.HELVETICA_BOLD if font == "bold" else fontmetrics.HELVETICA
    first = fontmetrics.FIRST_CHAR
    total = 0
    for b in data:
        w = table[b - first] if b >= first else 0
        total += w or 556
    return total * size / 1000.0


def truncate(text, font, size, max_width):
    text = str(text)
    if text_width(text, font, size) <= max_width:
        return text
    while text and text_width(text + "\u2026", font, size) > max_width:
        text = text[:-1]
    return text + "\u2026"


def wrap(text, font="regular", size=10.0, max_width=500.0):
    """Split text into lines that fit max_width (breaks at spaces, '/', ',', ';' or anywhere)."""
    lines = []
    for paragraph in str(text).split("\n"):
        words = paragraph.split(" ")
        line = ""
        for word in words:
            candidate = word if not line else line + " " + word
            if text_width(candidate, font, size) <= max_width:
                line = candidate
                continue
            if line:
                lines.append(line)
            # a single word longer than the line: hard-break it
            while text_width(word, font, size) > max_width:
                cut = len(word)
                while cut > 1 and text_width(word[:cut], font, size) > max_width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            line = word
        lines.append(line)
    return lines


def _color(rgb):
    return " ".join("{:.3f}".format(c / 255.0 if c > 1 else c) for c in rgb)


class Page:
    def __init__(self, width, height):
        self.width = width
        self.height = height
        self._ops = []

    def text(self, x, y, text, font="regular", size=10.0, color=(0, 0, 0)):
        """Draw text with its BASELINE at y (top-left coordinates)."""
        name = FONTS[font][0]
        self._ops.append("BT /{} {:.2f} Tf {} rg {:.2f} {:.2f} Td ({}) Tj ET".format(
            name, size, _color(color), x, self.height - y,
            _escape(to_cp1252(text)).decode("latin-1")))

    def text_right(self, x_right, y, text, font="regular", size=10.0, color=(0, 0, 0)):
        self.text(x_right - text_width(text, font, size), y, text, font, size, color)

    def line(self, x1, y1, x2, y2, width=0.5, color=(0, 0, 0)):
        self._ops.append("{:.2f} w {} RG {:.2f} {:.2f} m {:.2f} {:.2f} l S".format(
            width, _color(color), x1, self.height - y1, x2, self.height - y2))

    def rect(self, x, y, w, h, fill=None, stroke=None, width=0.5):
        """Rectangle with its TOP-LEFT corner at (x, y)."""
        ops = ["{:.2f} {:.2f} {:.2f} {:.2f} re".format(x, self.height - y - h, w, h)]
        if fill is not None:
            ops.insert(0, "{} rg".format(_color(fill)))
        if stroke is not None:
            ops.insert(0, "{:.2f} w {} RG".format(width, _color(stroke)))
        ops.append("B" if fill is not None and stroke is not None else ("f" if fill is not None else "S"))
        self._ops.append(" ".join(ops))

    def content(self):
        return ("\n".join(self._ops) + "\n").encode("latin-1")


class PdfDocument:
    def __init__(self, page_size=A4, title="", author="", subject="", creator=""):
        self.page_size = page_size
        self.pages = []
        self.info = {"Title": title, "Author": author, "Subject": subject, "Creator": creator,
                     "Producer": "ctrlx-network-scanner pdf_writer"}

    def new_page(self):
        page = Page(*self.page_size)
        self.pages.append(page)
        return page

    def output(self, compress=True):
        objects = []   # bytes of object i+1

        def add(body):
            objects.append(body)
            return len(objects)

        catalog = add(None)
        pages_obj = add(None)
        font_ids = {}
        for key, (name, base) in FONTS.items():
            enc = "" if key == "mono" else " /Encoding /WinAnsiEncoding"
            font_ids[name] = add("<< /Type /Font /Subtype /Type1 /BaseFont /{}{} >>".format(base, enc).encode("ascii"))
        fonts = " ".join("/{} {} 0 R".format(n, i) for n, i in font_ids.items())
        kids = []
        for page in self.pages or [self.new_page()]:
            data = page.content()
            if compress:
                data = zlib.compress(data, 6)
                stream = b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data) + data + b"\nendstream"
            else:
                stream = b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream"
            content_id = add(stream)
            kids.append(add("<< /Type /Page /Parent {} 0 R /MediaBox [0 0 {:.2f} {:.2f}] "
                            "/Resources << /Font << {} >> >> /Contents {} 0 R >>".format(
                                pages_obj, page.width, page.height, fonts, content_id).encode("ascii")))
        objects[pages_obj - 1] = "<< /Type /Pages /Kids [{}] /Count {} >>".format(
            " ".join("{} 0 R".format(k) for k in kids), len(kids)).encode("ascii")
        objects[catalog - 1] = "<< /Type /Catalog /Pages {} 0 R >>".format(pages_obj).encode("ascii")
        now = datetime.now(timezone.utc).strftime("D:%Y%m%d%H%M%SZ")
        info_items = ["/CreationDate ({})".format(now)]
        for key, value in self.info.items():
            if value:
                info_items.append("/{} ({})".format(key, _escape(to_cp1252(value)).decode("latin-1")))
        info = add("<< {} >>".format(" ".join(info_items)).encode("latin-1"))

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
        for off in offsets:
            out += b"%010d 00000 n \n" % off
        out += b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
            len(objects) + 1, catalog, info, xref)
        return bytes(out)
