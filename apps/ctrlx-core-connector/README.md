# Core Connector (`ctrlx-core-connector`)

ctrlX OS 4.6 app (snap, `core24`, Python) for **this device and other ctrlX COREs** on the network:

* **Status tiles**: host name, IP address, operating state (OPERATING / SETUP / SERVICE), ctrlX OS version,
  architecture, uptime, CPU and memory. The first tile is always the device the app runs on (it can't be
  removed); the others are the configured connections. A tile's **name opens that core's ctrlX web UI** in a
  new tab; a **click anywhere else on the tile opens the Data Layer browser** of that core.
* **Data Layer browser** (`browse.html?core=<name>`, `_local` = this device), similar to the ctrlX Data
  Layer view: tree with lazily loaded children (filter, collapse, keyboard: arrows, Enter), simple values
  and types right in the tree (read while visible), details of the selected node (metadata, operations,
  references, live value, objects as JSON) - both read every `browser.refreshMs` (1 s by default, Settings →
  Data Layer browser), address field to jump to a node,
  **write** values (typed by the node's write type; flatbuffers objects as JSON).
* **Key Value Database (KVD) nodes**: when the core runs the KVD app (`rexroth-kvd`; the browser shows
  **Editable** in its top bar), **Add node** creates a variable (any scalar or array type, initial value, unit,
  description, read-only) or a folder, and **Delete KVD node** removes one that is defined in KVD's
  `nodes.json`. Such nodes carry a person icon in the tree ("added by a user").
* **Home screen widget** "Connected cores": every core with its operating state, an **Open** button for the
  app; a remote core's name opens its web UI.
* Settings (log level, poll interval, the browser's read interval) in a drawer that slides in from the right.
* **Help** button in the top bar (both pages): the app's page on GitHub and a **tutorial** that steps
  through the controls of the start page and continues in the Data Layer browser (as in the AMR Fleet View).

Formerly **Core Monitor** (`ctrlx-core-monitor` 0.1.x): see "Moving from Core Monitor" below.

**Known issues, caveats, measurements and what is not tested yet: [KNOWN-ISSUES.md](KNOWN-ISSUES.md)** (working document - keep it current).

## How it works

* Connections use the ctrlX OS feature **Data Layer remote connection**: for every configured core
  the app creates `devices/remotes/cm-<name>` in the Data Layer of *this* device with the address
  `tcp://<user>:<password>@<ip>?sslport=<port>&NoCheckCertificate=true`. ctrlX OS then mounts the
  whole Data Layer of the other core below `devices/remotes/cm-<name>/`. Status and browser read and
  write there (this device: from the root). The prefix `cm-` is Core Monitor's - kept so its remotes are taken over.

  | Shown | Node (below `devices/remotes/cm-<name>/`, or from the root for this device) |
  |---|---|
  | Host name, architecture, uptime | `system/info/hostname`, `architecture`, `uptime` |
  | Operating state | `scheduler/admin/state` → `state` |
  | ctrlX OS version | `system/info/ctrlxOsVersion` (4.x); ctrlX OS 3.6 has no such node → major.minor of `system/apps/installed/snap_rexroth-deviceadmin` → `release.version` |
  | CPU, memory | `framework/metrics/system/cpu-utilisation-percent`, `memused-percent`, `memused-mb`, `memtotal-mb` |

* A remote whose session the other core no longer accepts (`SEC_INVALID_SESSION`, e.g. after its clock was
  set forward) does not recover by itself: the app deletes and recreates it (fresh login), at most every 30 s.
* The browser uses bulk requests (metadata, browse, read of a folder's children in one request each): a
  folder of 60 nodes takes ~0.1-0.2 s through a remote instead of ~1.7 s node by node.
* **KVD**: KVD keeps its node definitions in its app data (`kvd/nodes.json`) and watches that file. The app
  edits that file: on this device directly (active-solution plug), on another core through its Solutions
  WebDAV (`/solutions/webdav/appdata/kvd/nodes.json`, HTTPS with the connection's user). **KVD restarts its
  nodes for every change: all KVD nodes of that core are unavailable for about a second** (an add takes
  ~2 s). Other nodes keep their values. KVD misses a change made during such a restart and exits on an entry
  it can't convert, so the app changes one entry at a time, waits until KVD runs again and shows the
  change, saves once more if KVD missed it, and removes an entry KVD did not take. 64-bit initial values
  are limited to ±2^53 (KVD reads numbers as float64; 2^64-1 made it exit) - larger values can be written
  once the node exists. Values written later are kept by KVD (nodes.json holds only the initial value) - also
  after a delete: a node created again at the same address starts with its old value, not the initial one.
* The app only creates and deletes remotes named `cm-*`. Remotes you create by hand
  (Settings → Data Layer → `devices/remotes`) are never touched.
* `NoCheckCertificate=true` ("Skip certificate check", default on) is needed for cores with the
  default self-signed certificate - without it the connection fails with `SEC_UNAUTHORIZED`. The same
  choice applies to the HTTPS requests for KVD.
* No extra slots or ports: the app uses the local Data Layer (`ipc://`) and, for KVD on other cores,
  outgoing HTTPS (`network` plug, already needed by the Data Layer library).
* Works with ctrlX OS 4.x and 3.6 targets (3.6: version taken from the Device Admin app). Every status value
  is optional: one ctrlX OS 3.6 core had no `system/info/hostname`/`uptime`/`architecture` and no
  `system/apps` - its tile shows CPU, memory and operating state, the rest "–" (gotchas #86).
* To reach a **COREvirtual** from a device on the LAN, use the Windows host's IP and HTTPS port
  8443, e.g. `192.168.1.211:8443` (ctrlX WORKS port forwarding; the Data Layer port 2069 is forwarded as-is).

## Permissions

| Scope | Allows |
|---|---|
| `ctrlx-core-connector.web.all.r` "View Core Connector" | tiles, widget, browsing and reading the Data Layer of every core |
| `ctrlx-core-connector.web.all.rw` "Manage Core Connector" | adding/removing connections, settings, **writing Data Layer values and adding/deleting KVD nodes** |

Writes and KVD changes run with the rights of the **app** on this device and of each **connection's
user** on the other cores - not with the rights of the person in the browser. Give "Manage Core
Connector" only to people who may change these devices; use connection users with just the rights needed.

## Configuration (app data)

`settings.json` in the app data (`Settings → Manage app data → ctrlx-core-connector/`), saved and
loaded with ctrlX configurations:

```json
{
  "settingsVersion": 1,
  "general": { "logLevel": "INFO", "pollIntervalMs": 2000 },
  "browser": { "refreshMs": 1000 },
  "cores": [
    { "name": "line-1", "host": "192.168.1.7", "user": "boschrexroth", "password": "...",
      "sslPort": 443, "skipCertificateCheck": true }
  ]
}
```

* Add cores with **+ Add core** (needs "Manage Core Connector"), remove them on their tile. Hand edits of
  the file are picked up automatically; invalid content is rejected and logged (the previous settings stay active).
* `name`: letters, digits, `-` (max. 24) - it becomes the remote `cm-<name>` (case kept; the dialog refuses
  a name that differs from another one only in case).
* `host` + `sslPort`: in the dialog one field, `192.168.1.1` (port 443) or `192.168.1.1:8443`.
* **Passwords are stored in plain text in settings.json** (as required for the remote
  connection) and therefore also in every saved configuration. They are never returned by the web
  API and never published in the Data Layer (`settings/active` and `cores/summary` are masked).
  The Data Layer itself shows remote addresses as `tcp://***:***@...`.

## Moving from Core Monitor

Core Connector is a new snap name, so it is installed next to Core Monitor. On its first start (no
`ctrlx-core-connector/settings.json` yet) it **imports** `ctrlx-core-monitor/settings.json` from the active
configuration (connections incl. passwords, poll interval, log level) and takes over the `cm-*` remotes.
Then **uninstall Core Monitor**: while both run, each one deletes remotes the other one added. The uninstall
leaves `ctrlx-core-monitor/settings.json` (passwords in plain text) in the active configuration: once Core
Connector shows its cores, delete that folder in Settings → Manage app data (gotchas #85).

## Data Layer nodes of this app (`ctrlx-core-connector/…`, all read-only)

| Node | Type | Content |
|---|---|---|
| `status/version`, `status/state`, `status/heartbeat` | string / string / uint64 | app status |
| `settings/active` | string | active settings as JSON, passwords masked |
| `cores/configured`, `cores/connected` | uint64 | number of cores incl. this device / of those delivering data |
| `cores/summary` | string | JSON list: name, host, state, operatingState, hostname, osVersion, cpuPercent, memUsedPercent |

## Web API (`/ctrlx-core-connector/api/v1`, token required)

| Route | |
|---|---|
| `GET /cores` | this device (`_local`) + configured cores with status, no passwords |
| `POST /cores`, `DELETE /cores/<name>` | add (`host` may be `host:port`; 400 with field errors, 409 duplicate name in any case) / remove a connection |
| `GET /cores/<id>/children?path=P` | child nodes: node class, operations, type, has children, simple values |
| `GET /cores/<id>/node?path=P`, `GET .../value?path=P` | metadata + value / value only |
| `POST /cores/<id>/values` `{"paths": [...]}` | values of tree rows (read permission) |
| `PUT /cores/<id>/node?path=P` `{"value": ...}` | write (numbers may be strings: int64/uint64 exactly) |
| `GET /cores/<id>/kvd` | KVD running? addresses defined in its nodes.json |
| `POST /cores/<id>/kvd/nodes`, `DELETE /cores/<id>/kvd/nodes?address=A` | add / delete a KVD node |

Data Layer errors come back as `{"title": "Data Layer: <RESULT>"}` (404 `INVALID_ADDRESS`, 403
`PERMISSION_DENIED`, 400 type/value problems, 503 Data Layer not connected). Plus the template routes
(`/status`, `/settings`, `/values`, app-data save/load).

## Develop

```bash
python3 -m unittest discover -s tests -t .     # tests, any PC (fake Data Layer in tests/fake_adapter.py)
./run-local.sh                                  # http://127.0.0.1:8080/ctrlx-core-connector/ (no Data Layer)
../../tools/check.sh .
python3 ../../tools/ui_check.py . [--device]    # start page, settings drawer, add dialog, widget, help + tutorial, browser
./build-snap-amd64.sh && ../../tools/ctrlx.py install ./*_amd64.snap --service
```

Careful with `run-local.sh` against a device's Data Layer: the app manages that device's `cm-*` remotes
from its local settings (an empty list deletes them).

| Path | What |
|---|---|
| `src/app/monitor.py` | remote naming/address, reconcile of `devices/remotes/cm-*`, session recovery, status polling (this device too) |
| `src/app/browser.py` | Data Layer browser: children (bulk), node details, writes |
| `src/app/kvd.py`, `src/app/rest_client.py` | KVD nodes.json entries, local file / remote WebDAV, REST login |
| `src/app/dltypes.py` | Data Layer type names, value conversion for writes and initial values |
| `src/app/datalayer_service.py` | provider glue + `ClientAdapter` (read, browse, metadata, bulk, JSON read/write, remotes) |
| `src/app/migrate.py` | one-time import of Core Monitor's settings |
| `src/app/web_server.py` | routes |
| `www/` | `index.html`/`app.js` tiles + settings drawer, `browse.html`/`browse.js` browser, `widgets.js` home screen widget, `common.js` token/API helpers, help menu + tutorial (each page passes its steps) |
| `tests/` | unit tests; `ui_scenario.py` for `tools/ui_check.py` |
