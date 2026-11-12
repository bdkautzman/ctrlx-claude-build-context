# Changelog

Versions follow semantic versioning and must match `version:` in `snap/snapcraft.yaml`.

## 0.6.0

- **Kit UI** (template 0.5.0, user's request 2026-11-10): the page runs on the kit's `www/kit.js` / `kit.css`
  (API calls, roles, settings drawer, help menu, About, light/dark) instead of its own copies; app code stays in
  `app.js` / `style.css`. The app keeps its own help menu entries and map tutorial.
  - **Three roles** view / operate / configure (new scope `ctrlx-amr-fleet.web.operate.x`; manifest, menus and
    widget list all three). Configure: settings, map uploads, AMR list. Operate: writes of the app's writable Data
    Layer values through the web API (`PUT /values/<node>`, `NodeSpec.write_role`). View: map, list, details,
    `GET /values` (without `settings/active`, `NodeSpec.view_role`). `/status` answers `role`, `canOperate`,
    `canConfigure`, `style`, `about`.
  - **Settings drawer**: help texts as ⓘ tooltips next to the labels, true/false as switches, *Save* closes the
    drawer; *Input* takes the whole row (`wide`); the AMR list is one line with *Edit…* (dialog to add, remove,
    upload and download ids).
  - **Light / dark** button in the top bar (per browser) with new settings `style.panelDark` / `style.textDark`;
    the map colors stay as set.
  - **About** in the help menu: name, version, author, license (MIT), AI note (`GET /status` `about`;
    `config.py` `APP_AUTHOR`, `APP_URL`, `APP_LICENSE`, `APP_AI_NOTE`; `snapcraft.yaml` `license: MIT`).
  - *Rotate map with AMR* is a switch; map keys are ignored while the drawer or a dialog is open (Esc closes
    About again).
- Manifest: scope names and descriptions for the three roles.
- Tests: `tests/test_about.py`, role split in `tests/test_web_and_nodes.py`, UI scenario for reader and operator.
- **UI feedback overlay, dev only** (2026-11-09, from the kit template, user's request): `./run-local.sh` sets
  `APP_DEV_FEEDBACK` to the kit's `tools/ui-feedback/overlay.js`; the dev server (`src/app/dev_feedback.py`, hooks in
  `web_server.py` and `config.py`) adds it to the HTML pages and keeps the notes in `.dev/feedback.json`. Alt+click an
  element, type a note; skill `/ui-feedback` works through the notes. Only with `APP_DEV_NO_AUTH=1` outside the snap;
  `APP_DEV_FEEDBACK=0 ./run-local.sh` turns it off. Tests: `tests/test_dev_feedback.py`. No snap built with it
  (nothing in the snap changes behaviour).

## 0.5.0

- Settings: **Map file** is a drop-down of the available maps - the `.txt` files in the app's app data
  folder (with their size) and the bundled maps; a configured file that is missing shows as "not found".
  **Upload map…** stores a map file in the app data at once and selects it (*Save* shows it): streamed to
  a temp file (`*.tmp`, not part of configurations), name cleaned to A-Z a-z 0-9 `.` `_` `-`, `.txt`, at
  most 64 MB, map points in the first 64 KB; replacing a file in the app data asks first; upload progress
  in percent. Replacing the active map reloads it at once. Web API `GET /maps`, `PUT /maps/<name>[?replace=1]`.
- Simulation: the navigation grid keeps its cells in byte and int arrays instead of Python sets, lists and
  a Counter: 103 x 177 m map 3.9 -> 0.3 MB kept (peak 5.5 -> 0.4 MB), a 208 x 356 m map (40 mm cells,
  320k nav cells) 15.2 -> 1.0 MB (peak 23.9 -> 1.7 MB); measured with tracemalloc in the build VM.

## 0.4.0

- Input modes (new setting `fleet.input`, in the settings and the app data): `fleet-json` (default, as
  before: the whole fleet as one JSON string in `fleet/amrs`), `amr-json` (one JSON string node per
  AMR: `fleet/amr/<id>`) and `amr-fields` (one node per value and AMR: `fleet/amr/<id>/x`, `y`,
  `rotation`, `battery`, `name`, `state`, `info`). Per-AMR writes are validated one by one and merged
  into one fleet update per write cycle (when the writes pause for 20 ms, at the latest after 200 ms);
  number nodes accept any number type; `fleet/amrs` is
  read-only there and shows the merged fleet.
- AMR list for the per-AMR modes: `amr-list.txt` in the app data (default AMR-01 ... AMR-04), edited in
  the settings - add / remove ids, upload or download a list file - and stored with Save; changes from
  outside (Manage app data, loaded configurations) are picked up. Web API `GET` / `PUT /amr-list`.
- One wildcard provider node serves `fleet/amr/**`, registered only in the per-AMR modes (no node per
  AMR and value).
- Settings rules may give an enum `enumLabels` (shown in the select, the value is stored).
- `scripts/perf_test.py --inputs`: writes a fleet through the device's REST bulk API in each input mode
  and measures the app and the device. Real CORE, 5 updates/s, app CPU at 10 / 50 / 200 AMRs: whole
  fleet JSON 1.1 / 2.5 / 7.2 %, JSON per AMR 2.6 / 9.2 / 31.6 %, node per value 6.6 / 29.2 / 50.8 % (200
  AMRs per value: only 2.1 updates/s reached) - ~0.24 ms per node write; the settings help says so.

## 0.3.1

- AMR list: a tile writes only values that changed, and tiles outside the visible part of the list are
  not laid out or painted (`content-visibility`). Every tile was rewritten with each fleet update (since
  0.1). A/B test at 200 AMRs against 0.2.4 and 0.3.0: layout 193 / 178 -> 18 ms/s, style 34 -> 5 ms/s,
  overview 2.9 / 2.7 -> 3.2 fps, locked 2.3 / 2.2 -> 3.0 fps. Same for the fleet status pill.
- AMRs keep their shape (body with heading arrow) when zoomed out, at least 16 px long; the small
  arrowhead below 14 px was hard to see.
- Crosshair: the AMR's blue outline (as when locked) pulses three times for 2 s, then goes; zoomed out
  it is drawn bigger (at least 60 px) so the AMR is easy to find.
- Double-click on the locked AMR unlocks it.
- Top bar: pills and buttons have one height (26 px); the help button was 24 px (28 px on phones).
- Tutorial: the ring over the step's control pulses as long as the step is shown (it blinked 3 times).
- `scripts/browser_ab.py`: A/B test of page versions in headless Chromium against one local app.

## 0.3.0

- AMR paths: a path button on each AMR tile (next to crosshair and lock) draws where the AMR drove, in
  a darker shade of its color; several paths at once, remembered per browser. The app keeps the paths
  in memory (new setting `fleet.pathHistoryMinutes`, default 5, 0 = off; one point per 0.25 m, at most
  200,000 points for all AMRs), so a path is complete as soon as it is switched on;
  new route `GET /fleet/path?id=`; `/view-config` has `pathHistoryMinutes` and `pathStepM`.
- Help button (`?`, top right) with the app's GitHub page and a tutorial: a card at the bottom of the
  map steps through the controls with arrow buttons (or ← →); the control of each step blinks, the AMR
  steps use an example AMR that pulses on the map, and "Show me" does the step's action.
- The tile buttons (crosshair, lock, path) are in one row.

## 0.2.5

- Less memory for big maps (0.2.4 on a real CORE: 62-63 MB instead of 49-51 MB with the 103 x 177 m
  map; Python keeps the memory of short-lived objects): the map file is parsed in 256 KB chunks
  instead of one regex pass over the whole file (parser peak 7 MB instead of 39 MB), and each tile level is
  derived from the bitmap bytes of the level below instead of a set of all occupied cells (peak 2 MB
  instead of 23 MB, and 2.3 s instead of 5.0 s for all 7 levels in the emulated build VM).
- The file size limit is checked with `fstat` before reading; `read(limit + 1)` allocated the full
  64 MB limit up front.
- Measured on the real CORE (with 0.3.0, 2026-10-24): 55-58 MB during the big-map test instead of
  62-63 MB, peak 60 MB instead of 82 MB; the big map is ready after 4.9 s instead of 6.8 s.

## 0.2.4

- Browser: while the view moves from frame to frame (locked onto an AMR, panning, zooming) the map is
  drawn straight onto the canvas again; the cached map layer is used once the view stands still. In 0.2.3
  the layer was redrawn and copied every frame while locked - slightly slower than before (measured).

## 0.2.3

Performance (from the 2026-10-23 test: 103 x 177 m map, 10-200 AMRs, COREvirtual and real CORE):
- The map loads in a background thread: the web UI is there right after a (re)start and a settings save
  answers at once; the previous map stays until the new one is ready, the page shows "Loading map ...".
- Map loading ~3x faster: one regex pass for files without commas (decimal-comma files keep the careful
  line-by-line path), cell size from the distinct values, cells via lookup tables.
- Right after loading, all tile levels are built in the background (each from the level below), so the
  first tile of a zoom level no longer waits ~1 s on a CORE.
- Simulation on a fixed 200 ms rhythm: the work of a step no longer stretches the period (200 AMRs on a
  CORE reached 3.6 instead of 5 updates/s).
- Browser: the map is drawn into an offscreen layer only when the view, the colors or the loaded tiles
  change; while AMRs move only they are redrawn. AMRs zoomed far out keep 8 px (was 18 px) and are drawn
  as an arrowhead below 14 px; smaller minimap dots with many AMRs.

## 0.2.2

- Simulation scales to big maps and many AMRs (performance tests): up to 200 AMRs (was 50); A* paths
  instead of a breadth-first search over the whole map, goals within 25 m, at most 4 new paths per 200 ms
  step, avoid zones and neighbour checks only for nearby AMRs (coarse position grid). 200 AMRs on a
  103 x 177 m map: ~200 ms per step in the emulated build VM (was ~2 s), a few ms on a COREvirtual.
- Simulation picks the right floor when the walls are the map border: a region may touch the map edge
  with up to 5 % of its cells (door gaps). Before, a building with doors in its outer wall made the
  simulation use a small enclosed pocket (e.g. inside a rack pair).
- `scripts/make_large_map.py`: generator for a 103 x 177 m test map in the HMI_Map.txt format
  (shelving, production, open area; deterministic per seed).

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
