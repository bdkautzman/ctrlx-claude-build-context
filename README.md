# ctrlX app build context kit (for Claude Code)

A curated, version-pinned context package for developing **Python snaps for ctrlX OS 4.6**
with Claude Code. Each app gets a web UI integrated in the ctrlX UI, ctrlX Data Layer nodes,
and a settings JSON in ctrlX app data.

## What's inside

| Path | Purpose |
|---|---|
| `CLAUDE.md` | Always-loaded rules for Claude Code (baseline, hard rules, definition of done, commands) |
| `.claude/skills/` | `/new-ctrlx-app`, `/ctrlx-check`, `/add-datalayer-node`, `/add-app-setting`, `/build-and-deploy`, `/ctrlx-release`, `/package-app`, `/record-gotcha` |
| `.claude/agents/ctrlx-reviewer.md` | independent review subagent before hand-over |
| `.claude/settings.json` | permissions (checks, tests, builds, UI checks, read-only device commands and installs allowed - also with `--device NAME`; Data Layer writes ask; reference read-only) + a hook that re-checks an app after edits to its snapcraft/manifest/validation files |
| `docs/ctrlx/` | curated rules (start at `docs/ctrlx/README.md`), `decisions.md` (what was decided and why), `gotchas.md` (grows over time; read through its topic index), verbatim SDK 4.6.0 docs in `upstream-sdk-4.6.0/` |
| `templates/python-webapp/` | known-good template app (web UI with three roles, settings drawer, help/tutorial and home screen widget + Data Layer provider with every PLC type + Data Layer client + app-data settings, dev-only UI feedback overlay on the `run-local.sh` page, 67 unit tests, fake Data Layer, UI scenario for `tools/ui_check.py`) |
| `tools/` | `check.sh` / `ctrlx_check.py` (ctrlX compliance checker, 40+ rules, schema validation), `new_app.py`, `ctrlx.py` (install/read nodes/call app APIs/logs/ssh on a test device from `devices.toml`), `ui_check.py` + `setup-ui-test.sh` (web UI in headless Chromium with screenshots, locally or on a device), `ui-feedback/overlay.js` (dev-only: Alt+click an element on the `run-local.sh` page, leave a note; skill `/ui-feedback` fixes the notes), `pdf_check.py` (generated PDFs → PNG), `foss_from_prime.py` |
| `reference/` | read-only: Python Data Layer API source 3.6.0, SDK 4.6.0 Python samples, ctrlX REST OpenAPI specs, example apps with notes |
| `apps/` | your apps go here |

## Setup (once)

1. **Where Claude Code runs:** inside the ctrlX **App Build Environment** VM (ctrlX WORKS) or another
   Ubuntu 24.04 machine, so it can run snapcraft, the tests and the checker itself. Windows-native
   Claude Code can edit files and run the tests, but cannot build snaps.
2. Put this folder into a **git repository** and clone it into the build environment. Keep LF line
   endings (`.gitattributes` is included; on Windows also `git config --global core.autocrlf input`).
   Windows doesn't keep Linux executable bits: run `bash tools/fix-permissions.sh` once in the
   build environment (it also converts stray CRLF) and commit with the `git add --chmod=+x` it prints.
3. In the build environment follow `docs/ctrlx/build-deploy-test.md` §0 (SDK 4.6.0, snapcraft 8.x,
   ctrlx-datalayer apt repo, `python3-yaml python3-jsonschema squashfs-tools`).
4. Optional: `cp kit.example.toml kit.toml` and put your author, URL and (if you want one) the company part of
   snap names in it - `tools/new_app.py` then needs only `--name <app> --title "..."`.
5. Verify: `tools/check.sh templates/python-webapp --pre-build` → `RESULT: OK`
   and `python3 -m unittest discover -s tools/tests` → OK.
   Optional, so Claude can check web UIs in a real browser and look at screenshots: `tools/setup-ui-test.sh`
   (no root; prints one `sudo apt-get install` line for missing libraries/fonts, ~42 MB), then
   `python3 tools/ui_check.py templates/python-webapp` → `UI CHECK: OK`.
6. First real build: `cd templates/python-webapp && ./build-snap-amd64.sh`, install it on the
   COREvirtual (`tools/ctrlx.py install …`), click through. Whatever breaks → `docs/ctrlx/gotchas.md`.
   This one-time shakedown validates the parts of the kit that could not be tested without a device
   (listed under "Unverified assumptions" in gotchas.md).

## Status (2026-11-07)

Test devices: amd64 tests move to a real amd64 CORE (X7, added 2026-11-04) when one is available (`docs/ctrlx/build-deploy-test.md` §3);
on the X7 only `ctrlx-barcode-reader` is tested so far (0.2.0, its `KNOWN-ISSUES.md` 22 and 24).

Verified on ctrlX OS 4.6 - a ctrlX COREvirtual (amd64) and a real ctrlX CORE (arm64):
* template: build, install (`tools/ctrlx.py install --service`), Data Layer, reverse proxy + scopes,
  app data save/load with configurations, restart and reboot (`docs/ctrlx/gotchas.md` #21–#38)
* one `.app` with both architectures installs on both devices (#49)
* UI checks in headless Chromium (`tools/ui_check.py`), locally and on the COREvirtual (#52)
* UI feedback notes on the `run-local.sh` page (2026-11-09, template and the five apps): overlay tested in headless
  Chromium (Alt+click, pick mode, parent, pins, delete, a table the app re-renders; on each app's page: a note on the
  title and on a topbar button, the app doesn't react); the user left two notes on the template from their own browser
  through the VM's port forward and `/ui-feedback` handled them (2026-11-08 evening CST; test changes, reverted);
  the user checks a new or changed UI there before a snap is built (`docs/ctrlx/build-deploy-test.md` §1)
* apps built with the kit, their versions and what they verified: `apps/README.md`
* template `templates/python-webapp` 0.4.0 (2026-11-08): node types for every PLC variable type (each node takes only
  its own type, no conversions), `DataLayerClient` (from core-connector) - both verified on the X3; test users per role
  (`tools/ctrlx.py test-users`, on the X3) and `ui_check.py --device --role`; RAM 35 MB part by part (gotchas #112).
  0.3.0 (2026-11-07): PLC commands with Execute / Busy / Done / Error (`PlcCommand`),
  `Commands` binding, no ERROR log line while the Data Layer is expected to be missing; 0.2.0 (2026-11-07, from a review of the five apps: what several apps built
  separately is now in the template - roles view / operate / configure, shared UI parts `www/kit.js` + `kit.css`
  with settings drawer and help menu + tutorial, home screen widget, download/upload helpers, JSON nodes; its
  CHANGELOG; roles on the X3 with real ctrlX users since 0.4.0); 0.1.8: first-start retry while app data is not writable, listen backlog 64;
  0.1.7: settings validator hook, `minItems`/`itemPattern`
* CPU/RAM of apps on a device: the System Analyzer is the instrument (`docs/ctrlx/build-deploy-test.md` §5)
* generated PDFs are checked like UI screenshots: `tools/pdf_check.py` (PDFium renders the pages to PNG)
* network access of a strict snap with the `network` plug, mapped for `ctrlx-network-scanner` (gotchas #61-#65)
* `/proc` access of a strict snap with `system-observe` (connected by the ctrlX installer, gotchas #67), mapped for
  `ctrlx-system-analyzer` (cost per sample: #68; real CORE layout, cgroup v2, no PSI, clock: #73-#75)
* Data Layer browsing/writing of other devices through remotes (JSON forms, bulk requests, stuck sessions) and
  Key Value Database nodes via `kvd/nodes.json`, mapped for `ctrlx-core-connector` (gotchas #77-#84);
  uninstalling an app keeps its app data (#85);
  a ctrlX OS 3.6 core without `system/info` host name/uptime (#86);
  a KVD collection's empty value (#87)
* native code in snaps: C-extension wheels and Ubuntu packages for arm64 built on the amd64 VM (parts `wheels` and
  `debs`, gotchas #99), numpy >= 2.4 not importable on the COREvirtual's CPU (#98), a GigE Vision camera through
  Aravis + PyGObject in a strict snap (control channel, stream, packet pacing on the CORE, #93, #100, #101), mapped
  for `ctrlx-barcode-reader`

Open items:
* `ctrlx-barcode-reader`: real codes tested with one Code 128 label only (other symbologies: simulated); software trigger of the STC-MCS500POE does not work with Aravis 0.8.30 (#100); RAM ~100 MB (accepted for this
  app); its fossinfo is generated by a throwaway script - a kit tool for wheel/deb license files would be a new
  tool (ask first)
* template candidate from `ctrlx-amr-fleet` (one app so far): settings rule `format: "color"` with color fields in the
  generated settings form. The apps made before template 0.2.0 keep their own versions of roles (read / read-write),
  drawer, tutorial and widget - not changed (user's decision 2026-11-07)
* still unverified parts of the kit: gotchas.md → "Unverified assumptions"
* `ctrlx-system-analyzer`: leak verdicts so far only from 30-60 min test runs (synthetic leak) - no multi-hour/day
  ranges yet; an SD card as medium not tested (a USB stick was)
* `ctrlx-network-scanner`: the whitelist/blacklist semantics (rating only) and the three default profiles are
  first versions - adjust after real use; IPv4 only; no ICMP/SYN scans possible in a strict snap (gotchas #61)
* `ctrlx-amr-fleet`: map units (mm) and the fleet JSON format were chosen without a real PLC/fleet
  manager - confirm when one is connected; performance (its README): with 200 AMRs the browser spends
  most time drawing AMRs + labels and rasterizing (0.3.1 fixed the list's share); per-value nodes cost
  ~0.24 ms per write on a CORE (gotchas #90) - a PLC write test (instead of REST from the build VM) is open;
  0.5.0 (map drop-down + upload) runs on the COREvirtual only - the 208 x 356 m map is not measured on a real CORE

History: git log, the CHANGELOGs, `docs/ctrlx/gotchas.md`. Decisions and their reasons: `docs/ctrlx/decisions.md`.

## Typical use

```
claude
> /new-ctrlx-app an app that reads 4 analog values from a Modbus TCP device and shows them in a trend
  (Claude starts the dev page; check the UI in your browser, Alt+click elements to leave notes)
> /ui-feedback                      # until the UI is OK - the snap build waits for that
> /build-and-deploy apps/ctrlx-acme-modbus amd64
> use the ctrlx-reviewer agent on apps/ctrlx-acme-modbus
```

## Maintaining the kit

* After each project: keep `docs/ctrlx/gotchas.md` current; turn repeat problems into checker rules
  (`tools/ctrlx_check.py` + `tools/tests/`; new tools and rules only after asking - CLAUDE.md "Custom tools: ask first"); update "Status" above and `apps/README.md`; new kit-wide
  decisions go to `docs/ctrlx/decisions.md`.
* New ctrlX OS / SDK version: `docs/ctrlx/sources.md` → "Refreshing".
* Snapshot date of all sources: 2026-10-15 (SDK tag 4.6.0).
