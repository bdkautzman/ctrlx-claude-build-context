# Known issues and caveats - ctrlx-ads-connector

Working document (kit rule, CLAUDE.md "Definition of done"): everything that does not work as expected, works only
with a workaround, is a caveat for users or maintainers, or was not tested yet - with the evidence (device, versions,
measurements, error texts), what was tried and what is still open, so a later analysis can start from here.

* Add an entry as soon as you find something; date it (YYYY-MM-DD) and name the device/setup.
* Fixed or obsolete entries stay: mark them (e.g. "fixed in 0.1.2") instead of deleting them.
* Generic lessons (ctrlX OS, snapcraft, the kit) also go to `docs/ctrlx/gotchas.md` - reference their numbers here.
* Keep "Not tested yet" honest: list what was only tested locally / in unit tests, not on a device.
* "Diagnostics": the commands and snippets that produced the findings, so they can be repeated.

## Test setup

* Controller for connection tests: Beckhoff **CX-39A650** (192.168.1.187), AMS Net ID `5.57.166.80.1.1`, **TwinCAT 3.1.4024
  on Windows CE 7.0** (build 2882), 32-bit (pointers 4 bytes). PLC project (started by the user 2026-11-23): device
  info "Plc30 App" 3.1.1967, 15 symbols / 54 data types (`tests/fixtures/`). **Rules (user, 2026-11-23): no changes on
  this controller; only `POU_Counter.bTest` and `POU_Counter.sTest` may be written.**
* ctrlX COREs: X3 (arm64, 192.168.1.7), X7 (amd64, 192.168.1.5), ctrlX OS 4.6; test users kit-view / kit-operate /
  kit-configure exist on both. The COREvirtual was not available.
* pyads 3.6.0 (wheels `py3-none-manylinux_2_28` x86_64 / aarch64 with Beckhoff's AdsLib), Python 3.12.
* Build VM: App Build Environment behind VirtualBox NAT (10.0.2.15; to the LAN with the Windows host's IP).

## Overview

| # | Issue | Status | Workaround / fix |
|---|---|---|---|
| 1 | ADS from the build VM goes out with the Windows host's IP: answered without a route, notifications of the host's XAE arrive | caveat (gotchas #115) | test from a CORE; from the VM only short read-only checks |
| 2 | The CX was in CONFIG mode at first: no PLC runtime (port 851: ADS 0x6) | by design (shown as "PLC not running") | start a PLC project (user did, 2026-11-23) |
| 3 | **Add route** | **works** (user, X7 and X3 → CX, 2026-11-24) | - |
| 4 | The CX needs a route per CORE: X7 without one got "Connection reset by peer" + 0x745 | by design (TwinCAT); error text names the route since the 2nd 0.1.0 build | Add route, or TwinCAT's route dialog |
| 5 | One PLC runtime per controller entry (one entry per AMS Net ID) | limitation | second runtime: not possible yet (see 5) |
| 6 | Not as Data Layer nodes: pointers, references, interfaces, bit-addressed symbols; bit members read-only | limitation | map the members / a copy in the PLC |
| 7 | Values are polled (sum reads), no ADS notifications | by design (v0.1) | poll interval per controller |
| 8 | A Data Layer write waits for the PLC (3 ADS requests, each up to `ads.timeoutMs`) in a Data Layer thread | caveat | short `ads.timeoutMs`; whether other Data Layer requests of the app wait meanwhile: not measured |
| 9 | Local AMS Net ID back to empty needs an app restart | caveat | restart the app |
| 10 | TwinCAT 2, TwinCAT/BSD, 64-bit TwinCAT, Secure ADS (TLS, port 8016) not tested / not supported | open | - |
| 11 | DATE / DT / TOD / TIME shown as raw numbers (s / ms) in the browser | caveat | - |
| 12 | AdsLib wrote "Info: connection closed by remote" to the Logbook at every reconnect (every 10 s while a route was missing, X7) | fixed in the 2nd 0.1.0 build: AdsLib's log level follows the app's (INFO -> AdsLib warnings + errors), retries every 30 s after 6 failures | - |
| 13 | Data Layer writes into the real CX from a CORE | **works** (X7, 2026-11-24, with the user's OK: bTest, sTest; restored) | - |

## Details

### 1. ADS from the build VM (2026-11-23)

Raw read-only probes and pyads from the VM (10.0.2.15) to the CX: TCP 48898 connects, ReadState / ReadDeviceInfo on
ports 10000 and 851 are answered for the source Net ID `10.0.2.15.1.1` although no route names it. While pyads was
connected AdsLib logged `Warning: No dispatcher found for notification` (4x): notifications of another ADS client on
the Windows host (TwinCAT XAE online view) came over this connection. Consequences: "works from the VM" says nothing
about the routes a CORE needs (4), and tests from the VM can disturb an XAE session. Gotchas #115.

What ran from the VM against the CX (all read-only except the allowed `sTest`): UDP server info; ReadState /
ReadDeviceInfo; symbol upload (19 ms); sum read of 4 variables + the symbol version: 8.8 ms; `sTest` written through a
handle ("ctrlX ADS test") and restored to `''` (2026-11-24 00:06 UTC); an invalid index offset -> ADS 0x703 mapped.

### 2. CONFIG mode

Before the user started the PLC project: system port 10000 state 15 (CONFIG), ports 851-854, 801, 811 ADS 0x6. The app
shows the tile "connected", TwinCAT state CONFIG, PLC "not running" and the error line; the nodes report
`RESOURCE_UNAVAILABLE` (unit test `test_plc_runtime_gone`). Gotchas #117.

### 3. Add route (user's request 2026-11-23: include it, the user tests it)

`POST /controllers/<name>/route` sends pyads' `adsAddRouteToPLC` (UDP 48899, TwinCAT's add-route request) with the
CORE's AMS Net ID, its IP toward the controller and the route name; True = added, False = user/password refused.
Not sent to any controller by the developer (no changes allowed on the CX). To test (the user): a controller without
a route for the CORE -> tile shows the route data and an error -> "Add route" with an administrator of the
controller -> the controller's route list (XAE / CE) has the new route -> the tile connects. Also to check: a wrong
password (expected: "The controller refused the user or password"), TwinCAT 3.1.4024+ with "Secure ADS" settings.
Caveat: like TwinCAT XAE's own request, it sends user and password **unencrypted** over UDP - use it only in the
machine network, or add the route on the controller instead.

### 3/4. Routes from the COREs (X7, 2026-11-24)

0.1.0 (1st build) on the X7: "Find" (UDP) answered from the X7 (5.57.166.80.1.1, CX-39A650, TwinCAT 3.1.4024); the TCP
connection opened, then the CX reset it (AdsLib: `Error: read frame failed with error: Connection reset by peer`,
`Info: connection closed by remote`) and every request timed out (0x745) - no route for `192.168.1.5.1.1` at
192.168.1.5. The user then used the app's **Add route** on the X7 (Logbook: `Add route on CX-39A650 (192.168.1.187) for
192.168.1.5.1.1 / 192.168.1.5 by boschrexroth: added`): connected, 15 symbols in 3 ms. The second 0.1.0 build names
the missing route in the error ("no ADS answer (timeout) - probably no route on the controller for AMS Net ID ... at
...") and keeps AdsLib's info lines out of the Logbook (12). X3: not reachable on 2026-11-24 (192.168.1.7:443 timed out).

### 5. One runtime per entry

pyads' `Connection.close()` deletes the route of its Net ID for all connections (gotchas #116); the app keeps one
route + AMS port per entry and refuses two entries with the same AMS Net ID (settings validator). Two entries with an
empty Net ID for the same controller are not caught: they would share AdsLib's route - avoid it.

## Device results

* **X7 (amd64), 0.1.0 2nd build, 2026-11-24**: install with `--service`, settings kept; Data Layer provider 9 + 2
  registrations; pyads/AdsLib load in the snap. Against the CX (after Add route): nodes `POU_Counter/iCounter` (int16,
  read-only), `bTest` (bool8) and `sTest` (string) added through the API; REST reads of
  `ctrlx-ads-connector/plc/CX-39A650/POU_Counter/*` return the PLC types and live values (iCounter counting). Browser API:
  `POU_Counter` tree with values and DL marks, `pou_counter.stest` -> `POU_Counter.sTest` (STRING(80), 81 bytes),
  `TwinCAT_SystemInfoVarList._TaskInfo[1]` decoded (`Untitled1_PlcTask`, CycleTime 100000 = 10 ms). RAM 36.5 MB RSS /
  16.6 MB anon, 8 threads, ~0.3 % of one core with 1 controller and 3 nodes at 100 ms (`/proc` over SSH; no System
  Analyzer on the X7).
  **Data Layer writes into the CX** (user's OK 2026-11-24, only `bTest` / `sTest`): REST `dl-write` of `bTest` true
  (bool8) and `sTest` "ctrlX via Data Layer" (string) -> the PLC holds both (read back through the app); refused before
  the PLC: int16 into `bTest` -> `DL_TYPE_MISMATCH`, a write to the read-only node `iCounter` -> `DL_PERMISSION_DENIED`,
  81 characters into `sTest` (STRING(80)) -> `DL_INVALID_VALUE` (Logbook: "longer than STRING(80) (81 of 80 bytes)");
  restored to FALSE and `''`.
* **X3 (arm64), 0.1.0 2nd build, 2026-11-24**: install with `--service`; Data Layer provider 11 registrations, pyads /
  AdsLib (aarch64) load; the CX resets the connection (no route yet): error "no ADS answer (timeout) - probably no route
  on the controller for AMS Net ID 192.168.1.7.1.1 at 192.168.1.7". System Analyzer: 35.4 MB RSS / 16.4 MB anon / 24.2 MB
  PSS, 7 threads, 0.0 % CPU (while retrying every 30 s).

## Not tested yet

* **X3 against the CX** (after the user's Add route on the X3, 2026-11-24; Logbook: `Add route on CX-39A650 ... for
  192.168.1.7.1.1 / 192.168.1.7 by boschrexroth: added`): connected (TwinCAT RUN, PLC RUN "Plc30 App"); nodes
  `iCounter` / `bTest` / `sTest` read through REST with the PLC types; `_TaskInfo[1]` decoded; Data Layer writes of
  `bTest` / `sTest` reached the PLC, int16 into `bTest` -> `DL_TYPE_MISMATCH`; restored to FALSE / `''`. System
  Analyzer while polling 3 nodes at 100 ms: 36.3 MB RSS / 17.3 MB anon, 1.2 % of one core, 8 threads.
  `ui_check --device x3` as admin: OK.

Roles on the devices (2026-11-24): `ui_check.py --device x7|x3 --role reader|operator|writer` as kit-view /
kit-operate / kit-configure (password = user name since 2026-11-24): all OK through the reverse proxy (X7 connected to
the CX, X3 without route - the scenario allows the browser's 503 for a controller that isn't connected).
* The widget in the ctrlX home screen, sidebar entry (the user clicks through).
* Writes on the real CX through the app (only `bTest` / `sTest` allowed); the struct / array / enum / WSTRING / 64-bit
  paths were tested against the fake PLC only (`src/app/dev_fake_plc.py`, unit tests + UI check).
* The parser against large projects (10k+ symbols), 64-bit TwinCAT (8-byte pointers), libraries with namespaces,
  `ARRAY [..] OF ARRAY`, unions, properties; TwinCAT 3.1.4026 (UTF-8 comments).
* Online change / download on a real PLC (symbol version change: unit tests with the fake only).
* Add route (3).

## Diagnostics

```bash
# UDP server info of a controller (read-only, no route needed) - what "Find" does
python3 - <<'PY'
import socket, struct
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(3)
s.sendto(struct.pack("<III6sHI", 0x71146603, 0, 1, bytes([1] * 6), 10000, 0), ("192.168.1.187", 48899))
print(s.recvfrom(2048)[0].hex())
PY
# the app's link against a controller from the build VM (read-only; mind #1)
PYTHONPATH=src:.dev/pylib python3 -c "from app import ads_link; l = ads_link.AdsLink('192.168.1.187', '5.57.166.80.1.1', 1000); l.open(); print(l.read_state(10000), l.read_state(851), l.read_device_info(851)); l.close()"
# on a device
../../tools/ctrlx.py --device x7 api GET /ctrlx-ads-connector/api/v1/controllers
../../tools/ctrlx.py --device x7 dl-read ctrlx-ads-connector/controllers/summary
../../tools/ctrlx.py --device x7 logs --snap ctrlx-ads-connector
```
