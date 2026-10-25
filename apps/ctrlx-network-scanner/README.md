# ctrlx-network-scanner — Network Scanner for ctrlX OS 4.6

An inventory of the networks a ctrlX CORE is connected to: which hosts are there, which TCP/UDP
ports are open, and what the services say about themselves. Scans run manually, on a schedule or
when a PLC writes to a Data Layer node; results are shown in the web UI, kept in a history, rated
against a whitelist/blacklist, compared with the previous scan and downloadable as PDF, CSV and JSON.

Built from the kit template (`templates/python-webapp`), Python standard library only - no extra
dependencies (the PDF writer is part of the app). Only open data is bundled: the IEEE MAC vendor
listing (`data/oui.tsv.gz`, public listing, see `fossinfo.json`).

## What it does (and does not do)

| Phase | How | Option in the profile |
|---|---|---|
| Targets | `auto` (all networks of this device), `if:XF10`, CIDR, ranges, addresses, host names; exclusions; refused above *Max. addresses per scan* | Targets / Exclude |
| Discovery | **neighbor table**: one empty UDP datagram per address on directly connected networks (discard port 9) makes the kernel ARP for it; the kernel's neighbor table (rtnetlink) then lists who answered, with MAC address. **TCP**: connection attempts to 10 common ports, accepted *or refused* = up. **Multicast**: mDNS, SSDP, WS-Discovery queries (+ EtherNet/IP ListIdentity broadcast with automation identification) | discovery checkboxes, *Skip discovery* |
| TCP ports | connect scan, port-major order (each host gets at most one attempt per round), `concurrency` attempts at a time, at most `ratePerSecond` new attempts per second | TCP ports, load limits |
| UDP ports | the protocol's own status/discovery request (DNS, NTP, NetBIOS, SSDP, mDNS, WS-Discovery, CoAP, EtherNet/IP, BACnet) or an empty datagram; answer = open, ICMP port unreachable = closed, nothing = "open or filtered" (listed, not counted) | UDP ports |
| Services | banners (SSH, FTP, SMTP, Telnet, VNC, MySQL, ...), `GET /` on web ports, TLS handshake + certificate (subject, issuer, validity, self-signed) on TLS ports | Service detection |
| Automation | Modbus/TCP *Read Device Identification*, OPC UA *Hello*, EtherNet/IP *ListIdentity* (TCP + broadcast), ISO-on-TCP (S7) *COTP connect* - identification requests only, nothing is written. (BACnet *Who-Is* and EtherNet/IP on UDP are part of the UDP requests.) | Identify automation devices |
| Names / MAC | reverse DNS, NetBIOS name status, mDNS reverse lookup; MAC from the neighbor table, vendor from the IEEE listing | Resolve names |

No logins, no passwords or community strings, no vulnerability checks, no data to raw printer
ports (9100-9107). No ICMP ping, no SYN scan, no OS fingerprinting: a strict snap with the `network`
plug has no raw or packet sockets, and ctrlX OS sets `ping_group_range` to "1 0" (no unprivileged
ping), see gotchas. Host discovery therefore relies on ARP (directly connected networks), TCP and
multicast; a host behind a router that drops everything to the 10 discovery ports is not found -
use *Skip discovery* for such networks. IPv4 only.

**Scanning can disturb fragile devices.** The default profiles are conservative (100 or 50
attempts/s, 16-32 at a time); scan production networks only with the plant owner's consent.

## Web UI

| Tab | |
|---|---|
| Overview | start/abort a scan, live progress with the hosts found so far, last scan, schedules, this device's networks |
| Results | any scan from the history: tiles, changes since the previous scan, host table with filter (text, rating), host details with ports/services; buttons add hosts/ports to the whitelist or blacklist; PDF/CSV/JSON download |
| History | chart of hosts per scan (red dot = forbidden findings), table with open/PDF/delete |
| Profiles | editor with live size estimate, target/port preset chips, load limits, schedule; save, save and run, duplicate, delete |
| Whitelist / Blacklist | the rating lists |
| Settings | address limit, history limits, log level; status; Data Layer nodes with their values |

Read-only users (`ctrlx-network-scanner.web.all.r`) see everything and download reports; starting
scans and changing anything needs `...web.all.rw` (or the ctrlX admin scope).

## Whitelist / blacklist (rating only - the user's decision)

The lists never limit what is scanned (that is what targets/exclude are for); they rate findings:
whitelist = **expected**, blacklist = **forbidden** (alarm), neither = **not listed**; the blacklist
wins. An entry has *hosts* (IP, network, range, MAC, MAC prefix like `00:60:34` = all devices of one
vendor, name pattern like `plc-*`; empty = all hosts) and *ports* (`502, tcp/22, udp/161,
tcp/8000-8100`; empty = the entry rates hosts). Ratings are computed with the **current** lists when
results are shown, so whitelisting a finding takes effect at once (also in `last/alarms`); the
history keeps the counts as rated when the scan finished ("Forbidden" column).

## Data Layer (`ctrlx-network-scanner/...`)

| Node | Type | |
|---|---|---|
| `scan/start` | string, writable | write a profile ID (`quick`, `automation`, `deep`, ...) to start a scan; `DL_INVALID_VALUE` while a scan runs or for an unknown ID |
| `scan/abort` | bool8, writable | `true` aborts the running scan (results so far are kept) |
| `scan/state`, `scan/id`, `scan/profile`, `scan/phase`, `scan/progress` | read-only | `idle`/`running`/`aborting`, progress in % |
| `last/id`, `last/profile`, `last/status`, `last/finished-at`, `last/duration-s` | read-only | newest finished scan |
| `last/hosts-up`, `last/open-ports`, `last/alarms`, `last/unlisted`, `last/changes` | uint64 | counts (`alarms` = blacklist findings, current lists) |
| `last/summary`, `last/hosts`, `last/alarm-list` | string (JSON) | `last/hosts`: `[{"ip","mac","vendor","name","rating","ports":["tcp/22",...],"forbidden":[...]}]` |
| `history/count`, `history/index` | uint64 / JSON | the newest 20 scans |
| `schedule/next`, `profiles/list` | JSON | next start per profile, profile IDs |
| `status/version`, `status/state`, `status/heartbeat`, `settings/active` | | as in every kit app |

## Storage

* Settings (profiles, lists, limits): `settings.json` in ctrlX app data (saved/loaded with
  configurations), validated on every change (target/port/schedule syntax included).
* History: `$SNAP_COMMON/history/<id>.json.gz` + `index.json`, **not** in app data (user's choice),
  capped by *Keep at most* scans / MB (oldest deleted first). Exactly two small writes per finished
  scan, nothing while scanning. A /24 with ~20 hosts is ~5-20 KB per scan.
* Schedules: interval (anchored on the last start; a scan missed while the device was off runs once
  after start-up) or fixed times on chosen weekdays in the **device's** local time (check the
  device clock/NTP). One scan at a time; scheduled scans wait in a queue, manual/Data Layer starts
  are refused while a scan runs.

## Files

| Path | |
|---|---|
| `src/app/scanner.py` | scan engine (asyncio in its own thread), phases, load limits |
| `src/app/netinfo.py` | interfaces, routes, neighbor table via rtnetlink (stdlib `socket`) |
| `src/app/probes.py` | service identification + UDP requests (pure parsers + async I/O) |
| `src/app/targets.py`, `ports.py` | target and port syntax, presets, service names |
| `src/app/rating.py` | whitelist/blacklist, change detection |
| `src/app/history.py`, `scheduler.py`, `scan_manager.py` | history store, schedules, glue to settings/Data Layer |
| `src/app/pdf_writer.py`, `report_pdf.py`, `fontmetrics.py` | stdlib PDF writer (reusable), the report, Helvetica widths |
| `scripts/make_oui.py` | rebuild `data/oui.tsv.gz` from the IEEE listing (`ieee-data` package or IEEE CSVs) |
| `scripts/make_font_metrics.py` | regenerate `fontmetrics.py` from Liberation Sans |

## Develop / test

```bash
./run-local.sh                                   # http://127.0.0.1:8080/ctrlx-network-scanner/
../../tools/check.sh .                           # unit tests (scans only touch 127.0.0.x) + checks
python3 ../../tools/ui_check.py . --narrow       # headless browser: scan, results, lists, downloads
python3 ../../tools/ui_check.py . --role reader
python3 ../../tools/pdf_check.py report.pdf      # render a report's pages to PNG and look at them
./build-snap-amd64.sh && ./build-snap-arm64.sh
```

Verification status: see CHANGELOG.md.
