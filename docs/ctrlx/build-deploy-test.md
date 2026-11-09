# Build, deploy, test

## 0. One-time: App Build Environment (Ubuntu VM from ctrlX WORKS)

Recommended by Bosch Rexroth: ctrlX WORKS → App Build Environment (QEMU VM, user/password
`boschrexroth`, SSH on host port 10022). Run **Claude Code inside this VM** (or WSL/Ubuntu 24.04
with the same setup) so it can run snapcraft itself. Then (SDK README, `upstream-sdk-4.6.0/install-sources-from-github.md`):

```bash
wget https://raw.githubusercontent.com/boschrexroth/ctrlx-automation-sdk/main/scripts/install-required-packages.sh
wget https://raw.githubusercontent.com/boschrexroth/ctrlx-automation-sdk/main/scripts/install-snapcraft.sh
wget https://raw.githubusercontent.com/boschrexroth/ctrlx-automation-sdk/main/scripts/clone-install-sdk.sh
chmod +x *.sh && ./install-required-packages.sh && ./install-snapcraft.sh
./clone-install-sdk.sh          # choose tag 4.6.0 when asked
# the clone script merges the release zip (contains the ctrlx-datalayer .deb) into the clone:
find ~/ctrlx-automation-sdk -name 'ctrlx-datalayer*.deb'
ctrlx-automation-sdk/scripts/install-ctrlx-datalayer.sh <folder containing that .deb>
sudo apt-get install -y python3-yaml python3-jsonschema squashfs-tools   # for tools/check.sh
```

Verify: `apt-cache policy ctrlx-datalayer` shows a candidate; `snap list snapcraft` shows 8.x;
`python3 tools/ctrlx_check.py templates/python-webapp --pre-build` has no `ENV-*` errors.
Put the kit on the VM with git (preferred) or scp; keep LF line endings (see gotchas).

Optional, for UI checks in a real browser (`tools/ui_check.py`, §1): `tools/setup-ui-test.sh`. It installs,
without root, a venv with a pinned Playwright (`~/.cache/ctrlx-kit/uivenv`) and its headless Chromium
(`~/.cache/ms-playwright`, ~280 MB) and prints one `sudo apt-get install ...` line for the shared libraries
and fonts the VM lacks (~25 packages, ~42 MB). Run it again afterwards: it ends with `READY`.
Everything else in the kit works without it.

## 1. Inner loop (no snap, seconds)

```bash
cd apps/<name>
python3 -m unittest discover -s tests -t .        # unit tests (fake Data Layer)
./run-local.sh                                     # http://127.0.0.1:8080/<name>/ , auth off
(eval "$(../../tools/ctrlx.py env)"; ./run-local.sh)   # + real Data Layer of the default device
```
For the last one install the Python deps in a venv (`python3 -m venv .venv && . .venv/bin/activate
&& pip install ctrlx-datalayer'<=3.6' ctrlx-fbs`) – the native lib comes from the .deb.

**UI check in a real browser** (after UI changes, before hand-over; setup: §0):
```bash
python3 ../../tools/ui_check.py .              # starts the app from a temp copy, headless Chromium, screenshots
python3 ../../tools/ui_check.py . --narrow     # + a 390 px wide (phone/HMI) screenshot
python3 ../../tools/ui_check.py . --role reader   # as a user with only <snap>.web.all.r (auth on)
../../tools/check.sh . --ui                    # unit tests + ctrlX checks + UI check
```
It opens the page, waits for `<body data-ui-ready="true">` (set by `www/app.js` with `Kit.ready()` once the data is shown),
runs the app's `tests/ui_scenario.py` (clicks, named screenshots; template: Edit -> Set on a value, valid
and invalid settings) and fails on JavaScript/console errors and HTTP errors. Screenshots land in
`<app>/.ui-check/` (git-ignored) - **look at them**: layout problems don't fail the run. About 1 min per
run in the emulated build VM (browser start ~12 s), so it is not part of every `check.sh`.

**UI feedback from the page** (dev only, template since 2026-11-09): `./run-local.sh` adds the kit's overlay
`tools/ui-feedback/overlay.js` to the page. **Alt+click** an element (or "Feedback" bottom left → "Pick an element"),
type a note, Ctrl+Enter: the note goes with the element's selector, structure path, text, size, window size and
light/dark into `<app>/.dev/feedback.json` (git-ignored); numbered pins mark the open notes. Then ask Claude Code for
`/ui-feedback` (skill): it fixes each open note, marks it `done` / `question` / `wontfix` with a `resolution`, and runs
`ui_check.py` - reload the page to see the result. On only when `run-local.sh` sets `APP_DEV_FEEDBACK` and auth is off
(`APP_DEV_NO_AUTH=1`): never in the snap, never in `ui_check.py` screenshots; `APP_DEV_FEEDBACK=0 ./run-local.sh` turns
it off. From a browser outside the VM (port forwarding of the App Build Environment):
`APP_DEV_HTTP_HOST=0.0.0.0 ./run-local.sh`, then `http://localhost:<forwarded port>/<name>/`. QEMU reads its port
forwards only when the VM starts: a forward added while it runs needs a full power-off and start (a reboot inside the
VM is not enough). Apps made before the template had it need `src/app/dev_feedback.py`, the hooks in `web_server.py`
and `config.py`, and the `run-local.sh` lines copied from the template.

## 2. Check and build

```bash
../../tools/check.sh .              # tests + ctrlX static checks; must be clean
./build-snap-amd64.sh               # CORE X5/X7, COREvirtual -> <name>_<ver>_amd64.snap  (incremental)
./build-snap-arm64.sh               # CORE X2/X3 -> <name>_<ver>_arm64.snap (cross build)
./build-snap-amd64.sh --clean       # full clean build: builds you hand over / release
python3 ../../tools/ctrlx_check.py . --snap <name>_<ver>_amd64.snap   # inspect the built snap
python3 ../../tools/foss_from_prime.py . --write   # sync fossinfo versions with prime/
```
Cross-build warnings "Unable to determine library dependencies" are expected (SDK quick start).
Builds are **incremental by default** (`tools/build-snap.sh`): only parts whose sources changed are
rebuilt. A full clean build runs automatically when the target architecture changed since the last
build in that folder; force it with `--clean` for handed-over/release builds and after updating the
SDK or the `ctrlx-datalayer` .deb. In an App Build Environment without hardware acceleration a full
build takes ~20 min (gotchas #47).

Optional, on demand (hand-over, "one file for both architectures"): package both snaps of the
current version into one `.app` - not part of the normal build:
```bash
../../tools/make-app.sh .           # <name>_<ver>_{amd64,arm64}.snap -> <name>_<ver>.app (unsigned)
../../tools/make-app.sh . --build   # first build missing/outdated architectures (--clean, run in the background)
```
Details: `release-and-validation.md` → "`.app` packages".

## 3. Deploy to a test device

Which device (user's request 2026-11-04, why: `decisions.md`):

| Snap | Test on | COREvirtual instead when |
|---|---|---|
| amd64 | a real amd64 CORE (X5, X7), if `devices.toml` has one | there is no real amd64 CORE, or a check needs SSH and the real one has no `ssh_user` (`ssh -` in `tools/ctrlx.py devices`) or doesn't allow SSH |
| arm64 | a real arm64 CORE (X2, X3) | - (the COREvirtual is amd64 only) |

* Performance figures (CPU, RAM, timing, network) count only from real hardware. The COREvirtual is a VM with a
  different CPU; a figure from it says little about a real CORE. Figures don't carry over between models either: the
  X5 and X7 have the same ports but different CPU, RAM and flash (`reference/ctrlx-core-hardware/README.md`).
* If SSH is the only reason for the COREvirtual, split the test: everything REST and the UI can reach stays on the
  real CORE (install, Data Layer, the app's API, `logs`, `ui_check.py --device`), and only the SSH checks go to the
  COREvirtual (scripts in the app's sandbox, kernel/AppArmor logs, `/proc`).
* Name the device with every result (CHANGELOG, `KNOWN-ISSUES.md`, reports to the user).
* A pass on a real amd64 CORE doesn't show that the app starts on a COREvirtual: its CPU level is lower (gotchas #98).

COREvirtual setup: ctrlX WORKS → create/start a COREvirtual (port forwarding), log in once in the browser
(https://localhost:8443 from Windows), then Settings → Apps → allow installation from
unknown sources (or `--allow-unknown` below).

Test devices: `cp devices.example.toml devices.toml` (kit root, git-ignored) and fill in address,
API and SSH credentials per device, each under its own `[name]`. `default` is the device a command
without `--device` talks to: make that the real amd64 CORE if there is one, otherwise `virtual`
(COREvirtual from the build VM, amd64). Pick another with `--device NAME` or `CTRLX_DEVICE=NAME`.
`tools/ctrlx.py` doesn't fall back by itself: if the default device can't be reached it stops with an
error. Say so and pass `--device virtual`.

```bash
../../tools/ctrlx.py devices                                    # list (no passwords)
../../tools/ctrlx.py install <name>_<ver>_amd64.snap --service   # SERVICE -> install -> back
../../tools/ctrlx.py --device x7 install <name>_<ver>_amd64.snap --service     # real amd64 CORE
../../tools/ctrlx.py --device x3 install <name>_<ver>_arm64.snap --service   # real arm64 CORE
../../tools/ctrlx.py --device x3 install <name>_<ver>.app --service   # .app: same file on every device
../../tools/ctrlx.py ssh 'sudo snap logs -n 50 <name>'           # SSH with the devices.toml login
../../tools/ctrlx.py packages                                   # name, version, health
../../tools/ctrlx.py dl-read <name>/status/heartbeat
../../tools/ctrlx.py appdata <name>/settings.json
../../tools/ctrlx.py logs --snap <name>
../../tools/ctrlx.py state [SERVICE|OPERATING]                  # show / set the operating state
```
ctrlX OS 4.6 installs apps **only in SERVICE state** (HTTP 409 otherwise); `--service` switches the
device (stops PLC/motion), installs and restores the previous state. During active app development,
installs and `--service` on the devices in `devices.toml` need no asking, also when other apps run there
(user's request 2026-11-04). Loading a configuration
(Solutions) needs SERVICE on a device with Motion, which refuses it otherwise (gotchas #24); an X3 without Motion loaded in OPERATING (#108). `tools/ctrlx.py` never uses `http(s)_proxy` (the build VM sets one
that can't reach the device); for curl add `--noproxy '*'`.
Manual alternative: web UI → Settings → Apps → Install from file.
The COREvirtual's license is the 4-hour engineering license; it shuts down after 4 h.

## 4. Smoke test on the device (every release)

1. App appears in Apps with the right version, state "running"; no errors in Logbook.
2. Sidebar/overview entry opens the UI in a new tab; values update; settings save.
   `python3 ../../tools/ui_check.py . --device [NAME]` does the opening part: logs in with the
   devices.toml credentials, opens `/<name>/?token=...` through the reverse proxy like the menu link and
   takes screenshots (scenarios change nothing on a device unless written to). The user still clicks
   through once per release (sidebar entry, permissions).
3. Roles: a user with only `.web.all.r` (view) sees no Edit and no Settings button, one with only
   `.web.operate.x` (operate) edits values but has no Settings; without scopes the API returns 403. Locally:
   `ui_check.py . --role reader|operator|writer`. On the device: `ui_check.py . --device NAME --role reader`
   (and `operator`, `writer`) logs in as the device's **test user** of that role and gives it the app's scope first.
   Test users (user's request 2026-11-08): `kit-view`, `kit-operate`, `kit-configure`, one password for the three in
   devices.toml `[<device>.test_users]`. `tools/ctrlx.py --device NAME test-users [--snap <name>]` shows them;
   missing ones: **ask the user**, then `test-users create` (they stay on the device for later apps; `test-users
   delete` removes them). `test-users grant <name>` gives each its role's scope of an installed app. On the X3 they
   exist since 2026-11-08.
4. Settings → Data Layer: `<name>/…` nodes readable; writable ones accept/reject correctly.
5. Manage app data: `<name>/settings.json` exists; save a configuration, change a setting,
   load the configuration → the old value is back (load phases in the log). Over REST: `app-data-settings.md`.
6. Reboot the device → app comes back, settings persisted.
7. Before release: the arm64 snap on a real arm64 CORE (different CPU, slower) and the amd64 snap on a real amd64
   CORE if there is one (§3).

## 5. Measuring CPU and memory of an app on a device

Use the **System Analyzer** (`apps/ctrlx-system-analyzer`) as the instrument - no measuring script per app
(the user's choice 2026-11-07, instead of a new `tools/ctrlx.py` command). Install it once on the test device
(it stays there; it costs ~2.5 % of one app core on an X3 and ~60 MB RAM itself). It samples every app every 5 s
from `/proc` (CPU in % of ONE core like `top`, RSS/anon/PSS in MB, threads) and keeps 2 h at 5 s, 24 h per minute,
7 days per 10 min (min/avg/max). Read it over REST from the build VM:

```bash
# now: one JSON array with every app (key "snap:<name>": cpu, anonMb, rssMb, pssMb, threads, processes)
tools/ctrlx.py --device x3 dl-read ctrlx-system-analyzer/apps/summary
# a test run: rows per sample (raw tier, < 2 h) or per minute, min/avg/max of every metric, all apps -> filter
t1=$(tools/ctrlx.py --device x3 api GET /ctrlx-system-analyzer/api/v1/status | python3 -c 'import json,sys;print(int(json.load(sys.stdin)["serverTime"]))')
tools/ctrlx.py --device x3 api GET "/ctrlx-system-analyzer/api/v1/export?level=apps&format=csv&from=$((t1-900))&to=$t1" --out apps.csv
grep -E '^epoch|snap:<name>,' apps.csv     # columns: cpu_pct_min/avg/max, anon_mb_*, rss_mb_*, pss_mb_*, threads_*
# memory that grows (leak check over the analysis window, every minute): [] = nothing grows
tools/ctrlx.py --device x3 dl-read ctrlx-system-analyzer/analysis/growing
```

* Take `from`/`to` from the device's clock (`serverTime` of its status): a CORE's clock can be days off (gotchas #75).
* Mark the start of a test in the charts and the CSV: `tools/ctrlx.py --device x3 dl-write ctrlx-system-analyzer/events/marker '"test started"' --type string`
  (writes to the analyzer only, runtime value).
* `level=processes` / `threads` gives the same per process / thread; the web UI (`/ctrlx-system-analyzer/`) shows
  the charts. An app's own key is `snap:<snap name>`.
* Baseline (X3, ctrlX OS 4.6, 2026-11-07): the template app idles at 32.9 MB RSS / 14.6 MB anon, 0.2 % CPU.
* Short CPU peaks below 5 s or a process' exact memory map are beyond it: over SSH `systemctl show -p MainPID --value
  snap.<snap>.service.service`, then `/proc/<pid>/stat` fields 14+15 and `/proc/<pid>/status` (gotchas #58).

## Debugging

* Logs: Diagnostics → Logbook (filter on the snap) or `tools/ctrlx.py logs --snap <name>`. With SSH
  (needs a user with SSH rights): `tools/ctrlx.py [--device NAME] ssh` (COREvirtual from the build VM:
  port 8022 - port 10022 is the build VM itself): `sudo snap logs -f <name>`, `snap services <name>`,
  `snap connections <name>`, `sudo snap run --shell <name>.service` then inspect `$SNAP_COMMON/solutions`.
* Python remote debugging with debugpy: `upstream-sdk-4.6.0/remote-debug-python.md` and the SDK
  sample `reference/sdk-4.6.0-samples-python/datalayer.remote.debug` (debug port only on demand!).
