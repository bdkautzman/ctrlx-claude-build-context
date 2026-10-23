# Changelog

Versions follow semantic versioning and must match `version:` in `snap/snapcraft.yaml`.

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
