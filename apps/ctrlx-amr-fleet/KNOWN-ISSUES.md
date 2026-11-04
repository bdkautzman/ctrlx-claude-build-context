# Known issues and caveats - ctrlx-amr-fleet

Working document: everything that does not work as expected, works only with a workaround, is a caveat for users
or maintainers, or was not tested, with the evidence, what was tried and what is still open. Keep it current: add
new findings, mark fixed ones (don't delete them - the history helps the next analysis), and date every entry.
Generic lessons also go to `docs/ctrlx/gotchas.md` (numbers in brackets).

Written 2026-11-04, after the fact, from the app's README and CHANGELOG, the gotchas, the kit `README.md`,
`apps/README.md` and the development notes of the sessions. Nothing was re-measured or re-checked on a device for
this document. **"Not recorded" means the records don't say** - it may have been done or not.

## Test setup

| What | Facts as recorded |
|---|---|
| **ctrlX COREvirtual** | x86, 4 cores, on a Windows PC, ctrlX OS 4.6 |
| **Real ctrlX CORE** | ARM, rexroth-arch01. The tables in the README say "2 cores": it has 4, apps get 2 of them [#73] |
| **Build VM** | emulated CPU [#47], headless Chromium without GPU: browser numbers only compare versions and AMR counts, they say nothing about a PC [#60] |
| **Who writes the AMRs** | no real PLC or fleet manager so far: the app's simulation, or the build VM through REST (`PUT /automation/api/v2/bulk?type=write`, up to 500 per request) |
| **Maps** | `HMI_Map.txt` (bundled, 10.4 x 17.8 m if the units are mm, 520 x 890 cells, 17,015 points); a generated 103 x 177 m map (`scripts/make_large_map.py`, 5153 x 8853 cells, 184k points; `Warehouse_Map.txt` on both devices); `Large_Warehouse_Map.txt` 208 x 356 m at 40 mm (5191 x 8893 cells, 477k points, 8.7 MB) |

Last recorded state of the devices. The state today was not checked.

| Device | App | Settings | Recorded |
|---|---|---|---|
| COREvirtual | 0.5.0 | Warehouse map, `fleet.input` = `amr-fields` (set through the web UI 2026-11-01 14:33 CST, presumably by the user), simulation with 4 AMRs. `Large_Warehouse_Map.txt` in the app data | 2026-11-02 |
| Real CORE | 0.4.0 (0.5.0 on the COREvirtual only: the user's choice) | `HMI_Map.txt`, simulation off. `Warehouse_Map.txt` in the app data | 2026-11-01, still 0.4.0 on 2026-11-02 |

Versions on devices: 0.1.0, 0.2.0, 0.2.1 on the COREvirtual; 0.2.2, 0.2.3/0.2.4, 0.3.0, 0.4.0 on both; 0.5.0 on the
COREvirtual. **0.2.5 and 0.3.1 went to no device as such** (recorded as "built, not installed" and "built; devices
still run 0.3.0"): their changes reached the devices with the next installed version.

## Overview

| # | Issue | Status | Workaround / fix |
|---|---|---|---|
| 1 | Map units (mm), fleet JSON format and rotation convention chosen without a real PLC or fleet manager | **open** (not confirmed by the user) | settings `map.unitsPerMeter`, `fleet.rotationUnit`; input modes |
| 2 | No test with a real PLC writing the nodes | **open** | tested through REST from the build VM only |
| 3 | 0.5.0 and the 208 x 356 m map are not measured on a real CORE | **open** | CORE left on 0.4.0 (user's choice, 2026-11-02) |
| 4 | Node per value: 200 AMRs at 5 updates/s not reachable on a CORE (~0.24 ms per node write) | caveat (measured) | one JSON node for big fleets; up to ~1000 node writes/s; write slow values on change |
| 5 | 200 AMRs in the browser: few frames per second, measured only in the emulated VM | **open** (no PC measurement) | 0.3.1 fixed the list's share |
| 6 | RSS 62-63 MB on the CORE after a big map (0.2.4) | fixed in 0.2.5 (measured with 0.3.0) | chunked parser, levels from bytes |
| 7 | `read(limit + 1)` counted as the full 64 MB limit | fixed in 0.2.5 | `fstat` size check first |
| 8 | Big map blocked the settings save (5.0 s on the CORE), first tile of a level ~1 s | fixed in 0.2.3 | background load, levels built ahead |
| 9 | Simulation stretched its period: 200 AMRs reached 3.6 of 5 updates/s on the CORE | fixed in 0.2.3 | fixed 200 ms rhythm |
| 10 | Locked view slightly slower with the cached map layer | fixed in 0.2.4 | layer used only when the view stands still |
| 11 | AMR list rewrote every tile with each update (since 0.1) | fixed in 0.3.1 (measured locally only) | writes only changes, `content-visibility` |
| 12 | "0.3.0 is slower than 0.2.4" in the browser | false alarm, VM noise [#60] | A/B in alternating rounds |
| 13 | Simulation: AMRs deadlocked in narrow free space | fixed in 0.2.1 | - |
| 14 | Simulation used a small enclosed pocket on maps whose walls are the border | fixed in 0.2.2 | - |
| 15 | Simulation: headings grew without bound | fixed in 0.1.1 | wrapped to -180..180 |
| 16 | Simulation grid kept 15 MB for the 208 x 356 m map | fixed in 0.5.0 [#92] | byte and int arrays |
| 17 | A write rejected in `on_write` still changed the node value | fixed in template 0.1.5 [#50] | `on_write` runs before storing |
| 18 | Widget replaced all rows every 2 s | fixed in 0.2.1 [#57] | rows rebuilt only on visible change |
| 19 | A rejected upload: the client saw a reset instead of the answer | fixed in 0.5.0 [#91] | `_drain` reads the rest of the body |
| 20 | `PUT /settings` replaces all settings; missing keys get the defaults | caveat, template behaviour [#89] | `GET`, change the copy, `PUT` everything |
| 21 | A real oversized test body to a writable route can get stored (`/amr-list`) | caveat for testers [#91] | use a header-only probe |
| 22 | While the simulation runs it overwrites external writes | by design | switch it off when a PLC writes |
| 23 | Limits (AMRs, map size, path points) | by design | see below |
| 24 | `settings/active` follows the ctrlX user's Data Layer permissions, not the app's scopes | caveat [#55] | - |
| 25 | The 208 x 356 m map and its generator are not in the repo | maintenance caveat | copy in `.dev/maps/` (git-ignored) |
| 26 | Parts of the README performance section predate later versions | documentation caveat | see below |
| 27 | Not tested yet (list below) | open | - |

## Details

### 1. Choices made without the user or a real PLC

Made at the start (2026-10-22) and not confirmed since. The kit `README.md` still lists them: "confirm when one is
connected". They decide whether the PLC side can write to the node without changes.

| Choice | What the app assumes | Where to change it |
|---|---|---|
| Map units | mm, `map.unitsPerMeter` 1000: `HMI_Map.txt` is then 10.4 x 17.8 m with 20 mm cells. Could also be cm | setting |
| Fleet JSON | `{"amrs":[{"id","x","y","rotation"}]}`, optional name/state/battery/info. The user's PLC or fleet manager may already use another format | code; 0.4.0 added per-AMR nodes (user's request) |
| Rotation | counter-clockwise from +x, degrees | `fleet.rotationUnit` for radians; the direction is fixed |
| Lock | heading-up by default: the map turns, the AMR appears stationary | toggle in the UI |

On 2026-10-23 the user looked at 0.1.0 in the browser: "things look good". That covers the view, not these points.

### 2. No real PLC write

All write tests come from the build VM through REST bulk. #90: "whole device 31 % of 2 cores" includes the REST
server. A PLC writes over IPC without it; the app's own cost stays. README: number nodes take any number type (a
PLC's REAL or INT). That was not tried with a PLC.

### 3. 0.5.0 on the real CORE

Measured on the COREvirtual only (2026-11-02): upload through the reverse proxy 7.3 s from the build VM, load
0.65-0.7 s, tile levels 0.4-0.5 s, simulation grid (433 x 742 cells) 0.4 s later, app memory after a start 60.6 MB
(peak 62.7 MB), against 52.4 MB with the 103 x 177 m map.

For scale, not a measurement: parsing a 184k-line map took 5.0 s on the CORE against 0.8 s on the COREvirtual (0.2.2,
~6x, #58). On the CORE the 103 x 177 m map needed 55-58 MB (0.3.0). What the larger map needs there, and how close it
comes to the 75 MB target, is not known.

### 4. Cost of a node write [#88, #90]

Measured 2026-11-01 with 0.4.0: the build VM writes a moving fleet through REST bulk 5 times per second, simulation
off. App CPU in % of one core on the **real CORE** (COREvirtual table: README, "Input modes"):

| | 10 AMRs | 50 AMRs | 200 AMRs |
|---|---|---|---|
| whole fleet as JSON (5 writes/s) | 1.1 % | 2.5 % | 7.2 % |
| JSON per AMR (5 writes/s per AMR) | 2.6 % | 9.2 % | 31.6 %, reached 4.5 of 5 updates/s |
| node per value (25 writes/s per AMR) | 6.6 % | 29.2 % | 50.8 %, reached 2.1 of 5 updates/s |

~0.24 ms per node write on the CORE, ~0.05 ms on the COREvirtual. The app's own Python is ~0.03 ms of it (build VM
benchmark), the rest is the `ctrlxdatalayer` callback path (ctypes, Variant). The README says ~0.25 ms in one place
and ~0.24 ms in another; the measured figure is 0.24. Memory stayed flat (CORE 36-37 MB, COREvirtual 55-57 MB), with
no failed or rejected writes. On the COREvirtual, node per value at 200 AMRs reached 4349 writes/s (4.35 of 5
updates/s). Rule of thumb for a CORE: up to ~1000 node writes per second (e.g. 50 AMRs x 4 values x 5/s), beyond that
one JSON node.

Internally one wildcard provider node serves `fleet/amr/**` instead of 1400 registrations (200 AMRs x 7 values). The
app also registers `<path>` itself; whether `/**` alone covers `<path>` was not tested [#88].

### 5. Browser at 200 AMRs

All browser numbers come from headless Chromium in the emulated build VM. With 0.3.1 the A/B test (2026-10-24, one
local app, 200 simulated AMRs, medians of 3 rounds) gave:

| Page | Overview fps | Locked fps | Layout ms/s (overview / locked) | Style ms/s |
|---|---|---|---|---|
| 0.2.4 | 2.9 | 2.3 | 193 / 136 | 34 / 30 |
| 0.3.0 | 2.7 | 2.2 | 178 / 127 | 34 / 31 |
| 0.3.1 | 3.2 | 3.0 | 18 / 18 | 5 / 10 |

What is left is drawing 200 AMRs with labels (JavaScript ~350 ms/s, a bit more since AMRs keep their full shape) and
software rasterizing without a GPU. The README expects a PC browser to have "far more room"; that was not measured.
Locked fps depend on whether the followed AMR moves (a parked one reuses the map layer: one run had 14 fps).

### 6-7. Memory after a big map [#59] (fixed in 0.2.5)

0.2.4 on the real CORE (2026-10-24): 62-63 MB instead of 49-51 MB, peak 82 MB, after the faster map parser of 0.2.3.
Python keeps the memory of millions of short-lived small objects for reuse, so RSS does not go back down. Fix: parse
in 256 KB chunks (parser peak 7 MB instead of 39 MB), derive each tile level from the bitmap bytes of the level below
(peak 2 MB instead of 23 MB, 2.3 s instead of 5.0 s for all 7 levels in the build VM), check the size with `fstat`.
The CHANGELOG says `read(limit + 1)` "allocated the full 64 MB limit up front"; #59 adds that tracemalloc shows the full
limit although on Linux only the bytes read become resident.

Measured on the real CORE with 0.3.0, the same day: 55-58 MB during the big-map test, peak 60 MB, 37 MB after a start
with the bundled map, big map ready after 4.9 s (0.2.4: 6.8 s). The result stays above 0.2.2's 49-51 MB, which also
had no path history.

### 8-10. Performance work on 0.2.3 and 0.2.4

Test 2026-10-23: 103 x 177 m map, 10-200 AMRs, both devices. Results before/after (0.2.2 → 0.2.4) are in the README's
performance table, for example on the CORE: settings save with the big map 5.0 s → 0.11 s, first tile of a level
0.9-1.2 s → 0.07 s, updates/s at 200 AMRs 3.63 → 4.81. Device differences [#58]: parsing ~6x, CPU for the same
simulation load ~5x (25 % vs 5 % of a core) slower on the CORE; the build VM another ~6x slower than the CORE.

0.2.3 redrew and copied the map layer every frame while locked, slightly slower than before (measured); 0.2.4 draws
straight onto the canvas while the view moves.

### 11-12. The AMR list and the false alarm [#60]

0.3.0 on the CORE test gave 2.5-2.7 fps in the overview at 200 AMRs against 4.1 for 0.2.4 the day before. The heavier
list was suspected. The A/B test showed the VM's speed had changed and 0.3.0 was **not** worse. The same page gave 4.1
fps one day and 2.5 the next; a locked view gave 1.0 / 2.4 / 14 fps depending on whether the AMR moved. The test also
found the real cost: the list rewrote all tiles with every update since 0.1, which 0.3.1 fixed.

The 0.3.1 fix was measured locally only. No browser measurement on a device is recorded since 0.3.0.

### 13-16. Simulation

* **Deadlock** (seen on the COREvirtual with the bundled map, 0.2.0, all 4 AMRs stuck): after replanning around a
  blocking AMR they went back to their own cell centre first, often towards the blocker, and stayed "blocked" for
  minutes. The open-room test with larger steps did not show it; regression test on the bundled map at the real 200 ms
  step (0.2.1).
* **Enclosed pocket**: with doors in the outer wall the simulation used a small enclosed area, e.g. inside a rack pair.
  A region may now touch the map edge with up to 5 % of its cells (0.2.2).
* **Headings** grew without bound, e.g. -495 (0.1.1).
* **Grid memory** [#92]: 103 x 177 m map 3.9 → 0.3 MB kept (peak 5.5 → 0.4 MB); 208 x 356 m map 15.2 → 1.0 MB (peak 23.9
  → 1.7 MB). Measured with tracemalloc in the build VM (0.5.0).

The simulation is for demos and tests. The numbers "with a real PLC expect ~5 % of a CORE core at 200 AMRs" in the
README are an estimate from the split of the work (78 % simulation, 17 % reading the JSON, 5 % `/fleet`). The later
REST measurement (table in 4) gave 7.2 %.

### 17-19. Fixed in the write and upload paths

* **Rejected write** [#50]: found while building 0.1.0 (a read returned the invalid JSON). Template 0.1.5 runs `on_write`
  before storing; a rejected write answers `DL_INVALID_VALUE` and keeps the old value.
* **Widget churn** [#57]: a widget replacing its rows every poll made Playwright's `locator.wait_for` time out; rows are
  rebuilt only when something visible changed (0.2.1).
* **Upload** [#91] (COREvirtual, 2026-11-02): when the app answered early (409 exists, 400 name) without reading the
  body, `http.server` closed the socket and the client saw a reset or `EOF occurred in violation of protocol`.
  A header-only probe got `100 Continue` up to 100 MB, so there is no small proxy body limit.

### 20-21. Traps when testing [#89, #91]

* A UI test cleanup that sent `{"fleet": {"input": "fleet-json"}}` switched the simulation off (its default), and a
  later step waited 90 s for AMRs. The web UI always sends the whole form.
* While probing the upload limits, a real oversized test body went to `/amr-list`: a body of `#` characters is a valid,
  **empty** list, and it was stored. Which device and how it was restored is not recorded.

### 22-23. Behaviour and limits (README)

* The simulation overwrites external writes while it runs; switching it off clears the fleet once.
* Fleet: max. 1000 AMRs, 1 MiB per write. Simulation: up to 200 AMRs. AMR list: at most 1000 ids.
* An invalid fleet JSON is rejected as a whole. An AMR missing from the next write disappears.
* Values are in memory only: after an app restart the fleet is empty until the next write. Switching the input mode
  starts with an empty fleet.
* Map: limit 100 million cells, upload at most 64 MB. Largest loaded so far: 46 million cells, 8.7 MB.
* Paths: at most 200,000 points for all AMRs together; with many AMRs the paths get shorter.
* Labels without room are left out (the list still has them).
* The maps in the app data are saved and loaded with ctrlX configurations.

### 24. Read-only users [#55]

An app that hides settings from `.r` users must also close `/settings/meta` and `/values` (which contains
`settings/active`) and give the page its data through its own endpoint (`/view-config`). The Data Layer node
`settings/active` follows the ctrlX user's Data Layer permissions.

### 25. Test maps

`scripts/make_large_map.py` generates the 103 x 177 m map. The generator of the 208 x 356 m map was a scratchpad script
(the user's choice) and is **not in the repo**. A copy of the map is in `apps/ctrlx-amr-fleet/.dev/maps/`, which is
git-ignored, so a clone of the repo does not have it. It was also uploaded to the COREvirtual.

### 26. README sections that predate later versions

* The table line "0.2.5 lowers the load peaks, not re-measured": it was re-measured with 0.3.0 (see 6-7).
* "Locked onto an AMR at 200 AMRs ... each rewriting all 200 rows of the AMR list (not yet measured separately)": 0.3.1
  measured and fixed that (see 11).
* The tables' "real ctrlX CORE (ARM, 2 cores)": 2 cores for apps, 4 in total [#73].

### Other gotchas from this app

* **Widget login** [#53, #57]: not in the SDK docs. The ctrlX UI (rexroth-deviceadmin 4.6) keeps the token in
  `sessionStorage["idm_token"]`. Verified on a real home screen of the COREvirtual with 0.2.0.
* **UI tests** [#51, #54]: the first QuickJS harness was replaced by headless Chromium in 0.1.1. With a second page use
  another tab, not `page.set_content()`: the old page's timers keep running.

## Not tested yet

* **A real PLC or fleet manager** writing any of the nodes, incl. the per-AMR nodes and PLC number types (REST only).
* **0.5.0 on the real CORE**: map drop-down, upload through the reverse proxy, the 208 x 356 m map (load, tiles, RSS,
  simulation). Bigger than recorded: nothing near the 64 MB upload limit or 100 million cells.
* **More than 200 AMRs**, up to the 1000 the app accepts.
* **A browser on a PC or an HMI**: every recorded fps number comes from the emulated VM; `--narrow` runs locally only.
* **0.3.1's list change on a device**: included since 0.4.0, no device measurement recorded.
* **`ui_check --device` on 0.4.0 and 0.5.0**: not recorded (last recorded: 0.3.0 on the COREvirtual). For 0.5.0 the map
  upload with the button through the reverse proxy is recorded.
* **`ui_check --device` and the home screen widget on the real CORE**: recorded for the COREvirtual only (widget: 0.2.0).
* **Read-only user on a device**: `ui_check --role reader` is recorded locally.
* **Long runs** (hours/days): memory growth, the path history at its 200,000-point cap.
* **Save/load of a ctrlX configuration** with the AMR list and large map files (size, time): not recorded.
* **Wildcard node**: whether `/**` alone covers the parent address [#88].

## Diagnostics

Run from the app folder. `perf_test.py` changes settings on the device and restores them at the end, and it leaves the
uploaded map in the app data. Ask first.

```bash
python3 scripts/perf_test.py --device x3 --map MAP.txt [--counts 10,25,50,100,200] [--browser 10,50,200] [--out perf.json]
python3 scripts/perf_test.py --device x3 --inputs fleet-json,amr-json,amr-fields [--input-counts 10,50,200] [--rate 5]
python3 scripts/make_large_map.py OUT.txt [--seed 7] [--preview PREFIX]        # the 103 x 177 m map
~/.cache/ctrlx-kit/uivenv/bin/python scripts/browser_ab.py --map BIG.txt --amrs 200 --www 0.2.4=<dir>/www --www new=www [--rounds 3]
python3 ../../tools/ui_check.py . --narrow
python3 ../../tools/ui_check.py . --role reader
python3 ../../tools/ui_check.py . --device
```

`browser_ab.py` serves each page version in turns and reads CDP `Performance.getMetrics`: compare `LayoutDuration`,
`RecalcStyleDuration`, `ScriptDuration`, not only fps [#60]. An older page: `unsquashfs -d <dir> ctrlx-amr-fleet_0.2.4_amd64.snap www`.

From the kit root:

```bash
tools/ctrlx.py --device x3 ssh 'systemctl show -p MainPID --value snap.ctrlx-amr-fleet.service.service'   # then /proc/<pid>/stat fields 14+15, VmRSS, VmHWM [#58, #59]
tools/ctrlx.py dl-write ctrlx-amr-fleet/fleet/amrs '"[{\"id\":\"A1\",\"x\":1000,\"y\":30000,\"rotation\":90}]"' --type string   # mode fleet-json, simulation off
tools/ctrlx.py dl-read ctrlx-amr-fleet/fleet/last-error
tools/ctrlx.py dl-read ctrlx-amr-fleet/fleet/rejected
tools/ctrlx.py dl-read ctrlx-amr-fleet/map/info
tools/ctrlx.py api GET /ctrlx-amr-fleet/api/v1/map
```

Measuring notes: `tracemalloc` slows allocation-heavy code ~5-20x, so time without it [#59, #92]. `f.read(limit)` shows up
in tracemalloc as the full limit [#59]. Upload limits: only a header-only probe (`Content-Length` + `Expect: 100-continue`, no
body) [#91]. Change settings from a tool with `GET /settings`, change the copy, `PUT` the whole object [#89].
