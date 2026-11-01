# Changelog

Versions follow semantic versioning and must match `version:` in `snap/snapcraft.yaml`.

## 0.2.4

- **Setting "Data Layer browser → Read interval [ms]"** (`browser.refreshMs`, 500-60000, default 1000): how
  often the browser reads the visible tree values and the selected node's value (were fixed: 2 s / 1 s - the
  tree now also reads every second by default). The page picks up a change within 5 s; the next read is
  planned when the last one is done. Older settings files get the default.
- Browser: "**Editable**" instead of "KVD" in the top bar (nodes can be added: the Key Value Database app runs
  on the core); a **person icon** instead of the "KVD" tag marks a node a user added.
- Tutorial: the card stays at the bottom unless it would hide the step's control completely; the blinking
  ring is drawn under the card (step "Tree" had it over the card). "Add node" is now explained on every
  core (available wherever the KVD app is installed); texts name the new marks and the read intervals.
- Fix: the details of a node without a value - KVD's sample `samples/kvd/collection` - failed with "Cannot read
  this node" (HTTP 400 "Expecting value"): the read returns an empty JSON text (gotchas #87). Shown without a
  value now. The tutorial's "Show me" for KVD nodes skips KVD's `…/**` patterns.

Verified 2026-11-01 on the COREvirtual (amd64) and the real CORE (arm64), clean builds: `tools/ui_check.py
--device` on both (card at the bottom with the ring under it on "Tree", person icon in tree and details,
"Editable", the read interval in the settings drawer); `samples/kvd/collection` details without a value on
both cores and through a remote (COREvirtual → real CORE). The user's ctrlX OS 3.6 core, added on the
COREvirtual, shows "connected" (0.2.2).

## 0.2.3

- **Help button** in the top bar of both pages (as in the AMR Fleet View): the app's page on GitHub and a
  **tutorial** - a card that steps through the controls (arrows or ← →, Escape ends) while the control of
  each step blinks; everything stays usable meanwhile. Start page: cores, a core's status, web UI link,
  connection state, add/remove (users who may change settings), settings, Data Layer, home screen widget,
  help; its last step's "Show me" opens the browser of this device and continues there: tree, filter,
  address, details ("Show me" selects the CPU load), write and KVD steps (selecting only - nothing is
  written or deleted), this core's state, web UI, back, help. Shared code in `www/common.js` (`setupHelp`).
- Phone width: the top bar stays one row (no version number); the hint below "+ Add core" no longer touches it.

Verified 2026-10-31 on the COREvirtual (amd64) and the real CORE (arm64), clean builds: `tools/ui_check.py
--device` steps through both tutorials (start page 12 steps, browser 12 steps with the write and KVD steps),
the handover to the browser, "Show me" on "Details", the ring over the control, keyboard and 390 px width.

## 0.2.2

- **ctrlX OS 3.6 core shown as "not connected (INVALID_ADDRESS)"** although its remote connection worked: that
  core (192.168.1.10) has no `system/info/hostname`, `uptime` or `architecture` (its `system/info` holds only
  `ledDevice` and `setup-in-progress`) and no `system/apps`; the app required `system/info/uptime`. Now every
  status value is optional - a core is "connected" when any of the CPU/memory/uptime nodes can be read; missing
  ones show "–". A login/connection/session error still ends the poll at the first node.
- Data Layer browser on "This device": nodes of a remote below `devices/remotes/<name>/` showed no type and no
  value (their type reference is `devices/remotes/<name>/types/datalayer/<type>`) - recognized now.

Checked 2026-10-31 through the user's remote `iot-core` on the COREvirtual (ctrlX OS 3.6 core, 192.168.1.10):
metrics and `scheduler/admin/state` readable, `system/info/{hostname,uptime}` INVALID_ADDRESS; the app's
browser API browses it (bulk requests work with 3.6).

## 0.2.1

- **This device counts**: "(n of m connected)" on the start page and in the widget, and the Data Layer nodes
  `cores/configured` / `cores/connected`, now include the device the app runs on (`cores/summary` still lists
  only the connections).
- **Names may have capital letters** (`Line-8-Core`; letters, digits, `-`, max. 24). The remote keeps the
  case (`devices/remotes/cm-Line-8-Core`, checked on the COREvirtual); a name that differs from an existing one
  only in case is refused ("already used").
- **No HTTPS port field** in "Add core": a port other than 443 goes into the host field (`192.168.1.1:8443`, a
  pasted `https://host:port/` works too); the app stores it as before (`host` + `sslPort`).
- Data Layer browser: a click on the empty part of the tree deselects the node.
- Info icons: smaller, a filled gray circle with an upright "i" (was an italic serif "i" in a ring).
- Add core: placeholders `Line-8-Core` and `192.168.1.1`.

Verified 2026-10-31 on the COREvirtual (amd64) and the real CORE (arm64), clean builds: on each core a core
`Line-8-Core` added for the other one (CORE: `192.168.1.211:8443`, COREvirtual: pasted `https://192.168.1.7/`),
connected within 2 s as `devices/remotes/cm-Line-8-Core`, browsed and its KVD read through it, `line-8-core`
refused (409), counts 3 of 3 / 3 of 4 incl. this device on the page and in `cores/configured` /
`cores/connected`, then removed (remote gone). `tools/ui_check.py --device` on both: dialog without port field,
count, widget, deselect by a click below the tree.

## 0.2.0

Verified 2026-10-31 on the COREvirtual (amd64, ctrlX OS 4.6, KVD 4.6.3) and the real CORE (arm64, ctrlX OS
4.6.5/4.6.6, KVD 4.6.5), each watching the other: import of Core Monitor's settings (on the CORE after a
"Permission denied" retry on the first start), tiles + operating state, settings drawer, widget (test page),
Data Layer browser (`tools/ui_check.py --device`), and through the API on both cores in both directions:
browse, writes (float64, uint16 range check, read-only node, arint16, string, uint64 2^64-2), KVD add of 8
node kinds via the local file and via the other core's WebDAV (~1.8-2 s each, KVD restart), 2^64-1 refused,
duplicate refused, delete of all of them (~1.6-2 s each) - the user's KVD nodes untouched.

- **Renamed** Core Monitor → **Core Connector**: snap `ctrlx-core-connector` (URL, app data folder, Data Layer
  root, scopes renamed). On the first start it imports `ctrlx-core-monitor/settings.json` and takes over the
  `cm-*` remotes (prefix kept). Uninstall Core Monitor afterwards.
- Start page: the "This app" card is gone; settings are in a drawer that slides in from the right
  (only for users who may change them). A permanent tile for **this device**; the operating state on every
  tile; a tile's name opens that core's ctrlX web UI in a new tab, the rest of the tile the Data Layer browser.
- **Data Layer browser** per core: tree (bulk requests, values of simple nodes refreshed while visible,
  filter, keyboard), node details with metadata and live value, writing values (scalars, arrays, flatbuffers
  as JSON), address field.
- **Key Value Database nodes**: add variables (scalar/array types, initial value, unit, description,
  read-only) and folders, delete nodes defined in KVD's `nodes.json` - on this device (app data file) and
  on other cores (their Solutions WebDAV, HTTPS with the connection's user). One change at a time; waits
  until KVD (which restarts its nodes for ~1 s on every change) runs again, saves again if KVD missed the
  change, removes an entry KVD did not take; 64-bit initial values within ±2^53 (2^64-1 made KVD exit).
- **Home screen widget** "Connected cores": cores with operating state, Open button.
- A remote whose session became invalid (`SEC_INVALID_SESSION`, e.g. after the other core's clock was set)
  is recreated automatically; it stayed broken before.
- First start right after the install: the app data folder can be visible but not yet accessible -
  settings import/load is retried instead of crashing (the import would otherwise be skipped).
- Error texts on the tiles were never shown on a device: the Python library names results without
  the `DL_` prefix (`INVALID_ADDRESS`); now normalized.

## 0.1.2

- Template 0.1.4 layout: Python package in `src/`, one `files` part, incremental builds
  and `python-packages: []` (same snap content and behaviour). Builds: UI change 4.6 min
  instead of 12.8, Python change 13.3 instead of ~20, full 16.7 instead of ~20.

## 0.1.1

- ctrlX OS version also for ctrlX OS 3.6 devices (no `system/info/ctrlxOsVersion` there: taken
  from the Device Admin app, node `snap_rexroth-deviceadmin`); the UI shows only "ctrlX OS x.y".
- Add dialog: "Skip certificate check" label next to its checkbox; hints as (i) tooltips
  (core name, password storage, connections).

## 0.1.0

- Created from the ctrlX Python web-app template (0.1.2).
- Monitoring of other ctrlX COREs via Data Layer remote connections (`devices/remotes/cm-<name>`):
  host name, IP, ctrlX OS / Device Admin version, architecture, uptime, CPU and memory.
- Connections stored in `settings.json` (app data); web UI with "+ Add core" dialog and remove.
- Passwords never leave the app (masked in API responses and Data Layer nodes).
