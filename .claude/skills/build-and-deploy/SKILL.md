---
name: build-and-deploy
description: Build the snap(s) of a ctrlX app and install it on the ctrlX COREvirtual (or a CORE) for testing, then smoke-test it. Use when the user wants to build, deploy, install or try an app on a device.
argument-hint: "[apps/<name>] [amd64|arm64]"
---

# Build and deploy

Read `docs/ctrlx/build-deploy-test.md` (and `docs/ctrlx/gotchas.md` when anything fails).

1. `tools/check.sh <app> --pre-build` → must be OK (also checks the apt repo for `ctrlx-datalayer` and snapcraft).
   If ENV-* errors: explain the one-time setup steps to the user; don't try to install system packages without asking.
2. Bump the version if this build will be handed over (snap/snapcraft.yaml + src/pyproject.toml + CHANGELOG).
3. `cd <app> && ./build-snap-amd64.sh` (COREvirtual) and/or `./build-snap-arm64.sh` (hardware). Incremental by default;
   add `--clean` when this build is handed over (step 2) or after an SDK / ctrlx-datalayer update. Run builds in the
   background: a full build takes ~20 min in the App Build Environment (no hardware acceleration).
   On failure: read the full snapcraft output, fix the cause, record new knowledge in `docs/ctrlx/gotchas.md`.
4. `python3 tools/ctrlx_check.py <app> --snap <file>.snap` and `python3 tools/foss_from_prime.py <app> --write`.
5. Deploy only if the user agreed. Devices come from `devices.toml` (`tools/ctrlx.py devices`): `virtual`
   (amd64 snap) is the default, `--device core` is real hardware (arm64 snap; the tool refuses a wrong architecture):
   `tools/ctrlx.py [--device core] install <file>.snap`. ctrlX OS 4.6 installs only in SERVICE state: `--service` switches
   SERVICE → install → previous state (stops PLC/motion meanwhile) - ask the user before the first use.
   Never pass `--allow-unknown` unless the user said so.
6. Smoke test: `tools/ctrlx.py packages`, `tools/ctrlx.py dl-read <snap>/status/state` (expect "running"),
   `tools/ctrlx.py dl-read <snap>/status/heartbeat` twice (increasing), `tools/ctrlx.py appdata <snap>/settings.json`,
   `tools/ctrlx.py logs --snap <snap> --limit 30` (no errors), `tools/ctrlx.py ssh 'snap services <snap>'`. Then ask the user to click through the UI
   (sidebar entry, settings save, permissions) - list exactly what to check (build-deploy-test.md §4).
