# Template App (`ctrlx-acme-template`)

ctrlX OS 4.6 app (snap, `core24`) written in Python:

* **Web UI** served on a Unix socket behind the ctrlX reverse proxy, opened from the ctrlX
  sidebar / overview (new tab). No Settings-page tile: the ctrlX Settings page can't open a new tab
  (docs/ctrlx/package-manifest.md). Settings in a drawer (help texts as info tooltips, page colors under Styling),
  help menu with a tutorial and an About dialog (version, author, license, AI note), light/dark mode per browser
  (top bar button), pages as tabs (Overview, Data Layer), switches for true/false.
* **Three roles** (ctrlX scopes, Settings → Users & Permissions): *View* (`ctrlx-acme-template.web.all.r`: status
  and values), *Operate* (`.web.operate.x`: also write the runtime values), *Configure* (`.web.all.rw`: also settings).
* **Home screen widget** "Template App" (state and values, "Open" button) - add it from the home screen's widget list.
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
| `LICENSE` | MIT (new apps: `tools/new_app.py` writes the year and author; `--license` for another) |
| `KNOWN-ISSUES.md` | working document: problems, workarounds, caveats, what is not tested on a device, diagnostics (keep current) |
| `defaults/settings.json` | default settings = structure + types of `settings.json` |
| `src/app/settings_rules.py` | value rules for settings (enum/min/max/labels) |
| `src/app/datalayer_nodes.py` | **the nodes this app provides** (`build_node_specs`) |
| `src/app/datalayer_service.py` | ctrlxdatalayer provider glue (reconnects automatically); `on_client` for a client |
| `src/app/datalayer_client.py` | `DataLayerClient`: read / write / browse / metadata of other nodes (PLC variables) |
| `src/app/web_server.py` | HTTP routes (static UI + `/api/v1/...`) with their roles; helpers for downloads and uploads |
| `src/app/auth.py` | roles view / operate / configure from the token's scopes |
| `src/app/settings_store.py` | load/validate/persist/watch settings in app data |
| `src/app/appdata_commands.py` | Solutions save/load phases |
| `www/` | web UI (plain HTML/JS/CSS, no CDN): `index.html` + `app.js` + `style.css` = this app's page; `kit.js` + `kit.css` = the kit's shared parts (leave them as they are); `widgets.js` + `widget.svg` = home screen widget |
| `validation/` | slot/plug, unix socket, port descriptions (Bosch app validation) |
| `tests/` | unit tests incl. a fake `ctrlxdatalayer`; `ui_scenario.py` for `tools/ui_check.py` |

## Develop

```bash
python3 -m unittest discover -s tests -t .     # tests, any PC
./run-local.sh                                  # http://127.0.0.1:8080/ctrlx-acme-template/
#   there: Alt+click an element -> note in .dev/feedback.json; skill /ui-feedback fixes the notes (dev only)
APP_DEV_HTTP_HOST=0.0.0.0 ./run-local.sh        # the same, reachable through the VM's port forwarding
python3 ../../tools/ui_check.py . --role operator   # the page in headless Chromium as an operator (reader, writer)
../../tools/check.sh .                          # ctrlX compliance checks
./build-snap-amd64.sh                           # snap for amd64: CORE X5/X7, COREvirtual (incremental)
./build-snap-arm64.sh                           # snap for arm64: CORE X2/X3
./build-snap-amd64.sh --clean                   # full clean build (hand-over / release)
../../tools/ctrlx.py install ./*_amd64.snap --service   # install on the default test device (needs SERVICE state)
```

## On the device

* Open: sidebar → **Template App** (opens a new tab with the token in the URL).
* Settings file: `Settings → Manage app data → ctrlx-acme-template/settings.json`.
* Data Layer: `Settings → Data Layer → ctrlx-acme-template/`.
* Logs: Diagnostics → Logbook, filter on the app (`snap.ctrlx-acme-template.service`).
