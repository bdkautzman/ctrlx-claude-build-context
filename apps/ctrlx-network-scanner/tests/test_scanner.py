"""The scan engine end to end against servers on 127.0.0.x (the test machine itself)."""

import threading
import time
import unittest

from app import scanner
from tests.helpers import FakeEnv, TcpServer, UdpServer, closed_port, profile


class ScannerTest(unittest.TestCase):
    def test_tcp_udp_services_and_result(self):
        ssh = TcpServer(greeting=b"SSH-2.0-OpenSSH_9.6\r\n")
        silent = TcpServer()
        udp = UdpServer(b"pong")
        closed = closed_port()
        try:
            p = profile(tcpPorts="{}, {}, {}".format(ssh.port, silent.port, closed), udpPorts=str(udp.port))
            scan = scanner.Scan("t1", p, 16, env=FakeEnv(names={"127.0.0.1": "localhost"}))
            r = scan.run()
        finally:
            ssh.close(), silent.close(), udp.close()
        self.assertEqual(r["status"], "completed", r.get("error"))
        self.assertEqual(r["summary"], {"targets": 1, "hostsUp": 1, "openTcp": 2, "openUdp": 1})
        h = r["hosts"][0]
        self.assertEqual(h["ip"], "127.0.0.1")
        self.assertEqual(h["via"], ["self"])   # 127.0.0.1 is this machine: no discovery needed
        tcp = {x["port"]: x for x in h["ports"] if x["proto"] == "tcp"}
        self.assertEqual(tcp[ssh.port]["service"], "ssh")
        self.assertNotIn(closed, tcp)
        self.assertGreaterEqual(h["tcpClosed"], 1)
        self.assertEqual([x["port"] for x in h["ports"] if x["proto"] == "udp"], [udp.port])
        self.assertEqual(r["targets"]["ranges"], [["127.0.0.1", "127.0.0.1"]])
        self.assertEqual(r["device"]["hostname"], "test-host")
        self.assertGreater(r["probes"], 3)
        self.assertEqual(scan.progress()["percent"], 100.0)

    def test_neighbor_discovery_and_vendor(self):
        # 127.0.0.0/24 posing as a directly connected network: 127.0.0.2 "answered ARP" (fake table)
        env = FakeEnv(iface_addr="127.0.0.1/24", neighbors={"127.0.0.2": "00:60:34:85:75:a4"},
                      vendors={"00:60:34:85:75:a4": "ROBERT BOSCH GmbH"})
        p = profile(targets="127.0.0.2-3", discoverNeighbors=True, discoverTcp=False)
        orig = scanner.NEIGHBOR_WAIT_S
        scanner.NEIGHBOR_WAIT_S = 0.1
        try:
            r = scanner.Scan("t2", p, 16, env=env).run()
        finally:
            scanner.NEIGHBOR_WAIT_S = orig
        self.assertEqual([h["ip"] for h in r["hosts"]], ["127.0.0.2"])
        h = r["hosts"][0]
        self.assertEqual((h["mac"], h["vendor"], h["via"], h["interface"]), ("00:60:34:85:75:a4", "ROBERT BOSCH GmbH", ["arp"], "fake0"))

    def test_no_tcp_ping_for_silent_local_addresses(self):
        # 127.0.0.3 is "directly connected" and did not answer ARP: no TCP attempts for it
        env = FakeEnv(iface_addr="127.0.0.1/24", neighbors={"127.0.0.2": "02:00:00:00:00:02"})
        p = profile(targets="127.0.0.2-3", discoverNeighbors=True, discoverTcp=True)
        orig = scanner.NEIGHBOR_WAIT_S
        scanner.NEIGHBOR_WAIT_S = 0.1
        try:
            scan = scanner.Scan("t7", p, 16, env=env)
            r = scan.run()
        finally:
            scanner.NEIGHBOR_WAIT_S = orig
        self.assertEqual([h["ip"] for h in r["hosts"]], ["127.0.0.2"])
        self.assertEqual(r["probes"], 2)   # the two ARP-triggering datagrams, no TCP attempts

    def test_own_address_is_found_without_arp(self):
        env = FakeEnv(iface_addr="127.0.0.1/24")
        p = profile(targets="127.0.0.1", discoverNeighbors=True, discoverTcp=True)
        orig = scanner.NEIGHBOR_WAIT_S
        scanner.NEIGHBOR_WAIT_S = 0.1
        try:
            r = scanner.Scan("t9", p, 16, env=env).run()
        finally:
            scanner.NEIGHBOR_WAIT_S = orig
        self.assertEqual([(h["ip"], h["self"], h["via"]) for h in r["hosts"]], [("127.0.0.1", True, ["self"])])

    def test_unreadable_neighbor_table_falls_back_to_tcp(self):
        class Broken(FakeEnv):
            def neighbor_map(self):
                raise OSError("netlink denied")
        p = profile(targets="127.0.0.1", discoverNeighbors=True, discoverTcp=True)
        orig = scanner.NEIGHBOR_WAIT_S
        scanner.NEIGHBOR_WAIT_S = 0.1
        try:
            r = scanner.Scan("t8", p, 16, env=Broken(iface_addr="127.0.0.1/24")).run()
        finally:
            scanner.NEIGHBOR_WAIT_S = orig
        self.assertEqual(r["summary"]["hostsUp"], 1)   # found by TCP instead
        self.assertTrue(any("neighbor table not readable" in n for n in r["notes"]))

    def test_assume_alive_and_exclude(self):
        p = profile(targets="127.0.0.1-4", exclude="127.0.0.3", assumeAlive=True)
        r = scanner.Scan("t3", p, 16, env=FakeEnv()).run()
        self.assertEqual(r["targets"]["count"], 3)
        self.assertEqual(r["targets"]["ranges"], [["127.0.0.1", "127.0.0.2"], ["127.0.0.4", "127.0.0.4"]])
        self.assertEqual(r["summary"]["hostsUp"], 3)

    def test_address_limit_fails_cleanly(self):
        r = scanner.Scan("t4", profile(targets="127.0.0.0/16"), 1024, env=FakeEnv()).run()
        self.assertEqual(r["status"], "failed")
        self.assertIn("65534", r["error"])
        self.assertEqual(r["hosts"], [])

    def test_cancel_keeps_partial_results(self):
        p = profile(targets="127.0.0.1", tcpPorts="1-2000", ratePerSecond=200, concurrency=4)
        scan = scanner.Scan("t5", p, 16, env=FakeEnv())
        out = {}
        t = threading.Thread(target=lambda: out.update(r=scan.run()))
        t.start()
        deadline = time.time() + 10
        while scan.progress()["phase"] != "tcp" and time.time() < deadline:
            time.sleep(0.05)
        time.sleep(0.3)
        scan.cancel()
        t.join(10)
        self.assertFalse(t.is_alive())
        self.assertEqual(out["r"]["status"], "aborted")
        self.assertEqual(out["r"]["summary"]["hostsUp"], 1)   # found before the abort
        self.assertLess(out["r"]["probes"], 1000)

    def test_rate_limit(self):
        p = profile(targets="127.0.0.1", tcpPorts="1-60", ratePerSecond=100, assumeAlive=True,
                    serviceDetection=False)
        t0 = time.monotonic()
        r = scanner.Scan("t6", p, 16, env=FakeEnv()).run()
        self.assertGreaterEqual(time.monotonic() - t0, 0.5)   # 60 attempts at 100/s
        self.assertEqual(r["probes"], 60)


if __name__ == "__main__":
    unittest.main()
