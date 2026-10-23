# Decisions (and why)

Architectural choices of this kit and the reasons behind them. The rules that follow from them are in
`CLAUDE.md`; this file keeps the *why*, so a later session doesn't undo a deliberate choice.

* **Change an entry only with the user**, and record the reason and the date. Add new kit-wide decisions
  here (app-specific choices belong in the app's README).
* Found by experience (build errors, device behaviour)? That goes to `gotchas.md`; link it from here
  when it led to a decision.

Context: the kit was designed in a claude.ai conversation (Oct 2026) without a device, then verified
and extended in Claude Code on the ctrlX App Build Environment VM with a COREvirtual and a real CORE.
`reference/example-apps/` served as inspiration (ctrlx-cam-builder is the user's own repo).

| Decision | Reason |
|---|---|
| base `core24`, SDK 4.6.0, snapcraft 8.x | matches ctrlX OS 4.x; all SDK 4.6 samples use core24 |
| Web server = Python stdlib `http.server` on a Unix socket | no extra FOSS deps; socket-only is the App Guide recommendation |
| API under `/<snap>/api` listed as `restricted`; JWT scopes read without signature check | the reverse proxy validates tokens on restricted paths |
| Token passed via menu link `?token=${bearertoken}`, stored in sessionStorage | documented ctrlX pattern (SDK webserver sample) |
| Settings: `defaults/settings.json` (structure/types) + `src/app/settings_rules.py` (limits) | one generic validator + auto-generated settings form for every app |
| `copyOnLoad: true`, `writeProtected: false`, file watched by mtime | users can also edit settings.json in "Manage app data" |
| Data Layer writes are runtime-only, never persisted | PLCs may write cyclically → flash wear |
| Logging to stdout with `<N>` syslog prefixes | journald levels without bundling cysystemd |
| Menu target `_blank` (not `embedded`) | Bosch recommends `_blank` for 3rd-party UIs |
| Package-manifest schema v1.7 | released with ctrlX OS 4.4, superset of the v1.6 the samples use |
| `boschrexroth/ctrlx-actions` not used | last updated 2022, not suitable for core24 |
| No CI workflow (2026-10-21) | build locally for now (amd64 + arm64 in the build VM); the untested GitHub Actions template was removed |
| No ctrlX licensing (2026-10-21) | unofficial, unlicensed apps for now; licensing snippet and docs removed |
| No `menus.settings` entry (2026-10-21) | the ctrlX Settings page can't open `_blank` tiles; settings live in the app UI (gotchas #34) |
| Python package in `src/`, one `files` dump part, incremental builds (2026-10-22, template 0.1.4) | the build VM runs emulated (TCG): full builds take ~20 min, every snapcraft part costs 30-60 s per step; only Python changes should rebuild the venv (gotchas #47) |
| `.app` packages only on request (2026-10-22) | normal builds and tests use `.snap` files; one `.app` for both architectures costs both snaps' upload time (gotchas #48, #49) |
| UI tests in headless Chromium via Playwright, pinned, optional (2026-10-23) | Claude can look at screenshots and test on the device through the reverse proxy; QuickJS + DOM stubs (tried first) found script errors but no layout problems and needed stubs per app. Cost: one `sudo apt` line, ~1 min per run in the emulated VM, so not in every `check.sh` (gotchas #51, #52) |
| Generic fixes found while building an app go back into the template/tools/docs (since 2026-10-21) | the kit gets better with every app; each app's CHANGELOG / `apps/README.md` notes what was brought back |
