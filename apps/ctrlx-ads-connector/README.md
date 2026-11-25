# ADS Connector (`ctrlx-ads-connector`)

ctrlX OS 4.6 app (snap, `core24`, Python) for **Beckhoff TwinCAT controllers** (TwinCAT 3; TwinCAT 2 untested) over
**ADS**, with an interface like Core Connector's:

* **Controller tiles** (several controllers): name, IP address, AMS Net ID and port, device name, TwinCAT version and
  OS, **TwinCAT state** (RUN / CONFIG / ...) and **PLC state** (RUN / STOP), number of PLC symbols, Data Layer nodes
  with a value, poll interval and the last cycle's duration, the **route** the controller needs for this device,
  and what is wrong (red line). A click on a tile opens the PLC browser.
* **PLC browser** (`browse.html?controller=<name>`): namespaces (MAIN, the GVLs, ...) → variables → members of
  structs and function blocks → array elements, with values right in the tree (read while visible), the PLC type
  and marks (DL = in the Data Layer, RO = constant). Details of the selected variable: live value (structs as JSON),
  PLC type, Data Layer type, size, index group / offset, array bounds, enum values, comment, attributes.
  **Write** values (numbers, TRUE/FALSE, enums, texts, arrays as JSON). A path field (`MAIN.fbAxis.fPos`,
  `GVL.aValues[3]`, `GVL.aGrid[1,2]`) opens the tree on the way.
* **Data Layer nodes of PLC variables**: "Add to Data Layer" in the browser makes a variable a node
  `ctrlx-ads-connector/plc/<controller>/<address>` in this device's Data Layer, **with the type of the PLC variable**
  (REAL = float32, INT = int16, STRING = string, ARRAY OF REAL = array-of-float32, ...); a struct, function block,
  array of structs or a whole namespace adds one node per value below it. Read-only or **writable** (a Data Layer
  write goes straight into the PLC). A list of all nodes with live values on the start page (tab "Data Layer nodes").
* **Add route**: asks a controller to add the route for this device (TwinCAT's own UDP add-route request, with the
  user and password of an administrator of the controller; not stored). **Not tested yet** - see KNOWN-ISSUES 3.
* **Home screen widget** "TwinCAT controllers": every controller with the TwinCAT and PLC state, an **Open** button.
* Settings in a drawer (log level, ADS timeout, local AMS Net ID, the browser's read interval, page colors), help
  menu with a tutorial over both pages and About, light/dark mode per browser.

**Known issues, caveats, measurements and what is not tested yet: [KNOWN-ISSUES.md](KNOWN-ISSUES.md)** (working
document - keep it current).

## How it works

* **ADS** with [pyads](https://github.com/stlehmann/pyads) 3.6.0 (MIT) and Beckhoff's ADS library (`AdsLib.so`, MIT,
  part of the pyads wheels for x86_64 and aarch64). AdsLib routes in-process: one TCP connection to port **48898**
  per controller, client only - the app opens no port. `src/app/ads_link.py` keeps one AMS port + route per
  controller, tries a plain TCP connect first (2 s; AdsLib's own connect can block for minutes) and closes and
  reopens the link after a lost connection (1, 2, 5, 10 s). Gotchas #116.
* **AMS Net ID of the controller**: from the settings, or TwinCAT's **UDP server info** (port 48899, read-only: Net
  ID, host name, TwinCAT and OS version; "Find" in the dialog) at every connect.
* **This device's AMS Net ID** (one for the whole app): settings `ads.localNetId`, or AdsLib's default = this device's
  IP toward the first controller + `.1.1`. **Every controller needs a route** for that Net ID with this device's IP
  toward it (the tile shows both). Add it in TwinCAT (XAE: Router → Edit Routes → Add; or on the controller) or with
  "Add route". Controllers on several networks of the CORE: all routes name the same Net ID, each with the CORE's IP
  in that network.
* **Symbols**: the app uploads the controller's symbol and data type tables (index groups 0xF00F / 0xF00B / 0xF00E)
  when the browser or the nodes need them and parses them itself (`src/app/ads_types.py`, layout checked against a
  TwinCAT 3.1.4024 CX: `tests/fixtures/`). It keeps the raw tables (bytes + an index, parsed on demand: large projects
  stay small in RAM; at most 24 MB per controller) and loads them again when the **symbol version** changes (download,
  online change).
* **Values**: every controller's Data Layer nodes are read **every `pollIntervalMs`** (100 ms by default) with ADS **sum
  reads** (up to 500 variables per request, by index group / offset) - the symbol version is the first item of the
  request, so values read after an online change are dropped and the symbols loaded again. Only values whose bytes
  changed are decoded. A Data Layer read answers the last value (no waiting for the PLC);
  `COMMUNICATION_ERROR` when the controller is not connected, `RESOURCE_UNAVAILABLE` when the PLC or the variable is
  missing.
* **Writes** (browser: role Operate; Data Layer: the writer's Data Layer permission) go through a **symbol handle**
  (get handle by name, write, release - safe across online changes) and wait for the PLC's answer (each request up to
  `ads.timeoutMs`); errors come back to the writer (`INVALID_VALUE`, `PERMISSION_DENIED` for a PLC constant, `TIMEOUT`,
  `COMMUNICATION_ERROR`). A Data Layer node keeps the type it was added with: when the PLC variable's type changes
  (online change), the node reports that instead of converting (kit rule: no conversions).
* **Types** (Data Layer = the PLC's own representation, nothing converted):

  | TwinCAT | Data Layer | | TwinCAT | Data Layer |
  |---|---|---|---|---|
  | BOOL | bool8 | | REAL / LREAL | float32 / float64 |
  | SINT / USINT, BYTE | int8 / uint8 | | STRING(n), WSTRING(n) | string (n characters at most) |
  | INT / UINT, WORD | int16 / uint16 | | TIME, TOD (ms), DATE, DT (s) | uint32 (unit ms / s) |
  | DINT / UDINT, DWORD | int32 / uint32 | | LTIME, LDT ... (ns) | uint64 (unit ns) |
  | LINT / ULINT, LWORD | int64 / uint64 | | ENUM | its base type (INT: int16) |
  | ARRAY [..] OF one of these (all dimensions, flattened) | array-of-&lt;type&gt; (exactly all elements) | | STRUCT, FB | one node per member |

  Not as nodes (shown in the browser): pointers, references, interfaces, bit-addressed symbols; bit members of structs
  are read-only. STRING is Windows-1252 (UTF-8 with `{attribute 'TcEncoding' := 'UTF-8'}`).

## Read speed (measured, 0.1.1)

All nodes of a controller are read in one ADS sum read per poll interval (`pollIntervalMs`, 20 ms ... 60 s). Against
a CX (TwinCAT 3.1.4024, PLC task 10 ms), 2026-11-25 - details in `KNOWN-ISSUES.md`:

| | 3 nodes, 100 ms | 3 nodes, 20 ms | 98 nodes, 20 ms |
|---|---|---|---|
| X7 (amd64): read time / app CPU | 0.45 ms / 0.2 % | 0.4 ms / 0.6 % | 0.65 ms / 1.3 % |
| X3 (arm64): read time / app CPU | 1.8 ms / 1.2 % | 1.5 ms / 4.7 % | 3.6 ms / 12 % (0.3 % of the reads late) |

20 ms holds on both COREs. If a read takes longer than the interval, the next one starts at once (no queue, an
"overrun" in the controller's `stats`); the rate then drops and the app's CPU rises - values stay correct. Polling
samples: a value that changes every PLC cycle (10 ms) is seen every second time at 20 ms.
The controller's real period and read time: `stats` in `GET /api/v1/controllers`, `periodMsAvg` / `cycleMsAvg` in
the Data Layer node `controllers/summary`.

## Permissions

| Scope | Allows |
|---|---|
| `ctrlx-ads-connector.web.all.r` "View ADS Connector" | tiles, widget, browsing and reading PLC variables, the node list |
| `ctrlx-ads-connector.web.operate.x` "Operate ADS Connector" | the above, plus **writing PLC variables** in the browser |
| `ctrlx-ads-connector.web.all.rw` "Configure ADS Connector" | the above, plus controllers (add, edit, remove), **Add route**, **Data Layer nodes** (add, remove), settings |

Writes into the PLC run with the app's ADS route - not with rights of the person in the browser: give "Operate" and
"Configure" only to people who may change the PLC's values. Writable Data Layer nodes can be written by every
Data Layer client with write permission on `ctrlx-ads-connector/plc/...` (ctrlX Settings → Users & Permissions).

## Configuration (app data)

`settings.json` in the app data (`Settings → Manage app data → ctrlx-ads-connector/`), saved and loaded with ctrlX
configurations:

```json
{
  "settingsVersion": 1,
  "general": { "logLevel": "INFO", "heartbeatIntervalMs": 1000 },
  "ads": { "localNetId": "", "timeoutMs": 1000 },
  "browser": { "refreshMs": 1000 },
  "controllers": [
    { "name": "Line-1-CX", "host": "192.168.1.187", "amsNetId": "", "amsPort": 851, "pollIntervalMs": 100,
      "enabled": true }
  ],
  "nodes": [
    { "controller": "Line-1-CX", "symbol": "POU_Counter.iCounter", "address": "POU_Counter/iCounter",
      "type": "int16", "writable": false, "description": "", "unit": "" }
  ]
}
```

* Controllers: **+ Add controller** / Edit / Remove (Configure). `name`: letters, digits, `_`, `-` (max. 24) - also the
  Data Layer folder `plc/<name>/`. One entry per AMS Net ID (one PLC runtime per entry; a second runtime of the same
  controller: KNOWN-ISSUES 5). `amsPort`: 851 (first TwinCAT 3 PLC), 852, ...; 801 TwinCAT 2.
* Nodes: added and removed in the browser / the node list. Up to 2000 nodes, 32 controllers. Hand edits of the file
  are picked up automatically; invalid content is rejected and logged (the previous settings stay active).
* No passwords are stored (ADS needs none; "Add route" uses the password once).

## Data Layer nodes of this app (`ctrlx-ads-connector/…`)

| Node | Type | Content |
|---|---|---|
| `plc/<controller>/<address>` | the PLC variable's | the PLC variables added in the browser (read-only or writable) |
| `status/version`, `status/state`, `status/heartbeat` | string / string / uint64 | app status |
| `settings/active` | string (JSON) | active settings |
| `controllers/configured`, `controllers/connected` | uint32 | controllers / of those connected with a PLC |
| `controllers/summary` | string (JSON) | name, host, netId, state, systemStateName, plcStateName, nodes, nodesOk, error, pollIntervalMs, periodMsAvg (real poll period, last 10 s), cycleMsAvg (time one read takes) |
| `nodes/configured`, `nodes/bad` | uint32 | Data Layer nodes of PLC variables / of those without a value |

## Web API (`/ctrlx-ads-connector/api/v1`, token required)

| Route | Role | |
|---|---|---|
| `GET /controllers` | view | status of every controller |
| `POST /controllers`, `PUT /controllers/<name>`, `DELETE /controllers/<name>` | configure | add / change / remove (with its nodes) |
| `POST /discover` `{"host"}` | configure | UDP server info: Net ID, host name, TwinCAT version, this device's IP toward it |
| `POST /controllers/<name>/route` `{"user", "password", "routeName"}` | configure | add a route on the controller |
| `GET /controllers/<name>/children?path=P` | view | namespaces / variables / members / elements with values |
| `GET /controllers/<name>/symbol?path=P`, `GET .../value?path=P` | view | details + value / value only |
| `POST /controllers/<name>/values` `{"paths": [...]}` | view | values of tree rows |
| `PUT /controllers/<name>/symbol?path=P` `{"value": ...}` | operate | write (numbers may be text: 64-bit exactly) |
| `GET /nodes[?controller=C]` | view | Data Layer nodes with values |
| `POST /nodes/plan`, `POST /nodes` `{"controller", "path", "address"?, "writable", "description"?}` | configure | preview / add |
| `POST /nodes/delete` `{"controller", "addresses": [...]}` | configure | remove nodes |

503 = controller or PLC not available, 502 = the PLC refused (ADS error), 400 = a value / type problem. 64-bit integers
beyond 2^53 come as text (JavaScript numbers would round them). Plus the template routes (`/status`, `/settings`,
`/values`, app-data save/load).

## Develop

```bash
python3 -m pip install --no-deps --only-binary=:all: --require-hashes --target .dev/pylib -r wheels/requirements.txt
python3 -m unittest discover -s tests -t .     # tests, any PC, no pyads needed (fake PLC: src/app/dev_fake_plc.py)
./run-local.sh                                  # http://127.0.0.1:8080/ctrlx-ads-connector/ (no Data Layer)
#   a controller with the host "fake-plc" is the demo PLC (local runs only); Alt+click -> notes (/ui-feedback)
APP_DEV_HTTP_HOST=0.0.0.0 ./run-local.sh        # the same, reachable through the VM's port forwarding
../../tools/check.sh .
PYTHONPATH=$PWD/.dev/pylib python3 ../../tools/ui_check.py .   # start page, dialogs, nodes, widget, browser, tutorial
./build-snap-amd64.sh && ../../tools/ctrlx.py --device x7 install ./*_amd64.snap --service
```

Real controllers from the build VM go out with the Windows host's IP (NAT): they may answer without a route and
the session can take over traffic of TwinCAT XAE on the host (gotchas #115). Test ADS from a CORE.

| Path | What |
|---|---|
| `src/app/ads_types.py` | symbol + data type tables, TwinCAT paths, values ↔ bytes, sum reads, UDP server info (pure Python) |
| `src/app/ads_link.py` | pyads transport: route + AMS port per controller, reads/writes/sum reads, discovery, add route |
| `src/app/controllers.py` | one thread per controller: connect, states, symbol upload, polling, browser functions, writes |
| `src/app/plc_nodes.py` | the Data Layer nodes of PLC variables (browse / metadata / read / write for the wildcard node) |
| `src/app/datalayer_service.py` | provider glue + the `plc/**` wildcard node (gotchas #88) |
| `src/app/dev_fake_plc.py` | dev only, primed out of the snap: fake TwinCAT PLC (tests, UI check, run-local) |
| `src/app/web_server.py` | routes |
| `www/` | `index.html`/`app.js` tiles, node list, dialogs; `browse.html`/`browse.js` PLC browser; `widgets.js`; `common.js` |
| `wheels/requirements.txt` | pyads 3.6.0 with the sha256 of the x86_64 and aarch64 wheels (snap part `wheels`) |
| `tests/` | unit tests (`fixtures/`: raw tables of a real CX); `ui_scenario.py` for `tools/ui_check.py` |

## License

MIT (`LICENSE`). Open source components shipped in the snap (ctrlx-datalayer, ctrlx-fbs, flatbuffers, pyads,
Beckhoff ADS library): `configs/package-assets/ctrlx-ads-connector.fossinfo.json`.
