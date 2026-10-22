# Core Monitor (`ctrlx-core-monitor`)

ctrlX OS 4.6 app (snap, `core24`, Python) that shows the status of **other ctrlX COREs** on the
network: host name, IP address, ctrlX OS version, architecture, uptime, CPU and memory utilisation.

## How it works

* Connections use the ctrlX OS feature **Data Layer remote connection**: for every configured core
  the app creates `devices/remotes/cm-<name>` in the Data Layer of *this* device with the address
  `tcp://<user>:<password>@<ip>?sslport=<port>&NoCheckCertificate=true`. ctrlX OS then mounts the
  whole Data Layer of the other core below `devices/remotes/cm-<name>/`, and the app reads:

  | Shown | Node below `devices/remotes/cm-<name>/` |
  |---|---|
  | Host name, architecture, uptime | `system/info/hostname`, `architecture`, `uptime` |
  | ctrlX OS version | `system/info/ctrlxOsVersion` (4.x); ctrlX OS 3.6 has no such node → major.minor of `system/apps/installed/snap_rexroth-deviceadmin` → `release.version` |
  | CPU, memory | `framework/metrics/system/cpu-utilisation-percent`, `memused-percent`, `memused-mb`, `memtotal-mb` |

* The app only creates and deletes remotes named `cm-*`. Remotes you create by hand
  (Settings → Data Layer → `devices/remotes`) are never touched.
* `NoCheckCertificate=true` ("Skip certificate check", default on) is needed for cores with the
  default self-signed certificate - without it the connection fails with `DL_SEC_UNAUTHORIZED`.
* No extra plugs, slots or ports: the app only uses the local Data Layer (`ipc://`).
* Works with ctrlX OS 4.x and 3.6 targets (3.6: version taken from the Device Admin app).
* To watch a **COREvirtual** from a device on the LAN, use the Windows host's IP and HTTPS port
  8443 (ctrlX WORKS port forwarding; the Data Layer port 2069 is forwarded as-is).

## Configuration (app data)

`settings.json` in the app data (`Settings → Manage app data → ctrlx-core-monitor/`), saved and
loaded with ctrlX configurations:

```json
{
  "settingsVersion": 1,
  "general": { "logLevel": "INFO", "pollIntervalMs": 2000 },
  "cores": [
    { "name": "line-1", "host": "192.168.1.7", "user": "boschrexroth", "password": "...",
      "sslPort": 443, "skipCertificateCheck": true }
  ]
}
```

* Add cores with **+ Add core** in the web UI (needs `ctrlx-core-monitor.web.all.rw`), remove them
  on their tile. Hand edits of the file are picked up automatically; invalid content is rejected
  and logged (the previous settings stay active).
* `name`: lowercase letters, digits, `-` (max. 24) - it becomes the remote `cm-<name>`.
* **Passwords are stored in plain text in settings.json** (as required for the remote
  connection) and therefore also in every saved configuration. They are never returned by the web
  API and never published in the Data Layer (`settings/active` and `cores/summary` are masked).
  The Data Layer itself shows remote addresses as `tcp://***:***@...`. Use a dedicated user with
  read-only rights on the monitored cores.

## Data Layer nodes of this app (`ctrlx-core-monitor/…`, all read-only)

| Node | Type | Content |
|---|---|---|
| `status/version`, `status/state`, `status/heartbeat` | string / string / uint64 | app status |
| `settings/active` | string | active settings as JSON, passwords masked |
| `cores/configured`, `cores/connected` | uint64 | number of configured / delivering cores |
| `cores/summary` | string | JSON list: name, host, state, hostname, osVersion, cpuPercent, memUsedPercent |

## Web API (`/ctrlx-core-monitor/api/v1`, token required)

`GET /cores` (status of all cores, no passwords) · `POST /cores` (add; 400 with field errors,
409 for a duplicate name) · `DELETE /cores/<name>` · plus the template routes (`/status`,
`/settings`, `/values`, app-data save/load).

## Develop

```bash
python3 -m unittest discover -s tests -t .     # tests, any PC (fake Data Layer client)
./run-local.sh                                  # http://127.0.0.1:8080/ctrlx-core-monitor/ (no Data Layer)
# against the real Data Layer of the default test device (creates cm-* remotes there!):
(eval "$(../../tools/ctrlx.py env)"; PYTHONPATH=<site-packages with ctrlxdatalayer> ./run-local.sh)
../../tools/check.sh .
./build-snap-amd64.sh && ../../tools/ctrlx.py install ./*_amd64.snap --service
```

| Path | What |
|---|---|
| `app/monitor.py` | remote naming/address, reconcile of `devices/remotes/cm-*`, polling, status |
| `app/datalayer_service.py` | provider glue + `ClientAdapter` (read / create remote / remove) |
| `app/settings_rules.py` | rules incl. the `cores` item rules (`items`, `pattern`, `secret`) |
| `app/web_server.py` | routes incl. `/cores` |
| `www/` | UI: core tiles, + dialog, generic settings form |
| `tests/test_monitor.py` | monitor logic against a fake Data Layer client |
