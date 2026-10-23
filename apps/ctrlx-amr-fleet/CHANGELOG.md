# Changelog

Versions follow semantic versioning and must match `version:` in `snap/snapcraft.yaml`.

## 0.2.1

- Simulation: AMRs no longer deadlock in narrow free space. After replanning around a blocking AMR they
  went back to their own cell centre first - often towards the blocker - and stayed "blocked" for
  minutes (seen on the COREvirtual with the bundled map: all 4 AMRs stuck). Regression test on the
  bundled map at the real 200 ms step (the open-room test with larger steps did not show it).
- Widget: rows are rebuilt only when name, state, rounded battery, staleness or colors change (it
  replaced all rows every 2 s - needless work on the busy ctrlX home screen).

## 0.2.0

- AMR list: a click on a tile expands its details (state, battery, position, heading, speed, last
  update, `info` fields); only the crosshair moves the map, the lock follows. Double-click on a tile
  no longer locks.
- Fleet JSON: optional `info` object (up to 16 fields) per AMR; the app adds `speed` (map units/s).
- Styling settings: accent, panel and text colors (the rest of the page is derived from them), map
  background/floor/walls/grid, stale AMRs and the AMR color list - color picker + hex field each,
  live preview, "Default colors".
- Read-only users (`.web.all.r`): no settings button, `#settings` does nothing, and GET /settings,
  /settings/meta and /values answer 403. The view gets its colors and units from the new
  GET /view-config.
- ctrlX home screen widget "AMR Fleet" (`widgets.js`, tag `ctrlx-amr-fleet-widget`): AMRs with name,
  state and battery; uses the ctrlX UI's login; "Open" starts the full view.
- Map labels are placed where they don't cover other labels or AMRs (else left out; the selected and
  the locked AMR always keep theirs); label colors follow the map floor.
- Simulation: AMRs keep their footprints apart, wait when blocked and plan around each other; they
  report goal, distance and trips as `info`.
- Narrow screens (<= 600 px): one-row top bar (icon-only settings, no simulation badge), smaller
  minimap, the lock bar at the top.

## 0.1.1

- Simulation: headings are wrapped to -180..180 degrees (they grew without bound, e.g. -495).
- `<body data-ui-ready>`: "true" once the first fleet answer is in and no visible map tile is loading.
- UI test: `tests/ui_scenario.py` for `tools/ui_check.py` (headless Chromium) replaces the QuickJS harness.

## 0.1.0

- Created from the ctrlX Python web-app template (0.1.4).
- Data Layer node `fleet/amrs`: all AMRs as one JSON string (id, x, y, rotation; optional name,
  state, battery), validated, rejected writes report `fleet/last-error`; status nodes `fleet/count`,
  `fleet/updates`, `fleet/rejected`, `map/info`.
- Occupancy map from a point-list file (bundled `HMI_Map.txt` or a file in the app data), reloaded on
  change, served as PNG tiles per zoom level.
- Web UI: canvas map with pan, zoom, pinch, fit, north-up, lock to an AMR (optionally heading-up),
  interpolated motion, labels, minimap, AMR list with filter, settings drawer.
- Built-in AMR simulation for demos and tests.
