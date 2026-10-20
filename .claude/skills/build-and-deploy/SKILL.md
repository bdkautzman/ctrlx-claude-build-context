---
name: build-and-deploy
description: Build the snap(s) of a ctrlX app and install it on the ctrlX COREvirtual (or a CORE) for testing, then smoke-test it. Use when the user wants to build, deploy, install or try an app on a device.
argument-hint: "[apps/<name>] [amd64|arm64]"
---

# Build and deploy

Read `docs/ctrlx/build-deploy-test.md` (and `docs/ctrlx/gotchas.md` when anything fails).

1. `tools/check.sh <app> --pre-build` → must be OK (also checks the apt repo for `ctrlx-datalayer` and snapcraft).
   If ENV-* errors: explain the one-time setup steps to the user; don't try to install system packages without asking.
2. Bump the version if this build will be handed over (snapcraft.yaml + pyproject.toml + CHANGELOG).
3. `cd <app> && ./build-snap-amd64.sh` (COREvirtual) and/or `./build-snap-arm64.sh` (hardware).
   On failure: read the full snapcraft output, fix the cause, record new knowledge in `docs/ctrlx/gotchas.md`.
4. `python3 tools/ctrlx_check.py <app> --snap <file>.snap` and `python3 tools/foss_from_prime.py <app> --write`.
5. Deploy only if the user agreed and `CTRLX_HOST` is set (COREvirtual from the build VM: `10.0.2.2:8443`):
   `tools/ctrlx.py install <file>.snap`. Never pass `--allow-unknown` unless the user said so.
6. Smoke test: `tools/ctrlx.py packages`, `tools/ctrlx.py dl-read <snap>/status/state` (expect "running"),
   `tools/ctrlx.py dl-read <snap>/status/heartbeat` twice (increasing), `tools/ctrlx.py appdata <snap>/settings.json`,
   `tools/ctrlx.py logs --search <snap> --limit 30` (no errors). Then ask the user to click through the UI
   (sidebar entry, settings save, permissions) - list exactly what to check (build-deploy-test.md §4).
