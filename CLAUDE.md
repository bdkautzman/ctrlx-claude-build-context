# ctrlX OS app development kit

Python snaps for **ctrlX OS 4.6** (Bosch Rexroth ctrlX CORE / COREvirtual), tested on real hardware where
possible ("Test devices" below). Every app has a web UI behind the ctrlX reverse proxy, Data Layer nodes, and a settings
JSON in ctrlX app data (saved and loaded with configurations).

The user prefers more work up front for fewer problems later: verify on the device, test, record what you
learn in `docs/ctrlx/gotchas.md`, and bring generic fixes from an app back into the template/tools/docs.
Why things are the way they are: `docs/ctrlx/decisions.md` - read it before changing an architectural choice.
Current state of the kit and open items: `README.md` → "Status"; apps: `apps/README.md`.

## Pinned baseline (never change without the human)
- base `core24`, snapcraft 8.x, ctrlX AUTOMATION SDK 4.6.0, `ctrlx-datalayer<=3.6` + `ctrlx-fbs`
- package-manifest schema v1.7, Python 3.12 in the snap, stdlib-only web server
- Unofficial, unlicensed apps: no ctrlX license check (`licensing-service`, manifest `licenses`)
- Optional UI tests: Playwright 1.64.1 + its headless Chromium, pypdfium2 5.14.0 for PDF checks (`tools/setup-ui-test.sh`, in `~/.cache`)
- Details: `docs/ctrlx/platform-baseline.md`

## Layout
- `apps/<snap-name>/` – the apps (create with `python3 tools/new_app.py ...`, never by hand-copying)
- `templates/python-webapp/` – reference implementation; keep it working, don't put app code in it
- `tools/` – `check.sh` (tests + compliance), `ctrlx_check.py`, `new_app.py`, `ctrlx.py` (device REST + SSH, devices from `devices.toml`), `build-snap.sh` (called by the apps' build scripts), `make-app.sh` (on demand: amd64 + arm64 snaps → one `.app`), `ui_check.py` (web UI in headless Chromium, screenshots; setup once: `setup-ui-test.sh`), `pdf_check.py` (PDF pages → PNG), `foss_from_prime.py`, `fix-permissions.sh` (after Windows copies)
- `docs/ctrlx/` – curated rules; start at `docs/ctrlx/README.md`; `upstream-sdk-4.6.0/` = verbatim SDK docs
- `reference/` – read-only: Data Layer API source, SDK samples, REST specs, example apps, CORE hardware facts (see `reference/README.md`)

## Definition of done (every change)
1. `tools/check.sh apps/<app>` → `RESULT: OK` (unit tests + 0 errors). Fix warnings or explain them.
2. New behaviour has a unit test (`tests/`, runs without ctrlX; fake Data Layer in `tests/fakes/`).
3. If you learned something non-obvious (build error, device behaviour): append it to `docs/ctrlx/gotchas.md`
   (next number, and a `#N hint` in the topic index at its top).
4. Snap builds (`./build-snap-amd64.sh`, incremental) when snapcraft is available; say so if you could not build.
   Builds you hand over are full builds (`--clean`).
5. UI changed: `python3 tools/ui_check.py apps/<app>` → `UI CHECK: OK`, and **look at the screenshots** it lists;
   keep `tests/ui_scenario.py` in step with the UI. Not set up (`tools/setup-ui-test.sh --check` fails): say so.
6. **Known issues**: every app keeps a working document `apps/<app>/KNOWN-ISSUES.md` (from the template; user's
   request 2026-11-03) - each problem, workaround, caveat and limitation with date, setup, evidence/measurements,
   what was tried and what is open, plus what is not tested on a device yet and the diagnostic commands used.
   Update it with every finding; mark fixed entries instead of deleting them. Generic lessons also go to gotchas.

## Hard rules
- **Interfaces**: never add a plug/slot or open a port without asking. Check
  `docs/ctrlx/reserved-interfaces-and-ports.md`; every interface must also be in `validation/slotplug-description.json`.
- **Custom tools: ask first.** Before you build something the kit, the template and the standard library don't
  already offer, just to solve this one case, stop and ask the user (AskUserQuestion; best while planning, together
  with the other open questions). This covers: your own implementation of a file format, protocol or parser that a
  library normally provides (a PDF writer, a binary protocol reader); a new tool or script in `tools/` or an app's
  `scripts/`, or a new command in an existing tool; a new test or verification harness; a new dev dependency (also
  outside the snap, e.g. in the UI test venv). Say what it is and why it's needed, give the options (existing open-source
  library: license, size, aarch64 wheel / small custom version: rough size / reduce the feature or check it another
  way) and recommend one. Not needed for the code of the feature the user asked for, unit tests, small helpers or
  throwaway scripts in the scratchpad. Checklist: skill `new-ctrlx-app`; why: `docs/ctrlx/decisions.md`.
- **Names**: snap `ctrlx-[<company>-]<app>` ≤ 29 chars - the company part is optional (the apps so far have none;
  the App Guide asks ctrlX World Partners for it before a validation; `company` in the git-ignored `kit.toml`, template
  `kit.example.toml`); manifest file `<snap>.package-manifest.json` with `id` = snap name;
  proxy name `<snap>.web`, url `/<snap>`; scopes `<snap>.web.all.r` (view), `<snap>.web.operate.x` (operate),
  `<snap>.web.all.rw` (configure); Data Layer root `<snap>/`; app-data dir `<snap>/`; widget tag `<snap>-widget`.
  Never keep identifiers from samples (`sdk-py-*`, `rexroth-python-webserver`).
- **Web**: Unix socket only (`$SNAP_DATA/package-run/<snap>/web.sock`); all data endpoints under `/<snap>/api/` (restricted
  in the manifest) with `auth.require(headers, <role>)`; static files hold no data; relative URLs; no CDNs.
- **Roles** (user's request 2026-11-07): view < operate < configure. Every route and every UI control belongs to the
  lowest role that may use it: show = view, run the app (write runtime values, start/stop/trigger) = operate, change
  its setup (settings, uploads of configuration files) = configure. Settings are configure-only. Ask the user when an
  action's role is not obvious. Details: `docs/ctrlx/web-ui-and-reverse-proxy.md` "Roles".
- **Shared UI parts**: `www/kit.js` + `www/kit.css` (API calls, roles, settings drawer, help menu + tutorial,
  downloads/uploads) stay as they come from the template; app code goes into `app.js` / `style.css`. A feature
  several apps need goes into the template (kit.js, `web_server.py` helpers), not into each app.
- **Settings**: only through `SettingsStore` (validated, atomic, app data). Structure in `defaults/settings.json`,
  limits in `src/app/settings_rules.py`. Backward compatible forever (old configurations get loaded).
- **Flash**: no log files, no cyclic disk writes (also not on Data Layer writes). Log to stdout via `logging`.
- **RAM: < 75 MB per app** (App Guide 6.2) stays the target. When an app will likely need more (image processing,
  large libraries, big buffers), say so with an estimate and ask the user before building (AskUserQuestion, with
  ways to stay below it); an accepted exception goes into the app README and `docs/ctrlx/decisions.md`.
- **Data Layer**: look up signatures in `reference/ctrlxdatalayer-api-3.6.0/` – never guess. Callbacks call `cb` exactly once.
  Nodes are declared in `src/app/datalayer_nodes.py::build_node_specs`. A PLC starts actions through `PlcCommand`
  (Execute / Busy / Done / Error, user's choice 2026-11-07) - no app-specific trigger schemes.
- **Secrets**: no credentials in snap code (`ipc://` needs none). Device credentials only in the git-ignored
  `devices.toml` (template `devices.example.toml`) or env vars for tools - never in committed files.
- **Versions**: bump `version` (snap/snapcraft.yaml + src/pyproject.toml) and CHANGELOG for every build you hand over.
- **Files**: LF line endings, `wrapper/run.sh` executable. Don't edit `reference/` or `docs/ctrlx/upstream-*`.
- **Test devices** (user's request 2026-11-04): test amd64 builds on a real amd64 CORE (X5, X7) when `devices.toml`
  has one - its performance figures hold in real use, the COREvirtual's don't. Use the COREvirtual when no real amd64 CORE is
  available (none in `devices.toml`, or not reachable), or for checks that need SSH when the real CORE has no `ssh_user` or doesn't allow SSH; everything else still
  runs on the real CORE. Test arm64 builds on a real arm64 CORE (X2, X3). Name the device with every result.
  During active app development, installing the app and `--service` on these devices need no asking, whatever else is
  installed there. Details: `docs/ctrlx/build-deploy-test.md` §3.
- Don't run `snap install`/`snapcraft` with sudo tricks, don't touch other snaps, don't change device settings
  (e.g. `--allow-unknown`) unless the human asked.

## Commands
```bash
python3 tools/new_app.py --name <app> --title "<Title>"   # prefix, author, url from kit.toml (else --author --url)
tools/check.sh apps/<app> [--release] [--pre-build] [--snap file.snap]
cd apps/<app> && ./run-local.sh                # http://127.0.0.1:8080/<snap>/  (no ctrlX needed)
python3 tools/ui_check.py apps/<app> [--narrow] [--role reader|operator|writer] [--device [NAME]]   # headless Chromium + screenshots (~1-2 min)
python3 tools/pdf_check.py <file.pdf> [--text]  # a generated PDF: PDFium opens it, pages -> PNG to look at
cd apps/<app> && ./build-snap-amd64.sh         # / ./build-snap-arm64.sh; incremental, --clean for hand-over builds
tools/ctrlx.py devices                         # test devices from devices.toml: name, arch, address, SSH user
tools/ctrlx.py [--device NAME] install apps/<app>/<snap>_<ver>_<arch>.snap --service   # SERVICE -> install -> previous state
tools/make-app.sh apps/<app> [--build] [--sign <oem-cert> <key>]   # optional, on request: one .app for amd64 + arm64
tools/ctrlx.py [--device NAME] dl-read <snap>/status/heartbeat | appdata <snap>/settings.json | logs --snap <snap> | state
tools/ctrlx.py [--device NAME] api GET /<snap>/api/v1/status [--data JSON] [--out file]   # the app's own API on the device
tools/ctrlx.py [--device NAME] ssh 'sudo snap logs -n 50 <snap>'
tools/ctrlx.py [--device NAME] dl-read ctrlx-system-analyzer/apps/summary   # CPU/RAM of every app (build-deploy-test.md §5)
python3 -m unittest discover -s tools/tests    # self-tests of the tools and of the gotchas index
```

## Where to look
| Task | Read first |
|---|---|
| snapcraft.yaml | `docs/ctrlx/snapcraft-rules.md` |
| manifest / menus / scopes | `docs/ctrlx/package-manifest.md` |
| web routes, auth | `docs/ctrlx/web-ui-and-reverse-proxy.md` |
| Data Layer | `docs/ctrlx/datalayer-python.md` |
| settings / app data | `docs/ctrlx/app-data-settings.md` |
| build, deploy, debug | `docs/ctrlx/build-deploy-test.md`, then `docs/ctrlx/gotchas.md`: grep the error text or use its topic index - not the whole file |
| CPU / memory of an app on a device | `docs/ctrlx/build-deploy-test.md` §5 (System Analyzer) |
| roles, shared UI parts (`kit.js`), uploads/downloads, widget | `docs/ctrlx/web-ui-and-reverse-proxy.md` |
| hardware of a device: CPU, RAM, flash, ports, USB, SD | `reference/ctrlx-core-hardware/README.md` |
| release | `docs/ctrlx/release-and-validation.md` |
| an architectural choice (why X, not Y) | `docs/ctrlx/decisions.md` |
