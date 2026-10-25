"""The scan engine: one scan of one profile, run in its own thread with its own asyncio loop.

Phases (each optional, see the profile):
  1. targets    expand the target spec (targets.py), honour the address limit
  2. discovery  which addresses are in use?
                * multicast: mDNS / SSDP / WS-Discovery queries on every local network (and an
                  EtherNet/IP ListIdentity broadcast when "identify automation devices" is on)
                * neighbor:  directly connected networks - one empty UDP datagram per address
                  (to the discard port 9) makes the kernel resolve the MAC address; the
                  neighbor table (netinfo.py) then lists the hosts that answered ARP
                * tcp:       connection attempts to a few common ports; accepted OR refused = up
                Without raw sockets (strict snap, `network` plug only) there is no ICMP ping.
  3. tcp        TCP connect scan of the hosts that are up (port-major order: every host gets
                at most one connection attempt per round, gentle on small devices)
  4. udp        protocol-specific requests (probes.udp_payload); answer = open, ICMP port
                unreachable (ECONNREFUSED on a connected UDP socket) = closed, nothing = open|filtered
  5. services   identify the open TCP ports (probes.identify_tcp)
  6. names      reverse DNS, NetBIOS name status, mDNS reverse lookup; MAC vendors (oui.py)

Load limits: `concurrency` open attempts at a time and `ratePerSecond` new attempts per second
(token pacing) for the whole scan. Results are plain dicts (JSON), see `Scan.result()`.
"""

import asyncio
import errno
import ipaddress
import logging
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from app import netinfo, oui, probes
from app import ports as portlib
from app import targets as targetlib

log = logging.getLogger("scanner")

UNREACHABLE_ERRNOS = {errno.EHOSTUNREACH, errno.ENETUNREACH, errno.EHOSTDOWN}
NEIGHBOR_BATCH = 128          # addresses per ARP batch: keeps the kernel's neighbor table small
NEIGHBOR_WAIT_S = 2.0         # the kernel tries ARP 3x at 1 s; answers come within ~1 s
MULTICAST_LISTEN_S = 3.0
NAMES_TIMEOUT_S = 3.0
PHASE_WEIGHTS = {"targets": 1, "discovery": 25, "tcp": 45, "udp": 15, "services": 10, "names": 4}


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _ip_key(ip):
    try:
        return int(ipaddress.IPv4Address(ip))
    except ValueError:
        return 0


class Environment:
    """What the scanner needs from the device - replaced by fakes in tests."""

    def interfaces(self):
        return netinfo.interfaces()

    def neighbor_map(self):
        """{ip: Neighbor} of the valid entries; raises NetinfoError/OSError if the table can't be read."""
        return {n.ip: n for n in netinfo.neighbors() if n.valid}

    def gateway_ips(self):
        return netinfo.gateway_ips()

    def hostname(self):
        return socket.gethostname()

    def resolve(self, name):
        return socket.gethostbyname(name)

    def reverse_dns(self, ip):
        return socket.gethostbyaddr(ip)[0]

    def vendors(self, macs):
        return oui.lookup(macs)


class RateLimiter:
    """At most `rate` acquisitions per second, evenly spaced."""

    def __init__(self, rate):
        self.interval = 1.0 / max(1.0, float(rate))
        self.next = 0.0

    async def wait(self):
        loop = asyncio.get_running_loop()
        now = loop.time()
        slot = max(self.next, now)
        self.next = slot + self.interval
        if slot > now:
            await asyncio.sleep(slot - now)


class ScanCancelled(Exception):
    pass


class Scan:
    """One scan. Thread-safe accessors: progress(), partial(); run() blocks until done."""

    def __init__(self, scan_id, profile, max_addresses, trigger="manual", user="", env=None):
        self.id = scan_id
        self.profile = dict(profile)
        self.max_addresses = int(max_addresses)
        self.trigger = trigger
        self.user = user
        self.env = env or Environment()
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._loop = None
        self._main_task = None
        self._hosts = {}            # ip -> host dict (see _host)
        self._targets = []
        self._target_set = set()
        self._notes = []
        self._phase = "queued"
        self._phase_done = 0
        self._phase_total = 0
        self._phases = []
        self._probes = 0
        self._started_mono = None
        self._result = None
        self.started_at = None
        self.finished_at = None
        self.status = "queued"
        self.error = None
        self._limiter = RateLimiter(profile.get("ratePerSecond", 100))
        self._timeout = max(0.05, profile.get("timeoutMs", 1000) / 1000.0)

    # -- public -----------------------------------------------------------------
    def cancel(self):
        self._cancel.set()
        loop, task = self._loop, self._main_task
        if loop is not None and task is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass

    @property
    def cancelled(self):
        return self._cancel.is_set()

    def run(self):
        """Run the scan to the end (or until cancel()). Returns the result dict."""
        self.started_at = utc_now_iso()
        self._started_mono = time.monotonic()
        self.status = "running"
        try:
            asyncio.run(self._main())
            self.status = "aborted" if self.cancelled else "completed"
        except (ScanCancelled, asyncio.CancelledError):
            self.status = "aborted"
        except targetlib.TargetError as exc:
            self.status, self.error = "failed", str(exc)
        except Exception as exc:   # report, don't kill the app
            log.exception("Scan %s failed", self.id)
            self.status, self.error = "failed", "{}: {}".format(type(exc).__name__, exc)
        self.finished_at = utc_now_iso()
        self._result = self._build_result()
        return self._result

    def result(self):
        return self._result

    def progress(self):
        with self._lock:
            phases = list(self._phases)
            phase, done, total = self._phase, self._phase_done, self._phase_total
            hosts_up = sum(1 for h in self._hosts.values() if h["alive"])
            open_ports = sum(sum(1 for p in h["tcp"].values() if p["state"] == "open")
                             + sum(1 for p in h["udp"].values() if p["state"] == "open")
                             for h in self._hosts.values())
            probes_sent = self._probes
        weights = [PHASE_WEIGHTS[p] for p in phases] or [1]
        percent = 0.0
        if phase in phases:
            i = phases.index(phase)
            percent = sum(weights[:i]) + weights[i] * (done / total if total else 0.0)
            percent = 100.0 * percent / sum(weights)
        elif self.status in ("completed", "aborted", "failed"):
            percent = 100.0
        elapsed = time.monotonic() - self._started_mono if self._started_mono else 0.0
        return {"id": self.id, "profile": self.profile.get("id"), "profileName": self.profile.get("name"),
                "trigger": self.trigger, "status": self.status, "phase": phase,
                "phaseDone": done, "phaseTotal": total, "percent": round(min(100.0, percent), 1),
                "targets": len(self._targets), "hostsUp": hosts_up, "openPorts": open_ports,
                "probes": probes_sent, "startedAt": self.started_at, "elapsedS": round(elapsed, 1)}

    def partial(self):
        """Hosts found so far (for the live view while the scan runs)."""
        with self._lock:
            return [self._export_host(h) for h in sorted(self._hosts.values(), key=lambda h: _ip_key(h["ip"]))
                    if h["alive"]]

    # -- bookkeeping ---------------------------------------------------------------
    def _set_phase(self, name, total):
        with self._lock:
            self._phase, self._phase_done, self._phase_total = name, 0, total

    def _step(self, n=1):
        with self._lock:
            self._phase_done += n

    def _check(self):
        if self._cancel.is_set():
            raise ScanCancelled()

    def _host(self, ip):
        h = self._hosts.get(ip)
        if h is None:
            h = {"ip": ip, "alive": False, "via": set(), "mac": "", "vendor": "", "names": [],
                 "tcp": {}, "udp": {}, "udpOpenFiltered": [], "tcpClosed": 0, "tcpFiltered": 0,
                 "unreachable": False, "interface": "", "self": False, "gateway": False, "discovery": {}}
            self._hosts[ip] = h
        return h

    def _mark_alive(self, ip, how):
        with self._lock:
            h = self._host(ip)
            h["alive"] = True
            h["via"].add(how)

    def _add_name(self, ip, name, source):
        name = (name or "").strip().rstrip(".")
        # "_gateway", "_outbound": synthetic names of systemd-resolved, not the host's name
        if not name or name == ip or name.startswith("_"):
            return
        with self._lock:
            h = self._host(ip)
            if name.lower() not in (n["name"].lower() for n in h["names"]):
                h["names"].append({"name": name, "source": source})

    # -- main ---------------------------------------------------------------------
    async def _main(self):
        self._loop = asyncio.get_running_loop()
        self._main_task = asyncio.current_task()
        if self._cancel.is_set():
            raise ScanCancelled()
        p = self.profile
        tcp_ports = portlib.parse_spec(p.get("tcpPorts", ""), "tcp")
        udp_ports = portlib.parse_spec(p.get("udpPorts", ""), "udp")
        discovery = not p.get("assumeAlive", False) and (
            p.get("discoverNeighbors", True) or p.get("discoverTcp", True) or p.get("discoverMulticast", True))
        self._phases = (["targets"] + (["discovery"] if discovery else [])
                        + (["tcp"] if tcp_ports else []) + (["udp"] if udp_ports else [])
                        + (["services"] if tcp_ports and p.get("serviceDetection", True) else [])
                        + ["names"])

        # 1. targets -------------------------------------------------------------------
        self._set_phase("targets", 1)
        try:
            self._ifaces = await asyncio.to_thread(self.env.interfaces)
        except (netinfo.NetinfoError, OSError) as exc:
            self._ifaces = []
            self._notes.append("cannot read the network interfaces: {}".format(exc))
        self._local_nets = netinfo.local_networks(self._ifaces)
        self._own_ips = {str(n.ip) for i in self._ifaces for n in i.networks()}
        try:
            self._gateways = await asyncio.to_thread(self.env.gateway_ips)
        except (netinfo.NetinfoError, OSError):
            self._gateways = set()
        addresses, notes = await asyncio.to_thread(
            targetlib.expand, p.get("targets", ""), self._local_nets, self.max_addresses,
            p.get("exclude", ""), self.env.resolve)
        self._notes += notes
        self._targets = addresses
        self._target_set = set(addresses)
        self._local_targets = [ip for ip in addresses
                               if netinfo.find_interface_for(ip, self._ifaces) is not None]
        local_set = set(self._local_targets)
        self._step()
        self._check()

        # 2. discovery ---------------------------------------------------------------------
        if discovery:
            await self._discovery(local_set)
        else:
            for ip in addresses:
                self._mark_alive(ip, "assumed")
        self._check()
        alive = sorted((ip for ip, h in self._hosts.items() if h["alive"] and ip in self._target_set), key=_ip_key)

        # 3. TCP ---------------------------------------------------------------------------
        if tcp_ports:
            await self._tcp_scan(alive, tcp_ports)
        self._check()
        # 4. UDP ---------------------------------------------------------------------------
        if udp_ports:
            await self._udp_scan(alive, udp_ports)
        self._check()
        # 5. services ----------------------------------------------------------------------
        if tcp_ports and p.get("serviceDetection", True):
            await self._services()
        self._check()
        # 6. names, MACs, vendors -----------------------------------------------------------
        await self._names()

    # -- workers --------------------------------------------------------------------
    async def _pool(self, items, worker, concurrency):
        """Run worker(item) for every item with at most `concurrency` at a time."""
        iterator = iter(items)

        async def run_one():
            for item in iterator:
                if self._cancel.is_set():
                    return
                await worker(item)

        await asyncio.gather(*(run_one() for _ in range(max(1, concurrency))))

    async def _tcp_attempt(self, ip, port, timeout):
        """"open" | "closed" | "filtered" | "unreachable" | "error"."""
        await self._limiter.wait()
        with self._lock:
            self._probes += 1
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(False)
        try:
            await asyncio.wait_for(self._loop.sock_connect(sock, (ip, port)), timeout)
            return "open"
        except ConnectionRefusedError:
            return "closed"
        except asyncio.TimeoutError:
            return "filtered"
        except OSError as exc:
            if exc.errno in UNREACHABLE_ERRNOS:
                return "unreachable"
            if exc.errno == errno.ECONNREFUSED:
                return "closed"
            log.debug("connect %s:%d: %s", ip, port, exc)
            return "error"
        finally:
            sock.close()

    # -- discovery --------------------------------------------------------------------
    async def _discovery(self, local_set):
        p = self.profile
        total = len(self._targets)
        self._set_phase("discovery", max(1, total))
        multicast_task = None
        if p.get("discoverMulticast", True) and self._local_nets:
            multicast_task = asyncio.ensure_future(self._multicast())
        # this device's own addresses never appear in its neighbor table
        for ip in sorted(self._own_ips & self._target_set, key=_ip_key):
            self._mark_alive(ip, "self")
        arp_done = set()   # directly connected addresses whose ARP answer (or silence) we know
        if p.get("discoverNeighbors", True) and local_set:
            local = [ip for ip in self._targets if ip in local_set]
            for i in range(0, len(local), NEIGHBOR_BATCH):
                batch = local[i:i + NEIGHBOR_BATCH]
                await self._neighbor_batch(batch)
                try:
                    found = await asyncio.to_thread(self.env.neighbor_map)
                except (netinfo.NetinfoError, OSError) as exc:
                    self._notes.append("neighbor table not readable ({}) - TCP discovery used instead".format(exc))
                    break
                for ip in batch:
                    n = found.get(ip)
                    if n is not None and n.valid:
                        self._mark_alive(ip, "arp")
                        with self._lock:
                            self._host(ip)["mac"] = n.mac
                    arp_done.add(ip)
                self._step(len(batch))
                self._check()
        if p.get("discoverTcp", True):
            # an address on a directly connected network that did not answer ARP cannot answer TCP
            # either: only the others get connection attempts
            remaining = [ip for ip in self._targets
                         if ip not in arp_done and not self._hosts.get(ip, {}).get("alive")]
            await self._pool(remaining, self._tcp_ping, max(1, p.get("concurrency", 32)))
        if multicast_task is not None:
            try:
                await multicast_task
            except Exception:
                log.exception("multicast discovery failed")
        # the TCP attempts above also made the kernel resolve MAC addresses of local hosts
        if local_set:
            try:
                found = await asyncio.to_thread(self.env.neighbor_map)
            except (netinfo.NetinfoError, OSError):
                found = {}
            for ip in local_set:
                n = found.get(ip)
                if n is not None and n.valid:
                    with self._lock:
                        h = self._hosts.get(ip)
                        if h is not None and not h["mac"]:
                            h["mac"] = n.mac
                    if p.get("discoverNeighbors", True):
                        self._mark_alive(ip, "arp")
        with self._lock:
            self._phase_done = self._phase_total

    async def _neighbor_batch(self, batch):
        """One empty UDP datagram to the discard port of each address: the kernel resolves the
        MAC address first (ARP), which is all we want."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        try:
            for ip in batch:
                await self._limiter.wait()
                with self._lock:
                    self._probes += 1
                try:
                    sock.sendto(b"", (ip, 9))
                except OSError:
                    pass
            await asyncio.sleep(NEIGHBOR_WAIT_S)
        finally:
            sock.close()

    async def _tcp_ping(self, ip):
        timeout = self._timeout
        try:
            for port in portlib.DISCOVERY_TCP:
                state = await self._tcp_attempt(ip, port, timeout)
                if state in ("open", "closed"):
                    self._mark_alive(ip, "tcp")
                    with self._lock:
                        h = self._host(ip)
                        h["discovery"]["tcp"] = "{} {}".format(port, state)
                    return
                if state == "unreachable":
                    with self._lock:
                        self._host(ip)["unreachable"] = True
                    return
                if self._cancel.is_set():
                    return
        finally:
            self._step()

    async def _multicast(self):
        """mDNS, SSDP and WS-Discovery queries on every local network; answers for ~3 s."""
        queries = [(probes.MDNS_SERVICES_QUERY, ("224.0.0.251", 5353), "mdns"),
                   (probes.SSDP_MSEARCH, ("239.255.255.250", 1900), "ssdp"),
                   (probes.wsd_probe(), ("239.255.255.250", 3702), "wsd")]
        socks = []
        for _name, iface in self._local_nets:
            local_ip = str(iface.ip)
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.setblocking(False)
                s.bind((local_ip, 0))
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local_ip))
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
                sent = []
                for payload, dest, kind in queries:
                    try:
                        s.sendto(payload, dest)
                        sent.append(kind)
                    except OSError as exc:
                        log.debug("multicast %s on %s: %s", kind, local_ip, exc)
                if self.profile.get("identifyAutomation") and iface.network.prefixlen < 31:
                    try:
                        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                        s.sendto(probes.ENIP_LIST_IDENTITY, (str(iface.network.broadcast_address), 44818))
                    except OSError as exc:
                        log.debug("EtherNet/IP broadcast on %s: %s", local_ip, exc)
                socks.append(s)
            except OSError as exc:
                self._notes.append("multicast discovery on {}: {}".format(local_ip, exc))
        if not socks:
            return
        deadline = self._loop.time() + MULTICAST_LISTEN_S
        try:
            while self._loop.time() < deadline and not self._cancel.is_set():
                got = False
                for s in socks:
                    try:
                        data, (src, sport) = s.recvfrom(65535)
                    except (BlockingIOError, InterruptedError):
                        continue
                    except OSError:
                        continue
                    got = True
                    self._multicast_answer(src, sport, data)
                if not got:
                    await asyncio.sleep(0.05)
        finally:
            for s in socks:
                s.close()

    def _multicast_answer(self, src, sport, data):
        if src not in self._target_set:
            return
        if sport == 5353:
            self._mark_alive(src, "mdns")
            records = probes.parse_dns_records(data)
            services = sorted({v for _n, t, v in records if t == 12 and v and v.startswith("_")})
            names = {n for n, t, v in records if t == 1 and v == src}
            with self._lock:
                h = self._host(src)
                if services:
                    h["discovery"]["mdns"] = services[:20]
            for name in names:
                self._add_name(src, name, "mdns")
        elif sport == 1900:
            info = probes.parse_ssdp(data)
            self._mark_alive(src, "ssdp")
            if info:
                with self._lock:
                    self._host(src)["discovery"]["ssdp"] = info
        elif sport == 3702:
            self._mark_alive(src, "wsd")
            info = probes.parse_udp_response(3702, data)
            with self._lock:
                self._host(src)["discovery"]["wsd"] = info
        elif sport == 44818:
            info = probes.parse_enip(data)
            self._mark_alive(src, "ethernet-ip")
            if info:
                with self._lock:
                    self._host(src)["discovery"]["ethernetIp"] = info

    # -- TCP ------------------------------------------------------------------------------
    async def _tcp_scan(self, alive, ports):
        total = len(alive) * len(ports)
        self._set_phase("tcp", total)
        retries = max(0, int(self.profile.get("retries", 0)))
        timeout = self._timeout

        async def attempt(item):
            port, ip = item
            h = self._hosts[ip]
            try:
                if h["unreachable"]:
                    return
                state = "filtered"
                for _ in range(retries + 1):
                    state = await self._tcp_attempt(ip, port, timeout)
                    if state not in ("filtered", "error"):
                        break
                with self._lock:
                    if state == "open":
                        h["tcp"][port] = {"state": "open"}
                    elif state == "closed":
                        h["tcpClosed"] += 1
                    elif state == "filtered":
                        h["tcpFiltered"] += 1
                    elif state == "unreachable":
                        h["unreachable"] = True
            finally:
                self._step()

        items = ((port, ip) for port in ports for ip in alive)
        await self._pool(items, attempt, max(1, int(self.profile.get("concurrency", 32))))

    # -- UDP ------------------------------------------------------------------------------
    async def _udp_scan(self, alive, ports):
        self._set_phase("udp", len(alive) * len(ports))
        timeout = max(self._timeout, 1.0)

        async def attempt(item):
            port, ip = item
            try:
                state, info = await self._udp_attempt(ip, port, timeout)
                with self._lock:
                    h = self._hosts[ip]
                    if state == "open":
                        entry = {"state": "open"}
                        if info:
                            entry["info"] = info
                        h["udp"][port] = entry
                    elif state == "open|filtered":
                        h["udpOpenFiltered"].append(port)
            finally:
                self._step()

        items = ((port, ip) for port in ports for ip in alive)
        await self._pool(items, attempt, max(1, min(int(self.profile.get("concurrency", 32)), 64)))

    async def _udp_attempt(self, ip, port, timeout):
        await self._limiter.wait()
        with self._lock:
            self._probes += 1
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        try:
            sock.connect((ip, port))
            sock.send(probes.udp_payload(port))
            data = await asyncio.wait_for(self._loop.sock_recv(sock, 65535), timeout)
            return "open", probes.parse_udp_response(port, data)
        except ConnectionRefusedError:
            return "closed", None
        except asyncio.TimeoutError:
            return "open|filtered", None
        except OSError as exc:
            if exc.errno == errno.ECONNREFUSED:
                return "closed", None
            return "error", None
        finally:
            sock.close()

    # -- services -----------------------------------------------------------------------
    async def _services(self):
        with self._lock:
            items = [(ip, port) for ip, h in self._hosts.items() for port in h["tcp"]]
        self._set_phase("services", len(items))
        identify = bool(self.profile.get("identifyAutomation", False))
        timeout = max(self._timeout, 2.0)

        async def identify_one(item):
            ip, port = item
            try:
                await self._limiter.wait()
                with self._lock:
                    self._probes += 1
                found = await probes.identify_tcp(ip, port, timeout, identify)
                with self._lock:
                    self._hosts[ip]["tcp"][port].update(found)
            finally:
                self._step()

        await self._pool(items, identify_one, max(1, min(int(self.profile.get("concurrency", 32)), 16)))

    # -- names / MAC / vendor ----------------------------------------------------------------
    async def _names(self):
        with self._lock:
            alive = [ip for ip, h in self._hosts.items() if h["alive"]]
        self._set_phase("names", max(1, len(alive)))
        # MAC addresses of directly connected hosts (also those found by TCP only)
        try:
            found = await asyncio.to_thread(self.env.neighbor_map)
        except (netinfo.NetinfoError, OSError):
            found = {}
        with self._lock:
            for ip in alive:
                h = self._hosts[ip]
                n = found.get(ip)
                if n is not None and n.valid and not h["mac"]:
                    h["mac"] = n.mac
                h["interface"] = netinfo.find_interface_for(ip, self._ifaces) or ""
                h["self"] = ip in self._own_ips
                h["gateway"] = ip in self._gateways
                if h["self"]:
                    for iface in self._ifaces:
                        if any(str(n.ip) == ip for n in iface.networks()):
                            h["mac"] = h["mac"] or iface.mac
        macs = [h["mac"] for h in self._hosts.values() if h["alive"] and h["mac"]]
        if macs:
            vendors = await asyncio.to_thread(self.env.vendors, macs)
            with self._lock:
                for h in self._hosts.values():
                    if h["mac"] in vendors:
                        h["vendor"] = vendors[h["mac"]]
        if self.profile.get("resolveNames", True) and alive and not self._cancel.is_set():
            await self._resolve_names(alive)
        with self._lock:
            self._phase_done = self._phase_total

    async def _resolve_names(self, alive):
        executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="rdns")
        loop = self._loop

        async def rdns(ip):
            try:
                name = await asyncio.wait_for(loop.run_in_executor(executor, self.env.reverse_dns, ip),
                                              NAMES_TIMEOUT_S)
                self._add_name(ip, name, "dns")
            except (OSError, asyncio.TimeoutError, UnicodeError):
                pass

        async def netbios(ip):
            state, info = await self._udp_attempt(ip, 137, 1.5)
            if state == "open" and info and info.get("netbiosName"):
                self._add_name(ip, info["netbiosName"], "netbios")

        async def mdns_reverse(ip):
            query = (bytes([0x4E, 0x53, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0])
                     + probes._dns_name(".".join(reversed(ip.split("."))) + ".in-addr.arpa")
                     + bytes([0, 12, 0, 1]))
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setblocking(False)
            try:
                sock.connect((ip, 5353))
                sock.send(query)
                data = await asyncio.wait_for(loop.sock_recv(sock, 9000), 1.5)
                for _n, rtype, value in probes.parse_dns_records(data):
                    if rtype == 12 and value:
                        self._add_name(ip, value, "mdns")
            except (OSError, asyncio.TimeoutError):
                pass
            finally:
                sock.close()

        async def one(ip):
            try:
                tasks = [rdns(ip), netbios(ip)]
                if self._hosts[ip]["interface"]:
                    tasks.append(mdns_reverse(ip))
                await asyncio.gather(*tasks)
            finally:
                self._step()

        try:
            await self._pool(alive, one, 16)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    # -- result ---------------------------------------------------------------------------
    def _export_host(self, h):
        tcp = []
        for port in sorted(h["tcp"]):
            entry = dict(h["tcp"][port])
            entry.update({"proto": "tcp", "port": port, "name": portlib.service_name("tcp", port)})
            tcp.append(entry)
        udp = []
        for port in sorted(h["udp"]):
            entry = dict(h["udp"][port])
            entry.update({"proto": "udp", "port": port, "name": portlib.service_name("udp", port)})
            udp.append(entry)
        return {
            "ip": h["ip"], "mac": h["mac"], "vendor": h["vendor"], "names": list(h["names"]),
            "interface": h["interface"], "self": h["self"], "gateway": h["gateway"],
            "via": sorted(h["via"]), "ports": tcp + udp,
            "udpOpenFiltered": sorted(h["udpOpenFiltered"]),
            "tcpClosed": h["tcpClosed"], "tcpFiltered": h["tcpFiltered"],
            "discovery": dict(h["discovery"]),
        }

    def _build_result(self):
        with self._lock:
            hosts = [self._export_host(h) for h in sorted(self._hosts.values(), key=lambda h: _ip_key(h["ip"]))
                     if h["alive"] and h["ip"] in self._target_set]
        ranges = []
        for ip in sorted((_ip_key(t) for t in self._targets)):
            if ranges and ip == ranges[-1][1] + 1:
                ranges[-1][1] = ip
            else:
                ranges.append([ip, ip])
        p = self.profile
        open_tcp = sum(1 for h in hosts for x in h["ports"] if x["proto"] == "tcp")
        open_udp = sum(1 for h in hosts for x in h["ports"] if x["proto"] == "udp")
        try:
            hostname = self.env.hostname()
        except OSError:
            hostname = ""
        started = self._started_mono or time.monotonic()
        return {
            "format": 1,
            "id": self.id,
            "status": self.status,
            "error": self.error,
            "trigger": self.trigger,
            "user": self.user,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "durationS": round(time.monotonic() - started, 1),
            "profile": dict(p),
            "device": {
                "hostname": hostname,
                "interfaces": [{"name": i.name, "mac": i.mac, "addresses": list(i.addresses)}
                               for i in getattr(self, "_ifaces", []) if not i.loopback and i.addresses],
            },
            "targets": {
                "count": len(self._targets),
                "ranges": [[str(ipaddress.IPv4Address(a)), str(ipaddress.IPv4Address(b))] for a, b in ranges],
            },
            "notes": list(self._notes),
            "probes": self._probes,
            "hosts": hosts,
            "summary": {"targets": len(self._targets), "hostsUp": len(hosts), "openTcp": open_tcp,
                        "openUdp": open_udp},
        }
