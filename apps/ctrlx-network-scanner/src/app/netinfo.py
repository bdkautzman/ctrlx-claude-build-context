"""This device's network interfaces, routes and neighbor (ARP) table - stdlib only, via rtnetlink.

Why netlink: in a strict snap with only the `network` plug, AppArmor allows `network netlink
raw/dgram` and seccomp allows `AF_NETLINK` with `NETLINK_ROUTE` (verified on ctrlX OS 4.6), while
raw/packet sockets, `/proc/net/arp` and ICMP sockets are not available (gotchas). The kernel's
neighbor table tells us which hosts on a directly connected network answered ARP, and their MAC
addresses, without sending anything ourselves.

The parsers (`parse_*`) work on bytes and are unit-tested with hand-built messages; `query()` does
the socket I/O.
"""

import ipaddress
import os
import socket
import struct
from dataclasses import dataclass, field
from typing import List, Optional

# rtnetlink message types and flags (linux/rtnetlink.h, linux/netlink.h)
NLMSG_ERROR = 2
NLMSG_DONE = 3
RTM_NEWLINK, RTM_GETLINK = 16, 18
RTM_NEWADDR, RTM_GETADDR = 20, 22
RTM_NEWROUTE, RTM_GETROUTE = 24, 26
RTM_NEWNEIGH, RTM_GETNEIGH = 28, 30
NLM_F_REQUEST = 0x01
NLM_F_DUMP = 0x300

IFLA_ADDRESS, IFLA_IFNAME, IFLA_OPERSTATE = 1, 3, 16
IFA_ADDRESS, IFA_LOCAL, IFA_LABEL = 1, 2, 3
RTA_DST, RTA_OIF, RTA_GATEWAY, RTA_TABLE = 1, 4, 5, 15
NDA_DST, NDA_LLADDR = 1, 2
RT_TABLE_MAIN = 254
IFF_UP, IFF_LOOPBACK = 0x1, 0x8

# neighbor states (linux/neighbour.h)
NUD_INCOMPLETE, NUD_REACHABLE, NUD_STALE, NUD_DELAY = 0x01, 0x02, 0x04, 0x08
NUD_PROBE, NUD_FAILED, NUD_NOARP, NUD_PERMANENT = 0x10, 0x20, 0x40, 0x80
NUD_NAMES = {NUD_INCOMPLETE: "incomplete", NUD_REACHABLE: "reachable", NUD_STALE: "stale",
             NUD_DELAY: "delay", NUD_PROBE: "probe", NUD_FAILED: "failed", NUD_NOARP: "noarp",
             NUD_PERMANENT: "permanent"}
# a MAC address in one of these states means the host answered ARP (now or a short while ago)
NUD_VALID = NUD_REACHABLE | NUD_STALE | NUD_DELAY | NUD_PROBE | NUD_PERMANENT

_NLMSGHDR = struct.Struct("=IHHII")      # len, type, flags, seq, pid
_RTATTR = struct.Struct("=HH")           # len, type
_IFINFOMSG = struct.Struct("=BxHiII")    # family, type, index, flags, change
_IFADDRMSG = struct.Struct("=BBBBI")     # family, prefixlen, flags, scope, index
_RTMSG = struct.Struct("=BBBBBBBBI")     # family, dst_len, src_len, tos, table, protocol, scope, type, flags
_NDMSG = struct.Struct("=BBHiHBB")       # family, pad1, pad2, ifindex, state, flags, type


class NetinfoError(Exception):
    pass


@dataclass
class Interface:
    index: int
    name: str
    mac: str = ""
    up: bool = False
    loopback: bool = False
    addresses: List[str] = field(default_factory=list)   # "192.168.1.7/24"

    def networks(self):
        """IPv4 networks of this interface (ipaddress.IPv4Interface objects)."""
        return [ipaddress.IPv4Interface(a) for a in self.addresses]


@dataclass
class Neighbor:
    ip: str
    mac: str
    state: int
    ifindex: int

    @property
    def valid(self):
        return bool(self.mac) and bool(self.state & NUD_VALID)

    @property
    def state_name(self):
        return "|".join(n for bit, n in sorted(NUD_NAMES.items()) if self.state & bit) or "none"


@dataclass
class Route:
    dst: str            # "0.0.0.0/0" for the default route
    gateway: str
    ifindex: int


def _align(n):
    return (n + 3) & ~3


def format_mac(raw):
    """MAC bytes -> "aa:bb:cc:dd:ee:ff"; "" for none or all zeros (loopback, unresolved)."""
    return ":".join("{:02x}".format(b) for b in raw) if raw and any(raw) else ""


def iter_attrs(data, offset):
    """Yield (type, payload) of the rtattrs in data[offset:]."""
    while offset + _RTATTR.size <= len(data):
        length, atype = _RTATTR.unpack_from(data, offset)
        if length < _RTATTR.size or offset + length > len(data):
            break
        yield atype & 0x3FFF, data[offset + _RTATTR.size:offset + length]
        offset += _align(length)


def iter_messages(data):
    """Yield (type, body) of the netlink messages in one recv() buffer. Raises NetinfoError
    for NLMSG_ERROR with a non-zero error code."""
    offset = 0
    while offset + _NLMSGHDR.size <= len(data):
        length, mtype, _flags, _seq, _pid = _NLMSGHDR.unpack_from(data, offset)
        if length < _NLMSGHDR.size or offset + length > len(data):
            break
        body = data[offset + _NLMSGHDR.size:offset + length]
        if mtype == NLMSG_ERROR:
            code = struct.unpack_from("=i", body)[0] if len(body) >= 4 else -1
            if code:
                raise NetinfoError("netlink error {}: {}".format(-code, os.strerror(-code)))
        yield mtype, body
        offset += _align(length)


def parse_link(body):
    if len(body) < _IFINFOMSG.size:
        return None
    _family, _itype, index, flags, _change = _IFINFOMSG.unpack_from(body)
    iface = Interface(index=index, name="", up=bool(flags & IFF_UP), loopback=bool(flags & IFF_LOOPBACK))
    for atype, payload in iter_attrs(body, _IFINFOMSG.size):
        if atype == IFLA_IFNAME:
            iface.name = payload.split(b"\0", 1)[0].decode("utf-8", "replace")
        elif atype == IFLA_ADDRESS:
            iface.mac = format_mac(payload)
    return iface


def parse_addr(body):
    """(ifindex, "a.b.c.d/prefix", label) of an IPv4 RTM_NEWADDR, else None."""
    if len(body) < _IFADDRMSG.size:
        return None
    family, prefixlen, _flags, _scope, index = _IFADDRMSG.unpack_from(body)
    if family != socket.AF_INET:
        return None
    local = address = None
    label = ""
    for atype, payload in iter_attrs(body, _IFADDRMSG.size):
        if atype == IFA_LOCAL and len(payload) == 4:
            local = socket.inet_ntoa(payload)
        elif atype == IFA_ADDRESS and len(payload) == 4:
            address = socket.inet_ntoa(payload)
        elif atype == IFA_LABEL:
            label = payload.split(b"\0", 1)[0].decode("utf-8", "replace")
    ip = local or address   # IFA_LOCAL is this host's address on point-to-point links
    if ip is None:
        return None
    return index, "{}/{}".format(ip, prefixlen), label


def parse_route(body):
    if len(body) < _RTMSG.size:
        return None
    family, dst_len, _src, _tos, table, _proto, _scope, rtype, _flags = _RTMSG.unpack_from(body)
    if family != socket.AF_INET or rtype != 1:   # RTN_UNICAST only
        return None
    dst, gateway, oif = "0.0.0.0", "", 0
    for atype, payload in iter_attrs(body, _RTMSG.size):
        if atype == RTA_DST and len(payload) == 4:
            dst = socket.inet_ntoa(payload)
        elif atype == RTA_GATEWAY and len(payload) == 4:
            gateway = socket.inet_ntoa(payload)
        elif atype == RTA_OIF and len(payload) == 4:
            oif = struct.unpack("=i", payload)[0]
        elif atype == RTA_TABLE and len(payload) == 4:
            table = struct.unpack("=I", payload)[0]
    if table != RT_TABLE_MAIN:
        return None
    return Route(dst="{}/{}".format(dst, dst_len), gateway=gateway, ifindex=oif)


def parse_neigh(body):
    if len(body) < _NDMSG.size:
        return None
    family, _p1, _p2, ifindex, state, _flags, _ntype = _NDMSG.unpack_from(body)
    if family != socket.AF_INET:
        return None
    ip, mac = None, ""
    for atype, payload in iter_attrs(body, _NDMSG.size):
        if atype == NDA_DST and len(payload) == 4:
            ip = socket.inet_ntoa(payload)
        elif atype == NDA_LLADDR:
            mac = format_mac(payload)
    if ip is None:
        return None
    return Neighbor(ip=ip, mac=mac, state=state, ifindex=ifindex)


def build_request(msg_type, family_struct, seq):
    body = family_struct
    return _NLMSGHDR.pack(_NLMSGHDR.size + len(body), msg_type, NLM_F_REQUEST | NLM_F_DUMP, seq, 0) + body


def query(msg_type, timeout=2.0):
    """Send one rtnetlink dump request, return the list of (type, body) of all answers."""
    if msg_type == RTM_GETLINK:
        req_body = _IFINFOMSG.pack(socket.AF_UNSPEC, 0, 0, 0, 0)
    elif msg_type == RTM_GETADDR:
        req_body = _IFADDRMSG.pack(socket.AF_INET, 0, 0, 0, 0)
    elif msg_type == RTM_GETROUTE:
        req_body = _RTMSG.pack(socket.AF_INET, 0, 0, 0, 0, 0, 0, 0, 0)
    elif msg_type == RTM_GETNEIGH:
        req_body = _NDMSG.pack(socket.AF_INET, 0, 0, 0, 0, 0, 0)
    else:
        raise ValueError("unsupported request {}".format(msg_type))
    try:
        # SOCK_DGRAM: the snap's AppArmor profile names `network netlink dgram` explicitly
        sock = socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM, socket.NETLINK_ROUTE)
    except OSError as exc:
        raise NetinfoError("netlink not available: {}".format(exc))
    with sock:
        sock.settimeout(timeout)
        sock.bind((0, 0))
        sock.sendto(build_request(msg_type, req_body, 1), (0, 0))
        result = []
        while True:
            try:
                data = sock.recv(65536)
            except socket.timeout:
                raise NetinfoError("netlink dump timed out")
            done = False
            for mtype, body in iter_messages(data):
                if mtype == NLMSG_DONE:
                    done = True
                    break
                result.append((mtype, body))
            if done or not data:
                return result


def interfaces():
    """All interfaces with their IPv4 addresses, sorted by index."""
    by_index = {}
    for mtype, body in query(RTM_GETLINK):
        if mtype == RTM_NEWLINK:
            iface = parse_link(body)
            if iface:
                by_index[iface.index] = iface
    for mtype, body in query(RTM_GETADDR):
        if mtype != RTM_NEWADDR:
            continue
        parsed = parse_addr(body)
        if not parsed:
            continue
        index, cidr, label = parsed
        iface = by_index.setdefault(index, Interface(index=index, name=label or str(index)))
        iface.addresses.append(cidr)
    return [by_index[i] for i in sorted(by_index)]


def routes():
    return [r for r in (parse_route(b) for t, b in query(RTM_GETROUTE) if t == RTM_NEWROUTE) if r]


def neighbors():
    return [n for n in (parse_neigh(b) for t, b in query(RTM_GETNEIGH) if t == RTM_NEWNEIGH) if n]


def local_networks(ifaces, include_link_local=False):
    """[(interface name, IPv4Interface)] of the up, non-loopback interfaces - the networks this
    device is connected to."""
    result = []
    for iface in ifaces:
        if not iface.up or iface.loopback:
            continue
        for net in iface.networks():
            if net.ip.is_link_local and not include_link_local:
                continue
            result.append((iface.name, net))
    return result


def snapshot():
    """Interfaces + default gateways as a JSON-friendly dict (for the UI and the scan record).
    Never raises: errors are reported in "error"."""
    try:
        ifaces = interfaces()
    except (NetinfoError, OSError) as exc:
        return {"interfaces": [], "gateways": [], "error": str(exc)}
    names = {i.index: i.name for i in ifaces}
    gateways = []
    try:
        for r in routes():
            if r.dst == "0.0.0.0/0" and r.gateway:
                gateways.append({"ip": r.gateway, "interface": names.get(r.ifindex, "")})
    except (NetinfoError, OSError):
        pass
    result = []
    for iface in ifaces:
        if iface.loopback:
            continue
        result.append({"name": iface.name, "mac": iface.mac, "up": iface.up,
                       "addresses": list(iface.addresses),
                       "networks": [str(n.network) for n in iface.networks()]})
    return {"interfaces": result, "gateways": gateways, "error": None}


def gateway_ips():
    try:
        return {r.gateway for r in routes() if r.gateway}
    except (NetinfoError, OSError):
        return set()


def own_ips(ifaces=None) -> set:
    try:
        ifaces = ifaces if ifaces is not None else interfaces()
    except (NetinfoError, OSError):
        return set()
    return {str(n.ip) for i in ifaces for n in i.networks()}


def neighbor_map(only_valid=True) -> dict:
    """{ip: Neighbor} from the kernel's neighbor table ({} if not readable)."""
    try:
        return {n.ip: n for n in neighbors() if n.valid or not only_valid}
    except (NetinfoError, OSError):
        return {}


def find_interface_for(ip, ifaces) -> Optional[str]:
    """Name of the interface whose network contains ip (directly connected), else None."""
    addr = ipaddress.IPv4Address(ip)
    for name, net in local_networks(ifaces, include_link_local=True):
        if addr in net.network:
            return name
    return None
