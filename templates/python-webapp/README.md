# Template App (`ctrlx-acme-template`)

ctrlX OS 4.6 app (snap, `core24`) written in Python:

* **Web UI** served on a Unix socket behind the ctrlX reverse proxy, opened from the ctrlX
  sidebar / overview (new tab), permissions via ctrlX scopes. No Settings-page tile: the ctrlX
  Settings page can't open a new tab (docs/ctrlx/package-manifest.md).
* **Data Layer provider** with nodes under `ctrlx-acme-template/…`.
* **Settings** as `settings.json` in the ctrlX **app data** (Solutions active configuration),
  saved/loaded with configurations and editable in the web UI or "Manage app data".

## Layout

| Path | What |
|---|---|
| `snap/snapcraft.yaml` | snap definition (name, version, plugs/slots, parts `app` + `files`) |
| `src/pyproject.toml`, `src/app/` | the Python package - the only folder the snap's Python part builds (fast incremental builds) |
| `wrapper/run.sh` | start script of the daemon (`bin/run.sh` in the snap) |
| `configs/package-assets/<snap>.package-manifest.json` | ctrlX integration: proxy, menus, scopes, app-data commands |
| `configs/package-assets/<snap>.fossinfo.json` | open source components shipped in the snap |
| `defaults/settings.json` | default settings = structure + types of `settings.json` |
| `src/app/settings_rules.py` | value rules for settings (enum/min/max/labels) |
| `src/app/datalayer_nodes.py` | **the nodes this app provides** (`build_node_specs`) |
| `src/app/datalayer_service.py` | ctrlxdatalayer provider glue (reconnects automatically) |
| `src/app/web_server.py` | HTTP routes (static UI + `/api/v1/...`) |
| `src/app/settings_store.py` | load/validate/persist/watch settings in app data |
| `src/app/appdata_commands.py` | Solutions save/load phases |
| `www/` | web UI (plain HTML/JS/CSS, no CDN) |
| `validation/` | slot/plug, unix socket, port descriptions (Bosch app validation) |
| `tests/` | unit tests incl. a fake `ctrlxdatalayer` |

## Develop

```bash
python3 -m unittest discover -s tests -t .     # tests, any PC
./run-local.sh                                  # http://127.0.0.1:8080/ctrlx-acme-template/
../../tools/check.sh .                          # ctrlX compliance checks
./build-snap-amd64.sh                           # snap for ctrlX COREvirtual (incremental)
./build-snap-arm64.sh                           # snap for ctrlX CORE hardware
./build-snap-amd64.sh --clean                   # full clean build (hand-over / release)
../../tools/ctrlx.py install ./*_amd64.snap --service   # install on the COREvirtual (needs SERVICE state)
```

## On the device

* Open: sidebar → **Template App** (opens a new tab with the token in the URL).
* Settings file: `Settings → Manage app data → ctrlx-acme-template/settings.json`.
* Data Layer: `Settings → Data Layer → ctrlx-acme-template/`.
* Logs: Diagnostics → Logbook, filter on the app (`snap.ctrlx-acme-template.service`).
