"""MAC address -> vendor, from the IEEE public listing in data/oui.tsv.gz (scripts/make_oui.py).

The file is streamed once per lookup batch (end of a scan) instead of being kept in memory as a
dict (~40 000 entries would cost several MB of RAM for the whole lifetime of the app).
Longest prefix wins: MA-S (36 bit) before MA-M (28 bit) before MA-L (24 bit).
"""

import gzip
import logging
import os

from app import config

log = logging.getLogger("oui")

OUI_FILE = os.path.join(config.SNAP_DIR, "data", "oui.tsv.gz")


def normalize(mac):
    """"00:60:34:aa:bb:cc" / "00-60-34-AA-BB-CC" / "006034aabbcc" -> "006034AABBCC" ("" if invalid)."""
    hexdigits = "".join(c for c in (mac or "") if c not in ":-. ").upper()
    if not hexdigits or len(hexdigits) > 12 or any(c not in "0123456789ABCDEF" for c in hexdigits):
        return ""
    return hexdigits


def is_locally_administered(mac):
    h = normalize(mac)
    return len(h) >= 2 and bool(int(h[:2], 16) & 0x02)


def lookup(macs, path=None):
    """{mac: vendor} for the given MAC addresses (unknown ones are left out)."""
    wanted = {}
    for mac in macs:
        h = normalize(mac)
        if len(h) == 12:
            wanted.setdefault(h, []).append(mac)
    result = {}
    if not wanted:
        return result
    for mac_list in wanted.values():
        for mac in mac_list:
            if is_locally_administered(mac):
                result[mac] = "(locally administered address)"
    prefixes = {}
    for h in wanted:
        if not is_locally_administered(h):
            for n in (9, 7, 6):
                prefixes.setdefault(h[:n], []).append(h)
    best = {}   # normalized mac -> (prefix length, vendor)
    path = path or OUI_FILE
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                prefix, _, vendor = line.rstrip("\n").partition("\t")
                for h in prefixes.get(prefix, ()):
                    if len(prefix) > best.get(h, (0, ""))[0]:
                        best[h] = (len(prefix), vendor)
    except OSError as exc:
        log.warning("Vendor list not readable (%s): %s", path, exc)
        return result
    for h, (_n, vendor) in best.items():
        for mac in wanted[h]:
            result[mac] = vendor
    return result
