# Handoff from the claude.ai conversation that built this kit (Oct 2026)

Read this once at the start of the first Claude Code session in this folder. It records what
was decided and what is still open. Rules for daily work are in `CLAUDE.md`.

## What the user wants
- Python snaps for **ctrlX OS 4.6** (Bosch Rexroth ctrlX CORE). Testing on **ctrlX COREvirtual** first.
- Almost every app has three parts:
  1. a **web UI** integrated into the ctrlX UI (sidebar/overview/settings menu, reverse proxy, ctrlX permissions)
  2. **Data Layer nodes** to interface with the rest of the system
  3. a **settings JSON** stored in ctrlX **app data** (Solutions), so it's saved/loaded with configurations
- The user prefers doing more work up front to get fewer problems later.
- Agent: Claude Code. Existing work for inspiration: `reference/example-apps/` (one of them,
  ctrlx-cam-builder, is the user's own repo).

## Decisions made (and why)
| Decision | Reason |
|---|---|
| base `core24`, SDK 4.6.0, snapcraft 8.x | matches ctrlX OS 4.x; all SDK 4.6 samples use core24 |
| Web server = Python stdlib `http.server` on a Unix socket | no extra FOSS deps; socket-only is the App Guide recommendation |
| API under `/<snap>/api` listed as `restricted`; JWT scopes read without signature check | the reverse proxy validates tokens on restricted paths |
| Token passed via menu link `?token=${bearertoken}`, stored in sessionStorage | documented ctrlX pattern (SDK webserver sample) |
| Settings: `defaults/settings.json` (structure/types) + `app/settings_rules.py` (limits) | one generic validator + auto-generated settings form for every app |
| `copyOnLoad: true`, `writeProtected: false`, file watched by mtime | users can also edit settings.json in "Manage app data" |
| Data Layer writes are runtime-only, never persisted | PLCs may write cyclically → flash wear |
| Logging to stdout with `<N>` syslog prefixes | journald levels without bundling cysystemd |
| Menu target `_blank` (not `embedded`) | Bosch recommends `_blank` for 3rd-party UIs |
| No CI workflow (2026-10-21) | build locally for now (amd64 + arm64 in the build VM); the untested GitHub Actions template was removed |
| No ctrlX licensing (2026-10-21) | unofficial, unlicensed apps for now; licensing snippet and docs removed |
| No `menus.settings` entry (2026-10-21) | the ctrlX Settings page can't open `_blank` tiles; settings live in the app UI |
| Package-manifest schema v1.7 | released with ctrlX OS 4.4, superset of the v1.6 the samples use |
| `boschrexroth/ctrlx-actions` not used | last updated 2022, not suitable for core24 |

## Status
Verified in a cloud sandbox (no snapcraft, no device available there):
- template: 28 unit tests pass, incl. Data Layer glue against a fake `ctrlxdatalayer`
- template runs locally (`./run-local.sh`); UI checked in a headless browser (edit value, save settings)
- `tools/ctrlx_check.py`: 13 self-tests pass; the template is clean; it flags real issues in the SDK samples and example apps
- `tools/ctrlx.py install/dl-read/packages` tested against a mock HTTPS device only

**First Claude Code session (2026-10-20, App Build Environment VM + ctrlX COREvirtual 4.6):**
1. ✅ Build environment was already set up (snapcraft 8.14.5, ctrlx-datalayer 3.5.2 .deb, host builds);
   `tools/fix-permissions.sh` was needed after the Windows transfer; pre-build check OK.
2. ✅ Template snap builds (amd64). 0.1.1 primes only the target architecture's Data Layer lib.
3. ✅ `tools/ctrlx.py install` works; needs SERVICE state → new `--service` flag / `state` command.
4. ✅ Smoke test via REST/SSH: Data Layer nodes (read, write, range/type/read-only rejection),
   reverse proxy (401 without token), scopes (`.r` user → 403 on writes, no scope → 403),
   settings API → app data, Solutions save and full load cycle (needs SERVICE), Logbook levels,
   restart keeps settings and resets runtime values.
   ✅ Browser (user, 2026-10-21): sidebar + overview open the UI with the token; settings save to app
   data; read-only user has no access. Found: dead Settings-page tile (entry removed, 0.1.2) and
   "Set" writing the old Data Layer value back - both fixed in 0.1.2 and re-checked by the user.
5. ✅ fossinfo filled by `tools/foss_from_prime.py --write`.
6. ✅ arm64 build (2026-10-21) on the real CORE (192.168.1.7): install, Data Layer, reverse proxy +
   token checks, settings → app data, restart and reboot all pass. A device-side reverse-proxy
   problem (gotchas #38) needed a reboot of the CORE first.
**Apps built with the kit** (each adds checks/tests/context back into the kit; list: `apps/README.md`):
- `ctrlx-core-monitor` 0.1.1 (2026-10-22): monitors other cores via Data Layer remote connections;
  verified amd64 (COREvirtual) and arm64 (real CORE), targets ctrlX OS 4.6 and 3.6.
  Brought back into the template (0.1.3): settings rules `items`/`pattern`/`secret`, read-only-first
  write errors; docs: `datalayer-python.md` (client, remote connections), gotchas #39–#46.

Findings: `docs/ctrlx/gotchas.md` #21–#46. Devices: `devices.toml` (git-ignored).

## Delivery notes
- The Claude Code config arrived as `_claude/` on Windows (the transfer tool can't write `.claude`).
  Rename it to `.claude`, or use `ctrlx-app-build-context.tar.gz`, which already contains `.claude`
  and keeps Linux permissions.
- `.vscode/` folders of the example apps are only in the tar.gz (not important).
