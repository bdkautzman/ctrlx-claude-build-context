# Changelog

Versions follow semantic versioning and must match `version:` in `snap/snapcraft.yaml`.

## Unreleased

## 0.2.0 - 2026-11-25 (clean builds for amd64 + arm64)

- **Data Layer addresses of the PLC variables: `ctrlx-ads-connector/connections/<controller>/<address>`** (was
  `.../plc/...` up to 0.1.1; user's choice). Data Layer clients that read the old addresses need the new ones. The
  settings need no change (node addresses are stored below the folder), so configurations of 0.1.x load as they are.
- Tested on the X7 and the X3: reads and writes (bTest / sTest) at the new addresses, UI check on both; contains
  `ctrlx-fbs` 2.7.0 (0.1.x: 2.5.1; not pinned by the kit, gotchas #118).

UI notes of the user (2026-11-25, feedback overlay):
- PLC browser: no "Add to Data Layer" in the tree's tool bar, no "Go" (Enter) and no copy button at the path field.
  The details of a variable show "Add to Data Layer" only while it isn't a node; once it is, a "Data Layer" section
  with its address in a field + copy button and "Remove from Data Layer" (no "Not in the Data Layer" text). The write
  hint shows only the type.
- PLC browser: a struct, array or namespace whose members are nodes lists them in its Data Layer section (was: "Not
  in the Data Layer" + "Add its N values", which then failed with "address already used").
- Node list: sortable columns (a click on a title: high → low / A → Z, the other way, unsorted) through the
  template's new `Kit.setupSortable` (kit.js / kit.css from the template).
- Settings: section "Browser" (was "PLC browser"); its read interval's help says it's only for the browser page and
  that the Data Layer nodes use each controller's poll interval.

## 0.1.1 - 2026-11-25 (built for amd64 + arm64 for the read-speed tests on X7 and X3)

- Poll statistics per controller in the status (`/api/v1/controllers`, `stats`): cycles, overruns (a cycle longer
  than the poll interval), value changes, read time; real period and cycle time (avg / max) over the last 10 s. The
  Data Layer node `controllers/summary` also has pollIntervalMs, periodMsAvg, cycleMsAvg.
- PLC browser title: the device name only when it differs from the controller name.
- Read speed measured on X7 and X3 (README "Read speed", KNOWN-ISSUES 14): 20 ms holds on both, also with 98 nodes;
  X3 with 98 nodes at 20 ms: 12 % of one core, 0.3 % of the reads late.
- Unit test `test_nodes_are_polled` waits for all its nodes (failed now and then under load: 1 of 10 parallel runs).

## 0.1.0 - 2026-11-24 (built for amd64 + arm64; tested on the X7)

- First version: TwinCAT controllers over ADS (pyads 3.6.0 + Beckhoff AdsLib): controller tiles with TwinCAT / PLC
  state and the route the controller needs, PLC browser (namespaces, variables, struct members, array elements, values,
  details, write), PLC variables as Data Layer nodes `plc/<controller>/<address>` with the PLC variable's type (one
  wildcard provider node, sum-read polling, writes through symbol handles), node list, "Add route" (untested), home
  screen widget "TwinCAT controllers", settings drawer, tutorial, About; roles view / operate / configure.
- Symbol / data type parser checked against the tables of a TwinCAT 3.1.4024 CX (`tests/fixtures/`); fake PLC for tests
  and local runs (`src/app/dev_fake_plc.py`, not in the snap).
- After the first X7 test: the error of a controller that resets the connection names the missing route; AdsLib's own
  log lines follow the app's log level (no "connection closed by remote" every 10 s); retries every 30 s after 6 failures.
- X7 (amd64): installed, Add route (by the user) -> connected to the CX, nodes read through the Data Layer with the PLC
  types, 36.5 MB RSS, `ui_check --device x7` OK; Data Layer writes of bTest / sTest into the PLC, wrong type / read-only
  / too long refused. X3 (arm64): installed, Add route (by the user) -> the same reads, writes and UI checks as on the X7, 36.3 MB RSS /
  1.2 % CPU. Roles as the test users on both.
