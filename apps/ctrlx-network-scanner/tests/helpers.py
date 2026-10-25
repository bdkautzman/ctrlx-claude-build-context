"""Test helpers: a fake device environment for the scanner and small local servers on 127.0.0.1.

Scans in tests only ever touch 127.0.0.x (the test machine itself).
"""

import json
import os
import socket
import threading

from app import netinfo

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULTS_FILE = os.path.join(HERE, "..", "defaults", "settings.json")


def load_defaults():
    with open(DEFAULTS_FILE, encoding="utf-8") as f:
        return json.load(f)


def profile(**overrides):
    """A fast profile for 127.0.0.1 (no multicast, no names)."""
    p = dict(load_defaults()["profiles"][0])
    p.update({"id": "test", "name": "Test", "targets": "127.0.0.1", "tcpPorts": "", "udpPorts": "",
              "discoverNeighbors": False, "discoverTcp": True, "discoverMulticast": False,
              "resolveNames": False, "timeoutMs": 300, "concurrency": 16, "ratePerSecond": 2000})
    p.update(overrides)
    return p


class FakeEnv:
    """What the scanner reads from the device. `iface_addr` makes 127.0.0.0/24 look like a directly
    connected network (non-loopback interface) so the neighbor-table discovery can be tested."""

    def __init__(self, iface_addr=None, neighbors=None, names=None, vendors=None):
        self.iface_addr = iface_addr
        self.neighbors = neighbors or {}
        self.names = names or {}
        self.vendor_map = vendors or {}
        self.neighbor_reads = 0

    def interfaces(self):
        ifaces = [netinfo.Interface(index=1, name="lo", up=True, loopback=True, addresses=["127.0.0.1/8"])]
        if self.iface_addr:
            ifaces.append(netinfo.Interface(index=2, name="fake0", mac="02:00:00:00:00:01", up=True,
                                            addresses=[self.iface_addr]))
        return ifaces

    def neighbor_map(self):
        self.neighbor_reads += 1
        return {ip: netinfo.Neighbor(ip=ip, mac=mac, state=netinfo.NUD_REACHABLE, ifindex=2)
                for ip, mac in self.neighbors.items()}

    def gateway_ips(self):
        return set()

    def hostname(self):
        return "test-host"

    def resolve(self, name):
        if name == "localhost":
            return "127.0.0.1"
        raise OSError("unknown host " + name)

    def reverse_dns(self, ip):
        if ip in self.names:
            return self.names[ip]
        raise OSError("no PTR")

    def vendors(self, macs):
        return {m: self.vendor_map[m] for m in macs if m in self.vendor_map}


class TcpServer:
    """Accepts connections on 127.0.0.1; optionally sends a greeting or answers a request."""

    def __init__(self, greeting=b"", reply=None, port=0):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen(64)
        self.port = self.sock.getsockname()[1]
        self.greeting, self.reply = greeting, reply
        self.received = []
        self._stop = False
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        with conn:
            try:
                conn.settimeout(2)
                if self.greeting:
                    conn.sendall(self.greeting)
                if self.reply is not None:
                    data = conn.recv(4096)
                    self.received.append(data)
                    if data:
                        conn.sendall(self.reply(data) if callable(self.reply) else self.reply)
            except OSError:
                pass

    def close(self):
        self._stop = True
        self.sock.close()


class UdpServer:
    def __init__(self, reply):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.reply = reply
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(4096)
            except OSError:
                return
            self.sock.sendto(self.reply, addr)

    def close(self):
        self.sock.close()


def closed_port():
    """A TCP port on 127.0.0.1 nobody listens on."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port
