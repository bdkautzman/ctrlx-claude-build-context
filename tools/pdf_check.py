#!/usr/bin/env python3
"""Look at a PDF an app generated: open it with PDFium (Chrome's PDF engine), render the pages to PNG
and print their text - so Claude can Read the images like tools/ui_check.py screenshots.

    python3 tools/pdf_check.py report.pdf                 -> report-p1.png, report-p2.png, ... next to it
    python3 tools/pdf_check.py report.pdf --out DIR --pages 1-3 --scale 1.5 --text

Exit 1 if PDFium cannot open the file (broken xref, bad objects, ...) or a page fails to render.
Setup: pypdfium2 (Apache-2.0 / BSD-3, bundles PDFium) in the UI test venv, pinned:
    ~/.cache/ctrlx-kit/uivenv/bin/pip install pypdfium2==5.14.0   (tools/setup-ui-test.sh does it)
This script re-runs itself with that venv's Python, so plain `python3` works. PNGs are written with
the standard library (no Pillow).
"""

import argparse
import os
import struct
import sys
import zlib

VENV = os.environ.get("CTRLX_UI_VENV", os.path.expanduser("~/.cache/ctrlx-kit/uivenv"))

try:
    import pypdfium2 as pdfium
except ImportError:
    venv_python = os.path.join(VENV, "bin", "python")
    if os.path.exists(venv_python) and os.path.realpath(sys.prefix) != os.path.realpath(VENV):
        os.execv(venv_python, [venv_python, os.path.abspath(__file__)] + sys.argv[1:])
    sys.exit("pypdfium2 is not set up: run tools/setup-ui-test.sh (once)")


def write_png(path, width, height, rows):
    """rows: bytes of RGB pixels, row by row (no padding)."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\0" + rows[y * width * 3:(y + 1) * width * 3] for y in range(height))
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(raw, 6)))
        f.write(chunk(b"IEND", b""))


def bitmap_rgb(bitmap):
    """PdfBitmap (BGR / BGRA / BGRx) -> tightly packed RGB bytes."""
    width, height, stride = bitmap.width, bitmap.height, bitmap.stride
    n = bitmap.n_channels
    buf = bytes(bitmap.buffer)
    out = bytearray(width * height * 3)
    for y in range(height):
        row = buf[y * stride:y * stride + width * n]
        o = y * width * 3
        out[o:o + width * 3:3] = row[2::n]
        out[o + 1:o + width * 3:3] = row[1::n]
        out[o + 2:o + width * 3:3] = row[0::n]
    return bytes(out)


def parse_pages(spec, count):
    if not spec:
        return list(range(count))
    pages = set()
    for part in spec.split(","):
        a, _, b = part.partition("-")
        first, last = int(a), int(b or a)
        pages.update(range(max(1, first) - 1, min(count, last)))
    return sorted(pages)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("--out", help="directory for the PNGs (default: next to the PDF)")
    ap.add_argument("--pages", help="e.g. 1-3,5 (default: all)")
    ap.add_argument("--scale", type=float, default=1.4, help="pixels per PDF point (1.4 = ~100 dpi)")
    ap.add_argument("--text", action="store_true", help="print the text of each page")
    args = ap.parse_args()

    try:
        doc = pdfium.PdfDocument(args.pdf)
    except pdfium.PdfiumError as exc:
        print("PDF CHECK: FAILED - PDFium cannot open {}: {}".format(args.pdf, exc))
        return 1
    out_dir = args.out or os.path.dirname(os.path.abspath(args.pdf))
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(args.pdf))[0]
    count = len(doc)
    print("{}: {} page(s), PDF {}".format(args.pdf, count, doc.get_version()))
    meta = {k: v for k, v in doc.get_metadata_dict().items() if v}
    if meta:
        print("metadata:", meta)
    failed = 0
    for i in parse_pages(args.pages, count):
        page = doc[i]
        try:
            w, h = page.get_size()
            bitmap = page.render(scale=args.scale)
            path = os.path.join(out_dir, "{}-p{}.png".format(base, i + 1))
            write_png(path, bitmap.width, bitmap.height, bitmap_rgb(bitmap))
            print("page {}: {:.0f} x {:.0f} pt -> {}".format(i + 1, w, h, path))
            if args.text:
                text = page.get_textpage().get_text_range()
                print("---- text page {} ----\n{}".format(i + 1, text.strip()))
        except Exception as exc:   # report every page
            failed += 1
            print("page {}: FAILED {}: {}".format(i + 1, type(exc).__name__, exc))
    print("PDF CHECK: {}".format("OK" if not failed else "FAILED"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
