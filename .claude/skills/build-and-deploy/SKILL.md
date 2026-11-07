---
name: build-and-deploy
description: Build the snap(s) of a ctrlX app and install it on a test device (a real CORE, or the COREvirtual) for testing, then smoke-test it. Use when the user wants to build, deploy, install or try an app on a device.
argument-hint: "[apps/<name>] [amd64|arm64]"
---

# Build and deploy

Read `docs/ctrlx/build-deploy-test.md` (and `docs/ctrlx/gotchas.md` when anything fails).

1. `tools/check.sh <app> --pre-build` → must be OK (also checks the apt repo for `ctrlx-datalayer` and snapcraft).
   If ENV-* errors: explain the one-time setup steps to the user; don't try to install system packages without asking.
2. Bump the version if this build will be handed over (snap/snapcraft.yaml + src/pyproject.toml + CHANGELOG).
3. `cd <app> && ./build-snap-amd64.sh` (CORE X5/X7, COREvirtual) and/or `./build-snap-arm64.sh` (CORE X2/X3). Incremental by default;
   add `--clean` when this build is handed over (step 2) or after an SDK / ctrlx-datalayer update. Run builds in the
   background: a full build takes ~20 min in the App Build Environment (no hardware acceleration).
   On failure: read the full snapcraft output, fix the cause, record new knowledge in `docs/ctrlx/gotchas.md`
   (generic) and in the app's `KNOWN-ISSUES.md` (app-specific: problems, device results, untested parts).
4. `python3 tools/ctrlx_check.py <app> --snap <file>.snap` and `python3 tools/foss_from_prime.py <app> --write`.
5. Deploy: during active app development this needs no asking, whatever else is installed on the device. Devices come from `devices.toml` (`tools/ctrlx.py devices`). Pick one by the Test
   devices rule (CLAUDE.md, build-deploy-test.md §3): amd64 snap → a real amd64 CORE if one is available, else the COREvirtual;
   arm64 snap → a real arm64 CORE (the tool refuses a wrong architecture). If only SSH speaks against the real CORE, test
   there over REST and the UI and run just the SSH checks on the COREvirtual. Name the device with every result.
   `tools/ctrlx.py [--device NAME] install <file>.snap`. ctrlX OS 4.6 installs only in SERVICE state: `--service` switches
   SERVICE → install → previous state (stops PLC/motion meanwhile).
   Never pass `--allow-unknown` unless the user said so.
6. Smoke test: `tools/ctrlx.py packages`, `tools/ctrlx.py dl-read <snap>/status/state` (expect "running"),
   `tools/ctrlx.py dl-read <snap>/status/heartbeat` twice (increasing), `tools/ctrlx.py appdata <snap>/settings.json`,
   `tools/ctrlx.py logs --snap <snap> --limit 30` (no errors), `tools/ctrlx.py ssh 'snap services <snap>'` (if the device allows SSH).
   If `tools/setup-ui-test.sh --check` passes: `python3 tools/ui_check.py <app> --device [NAME]` (opens the UI through
   the reverse proxy with a login token, screenshots - look at them; changes nothing on the device).
   Then ask the user to click through the UI (sidebar entry, settings save, permissions) - list exactly what to
   check (build-deploy-test.md §4).
   CPU / memory of the app: read them from the System Analyzer on the device (`build-deploy-test.md` §5;
   `tools/ctrlx.py --device NAME dl-read ctrlx-system-analyzer/apps/summary`), not with a script of the app's own.
7. Optional, only when the user asks for one file for both architectures (`.app`): the package-app skill
   (`tools/make-app.sh <app>`). Normal builds and tests stay with the `.snap` files.
