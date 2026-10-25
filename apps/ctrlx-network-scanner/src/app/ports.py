"""Port lists, service names and the port specification syntax.

Port spec (per protocol, items separated by commas or spaces):
    common, industrial, web, well-known, all     presets (see PRESETS_TCP / PRESETS_UDP)
    22  8000-8100                                single ports and ranges
Example: "common, industrial, 8000-8100"

Service names are the IANA / vendor-registered uses of a port: they say what usually listens
there, not what was found (service detection fills in what the device itself reports).
"""

import re

_SPLIT = re.compile(r"[\s,;]+")

# (proto, port) -> service name. Curated: IT basics + automation protocols seen on shop floors.
SERVICES = {
    ("tcp", 20): "ftp-data", ("tcp", 21): "ftp", ("tcp", 22): "ssh", ("tcp", 23): "telnet",
    ("tcp", 25): "smtp", ("tcp", 53): "dns", ("tcp", 80): "http", ("tcp", 81): "http-alt",
    ("tcp", 88): "kerberos", ("tcp", 102): "iso-tsap (Siemens S7)", ("tcp", 110): "pop3",
    ("tcp", 111): "rpcbind", ("tcp", 135): "msrpc", ("tcp", 139): "netbios-ssn", ("tcp", 143): "imap",
    ("tcp", 179): "bgp", ("tcp", 389): "ldap", ("tcp", 443): "https", ("tcp", 445): "smb",
    ("tcp", 465): "smtps", ("tcp", 502): "modbus", ("tcp", 515): "lpd", ("tcp", 554): "rtsp",
    ("tcp", 587): "smtp-submission", ("tcp", 631): "ipp", ("tcp", 636): "ldaps",
    ("tcp", 789): "red-lion-crimson", ("tcp", 873): "rsync", ("tcp", 993): "imaps", ("tcp", 995): "pop3s",
    ("tcp", 1080): "socks", ("tcp", 1089): "ff-hse", ("tcp", 1090): "ff-hse", ("tcp", 1091): "ff-hse",
    ("tcp", 1200): "codesys", ("tcp", 1433): "mssql", ("tcp", 1521): "oracle", ("tcp", 1723): "pptp",
    ("tcp", 1880): "node-red", ("tcp", 1883): "mqtt", ("tcp", 1911): "niagara-fox",
    ("tcp", 1962): "pcworx", ("tcp", 2049): "nfs", ("tcp", 2069): "ctrlx-datalayer",
    ("tcp", 2070): "ctrlx-datalayer", ("tcp", 2375): "docker", ("tcp", 2376): "docker-tls",
    ("tcp", 2404): "iec-60870-5-104", ("tcp", 2455): "codesys", ("tcp", 3000): "http-alt",
    ("tcp", 3306): "mysql", ("tcp", 3389): "rdp", ("tcp", 4840): "opc-ua", ("tcp", 4843): "opc-ua-tls",
    ("tcp", 5000): "http-alt", ("tcp", 5007): "melsec-q", ("tcp", 5094): "hart-ip",
    ("tcp", 5432): "postgresql", ("tcp", 5672): "amqp", ("tcp", 5900): "vnc", ("tcp", 5901): "vnc",
    ("tcp", 6379): "redis", ("tcp", 6443): "kubernetes-api", ("tcp", 8000): "http-alt",
    ("tcp", 8008): "http-alt", ("tcp", 8080): "http-alt", ("tcp", 8081): "http-alt",
    ("tcp", 8443): "https-alt", ("tcp", 8883): "mqtt-tls", ("tcp", 8888): "http-alt",
    ("tcp", 9000): "http-alt", ("tcp", 9090): "http-alt", ("tcp", 9100): "jetdirect",
    ("tcp", 9443): "https-alt", ("tcp", 9600): "omron-fins", ("tcp", 18245): "ge-srtp",
    ("tcp", 20000): "dnp3", ("tcp", 27017): "mongodb", ("tcp", 34962): "profinet",
    ("tcp", 34963): "profinet", ("tcp", 34964): "profinet", ("tcp", 44818): "ethernet-ip",
    ("tcp", 48898): "ads (Beckhoff)",
    ("udp", 53): "dns", ("udp", 67): "dhcp", ("udp", 69): "tftp", ("udp", 123): "ntp",
    ("udp", 137): "netbios-ns", ("udp", 138): "netbios-dgm", ("udp", 161): "snmp", ("udp", 162): "snmp-trap",
    ("udp", 500): "isakmp", ("udp", 514): "syslog", ("udp", 520): "rip", ("udp", 623): "ipmi",
    ("udp", 1434): "mssql-monitor", ("udp", 1900): "ssdp", ("udp", 2222): "ethernet-ip-io",
    ("udp", 3702): "ws-discovery", ("udp", 4500): "ipsec-nat-t", ("udp", 5094): "hart-ip",
    ("udp", 5353): "mdns", ("udp", 5683): "coap", ("udp", 9600): "omron-fins",
    ("udp", 34964): "profinet-cm", ("udp", 44818): "ethernet-ip", ("udp", 47808): "bacnet",
}

INDUSTRIAL_TCP = [102, 502, 789, 1089, 1090, 1091, 1200, 1880, 1883, 1911, 1962, 2069, 2070, 2404, 2455,
                  4840, 4843, 5007, 5094, 8883, 9600, 18245, 20000, 34962, 34963, 34964, 44818, 48898]
WEB_TCP = [80, 81, 443, 3000, 5000, 8000, 8008, 8080, 8081, 8443, 8888, 9000, 9090, 9443]
COMMON_TCP = sorted(set([21, 22, 23, 25, 53, 80, 110, 111, 135, 139, 143, 389, 443, 445, 465, 515, 554,
                         587, 631, 636, 993, 995, 1433, 1521, 1723, 2049, 2375, 3306, 3389, 5432, 5672,
                         5900, 6379, 8080, 8443, 9100, 27017] + INDUSTRIAL_TCP + WEB_TCP))

INDUSTRIAL_UDP = [2222, 5094, 9600, 34964, 44818, 47808]
COMMON_UDP = sorted(set([53, 67, 69, 123, 137, 161, 500, 514, 520, 623, 1434, 1900, 3702, 4500, 5353,
                         5683] + INDUSTRIAL_UDP))

PRESETS_TCP = {
    "common": COMMON_TCP,
    "industrial": INDUSTRIAL_TCP,
    "web": WEB_TCP,
    "well-known": list(range(1, 1024)),
    "all": list(range(1, 65536)),
}
PRESETS_UDP = {
    "common": COMMON_UDP,
    "industrial": INDUSTRIAL_UDP,
    "well-known": list(range(1, 1024)),
}

# Ports a TCP scan may connect to but never sends data to: network printers print whatever
# arrives on their raw ports (the same exclusion nmap's version detection has).
NO_SEND_TCP = set(range(9100, 9108))

# Ports used for TCP host discovery ("is anybody there?"): any answer - accepted or refused - means
# the host is up. Kept short: every port is one connection attempt per target address.
DISCOVERY_TCP = [22, 80, 443, 445, 502, 4840, 44818, 102, 135, 8080]


class PortSpecError(ValueError):
    pass


def service_name(proto, port):
    return SERVICES.get((proto, port), "")


def parse_spec(spec, proto):
    """Sorted list of ports for a spec. Raises PortSpecError."""
    presets = PRESETS_TCP if proto == "tcp" else PRESETS_UDP
    ports = set()
    for item in [s for s in _SPLIT.split((spec or "").strip().lower()) if s]:
        if item in presets:
            ports.update(presets[item])
            continue
        m = re.fullmatch(r"(\d{1,5})(?:-(\d{1,5}))?", item)
        if not m:
            raise PortSpecError("{!r}: expected a port, a range (a-b) or one of {}".format(
                item, ", ".join(sorted(presets))))
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if not (1 <= a <= 65535 and 1 <= b <= 65535) or b < a:
            raise PortSpecError("{!r}: ports must be 1..65535, ranges ascending".format(item))
        ports.update(range(a, b + 1))
    return sorted(ports)


def check_spec(spec, proto):
    try:
        parse_spec(spec, proto)
        return []
    except PortSpecError as exc:
        return [str(exc)]


def parse_rating_ports(spec):
    """Port entries of a whitelist/blacklist line: "tcp/22, udp/161, 502, tcp/8000-8100".
    Returns [(proto or None for both, first, last)]. Raises PortSpecError."""
    result = []
    for item in [s for s in _SPLIT.split((spec or "").strip().lower()) if s]:
        m = re.fullmatch(r"(?:(tcp|udp)/)?(\d{1,5})(?:-(\d{1,5}))?", item)
        if not m:
            raise PortSpecError("{!r}: expected e.g. 502, tcp/22, udp/161 or tcp/8000-8100".format(item))
        a = int(m.group(2))
        b = int(m.group(3)) if m.group(3) else a
        if not (1 <= a <= 65535 and 1 <= b <= 65535) or b < a:
            raise PortSpecError("{!r}: ports must be 1..65535, ranges ascending".format(item))
        result.append((m.group(1), a, b))
    return result
