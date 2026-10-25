"""Service identification: parsers on captured/hand-built answers, and the async probes against local servers."""

import asyncio
import base64
import os
import shutil
import ssl
import struct
import subprocess
import tempfile
import unittest
from unittest import mock

from app import probes
from tests.helpers import TcpServer

# self-signed test certificate (public part only): O=ACME Automation, CN=plc-01,
# SAN DNS:plc-01.local + IP:192.168.1.2, valid 2026-10-25 .. 2036-10-22
CERT_DER = base64.b64decode(
    "MIIByzCCAXCgAwIBAgIUcD7+kB6wVB2Bga768Xo0iCfg96cwCgYIKoZIzj0EAwIwKzEYMBYGA1UECgwPQUNNRSBBdXRvbWF0aW9u"
    "MQ8wDQYDVQQDDAZwbGMtMDEwHhcNMjYxMDI1MTQyNTMzWhcNMzYxMDIyMTQyNTMzWjArMRgwFgYDVQQKDA9BQ01FIEF1dG9tYXRp"
    "b24xDzANBgNVBAMMBnBsYy0wMTBZMBMGByqGSM49AgEGCCqGSM49AwEHA0IABEyWC9V8RKkhAkOSHcM5OgpCR7rj7386ohvDegaY"
    "1yQks7YYidcHvVQinCelwsiOmExKA+z0Cfe/H/K+WXKqTJejcjBwMB0GA1UdDgQWBBQYWkER+DmaXvvfa4AbS/l9AVcD+jAfBgNV"
    "HSMEGDAWgBQYWkER+DmaXvvfa4AbS/l9AVcD+jAPBgNVHRMBAf8EBTADAQH/MB0GA1UdEQQWMBSCDHBsYy0wMS5sb2NhbIcEwKgB"
    "AjAKBggqhkjOPQQDAgNJADBGAiEAzAbcvSAdyazXsb/KyMQJ9BGq/umdA4ZQwEA53q9xI6ICIQDn05i78PI3TM/DtaPiOxibi3jE"
    "jrsV3/oPsI5gyLHgVQ==")


def run(coro):
    return asyncio.run(coro)


class ParserTest(unittest.TestCase):
    def test_banners(self):
        self.assertEqual(probes.classify_banner(b"SSH-2.0-OpenSSH_9.6p1 Ubuntu-3\r\n"), ("ssh", "OpenSSH 9.6p1 Ubuntu-3"))
        self.assertEqual(probes.classify_banner(b"220 ProFTPD Server (FTP) ready\r\n")[0], "ftp")
        self.assertEqual(probes.classify_banner(b"220 mail ESMTP Postfix\r\n")[0], "smtp")
        self.assertEqual(probes.classify_banner(b"\xff\xfd\x18\xff\xfd\x20")[0], "telnet")
        self.assertEqual(probes.classify_banner(b"RFB 003.008\n"), ("vnc", "RFB 003.008"))
        self.assertEqual(probes.classify_banner(b"\x4a\x00\x00\x00\x0a8.0.36\x00rest"), ("mysql", "MySQL 8.0.36"))
        self.assertEqual(probes.classify_banner(b""), ("", ""))
        self.assertEqual(probes.banner_text(b"\xff\x00\x01\x02\x03\x04\x05\x06\x07\x08"), "binary data (10 bytes)")
        self.assertEqual(probes.banner_text(b"220 ready\r\n"), "220 ready")
        self.assertEqual(probes.printable(b"a\x00b\r\nc" + b"x" * 300, 10), "a.b c" + "x" * 4 + "…")

    def test_http(self):
        r = probes.parse_http(b"HTTP/1.1 302 Found\r\nServer: Caddy\r\nLocation: /login\r\n\r\n"
                              b"<html><head><TITLE>\n ctrlX &amp; CORE </TITLE>")
        self.assertEqual(r, {"status": 302, "server": "Caddy", "location": "/login", "title": "ctrlX & CORE"})
        self.assertEqual(probes.parse_http(b"garbage"), {})

    def test_certificate(self):
        c = probes.parse_certificate(CERT_DER)
        self.assertEqual(c["subject"], "CN=plc-01, O=ACME Automation")
        self.assertTrue(c["selfSigned"])
        self.assertEqual(c["notAfter"], "2036-10-22T14:25:33Z")
        self.assertEqual(c["san"], ["plc-01.local", "192.168.1.2"])
        self.assertEqual(probes.parse_certificate(b"\x30\x03\x02\x01"), {})

    def test_modbus(self):
        objects = bytes([0, 4]) + b"ACME" + bytes([1, 3]) + b"X20" + bytes([2, 4]) + b"V1.2"
        pdu = bytes([0x2B, 0x0E, 0x01, 0x01, 0x00, 0x00, 3]) + objects
        frame = struct.pack(">HHHB", 1, 0, len(pdu) + 1, 0xFF) + pdu
        self.assertEqual(probes.parse_modbus(frame),
                         {"protocol": "modbus", "vendor": "ACME", "productCode": "X20", "revision": "V1.2"})
        self.assertEqual(probes.parse_modbus(struct.pack(">HHHB", 1, 0, 3, 0xFF) + bytes([0xAB, 0x01])),
                         {"protocol": "modbus", "exception": 1})
        self.assertEqual(probes.parse_modbus(b"HTTP/1.0 400"), {})

    def test_opcua(self):
        hello = probes.opcua_hello("opc.tcp://192.168.1.2:4840")
        self.assertEqual(hello[:4], b"HELF")
        self.assertEqual(struct.unpack_from("<I", hello, 4)[0], len(hello))
        ack = b"ACKF" + struct.pack("<IIIIII", 28, 0, 65536, 65536, 0, 0)
        self.assertEqual(probes.parse_opcua(ack), {"protocol": "opc-ua", "protocolVersion": 0})
        self.assertEqual(probes.parse_opcua(b"ERRF" + struct.pack("<II", 16, 0x80010000))["error"], "0x80010000")

    def test_enip(self):
        item = struct.pack("<H", 1) + b"\0" * 16 + struct.pack("<HHH", 1, 14, 0x36) + bytes([3, 2])
        item += struct.pack("<H", 0x30) + struct.pack("<I", 0xC0FFEE) + bytes([8]) + b"1756-L71" + bytes([3])
        body = struct.pack("<HHH", 1, 0x0C, len(item)) + item
        frame = struct.pack("<HHII8sI", 0x63, len(body), 0, 0, b"\0" * 8, 0) + body
        r = probes.parse_enip(frame)
        self.assertEqual((r["vendorId"], r["deviceType"], r["productCode"], r["revision"], r["serial"], r["product"]),
                         (1, 14, 0x36, "3.2", "0x00C0FFEE", "1756-L71"))

    def test_cotp_nbstat_dns_ssdp_bacnet(self):
        self.assertEqual(probes.parse_cotp(bytes([3, 0, 0, 22, 17, 0xD0]) + b"\0" * 16)["cotp"], "connection confirmed")
        names = b"DESKTOP-ENG01  \x00" + b"\x04\x00" + b"WORKGROUP      \x00" + b"\x84\x00"
        nb = (b"\x4e\x53\x84\x00\x00\x00\x00\x01\x00\x00\x00\x00" + b"\x20CKAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\x00"
              + b"\x00\x21\x00\x01\x00\x00\x00\x00" + struct.pack(">H", 1 + len(names) + 6) + bytes([2]) + names)
        self.assertEqual(probes.parse_nbstat(nb), {"netbiosName": "DESKTOP-ENG01", "workgroup": "WORKGROUP"})
        # mDNS answer: PTR _services._dns-sd._udp.local -> _http._tcp.local (with name compression)
        q = probes._dns_name("_services._dns-sd._udp.local")
        ans = b"\xc0\x0c" + struct.pack(">HHIH", 12, 1, 120, 2 + 6 + 2) + b"\x05_http\x04_tcp\xc0\x23"
        msg = struct.pack(">HHHHHH", 0, 0x8400, 1, 1, 0, 0) + q + struct.pack(">HH", 12, 1) + ans
        self.assertEqual(probes.parse_udp_response(5353, msg), {"mdnsServices": ["_http._tcp.local"]})
        ssdp = b"HTTP/1.1 200 OK\r\nSERVER: Linux UPnP/1.0 ctrlX\r\nLOCATION: http://192.168.1.2/desc.xml\r\nST: upnp:rootdevice\r\n\r\n"
        self.assertEqual(probes.parse_ssdp(ssdp)["server"], "Linux UPnP/1.0 ctrlX")
        iam = bytes([0x81, 0x0B, 0x00, 0x14, 0x01, 0x20, 0xFF, 0xFF, 0x00, 0xFF, 0x10, 0x00, 0xC4]) + struct.pack(">I", (8 << 22) | 1234)
        self.assertEqual(probes.parse_bacnet(iam), {"protocol": "bacnet", "deviceInstance": 1234})
        self.assertEqual(probes.parse_udp_response(123, bytes([0x24, 2]) + b"\0" * 46), {"protocol": "ntp", "stratum": 2})

    def test_udp_payloads(self):
        self.assertEqual(probes.udp_payload(161), b"")     # no SNMP community strings
        self.assertEqual(len(probes.udp_payload(123)), 48)
        self.assertIn(b"Probe", probes.udp_payload(3702))
        self.assertTrue(probes.udp_payload(53).endswith(struct.pack(">HH", 2, 1)))

    def test_product_line(self):
        self.assertEqual(probes.product_line({"http": {"status": 302, "location": "/login"}}), "HTTP 302 -> /login")
        line = probes.product_line({"http": {"server": "Caddy", "title": "ctrlX"}, "tls": {"subject": "CN=x"}})
        self.assertEqual(line, "Caddy; “ctrlX”; cert CN=x")
        self.assertEqual(probes.product_line({"info": {"protocol": "opc-ua", "protocolVersion": 0}}), "OPC UA (Hello acknowledged)")


class AsyncProbeTest(unittest.TestCase):
    def test_banner_http_and_automation(self):
        ssh = TcpServer(greeting=b"SSH-2.0-dropbear_2022.83\r\n")
        http = TcpServer(reply=b"HTTP/1.0 200 OK\r\nServer: lighttpd\r\n\r\n<title>HMI</title>")
        modbus = TcpServer(reply=lambda req: req[:4] + struct.pack(">HB", 3, req[6]) + bytes([0xAB, 0x01]))
        silent = TcpServer()
        try:
            r = run(probes.identify_tcp("127.0.0.1", ssh.port, 2.0, False))
            self.assertEqual((r["service"], r["product"]), ("ssh", "dropbear 2022.83"))
            with mock.patch.object(probes, "HTTP_PORTS", {http.port}):
                r = run(probes.identify_tcp("127.0.0.1", http.port, 2.0, False))
            self.assertEqual(r["http"], {"status": 200, "server": "lighttpd", "title": "HMI"})
            self.assertTrue(http.received[0].startswith(b"GET / HTTP/1.0\r\n"))
            self.assertEqual(run(probes.identify_tcp("127.0.0.1", silent.port, 0.5, False)), {})
            # automation identification runs only when asked for: without it we only listen
            with mock.patch.dict(probes.AUTOMATION_PORTS, {modbus.port: "modbus"}):
                run(probes.identify_tcp("127.0.0.1", modbus.port, 0.5, False))
                self.assertFalse(any(modbus.received))
                r = run(probes.identify_tcp("127.0.0.1", modbus.port, 2.0, True))
            self.assertEqual(r, {"info": {"protocol": "modbus", "exception": 1}, "service": "modbus"})
            self.assertEqual(modbus.received[-1], probes.MODBUS_READ_DEVICE_ID)
        finally:
            for s in (ssh, http, modbus, silent):
                s.close()

    def test_nothing_sent_to_printer_ports(self):
        self.assertEqual(run(probes.identify_tcp("127.0.0.1", 9100, 0.5, True)), {})

    @unittest.skipUnless(shutil.which("openssl"), "openssl CLI needed to make a test key")
    def test_tls_certificate_and_https(self):
        tmp = tempfile.mkdtemp()
        key, cert = os.path.join(tmp, "k.pem"), os.path.join(tmp, "c.pem")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                        "-nodes", "-keyout", key, "-out", cert, "-days", "30", "-subj", "/CN=hmi-test"],
                       check=True, capture_output=True)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        srv = TcpServer()

        def handle(conn):
            try:
                with ctx.wrap_socket(conn, server_side=True) as tls:
                    tls.recv(4096)
                    tls.sendall(b"HTTP/1.1 200 OK\r\nServer: nginx\r\n\r\n")
            except (OSError, ssl.SSLError):
                pass
        srv._handle = handle
        try:
            with mock.patch.object(probes, "TLS_PORTS", {srv.port}), mock.patch.object(probes, "HTTPS_PORTS", {srv.port}):
                r = run(probes.identify_tcp("127.0.0.1", srv.port, 3.0, False))
            self.assertEqual(r["service"], "https")
            self.assertEqual(r["http"]["server"], "nginx")
            self.assertEqual(r["tls"]["subject"], "CN=hmi-test")
            self.assertTrue(r["tls"]["selfSigned"])
            self.assertTrue(r["tls"]["version"].startswith("TLSv1"))
        finally:
            srv.close()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
