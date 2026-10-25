"""Whitelist / blacklist rating and the comparison with the previous scan.

The lists only RATE results, they never limit what is scanned (the user's choice, 2026-10-25):
  whitelist = expected (rating "ok"), blacklist = forbidden (rating "forbidden" = alarm),
  neither = "unlisted". The blacklist wins when both match.

One list entry = {"hosts": "...", "ports": "...", "note": "..."}:
  hosts  IP, network (CIDR), range (a.b.c.10-50), MAC (00:60:34:aa:bb:cc), MAC prefix (00:60:34 =
         all devices of one vendor), host name pattern (plc-*, *.local); empty = every host
  ports  "502, tcp/22, udp/161, tcp/8000-8100" (no protocol = TCP and UDP); empty = the entry
         rates HOSTS ("this device is expected / must not be on the network")
An entry with ports rates the OPEN PORTS of the matching hosts.

Ratings are computed when results are shown (web UI, PDF, Data Layer) with the current lists, so
adding a finding to the whitelist takes effect at once; the scan file keeps the counts as rated
when the scan finished (`summary.ratingAtScan`).
"""

import copy
import fnmatch
import ipaddress
import re

from app import oui
from app import ports as portlib
from app import targets as targetlib

RATINGS = ("forbidden", "ok", "unlisted")
_MAC_FULL = re.compile(r"[0-9A-Fa-f]{2}([:-][0-9A-Fa-f]{2}){5}")
_MAC_PREFIX = re.compile(r"[0-9A-Fa-f]{2}([:-][0-9A-Fa-f]{2}){2,4}")
_NAME = re.compile(r"[A-Za-z0-9*?._-]+")
_SPLIT = re.compile(r"[\s,;]+")


class _HostMatcher:
    def __init__(self, text):
        self.text = text
        self.kind = None
        if _MAC_FULL.fullmatch(text):
            self.kind, self.value = "mac", oui.normalize(text)
        elif _MAC_PREFIX.fullmatch(text):
            self.kind, self.value = "macprefix", oui.normalize(text)
        else:
            try:
                kind, value = targetlib.parse_item(text)
            except targetlib.TargetError:
                kind, value = None, None
            if kind == "net":
                self.kind, self.value = "range", (int(value.network_address), int(value.broadcast_address))
            elif kind == "range":
                self.kind, self.value = "range", (int(value[0]), int(value[1]))
            elif kind == "ip":
                self.kind, self.value = "range", (int(value), int(value))
            elif _NAME.fullmatch(text) and not text.replace(".", "").isdigit() and text.lower() not in ("auto",):
                self.kind, self.value = "name", text.lower()
        if self.kind is None:
            raise ValueError("{!r}: expected an IP address, network, range, MAC address/prefix or "
                             "host name pattern".format(text))

    def matches(self, host):
        if self.kind == "range":
            try:
                ip = int(ipaddress.IPv4Address(host.get("ip", "")))
            except ValueError:
                return False
            return self.value[0] <= ip <= self.value[1]
        if self.kind == "mac":
            return oui.normalize(host.get("mac", "")) == self.value
        if self.kind == "macprefix":
            return oui.normalize(host.get("mac", "")).startswith(self.value)
        names = [n["name"].lower() if isinstance(n, dict) else str(n).lower() for n in host.get("names", [])]
        return any(fnmatch.fnmatchcase(n, self.value) for n in names)


class _Entry:
    def __init__(self, raw, kind, index):
        self.kind = kind
        self.index = index
        self.note = (raw.get("note") or "").strip()
        self.hosts = [_HostMatcher(t) for t in _SPLIT.split((raw.get("hosts") or "").strip()) if t]
        self.ports = portlib.parse_rating_ports(raw.get("ports") or "")
        if not self.hosts and not self.ports:
            raise ValueError("needs hosts, ports or both")

    def host_matches(self, host):
        return not self.hosts or any(m.matches(host) for m in self.hosts)

    def port_matches(self, proto, port):
        return any((p is None or p == proto) and a <= port <= b for p, a, b in self.ports)

    def label(self):
        return self.note or "{} entry {}".format(self.kind, self.index + 1)


def check_entry(raw):
    """Error messages for one list entry (used by the settings validation)."""
    try:
        _Entry(raw, "list", 0)
        return []
    except (ValueError, portlib.PortSpecError) as exc:
        return [str(exc)]


class Lists:
    def __init__(self, whitelist, blacklist):
        self.white = self._parse(whitelist, "whitelist")
        self.black = self._parse(blacklist, "blacklist")

    @staticmethod
    def _parse(entries, kind):
        result = []
        for i, raw in enumerate(entries or []):
            try:
                result.append(_Entry(raw, kind, i))
            except (ValueError, portlib.PortSpecError, AttributeError):
                continue   # rejected when saved; an old/edited file must not break the rating
        return result

    @classmethod
    def from_settings(cls, settings):
        lists = settings.get("lists", {})
        return cls(lists.get("whitelist", []), lists.get("blacklist", []))

    def rate_host(self, host):
        for e in self.black:
            if not e.ports and e.host_matches(host):
                return "forbidden", e.label()
        for e in self.white:
            if not e.ports and e.host_matches(host):
                return "ok", e.label()
        return "unlisted", ""

    def rate_port(self, host, proto, port):
        for e in self.black:
            if e.ports and e.host_matches(host) and e.port_matches(proto, port):
                return "forbidden", e.label()
        for e in self.white:
            if e.ports and e.host_matches(host) and e.port_matches(proto, port):
                return "ok", e.label()
        return "unlisted", ""


def apply(result, lists):
    """Copy of a scan result with "rating"/"ratingNote" on every host and open port and
    summary["rating"] = {"forbidden", "ok", "unlisted", "alarms": [...]}."""
    result = copy.deepcopy(result)
    counts = {r: 0 for r in RATINGS}
    alarms = []
    for host in result.get("hosts", []):
        rating, note = lists.rate_host(host)
        host["rating"], host["ratingNote"] = rating, note
        counts[rating] += 1
        if rating == "forbidden":
            alarms.append({"type": "host", "ip": host["ip"], "mac": host.get("mac", ""), "note": note})
        for port in host.get("ports", []):
            rating, note = lists.rate_port(host, port["proto"], port["port"])
            port["rating"], port["ratingNote"] = rating, note
            counts[rating] += 1
            if rating == "forbidden":
                alarms.append({"type": "port", "ip": host["ip"], "proto": port["proto"], "port": port["port"],
                               "note": note})
    summary = result.setdefault("summary", {})
    summary["rating"] = dict(counts, alarms=alarms)
    return result


# ---------------------------------------------------------------------------
# changes since the previous scan of the same profile
# ---------------------------------------------------------------------------

def _target_ranges(result):
    out = []
    for a, b in result.get("targets", {}).get("ranges", []):
        try:
            out.append((int(ipaddress.IPv4Address(a)), int(ipaddress.IPv4Address(b))))
        except ValueError:
            pass
    return out


def _in_ranges(ip, ranges):
    try:
        v = int(ipaddress.IPv4Address(ip))
    except ValueError:
        return False
    return any(a <= v <= b for a, b in ranges)


def _covered_ports(result):
    profile = result.get("profile", {})
    covered = {}
    for proto in ("tcp", "udp"):
        try:
            covered[proto] = set(portlib.parse_spec(profile.get(proto + "Ports", ""), proto))
        except portlib.PortSpecError:
            covered[proto] = set()
    return covered


def compare(previous, current):
    """What changed from `previous` to `current` (both scan results of the same profile).
    Only compares what both scans covered (targets and port lists may differ)."""
    prev_hosts = {h["ip"]: h for h in previous.get("hosts", [])}
    cur_hosts = {h["ip"]: h for h in current.get("hosts", [])}
    prev_ranges, cur_ranges = _target_ranges(previous), _target_ranges(current)
    prev_ports, cur_ports = _covered_ports(previous), _covered_ports(current)
    new_hosts = [ip for ip in cur_hosts if ip not in prev_hosts and _in_ranges(ip, prev_ranges)]
    gone_hosts = [ip for ip in prev_hosts if ip not in cur_hosts and _in_ranges(ip, cur_ranges)]
    opened, closed, mac_changed = [], [], []
    for ip in cur_hosts:
        if ip not in prev_hosts:
            continue
        before = {(p["proto"], p["port"]) for p in prev_hosts[ip].get("ports", [])}
        after = {(p["proto"], p["port"]) for p in cur_hosts[ip].get("ports", [])}
        for proto, port in sorted(after - before):
            if port in prev_ports.get(proto, ()):
                opened.append({"ip": ip, "proto": proto, "port": port})
        for proto, port in sorted(before - after):
            if port in cur_ports.get(proto, ()):
                closed.append({"ip": ip, "proto": proto, "port": port})
        old_mac, new_mac = prev_hosts[ip].get("mac", ""), cur_hosts[ip].get("mac", "")
        if old_mac and new_mac and old_mac != new_mac:
            mac_changed.append({"ip": ip, "old": old_mac, "new": new_mac})
    key = lambda ip: int(ipaddress.IPv4Address(ip))   # noqa: E731
    return {
        "previousId": previous.get("id"),
        "previousFinishedAt": previous.get("finishedAt"),
        "newHosts": sorted(new_hosts, key=key),
        "goneHosts": sorted(gone_hosts, key=key),
        "openedPorts": opened,
        "closedPorts": closed,
        "macChanged": mac_changed,
        "total": len(new_hosts) + len(gone_hosts) + len(opened) + len(closed) + len(mac_changed),
    }
