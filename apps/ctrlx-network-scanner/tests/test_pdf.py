"""PDF writer structure and the scan report."""

import re
import unittest
import zlib

from app import pdf_writer, rating, report_pdf
from tests.test_rating_history_schedule import host, result


def check_structure(test, data):
    """xref offsets point at their objects, trailer/startxref consistent - what a viewer relies on."""
    test.assertTrue(data.startswith(b"%PDF-1.4\n"))
    test.assertTrue(data.rstrip().endswith(b"%%EOF"))
    startxref = int(re.search(rb"startxref\n(\d+)\n", data).group(1))
    test.assertEqual(data[startxref:startxref + 4], b"xref")
    m = re.match(rb"xref\n0 (\d+)\n", data[startxref:])
    count = int(m.group(1))
    entries = data[startxref + m.end():].split(b"\n")[:count]
    for i, line in enumerate(entries[1:], start=1):
        offset = int(line[:10])
        test.assertTrue(data[offset:].startswith(b"%d 0 obj" % i), "object {} not at {}".format(i, offset))


def page_texts(data):
    texts = []
    for m in re.finditer(rb"/Filter /FlateDecode >>\nstream\n(.*?)\nendstream", data, re.S):
        content = zlib.decompress(m.group(1)).decode("latin-1")
        texts.append(" ".join(re.findall(r"\((.*?)\) Tj", content)))
    return texts


class PdfWriterTest(unittest.TestCase):
    def test_document(self):
        doc = pdf_writer.PdfDocument(title="T (1)")
        page = doc.new_page()
        page.text(40, 60, "Hello (world) \\ ä€→中", "bold", 12)
        page.rect(40, 70, 100, 20, fill=(0, 123, 192), stroke=(0, 0, 0))
        page.line(40, 100, 200, 100)
        data = doc.output()
        check_structure(self, data)
        text = page_texts(data)[0]
        self.assertIn("Hello \\(world\\) \\\\ \xe4\x80->?", text)   # escaped, cp1252, replacements
        self.assertIn(b"/Title (T \\(1\\))", data)

    def test_measure_and_wrap(self):
        self.assertAlmostEqual(pdf_writer.text_width("AAAA", "regular", 10), 4 * 6.67, places=2)
        self.assertEqual(pdf_writer.text_width("abc", "mono", 10), 18.0)
        lines = pdf_writer.wrap("one two three four five six", "regular", 10, 60)
        self.assertTrue(all(pdf_writer.text_width(l, "regular", 10) <= 60 for l in lines))
        self.assertEqual(" ".join(lines), "one two three four five six")
        long = pdf_writer.wrap("x" * 200, "regular", 10, 50)
        self.assertGreater(len(long), 3)
        self.assertTrue(pdf_writer.truncate("a very long title", "regular", 10, 40).endswith("…"))


class ReportTest(unittest.TestCase):
    def test_report_pages_and_content(self):
        hosts = [host("192.168.1.{}".format(i), ["tcp/22", "tcp/23", "tcp/443"], mac="00:60:34:00:00:{:02x}".format(i),
                      names=["plc-{}".format(i)]) for i in range(1, 41)]
        r = result("20261025-100000-quick-aaaa", hosts)
        r["changes"] = {"previousId": "x", "previousFinishedAt": "2026-10-24T10:00:00Z", "newHosts": ["192.168.1.40"],
                        "goneHosts": [], "openedPorts": [], "closedPorts": [], "macChanged": [], "total": 1}
        rated = rating.apply(r, rating.Lists([], [{"hosts": "", "ports": "tcp/23", "note": "no telnet"}]))
        data = report_pdf.build(rated, "1.2.3")
        check_structure(self, data)
        texts = page_texts(data)
        self.assertGreater(len(texts), 3)                       # 40 hosts need several pages
        everything = " ".join(texts)
        self.assertIn("Forbidden findings", everything)
        self.assertIn("no telnet", everything)
        self.assertIn("new host", everything)
        self.assertIn("plc-40", everything)
        self.assertIn("Page 1 of {}".format(len(texts)), texts[0])
        self.assertIn("Page {} of {}".format(len(texts), len(texts)), texts[-1])

    def test_empty_failed_scan(self):
        r = result("x", [], status="failed")
        r["error"] = "the targets cover 65534 addresses, the limit is 1024"
        data = report_pdf.build(rating.apply(r, rating.Lists([], [])))
        check_structure(self, data)
        self.assertIn("No hosts found", " ".join(page_texts(data)))


if __name__ == "__main__":
    unittest.main()
