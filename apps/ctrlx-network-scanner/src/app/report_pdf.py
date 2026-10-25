"""The PDF report of one scan (rated result as produced by rating.apply)."""

from datetime import datetime, timezone

from app import probes
from app.pdf_writer import A4, PdfDocument, truncate, wrap

MARGIN = 40.0
INK = (31, 35, 40)
MUTED = (106, 115, 125)
LINE = (215, 219, 224)
ACCENT = (0, 123, 192)
HEAD_BG = (238, 241, 244)
RATING_COLORS = {"forbidden": (200, 16, 46), "ok": (0, 136, 74), "unlisted": (106, 115, 125)}
RATING_TEXT = {"forbidden": "FORBIDDEN", "ok": "expected", "unlisted": "not listed"}


def _local(iso):
    """ISO UTC -> 'YYYY-MM-DD HH:MM:SS' in the device's local time (with zone)."""
    if not iso:
        return "-"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return iso
    return dt.strftime("%Y-%m-%d %H:%M:%S %Z").strip()


def _duration(seconds):
    seconds = int(round(seconds or 0))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return "{}h {}m {}s".format(h, m, s) if h else ("{}m {}s".format(m, s) if m else "{}s".format(s))


class _Layout:
    """Cursor-based layout with automatic page breaks, header and footer."""

    def __init__(self, doc, header_left, header_right):
        self.doc = doc
        self.width, self.height = A4
        self.left, self.right = MARGIN, self.width - MARGIN
        self.top, self.bottom = 64.0, self.height - 48.0
        self.header_left, self.header_right = header_left, header_right
        self.page = None
        self.y = 0.0
        self.new_page()

    @property
    def inner(self):
        return self.right - self.left

    def new_page(self):
        self.page = self.doc.new_page()
        self.page.text(self.left, 36, self.header_left, "bold", 9, ACCENT)
        self.page.text_right(self.right, 36, self.header_right, "regular", 9, MUTED)
        self.page.line(self.left, 44, self.right, 44, 0.6, LINE)
        self.y = self.top

    def need(self, height):
        if self.y + height > self.bottom:
            self.new_page()
            return True
        return False

    def space(self, h):
        self.y += h

    def heading(self, text, size=13):
        self.need(size + 30)
        self.y += 8
        self.page.text(self.left, self.y + size, text, "bold", size, INK)
        self.y += size + 8

    def paragraph(self, text, size=9, color=MUTED, font="regular"):
        for line in wrap(text, font, size, self.inner):
            self.need(size + 4)
            self.page.text(self.left, self.y + size, line, font, size, color)
            self.y += size + 3.5
        self.y += 2

    def kv(self, rows, key_width=110, size=9):
        for key, value in rows:
            lines = wrap(value, "regular", size, self.inner - key_width)
            self.need(len(lines) * (size + 3.5) + 2)
            self.page.text(self.left, self.y + size, key, "regular", size, MUTED)
            for line in lines:
                self.page.text(self.left + key_width, self.y + size, line, "regular", size, INK)
                self.y += size + 3.5
            self.y += 1.5

    def tiles(self, items):
        """[(label, value, color)] as boxes in one row."""
        gap = 8
        n = len(items)
        w = (self.inner - gap * (n - 1)) / n
        h = 46
        self.need(h + 10)
        for i, (label, value, color) in enumerate(items):
            x = self.left + i * (w + gap)
            self.page.rect(x, self.y, w, h, fill=(248, 249, 250), stroke=LINE, width=0.6)
            self.page.rect(x, self.y, 3, h, fill=color)
            self.page.text(x + 10, self.y + 22, str(value), "bold", 16, color)
            self.page.text(x + 10, self.y + 37, truncate(label, "regular", 7.5, w - 14), "regular", 7.5, MUTED)
        self.y += h + 10

    def table(self, columns, rows, size=8.0, row_colors=None):
        """columns: [(title, width share)]; rows: [[cell text or (text, color, font)]]."""
        total = sum(c[1] for c in columns)
        widths = [self.inner * c[1] / total for c in columns]
        pad = 4

        def header():
            h = size + 8
            self.page.rect(self.left, self.y, self.inner, h, fill=HEAD_BG)
            x = self.left
            for (title, _), w in zip(columns, widths):
                self.page.text(x + pad, self.y + size + 3, truncate(title, "bold", size, w - 2 * pad), "bold", size, INK)
                x += w
            self.y += h

        self.need(size * 3 + 16)
        header()
        for r, row in enumerate(rows):
            cells = []
            for cell, w in zip(row, widths):
                text, color, font = (cell if isinstance(cell, tuple) else (cell, INK, "regular"))
                cells.append((wrap(text if text is not None else "", font, size, w - 2 * pad), color, font))
            lines = max(1, max(len(c[0]) for c in cells))
            h = lines * (size + 3) + 5
            if self.need(h):
                header()
            if row_colors and row_colors[r]:
                self.page.rect(self.left, self.y, self.inner, h, fill=row_colors[r])
            x = self.left
            for (lines_, color, font), w in zip(cells, widths):
                yy = self.y + size + 2.5
                for line in lines_:
                    self.page.text(x + pad, yy, line, font, size, color)
                    yy += size + 3
                x += w
            self.y += h
            self.page.line(self.left, self.y, self.right, self.y, 0.4, LINE)
        self.y += 6


def _rating_cell(rating):
    return (RATING_TEXT.get(rating, rating or ""), RATING_COLORS.get(rating, MUTED), "bold" if rating == "forbidden" else "regular")


def _host_name(h):
    names = [n["name"] for n in h.get("names", [])]
    return ", ".join(names[:2]) if names else ""


def build(result, app_version="dev", generated_at=None):
    """PDF bytes for one rated scan result."""
    generated_at = generated_at or datetime.now(timezone.utc)
    profile = result.get("profile", {})
    device = result.get("device", {})
    summary = result.get("summary", {})
    rating = summary.get("rating", {})
    changes = result.get("changes") or {}
    hostname = device.get("hostname") or "ctrlX device"
    doc = PdfDocument(title="Network scan report {}".format(result.get("id", "")),
                      author="ctrlx-network-scanner on {}".format(hostname),
                      subject="Scan profile {}".format(profile.get("name", "")),
                      creator="ctrlx-network-scanner {}".format(app_version))
    lay = _Layout(doc, "Network scan report", "{} · {}".format(hostname, _local(result.get("startedAt"))))

    # -- title ------------------------------------------------------------------------
    lay.page.text(lay.left, lay.y + 20, profile.get("name") or "Scan", "bold", 20, INK)
    status = result.get("status", "")
    status_color = {"completed": RATING_COLORS["ok"], "aborted": (199, 107, 0), "failed": RATING_COLORS["forbidden"]}
    lay.page.text_right(lay.right, lay.y + 20, status.upper(), "bold", 11, status_color.get(status, MUTED))
    lay.y += 34
    targets = result.get("targets", {})
    ranges = targets.get("ranges", [])
    range_text = ", ".join(a if a == b else "{} - {}".format(a, b) for a, b in ranges[:8])
    if len(ranges) > 8:
        range_text += ", ... ({} ranges)".format(len(ranges))
    methods = [m for m, key in (("neighbor table (ARP)", "discoverNeighbors"), ("TCP", "discoverTcp"),
                                ("mDNS/SSDP/WS-Discovery", "discoverMulticast")) if profile.get(key)]
    options = []
    if profile.get("assumeAlive"):
        options.append("all addresses treated as up (no discovery)")
    else:
        options.append("discovery: " + (", ".join(methods) or "none"))
    if profile.get("serviceDetection"):
        options.append("service detection")
    if profile.get("identifyAutomation"):
        options.append("automation device identification")
    if profile.get("resolveNames"):
        options.append("name resolution")
    trigger = {"manual": "manual start", "schedule": "scheduled start",
               "datalayer": "started via Data Layer"}.get(result.get("trigger", ""), result.get("trigger", ""))
    if result.get("user"):
        trigger += " by " + result["user"]
    lay.kv([
        ("Device", "{} ({})".format(hostname, ", ".join(
            "{} {}".format(i["name"], "/".join(i["addresses"])) for i in device.get("interfaces", [])) or "-")),
        ("Scan", "{} · {}".format(result.get("id", ""), trigger or "-")),
        ("Started", _local(result.get("startedAt"))),
        ("Duration", "{} (finished {})".format(_duration(result.get("durationS")), _local(result.get("finishedAt")))),
        ("Targets", "{} addresses: {}{}".format(targets.get("count", 0), range_text or "-",
                                                "; excluded: " + profile["exclude"] if profile.get("exclude") else "")),
        ("Ports", "TCP: {} · UDP: {}".format(profile.get("tcpPorts") or "none", profile.get("udpPorts") or "none")),
        ("Options", "; ".join(options)),
        ("Load limits", "{} parallel, max. {} attempts/s, timeout {} ms, {} retries ({} attempts in total)".format(
            profile.get("concurrency"), profile.get("ratePerSecond"), profile.get("timeoutMs"),
            profile.get("retries"), result.get("probes", 0))),
    ])
    if result.get("error"):
        lay.paragraph("Error: " + result["error"], 9, RATING_COLORS["forbidden"])
    for note in result.get("notes", []):
        lay.paragraph("Note: " + note, 8.5)
    lay.space(6)

    # -- summary ----------------------------------------------------------------------
    lay.tiles([
        ("Hosts up", summary.get("hostsUp", 0), ACCENT),
        ("Open TCP", summary.get("openTcp", 0), ACCENT),
        ("Open UDP", summary.get("openUdp", 0), ACCENT),
        ("Forbidden", rating.get("forbidden", 0),
         RATING_COLORS["forbidden"] if rating.get("forbidden") else MUTED),
        ("Expected", rating.get("ok", 0), RATING_COLORS["ok"]),
        ("Not listed", rating.get("unlisted", 0), MUTED),
        ("Changes", changes.get("total", 0) if changes else "-", (199, 107, 0) if changes.get("total") else MUTED),
    ])
    lay.paragraph("Ratings use the whitelist and blacklist as of {} (findings count hosts and open ports). "
                  "Changes compare with the previous completed scan of the same profile{}.".format(
                      _local(generated_at.isoformat()),
                      " ({})".format(_local(changes.get("previousFinishedAt"))) if changes else " (none found)"),
                  8)

    # -- alarms --------------------------------------------------------------------------
    alarms = rating.get("alarms", [])
    if alarms:
        lay.heading("Forbidden findings (blacklist)")
        rows = []
        for a in alarms:
            rows.append([("host" if a["type"] == "host" else "open port", RATING_COLORS["forbidden"], "bold"),
                         a["ip"], "" if a["type"] == "host" else "{}/{}".format(a["proto"], a["port"]),
                         a.get("note", "")])
        lay.table([("Finding", 1.2), ("Host", 1.4), ("Port", 1), ("Blacklist entry", 3)], rows)

    # -- changes ----------------------------------------------------------------------------
    if changes and changes.get("total"):
        lay.heading("Changes since the previous scan")
        rows = []
        for ip in changes.get("newHosts", []):
            rows.append([("new host", (199, 107, 0), "bold"), ip, ""])
        for ip in changes.get("goneHosts", []):
            rows.append(["host gone", ip, ""])
        for p in changes.get("openedPorts", []):
            rows.append([("port opened", (199, 107, 0), "bold"), p["ip"], "{}/{}".format(p["proto"], p["port"])])
        for p in changes.get("closedPorts", []):
            rows.append(["port closed", p["ip"], "{}/{}".format(p["proto"], p["port"])])
        for m in changes.get("macChanged", []):
            rows.append([("MAC changed", (199, 107, 0), "bold"), m["ip"], "{} -> {}".format(m["old"], m["new"])])
        lay.table([("Change", 1.2), ("Host", 1.4), ("Details", 4)], rows)

    # -- hosts overview -------------------------------------------------------------------------
    hosts = result.get("hosts", [])
    lay.heading("Hosts ({})".format(len(hosts)))
    if not hosts:
        lay.paragraph("No hosts found.", 9)
    rows, colors = [], []
    for h in hosts:
        tags = []
        if h.get("self"):
            tags.append("this device")
        if h.get("gateway"):
            tags.append("gateway")
        ip = h["ip"] + (" ({})".format(", ".join(tags)) if tags else "")
        mac = h.get("mac", "")
        if h.get("vendor"):
            mac = (mac + " " if mac else "") + h["vendor"]
        open_ports = ", ".join("{}/{}".format(p["proto"], p["port"]) for p in h.get("ports", []))
        forbidden = h.get("rating") == "forbidden" or any(p.get("rating") == "forbidden" for p in h.get("ports", []))
        rows.append([(ip, INK, "bold"), _host_name(h), mac, open_ports or "-", _rating_cell(h.get("rating"))])
        colors.append((253, 236, 238) if forbidden else None)
    if rows:
        lay.table([("Host", 1.5), ("Name", 1.5), ("MAC / vendor", 2.0), ("Open ports", 2.2), ("Host rating", 0.9)],
                  rows, size=7.5, row_colors=colors)

    # -- host details ------------------------------------------------------------------------------
    for h in hosts:
        lay.need(70)
        lay.y += 6
        title = h["ip"] + ("  —  " + _host_name(h) if _host_name(h) else "")
        lay.page.rect(lay.left, lay.y, lay.inner, 18, fill=HEAD_BG)
        lay.page.text(lay.left + 6, lay.y + 12.5, truncate(title, "bold", 10, lay.inner - 90), "bold", 10, INK)
        rating_text, rating_color, _f = _rating_cell(h.get("rating"))
        lay.page.text_right(lay.right - 6, lay.y + 12.5, rating_text, "bold", 8.5, rating_color)
        lay.y += 22
        facts = []
        if h.get("mac"):
            facts.append(("MAC", h["mac"] + (" · " + h["vendor"] if h.get("vendor") else "")))
        if h.get("interface"):
            facts.append(("Interface", h["interface"] + (" (this device)" if h.get("self") else "")
                          + (" (default gateway)" if h.get("gateway") else "")))
        facts.append(("Found by", ", ".join(h.get("via", [])) or "-"))
        names = ["{} ({})".format(n["name"], n["source"]) for n in h.get("names", [])]
        if names:
            facts.append(("Names", ", ".join(names)))
        disc = h.get("discovery", {})
        if disc.get("mdns"):
            facts.append(("mDNS services", ", ".join(disc["mdns"])))
        if disc.get("ssdp"):
            facts.append(("UPnP/SSDP", "; ".join("{}: {}".format(k, v) for k, v in disc["ssdp"].items())))
        if disc.get("wsd"):
            facts.append(("WS-Discovery", "; ".join("{}: {}".format(k, v) for k, v in disc["wsd"].items()
                                                     if k != "protocol")))
        if disc.get("ethernetIp"):
            e = disc["ethernetIp"]
            facts.append(("EtherNet/IP", "{} (vendor {}, type {}, product {}, rev {}, serial {})".format(
                e.get("product", ""), e.get("vendorId"), e.get("deviceType"), e.get("productCode"),
                e.get("revision"), e.get("serial"))))
        if h.get("rating") and h.get("ratingNote"):
            facts.append(("List entry", h["ratingNote"]))
        if h.get("udpOpenFiltered"):
            facts.append(("UDP, no answer", ", ".join(str(p) for p in h["udpOpenFiltered"]) + " (open or filtered)"))
        lay.kv(facts, key_width=90, size=8)
        ports = h.get("ports", [])
        if ports:
            rows = []
            for p in ports:
                service = p.get("service") or p.get("name") or ""
                details = probes.product_line(p)
                tls = p.get("tls") or {}
                if tls.get("version"):
                    details = (details + "; " if details else "") + tls["version"]
                if tls.get("notAfter"):
                    details += ", cert valid until " + tls["notAfter"][:10] + (", self-signed" if tls.get("selfSigned") else "")
                rows.append(["{}/{}".format(p["proto"], p["port"]), service, details or "-",
                             _rating_cell(p.get("rating"))])
            lay.table([("Port", 0.8), ("Service", 1.3), ("Details", 4.2), ("Rating", 0.9)], rows, size=7.5)
        else:
            lay.paragraph("No open ports found (closed: {}, no answer: {}).".format(
                h.get("tcpClosed", 0), h.get("tcpFiltered", 0)), 8)

    # -- footers (page x of y) --------------------------------------------------------------------
    total = len(doc.pages)
    footer = "ctrlx-network-scanner {} · generated {}".format(app_version, _local(generated_at.isoformat()))
    for i, page in enumerate(doc.pages, start=1):
        page.line(MARGIN, page.height - 34, page.width - MARGIN, page.height - 34, 0.6, LINE)
        page.text(MARGIN, page.height - 22, footer, "regular", 7.5, MUTED)
        page.text_right(page.width - MARGIN, page.height - 22, "Page {} of {}".format(i, total), "regular", 7.5, MUTED)
    return doc.output()
