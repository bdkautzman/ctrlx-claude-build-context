#!/usr/bin/env python3
"""Build data/oui.tsv.gz (MAC prefix -> vendor) from the IEEE Registration Authority listings.

    python3 scripts/make_oui.py [/usr/share/ieee-data]

Source: the Ubuntu package `ieee-data` (MA-L oui.csv, MA-M mam.csv, MA-S oui36.csv, IAB iab.csv) or the
same CSV files downloaded from https://standards-oui.ieee.org/. The IEEE asserts no copyright on the
public listing and does not restrict its distribution. Re-run it now and then: new prefixes are
assigned every week.

Output: one line per assignment "<hex prefix>\t<organization>", sorted, gzip-compressed (~0.5 MB).
app/oui.py streams through it when a scan ends (no table in memory).
"""

import csv
import gzip
import os
import sys

FILES = ("oui.csv", "mam.csv", "oui36.csv", "iab.csv")


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "/usr/share/ieee-data"
    entries = {}
    for name in FILES:
        path = os.path.join(src, name)
        if not os.path.isfile(path):
            print("skipping missing", path)
            continue
        with open(path, newline="", encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f):
                prefix = (row.get("Assignment") or "").strip().upper()
                org = " ".join((row.get("Organization Name") or "").split())
                if prefix and org and all(c in "0123456789ABCDEF" for c in prefix):
                    entries[prefix] = org.replace("\t", " ")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = os.path.join(here, "data", "oui.tsv.gz")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    data = "".join("{}\t{}\n".format(p, entries[p]) for p in sorted(entries)).encode("utf-8")
    with open(out, "wb") as f:
        # mtime=0: the file only changes when the listing changes (no needless snap rebuilds)
        with gzip.GzipFile(fileobj=f, mode="wb", compresslevel=9, mtime=0) as gz:
            gz.write(data)
    print("{} prefixes -> {} ({} KB)".format(len(entries), out, os.path.getsize(out) // 1024))


if __name__ == "__main__":
    main()
