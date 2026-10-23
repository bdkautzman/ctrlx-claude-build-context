# ctrlX OS app development kit

Python snaps for **ctrlX OS 4.6** (Bosch Rexroth ctrlX CORE / COREvirtual). Every app has a web
UI behind the ctrlX reverse proxy, Data Layer nodes, and a settings JSON in ctrlX app data.

First session in this folder? Read `docs/HANDOFF.md` (decisions so far + open verification tasks).

## Pinned baseline (never change without the human)
- base `core24`, snapcraft 8.x, ctrlX AUTOMATION SDK 4.6.0, `ctrlx-datalayer<=3.6` + `ctrlx-fbs`
- package-manifest schema v1.7, Python 3.12 in the snap, stdlib-only web server
- Unofficial, unlicensed apps: no ctrlX license check (`licensing-service`, manifest `licenses`)
- Optional UI tests: Playwright 1.64.1 + its headless Chromium (`tools/setup-ui-test.sh`, in `~/.cache`)
- Details: `docs/ctrlx/platform-baseline.md`

## Layout
- `apps/<snap-name>/` – the apps (create with `python3 tools/new_app.py ...`, never by hand-copying)
- `templates/python-webapp/` – reference implementation; keep it working, don't put app code in it
- `tools/` – `check.sh` (tests + compliance), `ctrlx_check.py`, `new_app.py`, `ctrlx.py` (device REST + SSH, devices from `devices.toml`), `build-snap.sh` (called by the apps' build scripts), `make-app.sh` (on demand: amd64 + arm64 snaps → one `.app`), `ui_check.py` (web UI in headless Chromium, screenshots; setup once: `setup-ui-test.sh`), `foss_from_prime.py`, `fix-permissions.sh` (after Windows copies)
- `docs/ctrlx/` – curated rules; start at `docs/ctrlx/README.md`; `upstream-sdk-4.6.0/` = verbatim SDK docs
- `reference/` – read-only: Data Layer API source, SDK samples, REST specs, example apps (see `reference/README.md`)

## Definition of done (every change)
1. `tools/check.sh apps/<app>` → `RESULT: OK` (unit tests + 0 errors). Fix warnings or explain them.
2. New behaviour has a unit test (`tests/`, runs without ctrlX; fake Data Layer in `tests/fakes/`).
3. If you learned something non-obvious (build error, device behaviour): append it to `docs/ctrlx/gotchas.md`.
4. Snap builds (`./build-snap-amd64.sh`, incremental) when snapcraft is available; say so if you could not build.
   Builds you hand over are full builds (`--clean`).
5. UI changed: `python3 tools/ui_check.py apps/<app>` → `UI CHECK: OK`, and **look at the screenshots** it lists;
   keep `tests/ui_scenario.py` in step with the UI. Not set up (`tools/setup-ui-test.sh --check` fails): say so.

## Hard rules
- **Interfaces**: never add a plug/slot or open a port without asking. Check
  `docs/ctrlx/reserved-interfaces-and-ports.md`; every interface must also be in `validation/slotplug-description.json`.
- **Names**: snap `ctrlx-<company>-<app>` ≤ 29 chars; manifest file `<snap>.package-manifest.json` with `id` = snap name;
  proxy name `<snap>.web`, url `/<snap>`; scopes `<snap>.web.<what>.<r|rw|x>`; Data Layer root `<snap>/`;
  app-data dir `<snap>/`. Never keep identifiers from samples (`sdk-py-*`, `rexroth-python-webserver`).
- **Web**: Unix socket only (`$SNAP_DATA/package-run/<snap>/web.sock`); all data endpoints under `/<snap>/api/` (restricted
  in the manifest) with `auth.require()`; static files hold no data; relative URLs; no CDNs.
- **Settings**: only through `SettingsStore` (validated, atomic, app data). Structure in `defaults/settings.json`,
  limits in `src/app/settings_rules.py`. Backward compatible forever (old configurations get loaded).
- **Flash**: no log files, no cyclic disk writes (also not on Data Layer writes). Log to stdout via `logging`.
- **Data Layer**: look up signatures in `reference/ctrlxdatalayer-api-3.6.0/` – never guess. Callbacks call `cb` exactly once.
  Nodes are declared in `src/app/datalayer_nodes.py::build_node_specs`.
- **Secrets**: no credentials in snap code (`ipc://` needs none). Device credentials only in the git-ignored
  `devices.toml` (template `devices.example.toml`) or env vars for tools - never in committed files.
- **Versions**: bump `version` (snap/snapcraft.yaml + src/pyproject.toml) and CHANGELOG for every build you hand over.
- **Files**: LF line endings, `wrapper/run.sh` executable. Don't edit `reference/` or `docs/ctrlx/upstream-*`.
- Don't run `snap install`/`snapcraft` with sudo tricks, don't touch other snaps, don't change device settings
  (e.g. `--allow-unknown`) unless the human asked.

## Commands
```bash
python3 tools/new_app.py --name ctrlx-<company>-<app> --title "<Title>" --author "<Company>" --url <url>
tools/check.sh apps/<app> [--release] [--pre-build] [--snap file.snap]
cd apps/<app> && ./run-local.sh                # http://127.0.0.1:8080/<snap>/  (no ctrlX needed)
python3 tools/ui_check.py apps/<app> [--narrow] [--role reader] [--device [NAME]]   # headless Chromium + screenshots (~1-2 min)
cd apps/<app> && ./build-snap-amd64.sh         # / ./build-snap-arm64.sh; incremental, --clean for hand-over builds
tools/ctrlx.py devices                         # test devices from devices.toml: virtual (amd64), core (arm64)
tools/ctrlx.py [--device core] install apps/<app>/<snap>_<ver>_<arch>.snap --service   # --service: ask first
tools/make-app.sh apps/<app> [--build] [--sign <oem-cert> <key>]   # optional, on request: one .app for amd64 + arm64
tools/ctrlx.py [--device core] dl-read <snap>/status/heartbeat | appdata <snap>/settings.json | logs --snap <snap> | state
tools/ctrlx.py [--device core] ssh 'sudo snap logs -n 50 <snap>'
python3 -m unittest discover -s tools/tests    # self-test of the checker
```

## Where to look
| Task | Read first |
|---|---|
| snapcraft.yaml | `docs/ctrlx/snapcraft-rules.md` |
| manifest / menus / scopes | `docs/ctrlx/package-manifest.md` |
| web routes, auth | `docs/ctrlx/web-ui-and-reverse-proxy.md` |
| Data Layer | `docs/ctrlx/datalayer-python.md` |
| settings / app data | `docs/ctrlx/app-data-settings.md` |
| build, deploy, debug | `docs/ctrlx/build-deploy-test.md`, then `docs/ctrlx/gotchas.md` |
| release | `docs/ctrlx/release-and-validation.md` |
