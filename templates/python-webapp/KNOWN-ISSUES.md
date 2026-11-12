# Known issues and caveats - ctrlx-acme-template

Working document (kit rule, CLAUDE.md "Definition of done"): everything that does not work as expected, works only
with a workaround, is a caveat for users or maintainers, or was not tested yet - with the evidence (device, versions,
measurements, error texts), what was tried and what is still open, so a later analysis can start from here.

* Add an entry as soon as you find something; date it (YYYY-MM-DD) and name the device/setup.
* Fixed or obsolete entries stay: mark them (e.g. "fixed in 0.1.2") instead of deleting them.
* Generic lessons (ctrlX OS, snapcraft, the kit) also go to `docs/ctrlx/gotchas.md` - reference their numbers here.
* Keep "Not tested yet" honest: list what was only tested locally / in unit tests, not on a device.
* "Diagnostics": the commands and snippets that produced the findings, so they can be repeated.

## Test setup

<!-- devices, firmware/OS versions, external hardware, library versions the findings below refer to -->

## Overview

| # | Issue | Status | Workaround / fix |
|---|---|---|---|
| - | none known yet | - | - |

<!-- Status: open / worked around / fixed in <version> / by design / caveat / accepted (by whom, date) -->

## Details

<!-- One section per issue: symptom (exact error text), evidence and measurements, what was tried (table of
     attempts and results), workaround, open questions / next steps for the analysis. -->

## Not tested yet

* Everything only checked locally (unit tests, `run-local.sh`, `tools/ui_check.py` without `--device`).
* 0.5.0 (UI changes of 2026-11-09/10) runs on the COREvirtual only (2026-11-10, CHANGELOG 0.5.0): not on a real CORE
  (X3 / X7 not reachable that day), no arm64 build yet. Saving Styling colors was tested locally only.
* 0.6.0 (2026-11-12, kit hooks from the apps' move onto kit.js): **arm64 on the real CORE X3**: app data of 2026-11-05
  kept, Data Layer and log clean, 33.2 MB; roles through the API with kit-view / kit-operate / kit-configure (settings and
  settings/meta 403/403/200, `PUT /values/values/setpoint` 403/200/200 - runtime only); `ui_check --device x3` as admin
  and the three test users OK. amd64 built, not installed (the COREvirtual had stopped).
* Info tooltips, Styling colors, light/dark mode, switches and tabs (2026-11-09): headless Chromium only (`tools/ui_check.py`), not
  through the ctrlX reverse proxy, not on a touch panel (tap on the "i" icon) and not in Firefox/Safari.
  `color-mix()` needs Chrome 111 / Firefox 113 / Safari 16.2 or newer - older browsers show the page without colors
  for the derived parts (the AMR Fleet app has used it since 0.2.1).

## Diagnostics

<!-- commands / snippets used: tools/ctrlx.py calls, SSH commands, scripts run in the app's sandbox
     (`echo <base64> | base64 -d | sudo snap run --shell <snap>.service -c 'python3 -'`), counters, logs -->
