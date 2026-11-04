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

## Diagnostics

<!-- commands / snippets used: tools/ctrlx.py calls, SSH commands, scripts run in the app's sandbox
     (`echo <base64> | base64 -d | sudo snap run --shell <snap>.service -c 'python3 -'`), counters, logs -->
