# Known issues and caveats - ctrlx-core-connector

Working document: everything that does not work as expected, works only with a workaround, is a caveat for users
or maintainers, or was not tested, with the evidence, what was tried and what is still open. Keep it current: add
new findings, mark fixed ones (don't delete them - the history helps the next analysis), and date every entry.
Generic lessons also go to `docs/ctrlx/gotchas.md` (numbers in brackets).

Written 2026-11-04, after the fact, from the app's README and CHANGELOG, the gotchas, the kit `README.md`,
`apps/README.md` and the development notes of the sessions. Nothing was re-measured or re-checked on a device for
this document. **"Not recorded" means the records don't say** - it may have been done or not.

## Test setup

| Device | Facts as recorded |
|---|---|
| **ctrlX COREvirtual** | ctrlX WORKS, amd64, ctrlX OS 4.6, KVD (`rexroth-kvd`) 4.6.3. From the LAN through the Windows host's port forwarding: `192.168.1.211:8443` (Data Layer ports 2069/2070 pass unchanged, verified 2026-10-22 [#45]) |
| **Real ctrlX CORE** | 192.168.1.7, arm64, ctrlX OS 4.6.5/4.6.6, KVD 4.6.5. Clock 14 h behind on 2026-10-22 [#45], 7 days behind until the user set it on 2026-10-31 [#77] |
| **The user's ctrlX OS 3.6 core** | 192.168.1.10. No login for it in `devices.toml`, so the kit tools can't reach it directly: seen only through the user's own remote `iot-core` on the COREvirtual and as core "IIoT-Core" in the app on the COREvirtual |
| Data Layer library | recorded as `ctrlx-datalayer` 3.5 client in #79; #78 quotes the 3.6 enum |

History on the devices. The state today was not checked.

| Version | Installed and verified | How |
|---|---|---|
| 0.2.0 | 2026-10-31, both, each watching the other | API in both directions, `ui_check --device`; details in the CHANGELOG |
| 0.2.1 | 2026-10-31 ~15:35 CDT, both, clean builds | device test, `ui_check --device` |
| 0.2.2 | 2026-10-31 ~16:55 CDT, both, clean builds | checks through the remote `iot-core` |
| 0.2.3 | 2026-10-31 ~18:15 CDT, both, clean builds | `ui_check --device` (both tutorials) |
| 0.2.4 | 2026-11-01 ~01:15 CDT, both, clean builds | `ui_check --device`, collection details locally and through a remote |

Both cores were offline from ~19:45 CDT on 2026-10-31 until ~01:00 CDT on 2026-11-01 (cause not recorded).
Core Monitor was uninstalled on both on 2026-10-31: on the COREvirtual by the user, on the real CORE on the user's
go-ahead at ~14:45 CDT (SERVICE, uninstall task, back to OPERATING; Core Connector stayed connected).

## Overview

| # | Issue | Status | Workaround / fix |
|---|---|---|---|
| 1 | KVD restarts all its nodes (~1 s) on every `nodes.json` change | caveat (KVD behaviour) | one change at a time; plan for the gap |
| 2 | KVD misses a change made during such a restart | worked around | app saves the file once more |
| 3 | KVD exits on a 64-bit initial value it can't convert (all KVD nodes down) | worked around | initial values within ±2^53; app takes a refused entry out |
| 4 | KVD keeps the values of deleted nodes | caveat (KVD behaviour) | write the value after creating the node |
| 5 | Solutions WebDAV ignores `If-Match` | caveat | app serializes its edits, re-reads before a change |
| 6 | Remote stuck at `SEC_INVALID_SESSION` after the other core's clock was set | fixed in 0.2.0 | app recreates the remote, at most every 30 s |
| 7 | Error texts never showed on a device (result names without `DL_`) | fixed in 0.2.0 | names normalized |
| 8 | First start after the install: "Permission denied" on the app data, import at risk | fixed in 0.2.0 | settings import/load retries |
| 9 | ctrlX OS 3.6 core shown "not connected (INVALID_ADDRESS)" | fixed in 0.2.2 | every status value optional |
| 10 | Browser on "This device": remote nodes without type and value | fixed in 0.2.2 | type reference below a remote recognized |
| 11 | Details of a KVD collection failed (HTTP 400 "Expecting value") | fixed in 0.2.4 | empty JSON text = no value |
| 12 | Passwords in plain text in `settings.json` and every saved configuration | by design | never returned by the API or the Data Layer; use users with few rights |
| 13 | Uninstalling an app keeps its app data, incl. plain-text passwords | caveat (ctrlX OS) | delete the folder afterwards |
| 14 | Core Monitor and Core Connector together delete each other's remotes | by design (same prefix) | uninstall Core Monitor |
| 15 | Certificate check off by default | caveat | needed for the default self-signed certificate; a setting per connection |
| 16 | Writes run with the rights of the app / connection user, not of the person in the browser | by design | give "Manage Core Connector" sparingly |
| 17 | `run-local.sh` against a device's Data Layer manages that device's `cm-*` remotes | caveat | an empty local list deletes them - don't |
| 18 | A missing node and a remote that is not mounted answer the same | caveat | see 9 |
| 19 | A remote to the device itself failed | **open**, tested once | use a second device; this device is read from the root anyway |
| 20 | Provider glue from before template 0.1.5: `on_write` can't veto | caveat, current state not recorded | see "Maintainer facts" |
| 21 | Two KVD sample values changed on the COREvirtual by mistake | noted, previous value not read | see below |
| 22 | Not tested yet (list below) | open | - |

## Details

### 1-4. KVD and its `nodes.json` [#80]

Seen 2026-10-31 on KVD 4.6.3 (COREvirtual) and 4.6.5 (real CORE). KVD watches `<active configuration>/kvd/nodes.json`
and applies a change without a load command, but logs `restarting ... stopped ... running`: for ~1 s **all** its
nodes and its own `kvd/...` root are gone. Measured through the app: add ~1.8-2 s, delete ~1.6-2 s (CHANGELOG 0.2.0).
Other nodes keep their current values (a written 12 stayed 12). What a PLC or other client that reads KVD nodes does
during the gap is not recorded.

* **Missed change**: after 8 deletes in a row on the real CORE the file was clean, but KVD still showed
  `cctest/folder` until the file was saved again (unchanged).
* **KVD exits**: a `uint64` initial value of 2^64-1 (KVD reads numbers as float64):
  `1.8446744073709552e+19 ... value not aUINT64 (DL_FAILED)` → "failed to connect", exit 1, systemd restarts it 10 s
  later (`restart-delay: 10s`) and it exits again while the entry is in the file. All KVD nodes were down ~11 s once
  on the COREvirtual. Larger values can be written once the node exists (a write of 2^64-2 worked, 0.2.0).
* **Early "gone"**: a check "is the node gone?" right after a change hits the restart window and says yes too early.
* **Old values**: KVD keeps the values of deleted nodes in its database (`kvd/value-count` counts them). A node
  created again at the same address starts with its last value, not with the initial value of the new entry.

What the app does: one entry at a time per core, waits until KVD runs again (`kvd` root back) and shows the change,
saves once more if KVD runs but missed it, takes an entry out at once when KVD exited. It only deletes entries that
are in `nodes.json`. The user's own KVD nodes stayed untouched in the 0.2.0 test.

### 5. Solutions WebDAV ignores `If-Match` [#81]

A PUT of `appdata/kvd/nodes.json` with a wrong ETag in `If-Match` still answered 201 and set a new ETag. There is no
optimistic locking: the app serializes its own edits and re-reads the file right before a change. An edit by someone
else between the read and the PUT is not detected.

### 6. Remote stuck after the clock was set [#77] (fixed in 0.2.0)

The real CORE's clock was 7 days behind. The user set it on 2026-10-31 (Logbook "Time settings changed successfully",
16:24:18 UTC). From that second every read through the COREvirtual's remote `cm-real-core` answered
`SEC_INVALID_SESSION` (REST: 401 `DL_SEC_INVALIDSESSION`), still 9 min later. Deleting `devices/remotes/cm-real-core`
and creating it again fixed it at once. The app now recreates a remote on `SEC_INVALID_SESSION`,
`SEC_INVALID_TOKEN_CONTENT` or `SEC_NO_TOKEN`, at most every 30 s. A second clock change after the fix: not recorded.

Note: a CORE that was powered off can come back with a wrong clock (#45, #75; NTP inactive), with remotes still working
in both directions but wrong Logbook timestamps.

### 7. Result names [#78] (fixed in 0.2.0)

REST and the C library say `DL_INVALID_ADDRESS`, `DL_SEC_INVALIDSESSION`. Python's `Result.name` says
`INVALID_ADDRESS`, `SEC_INVALID_SESSION` (and `SEC_NO_TOKEN` is spelled `sec_noSEC_NO_TOKEN_token` in the 3.6 enum).
Core Monitor compared with `"DL_..."`, so "not connected" and "login rejected" never showed on a device.

### 8. First start after the install [#82] (fixed in 0.2.0)

0.2.0 waited for `$SNAP_COMMON/solutions/activeConfiguration` (visible after 12 s), then `os.makedirs` of its own
folder raised `PermissionError` and the process crashed; systemd started it 1 s later and it worked. Here the import
of Core Monitor's settings must not decide "nothing to import" on that error, so `_load_settings` treats `OSError` as
"not ready yet" and tries again. Seen on the real CORE in the 0.2.0 test.

### 9, 18. ctrlX OS 3.6 core without `system/info/...` [#86, #44] (fixed in 0.2.2)

2026-10-31: the user's core 192.168.1.10 showed "not connected (INVALID_ADDRESS)" although its remote worked. Its
`system/info` held only `ledDevice` and `setup-in-progress` (= false), no `hostname`, `uptime`, `architecture`, and
it had no `system/apps`. `framework/metrics/system/*` and `scheduler/admin/state` were there. The app required
`system/info/uptime`. The address format was **not** the cause: the user's plain `tcp://user:pw@ip` remote connected,
and the app's `...?sslport=443&NoCheckCertificate=true` form connected to the same device on 2026-10-22.

Fix: every status value is optional; a core is "connected" when any of the CPU/memory/uptime nodes can be read;
missing ones show "–". A login, connection or session error still ends the poll at the first node. Confirmed: the
core, configured on the COREvirtual as "IIoT-Core", shows connected (recorded 2026-11-01). The tile of such a core has
no OS version: 3.6 versions come from `system/apps` (Device Admin), which this core lacks.

Caveats that remain: a read of a missing node through a remote answers `INVALID_ADDRESS`, the same as a read through a
remote that is not mounted. A failed login and a failed certificate check answer `SEC_UNAUTHORIZED` (tested with a
made-up user on 3.6 and 4.6). #44 saw `system/info/{hostname,architecture,uptime}` on a 3.6 device - don't rely on them.

### 10-11. Fixed browser problems

* **Nodes of a remote on "This device"** showed no type and no value: their type reference is
  `devices/remotes/<name>/types/datalayer/<type>` (#79). Recognized since 0.2.2.
* **`samples/kvd/collection`** (KVD 4.6.5, found on the real CORE): REST answers `""`, `read_json_sync` returns OK with
  an empty string, `json.loads("")` raised → "Cannot read this node" (HTTP 400 "Expecting value"). Fixed in 0.2.4,
  checked on both cores and through a remote [#87]. KVD's node list also holds patterns such as
  `samples/kvd/implicit/**` and `.../*`, which are not addresses; the tutorial's "Show me" skips them.

### 12-13. Passwords and uninstalling [#42, #85]

Passwords are in plain text in `settings.json` (needed for the remote connection) and so in every saved configuration.
They never leave through the web API or Data Layer nodes (`settings/active`, `cores/summary` masked); the Data Layer
shows remote addresses as `tcp://***:***@...`.

Uninstalling keeps the app data. Seen 2026-10-31: Core Monitor's `ctrlx-core-monitor/settings.json` stayed. On the
user's yes it was deleted on **both** cores (WebDAV DELETE, 204), after checking that Core Connector's own settings
were there. #85 holds for every app, so Core Connector's own folder will stay too when it is uninstalled.

### 14. Side by side with Core Monitor

The new snap name installs next to Core Monitor. While both run, each one deletes remotes the other one added (they
share the prefix `cm-`, kept on purpose to take over the remotes; the user's decision 2026-10-31: new name + import,
uninstall the old app afterwards). Done on both devices, see "Test setup".

### 15, 19. Certificates and remotes [#40, #41]

Without `NoCheckCertificate=true` a remote to a core with the default self-signed certificate never comes up. Logbook:
"Data layer remote connection could not be established - Connect error: DL_SEC_UNAUTHORIZED", although the same login
works for REST; the other device logs no login at all. The app's "Skip certificate check" is on by default and also
applies to its HTTPS requests for KVD.

A remote to the device itself (`tcp://...@127.0.0.1`) failed the same way, tested only **without** `NoCheckCertificate`
(2026-10-21). Not re-tested. The app reads this device from the root (`_local`), so it does not need one.

### 16-17. Rights and local runs (README)

Writes and KVD changes run with the rights of the **app** on this device and of each **connection's user** on the
other cores - not with those of the person in the browser. `run-local.sh` against a device's Data Layer manages that
device's `cm-*` remotes from its local settings: an empty list deletes them. The app never touches remotes that don't
start with `cm-`.

### 20. Maintainer facts

* **`on_write` order** [#50]: template 0.1.5 made `on_write` run before storing, so it can veto. #50 says apps created
  earlier (`ctrlx-core-monitor`) keep the old order, fine as long as `on_write` never rejects. Whether Core Connector
  still has the old order is not recorded.
* **Keep** the `cm-` prefix and the id `_local` (user's choices, 2026-10-31).
* **JSON forms of the client** [#79]: `read_json_sync` gives `{"type":"int32","value":77}` for simple types and
  arrays, the plain object for flatbuffers. `write_json_sync` wants the same; a bare `13` → `FAILED` "1: 2: error:
  declaration expected", a wrapper around a flatbuffers object → `FAILED` "unknown field: type".
* **Bulk** [#79], from the build VM through the COREvirtual's remote to the real CORE, 60 nodes: bulk read 55-72 ms, bulk
  metadata 150-160 ms, 60 single `read_json_sync` 1.6-1.8 s. Use responses inside the `with Bulk(...)` block; broker
  limit `maxBulkRequest` 1000.
* **Names**: arrays are `array-of-int16` in the type tree but `arint16` in `nodes.json`, REST and `read_json` [#83].
  Package ids are `snap_<name>`: compare `name`, not the id [#84].
* **Read-only first** [#43]: a wrong-typed write to a read-only node answered `DL_TYPE_MISMATCH`; now
  `DL_PERMISSION_DENIED`.
* **KVD entry schema**: `/snap/rexroth-kvd/current/configs/schemas/node.v1.0.schema.json` on the device [#80].
* **Remote versus shared Data Layer**: `devices/remotes/<name>`, not `datalayer/reverse-connections` [#39].

### 21. KVD sample values on the COREvirtual

On 2026-10-31, while probing, two KVD sample values on the COREvirtual were written by mistake and then restored:
`samples/kvd/variables/variable` was set to 0, its documented initial value. **Its previous value was not read**, so
that it was 0 before is not known. The records don't name the second value.

## Not tested yet

* **App memory and CPU**: no measurement of this app is recorded on any device (the kit's target is < 75 MB per app).
* **Home screen widget on a real home screen**: 0.2.0 recorded "widget (test page)"; later `ui_check --device` runs
  list the widget, the real home screen is not recorded.
* **ctrlX OS 3.6 target**: recorded are the connected state, metrics, `scheduler/admin/state` and browsing through the
  API. Writing and KVD add/delete on a 3.6 core are not recorded.
* **Automatic remote recovery**: the recreate on `SEC_INVALID_SESSION` was built after the manual cure on 2026-10-31;
  a repeated clock change that shows it working by itself is not recorded.
* **Connection users with few rights** (writes and KVD through WebDAV): tests with a limited user are not recorded,
  only a failed login with a made-up user.
* **A core with the certificate check on** (`skipCertificateCheck` off, a certificate that passes): not recorded.
* **Recovery after an outage** (the other core rebooting or offline for hours): not recorded as a test.
* **More cores**: the most recorded is this device plus 3 connections (counts "3 of 4"). More: not recorded.
* **Long runs** (hours/days, memory growth): not recorded.
* **Save/load of a ctrlX configuration with this app's settings**: verified for the template, not recorded for this app.
* **Read-only user on a device**: not recorded.
* **`.app` package**: one file for both architectures was verified with Core Monitor 0.1.2 (#49), not recorded for
  Core Connector.

## Diagnostics

The commands recorded with the findings; where only the node or file was recorded, the command is the usual kit form.
Run from the kit root; `--device x3` = real CORE, without it the default device.

```bash
python3 tools/ui_check.py apps/ctrlx-core-connector --device x3     # start page, drawer, add dialog, widget, tutorial, browser
tools/ctrlx.py --device x3 dl-browse system/info                    # what a core really has (#86)
tools/ctrlx.py dl-read devices/remotes/cm-<name>/scheduler/admin/state    # through a remote
tools/ctrlx.py dl-read devices/remotes/cm-<name>/system/info/uptime       # INVALID_ADDRESS: node missing OR remote not mounted
tools/ctrlx.py --device x3 api GET /ctrlx-core-connector/api/v1/cores/_local/kvd    # KVD running? addresses in nodes.json
tools/ctrlx.py --device x3 appdata kvd/nodes.json                   # KVD's file
tools/ctrlx.py --device x3 dl-read kvd/value-count                  # kvd/db, kvd/value-count, kvd/verbose: KVD is running
tools/ctrlx.py --device x3 ssh 'sudo snap logs -n 50 rexroth-kvd'   # restarting ... stopped ... running
tools/ctrlx.py --device x3 ssh timedatectl                          # clock, NTP (#45)
tools/ctrlx.py --device x3 packages                                 # names and snap_ ids (#84)
```

Uninstall an app and remove what it leaves behind (#85). These change the device, so ask first:

```bash
tools/ctrlx.py state SERVICE
tools/ctrlx.py api POST /package-manager/api/v1/tasks --data '{"action": "uninstall", "parameters": {"id": "snap_<name>"}}'
tools/ctrlx.py api GET /package-manager/api/v1/tasks/<id>             # until done
tools/ctrlx.py state OPERATING
tools/ctrlx.py api DELETE /solutions/webdav/appdata/<snap>/           # 204: the folder with its files
```

Careful when probing KVD or a browser write: those are real writes on the device (see 21).
