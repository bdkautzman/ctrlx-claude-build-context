"""netinfo (rtnetlink parsing), target specs and port specs."""

import ipaddress
import socket
import struct
import unittest

from app import netinfo, ports, targets


def rtattr(atype, payload):
    length = 4 + len(payload)
    return struct.pack("=HH", length, atype) + payload + b"\0" * ((4 - length % 4) % 4)


def nlmsg(mtype, body):
    return struct.pack("=IHHII", 16 + len(body), mtype, 0, 1, 0) + body


class NetlinkParseTest(unittest.TestCase):
    def test_link(self):
        body = struct.pack("=BxHiII", 0, 1, 3, netinfo.IFF_UP, 0)
        body += rtattr(netinfo.IFLA_IFNAME, b"XF10\0") + rtattr(netinfo.IFLA_ADDRESS, bytes.fromhex("006034112233"))
        link = netinfo.parse_link(body)
        self.assertEqual((link.index, link.name, link.mac, link.up, link.loopback), (3, "XF10", "00:60:34:11:22:33", True, False))

    def test_addr_prefers_local(self):
        body = struct.pack("=BBBBI", socket.AF_INET, 24, 0, 0, 3)
        body += rtattr(netinfo.IFA_ADDRESS, socket.inet_aton("10.9.9.9")) + rtattr(netinfo.IFA_LOCAL, socket.inet_aton("192.168.1.7"))
        body += rtattr(netinfo.IFA_LABEL, b"XF10\0")
        self.assertEqual(netinfo.parse_addr(body), (3, "192.168.1.7/24", "XF10"))
        v6 = struct.pack("=BBBBI", socket.AF_INET6, 64, 0, 0, 3)
        self.assertIsNone(netinfo.parse_addr(v6))

    def test_neighbor_states(self):
        body = struct.pack("=BBHiHBB", socket.AF_INET, 0, 0, 3, netinfo.NUD_STALE, 0, 1)
        body += rtattr(netinfo.NDA_DST, socket.inet_aton("192.168.1.2")) + rtattr(netinfo.NDA_LLADDR, bytes.fromhex("00603485 75a4".replace(" ", "")))
        n = netinfo.parse_neigh(body)
        self.assertEqual((n.ip, n.mac, n.valid, n.state_name), ("192.168.1.2", "00:60:34:85:75:a4", True, "stale"))
        failed = struct.pack("=BBHiHBB", socket.AF_INET, 0, 0, 3, netinfo.NUD_FAILED, 0, 1)
        failed += rtattr(netinfo.NDA_DST, socket.inet_aton("192.168.1.3"))
        self.assertFalse(netinfo.parse_neigh(failed).valid)

    def test_default_route(self):
        body = struct.pack("=BBBBBBBBI", socket.AF_INET, 0, 0, 0, 254, 3, 0, 1, 0)
        body += rtattr(netinfo.RTA_GATEWAY, socket.inet_aton("192.168.1.1")) + rtattr(netinfo.RTA_OIF, struct.pack("=i", 3))
        r = netinfo.parse_route(body)
        self.assertEqual((r.dst, r.gateway, r.ifindex), ("0.0.0.0/0", "192.168.1.1", 3))
        local_table = struct.pack("=BBBBBBBBI", socket.AF_INET, 32, 0, 0, 255, 2, 0, 1, 0)
        self.assertIsNone(netinfo.parse_route(local_table))

    def test_messages_and_errors(self):
        data = nlmsg(netinfo.RTM_NEWNEIGH, b"abcd") + nlmsg(netinfo.NLMSG_DONE, struct.pack("=i", 0))
        self.assertEqual([t for t, _ in netinfo.iter_messages(data)], [netinfo.RTM_NEWNEIGH, netinfo.NLMSG_DONE])
        with self.assertRaises(netinfo.NetinfoError):
            list(netinfo.iter_messages(nlmsg(netinfo.NLMSG_ERROR, struct.pack("=i", -13))))

    def test_zero_mac_is_no_mac(self):
        self.assertEqual(netinfo.format_mac(b"\0" * 6), "")

    def test_local_networks(self):
        ifaces = [netinfo.Interface(1, "lo", up=True, loopback=True, addresses=["127.0.0.1/8"]),
                  netinfo.Interface(2, "XF10", up=True, addresses=["192.168.1.7/24", "169.254.3.4/16"]),
                  netinfo.Interface(3, "XF11", up=False, addresses=["10.0.0.1/24"])]
        nets = netinfo.local_networks(ifaces)
        self.assertEqual([(n, str(i)) for n, i in nets], [("XF10", "192.168.1.7/24")])
        self.assertEqual(netinfo.find_interface_for("192.168.1.99", ifaces), "XF10")
        self.assertIsNone(netinfo.find_interface_for("10.0.0.5", ifaces))

    def test_live_query_on_this_machine(self):
        try:
            ifaces = netinfo.interfaces()
        except netinfo.NetinfoError as exc:
            self.skipTest(str(exc))
        self.assertTrue(any(i.loopback for i in ifaces))
        self.assertIsNone(netinfo.snapshot()["error"])


class TargetTest(unittest.TestCase):
    NETS = [("XF10", ipaddress.IPv4Interface("192.168.1.7/24")), ("XF11", ipaddress.IPv4Interface("10.0.0.1/30"))]

    def test_check_spec(self):
        self.assertEqual(targets.check_spec("auto, if:XF10 192.168.1.0/24\n10.0.0.5-9 10.1.1.1-10.1.1.4 plc-01.local"), [])
        self.assertTrue(targets.check_spec("300.1.1.1"))
        self.assertTrue(targets.check_spec("10.0.0.9-5"))
        self.assertTrue(targets.check_spec(""))
        self.assertEqual(targets.check_spec("", allow_empty=True), [])
        self.assertTrue(targets.check_spec("auto", allow_auto=False))
        self.assertTrue(targets.check_spec("a$b"))

    def test_expand_auto_excludes_and_order(self):
        addrs, notes = targets.expand("auto", self.NETS, 1024, exclude="192.168.1.100-254, 10.0.0.2")
        self.assertEqual(addrs[0], "10.0.0.1")
        self.assertNotIn("10.0.0.2", addrs)
        self.assertIn("192.168.1.1", addrs)
        self.assertNotIn("192.168.1.0", addrs)
        self.assertNotIn("192.168.1.150", addrs)
        self.assertEqual(len(addrs), 1 + 99)
        self.assertEqual(notes, [])

    def test_expand_limit_checked_before_materializing(self):
        with self.assertRaises(targets.TargetError) as ctx:
            targets.expand("10.0.0.0/8", [], 1024)
        self.assertIn("16777214", str(ctx.exception))

    def test_overlaps_merged_names_resolved(self):
        resolve = {"plc": "192.168.1.5"}.get
        addrs, notes = targets.expand("192.168.1.1-10, 192.168.1.5-12, plc, ghost", [], 100,
                                      resolve=lambda n: resolve(n) or (_ for _ in ()).throw(OSError("unknown")))
        self.assertEqual(len(addrs), 12)
        self.assertTrue(any("ghost" in n for n in notes))

    def test_interface_target_and_count(self):
        self.assertEqual(targets.expand("if:XF11", self.NETS, 10)[0], ["10.0.0.1", "10.0.0.2"])
        self.assertEqual(targets.count("auto", self.NETS), 256)
        self.assertIsNone(targets.count("plc.local", self.NETS))
        self.assertEqual(targets.expand("if:XF99", self.NETS, 10)[1], ["interface XF99 has no IPv4 network"])


class PortSpecTest(unittest.TestCase):
    def test_presets_and_ranges(self):
        self.assertEqual(ports.parse_spec("22, 80-82", "tcp"), [22, 80, 81, 82])
        self.assertIn(502, ports.parse_spec("industrial", "tcp"))
        self.assertIn(4840, ports.parse_spec("common", "tcp"))
        self.assertEqual(len(ports.parse_spec("all", "tcp")), 65535)
        self.assertIn(47808, ports.parse_spec("common", "udp"))
        self.assertEqual(ports.parse_spec("", "udp"), [])
        for bad in ("0", "70000", "90-80", "web", "x"):
            proto = "udp" if bad == "web" else "tcp"
            with self.assertRaises(ports.PortSpecError, msg=bad):
                ports.parse_spec(bad, proto)

    def test_rating_ports(self):
        self.assertEqual(ports.parse_rating_ports("502, tcp/22 udp/161;tcp/8000-8100"),
                         [(None, 502, 502), ("tcp", 22, 22), ("udp", 161, 161), ("tcp", 8000, 8100)])
        with self.assertRaises(ports.PortSpecError):
            ports.parse_rating_ports("icmp/1")

    def test_printers_never_get_data(self):
        self.assertTrue({9100, 9101}.issubset(ports.NO_SEND_TCP))


if __name__ == "__main__":
    unittest.main()
