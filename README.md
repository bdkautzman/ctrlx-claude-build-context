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
| `.claude/settings.json` | permissions (checks/tests allowed, device installs ask, reference read-only) + a hook that re-checks an app after edits to its snapcraft/manifest/validation files |
| `docs/ctrlx/` | curated rules (start at `docs/ctrlx/README.md`), `decisions.md` (what was decided and why), `gotchas.md` (grows over time), verbatim SDK 4.6.0 docs in `upstream-sdk-4.6.0/` |
| `templates/python-webapp/` | known-good template app (web UI + Data Layer provider + app-data settings, 32 unit tests, fake Data Layer, UI scenario for `tools/ui_check.py`) |
| `tools/` | `check.sh` / `ctrlx_check.py` (ctrlX compliance checker, 40+ rules, schema validation), `new_app.py`, `ctrlx.py` (install/read nodes/call app APIs/logs/ssh on a test device from `devices.toml`), `ui_check.py` + `setup-ui-test.sh` (web UI in headless Chromium with screenshots, locally or on a device), `pdf_check.py` (generated PDFs → PNG), `foss_from_prime.py` |
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
4. Verify: `tools/check.sh templates/python-webapp --pre-build` → `RESULT: OK`
   and `python3 -m unittest discover -s tools/tests` → OK.
   Optional, so Claude can check web UIs in a real browser and look at screenshots: `tools/setup-ui-test.sh`
   (no root; prints one `sudo apt-get install` line for missing libraries/fonts, ~42 MB), then
   `python3 tools/ui_check.py templates/python-webapp` → `UI CHECK: OK`.
5. First real build: `cd templates/python-webapp && ./build-snap-amd64.sh`, install it on the
   COREvirtual (`tools/ctrlx.py install …`), click through. Whatever breaks → `docs/ctrlx/gotchas.md`.
   This one-time shakedown validates the parts of the kit that could not be tested without a device
   (listed under "Unverified assumptions" in gotchas.md).

## Status (2026-10-25)

Verified on ctrlX OS 4.6 - a ctrlX COREvirtual (amd64) and a real ctrlX CORE (arm64):
* template: build, install (`tools/ctrlx.py install --service`), Data Layer, reverse proxy + scopes,
  app data save/load with configurations, restart and reboot (`docs/ctrlx/gotchas.md` #21–#38)
* one `.app` with both architectures installs on both devices (#49)
* UI checks in headless Chromium (`tools/ui_check.py`), locally and on the COREvirtual (#52)
* apps built with the kit, their versions and what they verified: `apps/README.md`
* template `templates/python-webapp` 0.1.7 (settings validator hook, `minItems`/`itemPattern`)
* generated PDFs are checked like UI screenshots: `tools/pdf_check.py` (PDFium renders the pages to PNG)
* network access of a strict snap with the `network` plug, mapped for `ctrlx-network-scanner` (gotchas #61-#65)

Open items:
* template candidates from `ctrlx-amr-fleet`: settings rule `format: "color"` with color fields in the
  generated settings form (`minItems` / `itemPattern` are in the template since 0.1.7); a "settings only for users who may change them" option
  (that app's `/view-config` pattern, gotchas #55); a help menu (`?`) with a step-by-step tutorial that
  marks each control (ring overlay + card, `tests/ui_scenario.py` checks it)
* still unverified parts of the kit: gotchas.md → "Unverified assumptions"
* `ctrlx-network-scanner`: the whitelist/blacklist semantics (rating only) and the three default profiles are
  first versions - adjust after real use; IPv4 only; no ICMP/SYN scans possible in a strict snap (gotchas #61)
* `ctrlx-amr-fleet`: map units (mm) and the fleet JSON format were chosen without a real PLC/fleet
  manager - confirm when one is connected; performance (its README): with 200 AMRs the browser spends
  most time drawing AMRs + labels and rasterizing (0.3.1 fixed the list's share)

History: git log, the CHANGELOGs, `docs/ctrlx/gotchas.md`. Decisions and their reasons: `docs/ctrlx/decisions.md`.

## Typical use

```
claude
> /new-ctrlx-app an app that reads 4 analog values from a Modbus TCP device and shows them in a trend
> /build-and-deploy apps/ctrlx-acme-modbus amd64
> use the ctrlx-reviewer agent on apps/ctrlx-acme-modbus
```

## Maintaining the kit

* After each project: keep `docs/ctrlx/gotchas.md` current; turn repeat problems into checker rules
  (`tools/ctrlx_check.py` + `tools/tests/`; new tools and rules only after asking - CLAUDE.md "Custom tools: ask first"); update "Status" above and `apps/README.md`; new kit-wide
  decisions go to `docs/ctrlx/decisions.md`.
* New ctrlX OS / SDK version: `docs/ctrlx/sources.md` → "Refreshing".
* Snapshot date of all sources: 2026-10-15 (SDK tag 4.6.0).
