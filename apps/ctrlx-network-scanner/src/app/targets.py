"""Target specifications: which IPv4 addresses a scan profile covers.

Syntax (items separated by commas, spaces or new lines):
    auto                    every IPv4 network this device is connected to (up, non-loopback)
    if:XF10                 the network(s) of one interface
    192.168.1.0/24          a network (without network/broadcast address for prefixes <= /30)
    192.168.1.10-50         a range in the last octet
    192.168.1.10-192.168.1.80   a range
    192.168.1.7             one address
    plc-01.local            a host name (resolved when the scan starts)

`check_spec()` validates the syntax (used when settings are saved); `expand()` turns a spec into
an ordered list of addresses when a scan starts.
"""

import ipaddress
import re
import socket

_SPLIT = re.compile(r"[\s,;]+")
_HOSTNAME = re.compile(r"(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,62})(\.[A-Za-z0-9]([A-Za-z0-9-]{0,62}))*\.?")
_IFNAME = re.compile(r"if:[A-Za-z0-9_.@-]{1,15}")
_SHORT_RANGE = re.compile(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.)(\d{1,3})-(\d{1,3})")

MAX_ITEMS = 256


class TargetError(ValueError):
    pass


def split_items(spec):
    return [s for s in _SPLIT.split(spec or "") if s]


def _parse_ip(text):
    try:
        return ipaddress.IPv4Address(text)
    except ValueError:
        raise TargetError("{!r} is not an IPv4 address".format(text))


def parse_item(item):
    """Classify one item: ("auto", None) | ("if", name) | ("net", IPv4Network) |
    ("range", (first, last)) | ("ip", IPv4Address) | ("name", hostname). Raises TargetError."""
    low = item.lower()
    if low == "auto":
        return "auto", None
    if _IFNAME.fullmatch(item):
        return "if", item[3:]
    if "/" in item:
        try:
            return "net", ipaddress.IPv4Network(item, strict=False)
        except ValueError:
            raise TargetError("{!r} is not an IPv4 network (e.g. 192.168.1.0/24)".format(item))
    m = _SHORT_RANGE.fullmatch(item)
    if m:
        first, last = _parse_ip(m.group(1) + m.group(2)), _parse_ip(m.group(1) + m.group(3))
        if last < first:
            raise TargetError("{!r}: range end is before its start".format(item))
        return "range", (first, last)
    if "-" in item and item.count("-") == 1 and all(c.isdigit() or c in ".-" for c in item):
        a, b = item.split("-")
        first, last = _parse_ip(a), _parse_ip(b)
        if last < first:
            raise TargetError("{!r}: range end is before its start".format(item))
        return "range", (first, last)
    if all(c.isdigit() or c == "." for c in item):
        return "ip", _parse_ip(item)
    if _HOSTNAME.fullmatch(item):
        return "name", item.rstrip(".")
    raise TargetError("{!r}: not an address, network, range, host name, 'auto' or 'if:<name>'".format(item))


def check_spec(spec, allow_auto=True, allow_empty=False):
    """List of error messages for a target spec (empty list = valid)."""
    items = split_items(spec)
    if not items:
        return [] if allow_empty else ["no targets given"]
    if len(items) > MAX_ITEMS:
        return ["at most {} entries".format(MAX_ITEMS)]
    errors = []
    for item in items:
        try:
            kind, _ = parse_item(item)
        except TargetError as exc:
            errors.append(str(exc))
            continue
        if not allow_auto and kind in ("auto", "if"):
            errors.append("{!r} is not allowed here".format(item))
    return errors


def _net_hosts(net):
    if net.prefixlen >= 31:
        return int(net.network_address), int(net.broadcast_address)
    return int(net.network_address) + 1, int(net.broadcast_address) - 1


def expand(spec, local_networks, max_hosts, exclude="", resolve=socket.gethostbyname):
    """Return (addresses as dotted strings in a stable order, notes).

    local_networks: [(interface name, IPv4Interface)] for "auto" / "if:<name>".
    Raises TargetError when the spec is invalid or expands to more than max_hosts addresses
    (checked before materializing the list, so a /8 does not eat the device's memory).
    """
    notes = []
    ranges = []   # (first_int, last_int)
    for item in split_items(spec):
        kind, value = parse_item(item)
        if kind == "auto" or kind == "if":
            matched = [(n, i) for n, i in local_networks if kind == "auto" or n == value]
            if kind == "if" and not matched:
                notes.append("interface {} has no IPv4 network".format(value))
            for _name, iface in matched:
                ranges.append(_net_hosts(iface.network))
        elif kind == "net":
            ranges.append(_net_hosts(value))
        elif kind == "range":
            ranges.append((int(value[0]), int(value[1])))
        elif kind == "ip":
            ranges.append((int(value), int(value)))
        elif kind == "name":
            try:
                ip = int(ipaddress.IPv4Address(resolve(value)))
                ranges.append((ip, ip))
            except (OSError, ValueError) as exc:
                notes.append("cannot resolve {}: {}".format(value, exc))
    excluded = []
    for item in split_items(exclude):
        kind, value = parse_item(item)
        if kind == "net":
            excluded.append((int(value.network_address), int(value.broadcast_address)))
        elif kind == "range":
            excluded.append((int(value[0]), int(value[1])))
        elif kind == "ip":
            excluded.append((int(value), int(value)))
        elif kind == "name":
            try:
                ip = int(ipaddress.IPv4Address(resolve(value)))
                excluded.append((ip, ip))
            except (OSError, ValueError) as exc:
                notes.append("cannot resolve excluded {}: {}".format(value, exc))
    merged = _merge(ranges)
    total = sum(b - a + 1 for a, b in merged)
    if total > max_hosts:
        raise TargetError("the targets cover {} addresses, the limit is {} - narrow the targets "
                          "or raise 'Max. addresses per scan' in the settings".format(total, max_hosts))
    excluded = _merge(excluded)
    result = []
    for a, b in merged:
        for ip in range(a, b + 1):
            if not any(x <= ip <= y for x, y in excluded):
                result.append(str(ipaddress.IPv4Address(ip)))
    if total and not result:
        notes.append("all targets are excluded")
    return result, notes


def count(spec, local_networks):
    """Number of addresses a spec covers before exclusions (None if a host name must be resolved)."""
    ranges = []
    for item in split_items(spec):
        try:
            kind, value = parse_item(item)
        except TargetError:
            return None
        if kind in ("auto", "if"):
            ranges += [_net_hosts(i.network) for n, i in local_networks if kind == "auto" or n == value]
        elif kind == "net":
            ranges.append(_net_hosts(value))
        elif kind == "range":
            ranges.append((int(value[0]), int(value[1])))
        elif kind == "ip":
            ranges.append((int(value), int(value)))
        else:
            return None
    return sum(b - a + 1 for a, b in _merge(ranges))


def _merge(ranges):
    merged = []
    for a, b in sorted(ranges):
        if b < a:
            continue
        if merged and a <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged
