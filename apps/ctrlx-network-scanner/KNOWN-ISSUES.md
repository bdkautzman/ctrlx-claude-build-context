# Known issues and caveats - ctrlx-network-scanner

Working document: everything that does not work as expected, works only with a workaround, is a caveat for users
or maintainers, or was not tested, with the evidence, what was tried and what is still open. Keep it current: add
new findings, mark fixed ones (don't delete them - the history helps the next analysis), and date every entry.
Generic lessons also go to `docs/ctrlx/gotchas.md` (numbers in brackets).

Written 2026-11-04, after the fact, from the app's README and CHANGELOG, the gotchas, the kit `README.md`,
`apps/README.md` and the development notes of the sessions. Nothing was re-measured or re-checked on a device for
this document. **"Not recorded" means the records don't say** - it may have been done or not.

## Test setup

All recorded tests are from 2026-10-25, with 0.1.0 (the only version).

| Device | Facts as recorded |
|---|---|
| **Real ctrlX CORE** | arm64, 4 cores, 2 for apps [#73]. Final 0.1.0, installed with `--service`, back to OPERATING. Network: LAN 192.168.1.0/24, 254 addresses, 4 hosts up (Bosch/HP vendors), 30 open ports |
| **ctrlX COREvirtual** | amd64, ctrlX WORKS, QEMU user networking. 2 networks: the NAT network and a second address 192.168.2.19/24 on eth0 with nobody else on it. Quick scan: 508 addresses, 4 hosts, 22 open ports. It was already in SERVICE before the install (left as found) |
| **Locally** | 96 unit tests (scans only touch 127.0.0.x), `ui_check` admin/narrow/reader (scan, list buttons, downloads), `pdf_check` |

The CHANGELOG lists the COREvirtual checks under a "test build of 0.1.0". The notes say the final 0.1.0 (both
architectures, `--clean`) was then installed on both devices. A re-check of the final build on the COREvirtual is not
recorded. Each device kept a few test scans in its history. The state today was not checked.

The user approved these device tests: install with `--service` on both devices and light LAN scans (common ports,
rate-limited). **Ask before scanning other networks.**

## Overview

| # | Issue | Status | Workaround / fix |
|---|---|---|---|
| 1 | No ICMP ping, SYN scan or OS fingerprinting possible in a strict snap | by design [#61] | ARP, TCP connect "pings", multicast |
| 2 | A host behind a router that drops all 10 discovery ports is not found | caveat | *Skip discovery* for such networks |
| 3 | IPv4 only | caveat | - |
| 4 | UDP without an answer is only "open or filtered" | caveat | listed, not counted |
| 5 | Scanning can fill the kernel's neighbor table, for the whole device | worked around [#62] | batches of 128 with ~2 s wait |
| 6 | Scanning can disturb fragile devices | caveat | conservative defaults; plant owner's consent |
| 7 | A scan from inside the COREvirtual partly sees itself | caveat (test setup) [#64] | test real discovery on a real CORE |
| 8 | Discovery was slow with TCP pings on local networks (77 s) | fixed before 0.1.0 [#66] | ARP decides there (20 s) |
| 9 | MAC vendor list would not have been in the snap | fixed before 0.1.0 [#63] | `stage:` entry, check `PART-STAGE-DIR` |
| 10 | Reverse DNS of the gateway returns `_gateway` | caveat [#65] | filter names starting with `_` |
| 11 | Ratings in the history can differ from the ratings shown | by design | history keeps the counts of scan end |
| 12 | Whitelist/blacklist semantics and the three default profiles are first versions | **open** | adjust after real use |
| 13 | Schedules run in the device's local time | caveat | check the clock/NTP |
| 14 | Inventory only: no logins, no vulnerability checks, no raw printer ports | by design | - |
| 15 | Custom code instead of libraries, built before the "ask first" rule | accepted (user, 2026-10-25) | see below |
| 16 | The MAC vendor list is a bundled snapshot | maintenance caveat | `scripts/make_oui.py` |
| 17 | `scan/start` is refused while a scan runs | by design [#65] | abort first, or wait |
| 18 | Not tested yet (list below) | open | - |

## Details

### 1. What a strict snap with the `network` plug can do [#61]

Read from the AppArmor and seccomp profiles of an installed kit app on the COREvirtual 4.6. AppArmor allows `network
inet/inet6 stream|dgram` and `network netlink raw|dgram`; seccomp allows `socket AF_NETLINK - NETLINK_ROUTE`. So TCP
connects, UDP (also multicast and broadcast sends) and rtnetlink dumps work: addresses, routes, the neighbor table.

Not possible: raw and packet sockets (`network-control` is denied for ctrlX apps), and unprivileged ping:
`net.ipv4.ping_group_range` is `1 0` on the COREvirtual **and** the real CORE, which excludes the snap daemon's gid 0.

What works instead: one empty UDP datagram per address (the kernel ARPs for it), then the neighbor table on directly
connected networks; TCP connect (accepted or refused = up); a connected UDP socket reports an ICMP port unreachable as
`ECONNREFUSED` (UDP "closed").

Consequences (README): a host behind a router that drops everything to the 10 discovery ports is not found (#2). A
UDP port without any answer can't be told from filtered (#4).

### 5. Neighbor table [#62]

Every probed address on a directly connected network gets an entry (INCOMPLETE → FAILED for silent ones).
`net.ipv4.neigh.default.gc_thresh3` is 1024 on ctrlX OS. Above it new entries fail ("neighbour table overflow"), for the
whole device, also real-time traffic of other apps. The scanner therefore probes local networks in batches of 128 and
waits ~2 s per batch.

Measured on the real CORE [#66]: no "neighbour table overflow" in dmesg after the /24 scan, the table was back to its
3 entries right after. Scans above *Max. addresses per scan* are refused. Larger networks: see "Not tested yet".

### 6. Fragile devices (README)

The default profiles are conservative (100 or 50 attempts/s, 16-32 at a time). Scan production networks only with the
plant owner's consent. No effect on a device was recorded; the only real network tested was the user's LAN.

### 7. A scan from inside the COREvirtual [#64]

Verified 2026-10-25, QEMU user networking:

* `10.0.2.2` (gateway) is the Windows PC. TCP connections land on the PC's own services, including the ports it forwards
  back into the COREvirtual (2069/2070 Data Layer, 8443 web with the COREvirtual's certificate): the device partly
  sees itself there.
* `10.0.2.3` (QEMU DNS) answers ARP but never sends ICMP port unreachable: every UDP port is "open|filtered".
* The second address 192.168.2.19/24 has nobody else on its network.
* The device's own addresses answer SSDP (its UPnP "DeviceDiscovery" service) but never appear in its own neighbor
  table, so #64 says to mark them up directly.

So a COREvirtual scan says little about real discovery. That was tested on the real CORE.

### 8. Scan time and cost [#66]

Real CORE, "Quick inventory" of a /24 (254 addresses, 4 hosts up, ~75 common TCP ports, 100 attempts/s, 32 parallel): 15 s
and 588 connection attempts. Neighbor-table discovery ~10 s (2 ARP batches of 128 + 2 s wait each), port scan ~5 s. App
process: CPU max 9.7 %, average 4.7 % of one core; RSS 38.7 → 42.2 MB.

COREvirtual, same profile, 508 addresses: 77 s without the rule "no TCP ping for addresses that did not answer ARP" (2.4 k
TCP attempts in discovery alone), 20 s with it (767 attempts, same 4 hosts). On directly connected networks ARP decides;
TCP pings are for routed targets. CHANGELOG, COREvirtual: CPU <= 4.7 %, RSS 45 MB.

History: ~1.5 KB per scan as gzip JSON in #66 (4 hosts). The README gives ~5-20 KB for a /24 with ~20 hosts; a
measurement behind that figure is not recorded.

### 9. Files that don't reach the snap [#63]

A new top-level folder of an app is not in the snap unless it is in the `stage:` list of the `files` dump part. Here
`data/oui.tsv.gz`: the vendor lookup would have failed silently on the device. The kit checker warns now
(`PART-STAGE-DIR`), and `tools/ui_check.py` copies `data/` for local runs.

### 10, 13, 17. Runtime facts [#65]

* Reverse DNS of the default gateway returns systemd-resolved's synthetic name `_gateway`. #65 says to filter names
  starting with `_`.
* The snap sees the device's time zone (`datetime.astimezone()` gave CDT, like the host). Fixed-time schedules run in the
  **device's** local time. A CORE's clock can be days off with NTP inactive (#45, #75), so check it.
* `scan/start` rejecting a value answers a REST write with HTTP 400 `DL_INVALID_VALUE` and keeps its old value, as designed
  (#50). The result is the same while a scan runs or for an unknown profile. Manual and Data Layer starts are refused
  while a scan runs; scheduled scans wait in a queue.

### 11-12. The lists (the user's decision, 2026-10-25)

The lists only rate findings: whitelist = expected, blacklist = forbidden (alarm), neither = not listed, the blacklist
wins. They never limit what is scanned; targets and exclude do that. Ratings are computed with the **current** lists when
results are shown (also in `last/alarms`), while the history keeps the counts as rated when the scan finished
("Forbidden" column), so the two can differ.

The kit `README.md` lists the semantics and the three default profiles (Quick inventory, Automation devices, Deep) as
first versions, to adjust after real use.

### 14. Scope (chosen during the build, the user was told)

Inventory only: no logins or credentials (no SNMP community, no MQTT login), no vulnerability checks, identification
requests only (nothing written), no data to raw printer ports 9100-9107. Plug: `network` only.

### 15-16. Custom code (README "Custom tools in this app")

Built before the kit's "Custom tools: ask first" rule (2026-10-25, the rule came from this app), listed in the hand-over
report and committed with the user's OK the same day. To maintain:

* `src/app/pdf_writer.py`, `report_pdf.py`, `fontmetrics.py` (`scripts/make_font_metrics.py`): instead of a PDF library
  (reportlab and fpdf2 need Pillow, a C extension)
* `src/app/netinfo.py`: rtnetlink reader, no library for it in the standard library
* `scripts/make_oui.py` (vendor list), `scripts/device_scan.py` (scan + CPU/RAM on a device)
* in the kit: `tools/pdf_check.py` (+ pypdfium2 in the UI test venv), `ctrlx.py api`, checker rule `PART-STAGE-DIR`

The vendor list `data/oui.tsv.gz` comes from the IEEE listing and is rebuilt with `scripts/make_oui.py`. Its date is not
recorded in these sources.

## Not tested yet

* **Real CORE: only "Quick inventory"** of the LAN /24 is recorded. *Automation devices* and *Deep* there: not recorded.
* **Real CORE: Data Layer start/abort and schedules**: recorded on the COREvirtual only.
* **A real PLC** writing `scan/start` or `scan/abort` (recorded: REST).
* **Automation identification against real devices**: recorded is EtherNet/IP ListIdentity over TCP and UDP, on the
  COREvirtual's networks. Modbus, OPC UA, ISO-on-TCP and BACnet on a device: not recorded.
* **Fixed-time schedules and the catch-up after a power-off**: recorded is one interval schedule (fired, restored).
* **Larger networks**: the most recorded is 508 addresses on the COREvirtual, one /24 on the real CORE. Neighbor table
  behaviour near `gc_thresh3` (1024) was not measured.
* **Routed targets**: the TCP discovery path through a router on a device is not recorded.
* **A production network or fragile devices**: none. Only the user's LAN.
* **Long runs**: many scheduled scans, the history at its cap, flash use over time: not recorded.
* **Read-only user on a device**: reader recorded locally only.
* **Target forms on a device** other than the defaults (`if:XF10`, ranges, host names): not recorded.
* **Save/load of a ctrlX configuration** with profiles and lists: not recorded for this app.
* **Final 0.1.0 on the COREvirtual**: see "Test setup".

## Diagnostics

Scans run **from the device**: only where the network owner agreed. Run from the app folder:

```bash
python3 scripts/device_scan.py --device x3 --profile quick [--via datalayer] [--pdf out.pdf]   # scan + CPU/RAM over SSH, adds a scan to the history
python3 ../../tools/pdf_check.py report.pdf                  # render a report's pages to PNG and look at them
python3 ../../tools/ui_check.py . --narrow                   # also --role reader, --device
```

From the kit root (where only the file or the value was recorded, the command is the usual form):

```bash
tools/ctrlx.py --device x3 ssh 'sysctl net.ipv4.ping_group_range net.ipv4.neigh.default.gc_thresh3'    # "1 0", 1024
tools/ctrlx.py --device x3 ssh 'ip neigh | wc -l; sudo dmesg | grep -i "neighbour table"'              # neighbor table after a scan
tools/ctrlx.py --device x3 ssh 'sudo cat /var/lib/snapd/apparmor/profiles/snap.ctrlx-network-scanner.*'   # also /var/lib/snapd/seccomp/bpf/*.src [#61]
tools/ctrlx.py --device x3 dl-read ctrlx-network-scanner/last/summary
tools/ctrlx.py --device x3 dl-read ctrlx-network-scanner/scan/state
tools/ctrlx.py --device x3 dl-write ctrlx-network-scanner/scan/start '"quick"' --type string    # starts a scan; unknown id: HTTP 400 DL_INVALID_VALUE
tools/ctrlx.py --device x3 api GET /ctrlx-network-scanner/api/v1/status
```
