"""Service identification for open ports: what does the device say about itself?

Everything here is read-only and the kind of request any client of that service makes first:
  * server banners (SSH, FTP, SMTP, ...) - we only listen
  * HTTP `GET /` on web ports, TLS handshake + certificate on TLS ports
  * with "identify automation devices" on: the standard identification requests of
    Modbus/TCP (Read Device Identification), OPC UA (Hello), EtherNet/IP (ListIdentity) and
    ISO-on-TCP/S7 (COTP connection request) - nothing is written, no session is used further
  * UDP: the protocols' own discovery/status requests (DNS, NTP, NetBIOS name status, SSDP, mDNS,
    WS-Discovery, CoAP, EtherNet/IP, BACnet Who-Is); other UDP ports get an empty datagram
No logins, no credentials, no default passwords: this is an inventory, not a penetration test.
Data is never sent to raw printer ports (ports.NO_SEND_TCP).

The parsers are pure functions on bytes (unit-tested); the async functions do the I/O.
"""

import asyncio
import re
import ssl
import struct
import uuid
import warnings
from datetime import datetime, timezone

from app import ports as portlib

HTTP_PORTS = {80, 81, 1880, 3000, 5000, 8000, 8008, 8080, 8081, 8888, 9000, 9090}
TLS_PORTS = {443, 465, 636, 993, 995, 2376, 4843, 5671, 6443, 8443, 8883, 9443}
HTTPS_PORTS = {443, 8443, 9443, 6443}
# port -> identification request (only with "identify automation devices")
AUTOMATION_PORTS = {502: "modbus", 4840: "opc-ua", 44818: "ethernet-ip", 102: "iso-tsap"}
MAX_BANNER = 160
READ_LIMIT = 16384


def is_binary(data):
    """True when less than 70 % of the bytes are printable text (e.g. a binary protocol greeting)."""
    if not data:
        return False
    text = sum(1 for b in data if 32 <= b < 127 or b in (9, 10, 13))
    return text < 0.7 * len(data)


def banner_text(data):
    return "binary data ({} bytes)".format(len(data)) if is_binary(data) else printable(data)


def printable(data, limit=MAX_BANNER):
    """Bytes -> one printable line (control characters replaced), at most limit chars."""
    if isinstance(data, bytes):
        data = data.decode("latin-1", "replace")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", ".", data)   # tab/CR/LF become spaces below
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


# ---------------------------------------------------------------------------
# banners
# ---------------------------------------------------------------------------

def classify_banner(data):
    """(service, product) from the first bytes a server sends without being asked."""
    if not data:
        return "", ""
    if data.startswith(b"SSH-"):
        line = data.split(b"\n", 1)[0].strip().decode("latin-1")
        parts = line.split("-", 2)
        software = parts[2] if len(parts) == 3 else line
        return "ssh", software.replace("_", " ", 1)
    if data[0] == 0xFF and len(data) > 2 and data[1] in (0xFB, 0xFC, 0xFD, 0xFE):
        return "telnet", ""
    if data.startswith(b"RFB "):
        return "vnc", "RFB " + data[4:11].decode("latin-1").strip()
    text = data.decode("latin-1", "replace")
    if text.startswith("220") and ("FTP" in text.upper() or "FILEZILLA" in text.upper()):
        return "ftp", printable(text[4:].split("\r")[0])
    if text.startswith("220") and ("SMTP" in text.upper() or "ESMTP" in text.upper()):
        return "smtp", printable(text[4:].split("\r")[0])
    if text.startswith("220"):
        return "", printable(text[4:].split("\r")[0])
    if text.startswith("+OK"):
        return "pop3", printable(text[3:].split("\r")[0])
    if text.startswith("* OK"):
        return "imap", printable(text[4:].split("\r")[0])
    if text.startswith("HTTP/"):
        return "http", ""
    # MySQL greeting: 3 byte length, seq 0, protocol 10, version string NUL-terminated
    if len(data) > 5 and data[3] == 0 and data[4] == 10:
        version = data[5:].split(b"\0", 1)[0]
        if version and all(32 <= b < 127 for b in version):
            return "mysql", "MySQL " + version.decode("ascii")
    return "", ""


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def http_request(host):
    return ("GET / HTTP/1.0\r\nHost: {}\r\nUser-Agent: ctrlx-network-scanner\r\n"
            "Accept: */*\r\nConnection: close\r\n\r\n").format(host).encode("ascii")


def parse_http(data):
    """{"status", "server", "title", "location"} of an HTTP response (partial is fine)."""
    text = data.decode("latin-1", "replace")
    head, _, body = text.partition("\r\n\r\n")
    lines = head.split("\r\n")
    result = {}
    m = re.match(r"HTTP/\d(?:\.\d)?\s+(\d{3})\s*(.*)", lines[0] if lines else "")
    if not m:
        return result
    result["status"] = int(m.group(1))
    for line in lines[1:]:
        key, _, value = line.partition(":")
        key = key.strip().lower()
        if key == "server":
            result["server"] = printable(value.strip(), 100)
        elif key == "location":
            result["location"] = printable(value.strip(), 160)
        elif key == "www-authenticate":
            result["auth"] = printable(value.strip().split(" ")[0], 30)
    t = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
    if t:
        title = re.sub(r"\s+", " ", t.group(1)).strip()
        title = re.sub(r"&amp;", "&", title)
        if title:
            result["title"] = printable(title, 100)
    return result


# ---------------------------------------------------------------------------
# TLS certificates (minimal DER reader: subject/issuer CN+O, validity, SAN DNS names)
# ---------------------------------------------------------------------------

_OID_CN = bytes([0x55, 0x04, 0x03])
_OID_O = bytes([0x55, 0x04, 0x0A])
_OID_SAN = bytes([0x55, 0x1D, 0x11])


def _der(data, offset):
    """(tag, content, next_offset) of the DER element at offset."""
    tag = data[offset]
    length = data[offset + 1]
    offset += 2
    if length & 0x80:
        n = length & 0x7F
        length = int.from_bytes(data[offset:offset + n], "big")
        offset += n
    return tag, data[offset:offset + length], offset + length


def _der_children(content):
    offset, result = 0, []
    while offset < len(content):
        tag, value, offset = _der(content, offset)
        result.append((tag, value))
    return result


def _der_name(content):
    """Name -> {"CN": ..., "O": ...}"""
    result = {}
    for _set_tag, rdn in _der_children(content):
        for _seq_tag, atv in _der_children(rdn):
            parts = _der_children(atv)
            if len(parts) == 2 and parts[0][0] == 0x06:
                key = {_OID_CN: "CN", _OID_O: "O"}.get(parts[0][1])
                if key and key not in result:
                    result[key] = parts[1][1].decode("utf-8", "replace")
    return result


def _der_time(tag, value):
    text = value.decode("ascii", "replace").rstrip("Z")
    try:
        if tag == 0x17:   # UTCTime YYMMDDHHMMSS
            dt = datetime.strptime(text[:12], "%y%m%d%H%M%S")
        else:             # GeneralizedTime YYYYMMDDHHMMSS
            dt = datetime.strptime(text[:14], "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def parse_certificate(der):
    """{"subject", "issuer", "notBefore", "notAfter", "selfSigned", "san"} or {} if unreadable."""
    try:
        _t, cert, _ = _der(der, 0)
        _t, tbs, _ = _der(cert, 0)
        fields = _der_children(tbs)
        if fields and fields[0][0] == 0xA0:   # explicit version
            fields = fields[1:]
        # serial, signature algorithm, issuer, validity, subject, spki, [extensions]
        issuer = _der_name(fields[2][1])
        validity = _der_children(fields[3][1])
        subject = _der_name(fields[4][1])
        result = {
            "subject": ", ".join("{}={}".format(k, v) for k, v in sorted(subject.items())),
            "issuer": ", ".join("{}={}".format(k, v) for k, v in sorted(issuer.items())),
            "notBefore": _der_time(*validity[0]),
            "notAfter": _der_time(*validity[1]),
            "selfSigned": subject == issuer,
        }
        san = []
        for tag, value in fields[5:]:
            if tag != 0xA3:   # [3] extensions
                continue
            for _st, ext in _der_children(_der_children(value)[0][1]):
                parts = _der_children(ext)
                if parts and parts[0][1] == _OID_SAN:
                    for gtag, gval in _der_children(_der_children(parts[-1][1])[0][1]):
                        if gtag == 0x82:   # dNSName
                            san.append(gval.decode("ascii", "replace"))
                        elif gtag == 0x87 and len(gval) == 4:   # iPAddress
                            san.append(".".join(str(b) for b in gval))
        if san:
            result["san"] = san[:10]
        return result
    except (IndexError, ValueError, TypeError):
        return {}


def tls_context():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE   # we want to see the certificate, not trust it
    try:
        # accept old devices' TLS 1.0/1.1 and weak ciphers - they are what we want to find
        with warnings.catch_warnings():   # TLS 1.0 is deprecated - which is why we want to see it
            warnings.simplefilter("ignore", DeprecationWarning)
            ctx.minimum_version = ssl.TLSVersion.TLSv1
        ctx.set_ciphers("ALL:@SECLEVEL=0")
    except (ValueError, ssl.SSLError):
        pass
    return ctx


# ---------------------------------------------------------------------------
# automation protocols (identification requests)
# ---------------------------------------------------------------------------

# Modbus/TCP: MBAP (transaction 1, protocol 0, length 5, unit 0xFF = the device itself) +
# function 0x2B / MEI 0x0E Read Device Identification, basic objects (vendor, product, revision)
MODBUS_READ_DEVICE_ID = bytes([0x00, 0x01, 0x00, 0x00, 0x00, 0x05, 0xFF, 0x2B, 0x0E, 0x01, 0x00])


def parse_modbus(data):
    if len(data) < 9 or data[2:4] != b"\x00\x00":
        return {}
    func = data[7]
    if func == 0xAB:
        return {"protocol": "modbus", "exception": data[8]}
    if func != 0x2B or len(data) < 14 or data[8] != 0x0E:
        return {"protocol": "modbus"} if func & 0x7F == 0x2B else {}
    count = data[13]
    offset, objects = 14, {}
    names = {0: "vendor", 1: "productCode", 2: "revision"}
    for _ in range(count):
        if offset + 2 > len(data):
            break
        oid, olen = data[offset], data[offset + 1]
        value = data[offset + 2:offset + 2 + olen].decode("latin-1", "replace")
        if oid in names:
            objects[names[oid]] = printable(value, 60)
        offset += 2 + olen
    result = {"protocol": "modbus"}
    result.update(objects)
    return result


def opcua_hello(endpoint_url):
    url = endpoint_url.encode("utf-8")
    body = struct.pack("<IIIII", 0, 65536, 65536, 0, 0) + struct.pack("<i", len(url)) + url
    return b"HELF" + struct.pack("<I", 8 + len(body)) + body


def parse_opcua(data):
    if data[:4] == b"ACKF" and len(data) >= 12:
        return {"protocol": "opc-ua", "protocolVersion": struct.unpack_from("<I", data, 8)[0]}
    if data[:4] == b"ERRF" and len(data) >= 12:
        return {"protocol": "opc-ua", "error": "0x{:08X}".format(struct.unpack_from("<I", data, 8)[0])}
    return {}


# EtherNet/IP encapsulation header, command ListIdentity (0x63), no data
ENIP_LIST_IDENTITY = struct.pack("<HHII8sI", 0x63, 0, 0, 0, b"\0" * 8, 0)


def parse_enip(data):
    if len(data) < 26 or struct.unpack_from("<H", data, 0)[0] != 0x63:
        return {}
    count = struct.unpack_from("<H", data, 24)[0]
    if count < 1 or len(data) < 30:
        return {"protocol": "ethernet-ip"}
    item_type, _item_len = struct.unpack_from("<HH", data, 26)
    if item_type != 0x0C:
        return {"protocol": "ethernet-ip"}
    o = 30 + 2 + 16   # protocol version, socket address
    if len(data) < o + 15:
        return {"protocol": "ethernet-ip"}
    vendor, dtype, product = struct.unpack_from("<HHH", data, o)
    major, minor = data[o + 6], data[o + 7]
    serial = struct.unpack_from("<I", data, o + 10)[0]
    nlen = data[o + 14]
    name = data[o + 15:o + 15 + nlen].decode("latin-1", "replace")
    return {"protocol": "ethernet-ip", "vendorId": vendor, "deviceType": dtype, "productCode": product,
            "revision": "{}.{}".format(major, minor), "serial": "0x{:08X}".format(serial),
            "product": printable(name, 60)}


# ISO-on-TCP (RFC 1006) + COTP connection request, TSAPs 0x0100 -> 0x0102 (PG/PC to rack 0 slot 2)
COTP_CONNECT = bytes([0x03, 0x00, 0x00, 0x16, 0x11, 0xE0, 0x00, 0x00, 0x00, 0x01, 0x00,
                      0xC0, 0x01, 0x0A, 0xC1, 0x02, 0x01, 0x00, 0xC2, 0x02, 0x01, 0x02])


def parse_cotp(data):
    if len(data) >= 6 and data[0] == 0x03 and data[1] == 0x00:
        pdu = data[5] & 0xF0
        if pdu == 0xD0:
            return {"protocol": "iso-tsap", "cotp": "connection confirmed"}
        if pdu == 0x80:
            return {"protocol": "iso-tsap", "cotp": "disconnect"}
        return {"protocol": "iso-tsap"}
    return {}


# ---------------------------------------------------------------------------
# UDP requests
# ---------------------------------------------------------------------------

def _dns_name(name):
    out = b""
    for label in name.strip(".").split("."):
        out += bytes([len(label)]) + label.encode("ascii")
    return out + b"\0"


DNS_QUERY = bytes([0x4E, 0x53, 0x01, 0x00, 0, 1, 0, 0, 0, 0, 0, 0]) + b"\0" + struct.pack(">HH", 2, 1)
NTP_REQUEST = bytes([0x23]) + b"\0" * 47
NBSTAT_QUERY = (bytes([0x4E, 0x53, 0x00, 0x00, 0, 1, 0, 0, 0, 0, 0, 0, 0x20]) + b"CK" + b"A" * 30 + b"\0"
                + struct.pack(">HH", 0x21, 1))
SSDP_MSEARCH = (b"M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
                b"MX: 1\r\nST: ssdp:all\r\n\r\n")
MDNS_SERVICES_QUERY = (bytes([0x4E, 0x53, 0x00, 0x00, 0, 1, 0, 0, 0, 0, 0, 0])
                       + _dns_name("_services._dns-sd._udp.local") + struct.pack(">HH", 12, 1))
COAP_DISCOVERY = bytes([0x40, 0x01, 0x4E, 0x53, 0xBB]) + b".well-known" + bytes([0x04]) + b"core"
BACNET_WHO_IS = bytes([0x81, 0x0A, 0x00, 0x08, 0x01, 0x00, 0x10, 0x08])


def wsd_probe():
    return ('<?xml version="1.0" encoding="utf-8"?>'
            '<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope" '
            'xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
            'xmlns:wsd="http://schemas.xmlsoap.org/ws/2005/04/discovery">'
            '<soap:Header><wsa:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</wsa:To>'
            '<wsa:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</wsa:Action>'
            '<wsa:MessageID>urn:uuid:{}</wsa:MessageID></soap:Header>'
            '<soap:Body><wsd:Probe/></soap:Body></soap:Envelope>').format(uuid.uuid4()).encode("utf-8")


def udp_payload(port):
    """The request sent to a UDP port (b"" = empty datagram)."""
    return {
        53: DNS_QUERY, 123: NTP_REQUEST, 137: NBSTAT_QUERY, 1900: SSDP_MSEARCH, 5353: MDNS_SERVICES_QUERY,
        5683: COAP_DISCOVERY, 44818: ENIP_LIST_IDENTITY, 47808: BACNET_WHO_IS,
    }.get(port, wsd_probe() if port == 3702 else b"")


def parse_nbstat(data):
    """NetBIOS node status answer -> {"netbiosName", "workgroup"}"""
    try:
        o = 12
        while data[o] != 0:          # answer name (encoded, 34 bytes)
            o += data[o] + 1
        o += 1 + 2 + 2 + 4 + 2       # NUL, type, class, ttl, rdlength
        count = data[o]
        o += 1
        result = {}
        for _ in range(count):
            name = data[o:o + 15].decode("latin-1").strip()
            suffix, flags = data[o + 15], struct.unpack_from(">H", data, o + 16)[0]
            group = bool(flags & 0x8000)
            if suffix == 0x00 and not group and "netbiosName" not in result:
                result["netbiosName"] = name
            elif suffix == 0x00 and group and "workgroup" not in result:
                result["workgroup"] = name
            o += 18
        return result
    except (IndexError, struct.error):
        return {}


def _dns_read_name(data, o, depth=0):
    labels = []
    while True:
        n = data[o]
        if n == 0:
            return ".".join(labels), o + 1
        if n & 0xC0 == 0xC0:
            if depth > 10:
                raise ValueError("compression loop")
            ptr = struct.unpack_from(">H", data, o)[0] & 0x3FFF
            name, _ = _dns_read_name(data, ptr, depth + 1)
            labels.append(name)
            return ".".join(labels), o + 2
        labels.append(data[o + 1:o + 1 + n].decode("utf-8", "replace"))
        o += 1 + n


def parse_dns_records(data):
    """[(name, type, value)] of all answer/authority/additional records (PTR/A/SRV/TXT names)."""
    records = []
    try:
        qd, an, ns, ar = struct.unpack_from(">HHHH", data, 4)
        o = 12
        for _ in range(qd):
            _, o = _dns_read_name(data, o)
            o += 4
        for _ in range(an + ns + ar):
            name, o = _dns_read_name(data, o)
            rtype, _rclass, _ttl, rdlen = struct.unpack_from(">HHIH", data, o)
            o += 10
            rdata_at = o
            value = None
            if rtype == 12:      # PTR
                value, _ = _dns_read_name(data, rdata_at)
            elif rtype == 1 and rdlen == 4:
                value = ".".join(str(b) for b in data[rdata_at:rdata_at + 4])
            elif rtype == 33:    # SRV: priority, weight, port, target
                port = struct.unpack_from(">H", data, rdata_at + 4)[0]
                target, _ = _dns_read_name(data, rdata_at + 6)
                value = "{}:{}".format(target, port)
            elif rtype == 2:     # NS
                value, _ = _dns_read_name(data, rdata_at)
            records.append((name, rtype, value))
            o = rdata_at + rdlen
    except (IndexError, struct.error, ValueError, UnicodeError):
        pass
    return records


def parse_ssdp(data):
    text = data.decode("latin-1", "replace")
    if not text.startswith("HTTP/1.1 200") and not text.upper().startswith("NOTIFY"):
        return {}
    result = {}
    for line in text.split("\r\n")[1:]:
        key, _, value = line.partition(":")
        key = key.strip().upper()
        if key in ("SERVER", "LOCATION", "ST", "USN"):
            result[key.lower()] = printable(value.strip(), 120)
    return result


def parse_bacnet(data):
    # BVLC (4) + NPDU (>=2) ... APDU 0x10 0x00 (I-Am) + object id tag 0xC4 + 4 bytes
    i = data.find(b"\x10\x00\xc4")
    if data[:1] != b"\x81" or i < 0 or len(data) < i + 7:
        return {"protocol": "bacnet"} if data[:1] == b"\x81" else {}
    oid = struct.unpack_from(">I", data, i + 3)[0]
    return {"protocol": "bacnet", "deviceInstance": oid & 0x3FFFFF}


def parse_udp_response(port, data):
    """What a UDP answer tells about the device (always at least {} - any answer = open)."""
    try:
        if port == 123 and len(data) >= 48 and data[0] & 0x07 == 4:
            return {"protocol": "ntp", "stratum": data[1]}
        if port == 137:
            return parse_nbstat(data)
        if port == 1900:
            return parse_ssdp(data)
        if port == 5353:
            services = sorted({v for _n, t, v in parse_dns_records(data) if t == 12 and v})
            return {"mdnsServices": services[:20]} if services else {}
        if port == 44818:
            return parse_enip(data)
        if port == 47808:
            return parse_bacnet(data)
        if port == 5683 and len(data) > 4 and data[0] >> 6 == 1:
            payload = data.split(b"\xff", 1)[1] if b"\xff" in data else b""
            return {"protocol": "coap", "resources": printable(payload, 160)} if payload else {"protocol": "coap"}
        if port == 3702:
            xaddrs = re.search(rb"XAddrs>([^<]+)<", data)
            types = re.search(rb"Types>([^<]+)<", data)
            result = {"protocol": "ws-discovery"}
            if types:
                result["types"] = printable(types.group(1), 120)
            if xaddrs:
                result["xaddrs"] = printable(xaddrs.group(1), 160)
            return result
        if port == 53 and len(data) >= 12:
            return {"protocol": "dns", "rcode": data[3] & 0x0F}
    except (IndexError, struct.error):
        pass
    return {}


# ---------------------------------------------------------------------------
# async I/O (one open TCP port)
# ---------------------------------------------------------------------------

async def _read_some(reader, timeout, limit=READ_LIMIT):
    data = b""
    try:
        while len(data) < limit:
            chunk = await asyncio.wait_for(reader.read(limit - len(data)), timeout)
            if not chunk:
                break
            data += chunk
            timeout = min(timeout, 0.5)   # after the first bytes only wait briefly for more
    except (asyncio.TimeoutError, ConnectionError, OSError):
        pass
    return data


async def _close(writer):
    try:
        writer.close()
        await asyncio.wait_for(writer.wait_closed(), 1.0)
    except (asyncio.TimeoutError, ConnectionError, OSError, ssl.SSLError):
        pass


async def _exchange(ip, port, request, timeout, tls=None):
    """Connect, send request (or nothing), read the answer. Returns (data, ssl_object_info)."""
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(ip, port, ssl=tls, server_hostname=None if tls is None else ""), timeout)
    tls_info = None
    try:
        if tls is not None:
            sslobj = writer.get_extra_info("ssl_object")
            if sslobj is not None:
                tls_info = {"version": sslobj.version()}
                cipher = sslobj.cipher()
                if cipher:
                    tls_info["cipher"] = cipher[0]
                der = sslobj.getpeercert(binary_form=True)
                if der:
                    tls_info.update(parse_certificate(der))
        if request:
            writer.write(request)
            await asyncio.wait_for(writer.drain(), timeout)
        data = await _read_some(reader, timeout)
    finally:
        await _close(writer)
    return data, tls_info


async def identify_tcp(ip, port, timeout, identify_automation):
    """Find out what runs on an open TCP port. Returns a dict with any of
    service, product, banner, http, tls, info. Never raises."""
    result = {}
    if port in portlib.NO_SEND_TCP:
        return result
    try:
        if port in TLS_PORTS:
            try:
                request = http_request(ip) if port in HTTPS_PORTS else None
                data, tls_info = await _exchange(ip, port, request, timeout, tls=tls_context())
                if tls_info:
                    result["tls"] = tls_info
                if data.startswith(b"HTTP/"):
                    result["service"] = "https"
                    result["http"] = parse_http(data)
                return result
            except (ssl.SSLError, ConnectionError, OSError, asyncio.TimeoutError, EOFError) as exc:
                result["tls"] = {"error": printable(str(exc) or type(exc).__name__, 100)}
                return result

        kind = AUTOMATION_PORTS.get(port) if identify_automation else None
        if kind:
            request, parser = {
                "modbus": (MODBUS_READ_DEVICE_ID, parse_modbus),
                "opc-ua": (opcua_hello("opc.tcp://{}:{}".format(ip, port)), parse_opcua),
                "ethernet-ip": (ENIP_LIST_IDENTITY, parse_enip),
                "iso-tsap": (COTP_CONNECT, parse_cotp)}[kind]
            data, _ = await _exchange(ip, port, request, timeout)
            info = parser(data)
            if info:
                result["info"] = info
                result["service"] = info.get("protocol", "")
            return result

        # server-first protocols: just listen
        data, _ = await _exchange(ip, port, None, min(timeout, 2.0))
        if data:
            service, product = classify_banner(data)
            if service:
                result["service"] = service
            if product:
                result["product"] = product
            result["banner"] = banner_text(data)
            return result
        if port in HTTP_PORTS or portlib.service_name("tcp", port).startswith("http"):
            data, _ = await _exchange(ip, port, http_request(ip), timeout)
            if data.startswith(b"HTTP/"):
                result["service"] = "http"
                result["http"] = parse_http(data)
    except (ConnectionError, OSError, asyncio.TimeoutError, EOFError, ssl.SSLError):
        pass
    return result


def product_line(entry):
    """Short human description of an identified port for tables and the PDF."""
    info, http, tls = entry.get("info") or {}, entry.get("http") or {}, entry.get("tls") or {}
    parts = []
    if entry.get("product"):
        parts.append(entry["product"])
    if info.get("product"):
        parts.append(info["product"])
    elif info.get("vendor"):
        parts.append(" ".join(x for x in (info.get("vendor"), info.get("productCode"), info.get("revision")) if x))
    if http.get("server"):
        parts.append(http["server"])
    if http.get("title"):
        parts.append("“{}”".format(http["title"]))
    if http.get("status") and not (http.get("server") or http.get("title")):
        parts.append("HTTP {}{}".format(http["status"], " -> " + http["location"] if http.get("location") else ""))
    if tls.get("subject"):
        parts.append("cert " + tls["subject"])
    if tls.get("error"):
        parts.append("TLS: " + tls["error"])
    if info.get("netbiosName"):
        parts.append("NetBIOS " + info["netbiosName"])
    if info.get("server"):
        parts.append(info["server"])
    if info.get("protocol") == "opc-ua" and not parts:
        parts.append("OPC UA (Hello acknowledged)" if "protocolVersion" in info else "OPC UA")
    if info.get("protocol") == "iso-tsap" and not parts:
        parts.append("ISO-on-TCP, " + info.get("cotp", "answered"))
    if info.get("protocol") == "bacnet" and "deviceInstance" in info:
        parts.append("BACnet device {}".format(info["deviceInstance"]))
    if info.get("protocol") == "ntp":
        parts.append("NTP stratum {}".format(info.get("stratum")))
    if not parts and entry.get("banner"):
        parts.append(entry["banner"])
    return "; ".join(parts)
