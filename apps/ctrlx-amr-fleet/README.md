# AMR Fleet View (`ctrlx-amr-fleet`)

ctrlX OS 4.6 app (snap, `core24`, Python) that shows **autonomous mobile robots (AMRs) live on a map
of the site**. A PLC or fleet manager writes the position and heading of every AMR as one JSON string
to a Data Layer node; the web UI draws them on the map with pan, zoom and a lock that follows one AMR.

## Feeding the AMRs: Data Layer node `ctrlx-amr-fleet/fleet/amrs` (string, writable)

Write the **whole fleet** as one JSON string, as often as positions change (e.g. every 100–500 ms):

```json
{"amrs": [
  {"id": "AMR-01", "x": 1250.0, "y": 30480.0, "rotation": 90.0},
  {"id": "AMR-02", "x": 4800.0, "y": 27310.5, "rotation": 182.5, "name": "Tugger", "state": "driving", "battery": 74}
]}
```

| Field | | Meaning |
|---|---|---|
| `id` | required | string or integer, unique, 1–64 characters |
| `x`, `y` | required | position in **map units** (the coordinates of the map file; default mm) |
| `rotation` | required | heading, **counter-clockwise from the +x axis**, in degrees (or radians: setting `fleet.rotationUnit`) |
| `name`, `state` | optional | strings ≤ 64 characters, shown in the list and on the map label |
| `battery` | optional | 0–100 (%), shown as a small gauge in the list |
| `info` | optional | object with up to 16 more fields for the detail view, e.g. `{"task": "T-4711", "load": "pallet", "errors": 0}`: keys 1–32 characters, values string (≤ 64), number or boolean |

The app adds `speed` (map units per second, smoothed) from the change of position between updates.

* A bare array `[{...}, ...]` works too. Other keys are ignored; `null` = not set.
* An AMR that is missing from the next write disappears from the view.
* **Invalid writes are rejected as a whole** (Data Layer result `DL_INVALID_VALUE`); the previous fleet
  stays on screen and the reason, e.g. `amrs[2].rotation: missing`, is in `fleet/last-error` and in the
  UI's AMR list. Max. 1000 AMRs, 1 MiB.
* Values are kept in memory only (no disk writes, safe for cyclic PLC writes). After an app restart the
  fleet is empty until the next write.
* Test from the build VM (kit root; the value argument is JSON, so the fleet JSON goes in as a JSON string):
  `tools/ctrlx.py dl-write ctrlx-amr-fleet/fleet/amrs '"[{\"id\":\"A1\",\"x\":1000,\"y\":30000,\"rotation\":90}]"' --type string`,
  or the web API `PUT /ctrlx-amr-fleet/api/v1/values/fleet/amrs` with `{"value": "<fleet json>"}`.

Other nodes (read-only): `fleet/count`, `fleet/updates` (accepted writes), `fleet/rejected`,
`fleet/last-error`, `map/info` (active map as JSON), plus the template's `status/version`,
`status/state`, `status/heartbeat`, `settings/active`.

## The map

The environment is an **occupancy map file**: one occupied cell per line, `x<TAB>y` (also `;`, `,` or
spaces; decimal commas with tab/`;`; header lines and extra columns are ignored), in the same
coordinates as the AMRs. Each point is the centre of a cell; the cell size is detected automatically
(greatest common divisor, here 20) or set with `map.resolution`.

* Bundled: `defaults/maps/HMI_Map.txt` (10.4 m × 17.8 m if the units are mm, 520 × 890 cells, 17,015 points).
* Own maps: upload the file into the app's folder in the ctrlX app data (`Settings → Manage app data →
  ctrlx-amr-fleet/`) and set `map.file` to its name. App data is searched before the bundled maps, and
  the map is saved and loaded with ctrlX configurations.
* The file is checked every 2 s and reloaded when it changes; the open pages pick up the new map on
  their own. A file that breaks keeps the last good version on screen (with the error shown).
* Large maps: the server cuts the map into 256 × 256 PNG tiles per zoom level (level 0 = one pixel per
  cell, each level halves), so the browser only loads what is visible. Limit: 100 million cells
  (e.g. 10,000 × 10,000); choose a larger `map.resolution` for bigger areas.

## Web UI

Open from the ctrlX sidebar or overview (**AMR Fleet View**).

| Action | Mouse / touch | Keys |
|---|---|---|
| Pan | drag (one finger) | arrow keys |
| Zoom | wheel, pinch, double-click on empty map, +/− buttons | `+` `-` |
| Whole map | fit button | `F` / `0` |
| North up | compass button | `N` |
| Details of an AMR | click its tile in the list (again: closes), or click the AMR on the map | |
| Show an AMR on the map | crosshair button on its tile (the only thing in the list that moves the map); its outline pulses for 2 s, bigger when zoomed out | |
| **Lock to an AMR** | lock button on its tile, or double-click the AMR on the map (again: unlocks) | `Esc` unlocks |
| Path of an AMR | path button on its tile (again: hides it); several at once | |
| Overview | minimap (bottom right): click/drag to move the view | |
| Help | `?` button (top right): the app's GitHub page, **Tutorial** | in the tutorial: `←` `→`, `Esc` |

* **Lock (follow)**: the AMR stays in the centre and the map moves around it. With *Rotate map with AMR*
  (on by default) the map also turns, so the AMR always points up and appears completely stationary.
  Zooming keeps the lock; dragging the map ends it.
* Positions are fetched every `display.refreshIntervalMs` (250 ms) and **interpolated** between
  updates, so motion is smooth even with slower writers. The status pill shows `live` or `no update for
  N s` (greyed AMRs) after `fleet.staleAfterMs`.
* Labels are placed where they cover neither another label nor an AMR; a label without room is left
  out (the list still has it). The selected and the locked AMR always show theirs.
* **Paths**: the app keeps the driven path of every AMR for `fleet.pathHistoryMinutes` (5 min; in memory,
  one point per 0.25 m, at most 200,000 points for all AMRs together - with many AMRs the paths get
  shorter; a jump of more than 5 m at > 15 m/s, e.g. a relocalization, starts a path anew). The path
  button draws it up to the AMR in a darker shade of the AMR's color; switched-on paths are remembered
  per browser. `0` minutes: no paths, no buttons.
* **Tutorial** (help menu): a card at the bottom of the map steps through the controls - map, list,
  details, focus, lock, path, zoom/view, names/overview, overview map, status, settings (only for users
  who may change them), help. The control of each step pulses while it is shown; the AMR steps use an example AMR that
  pulses on the map, and *Show me* does the step's action. Everything stays usable while it is open.
* Labels, minimap and the AMR list can be switched off (remembered per browser).
* **Read-only users** (scope `ctrlx-amr-fleet.web.all.r` only) see the map, the list and the details,
  but no settings: no button, `#settings` does nothing, and the settings API answers 403.
* Phone/HMI width (≤ 600 px): one-row top bar, smaller minimap, the list floats over the map.
* AMR size on the map: `fleet.amrLengthM` × `fleet.amrWidthM`; zoomed out they keep their shape at a
  minimum length of 16 px.
* The AMR list writes only what changed and leaves tiles outside its visible part alone - with 200
  AMRs rewriting every tile per update cost the browser more than the map (0.3.1).

## Settings (app data, `settings.json`)

| Setting | Default | |
|---|---|---|
| `map.file` | `HMI_Map.txt` | map file name (app data first, then bundled maps) |
| `map.resolution` | `0` | cell size in map units, 0 = automatic |
| `map.unitsPerMeter` | `1000` | map units per meter (1000 = mm): scale bar, AMR size, simulation speed |
| `fleet.rotationUnit` | `deg` | `deg` or `rad` |
| `fleet.staleAfterMs` | `5000` | grey out the AMRs when the data is older |
| `fleet.amrLengthM` / `amrWidthM` | `1.0` / `0.7` | AMR footprint in m |
| `fleet.pathHistoryMinutes` | `5` | how long the app keeps each AMR's path (0..60, 0 = no paths) |
| `display.refreshIntervalMs` | `250` | how often the page fetches positions |
| `simulation.enabled` | `false` | built-in demo fleet (no PLC needed) |
| `simulation.amrCount` / `speedMps` | `4` / `0.8` | up to 200 AMRs (performance tests) |
| `style.accent` / `panel` / `text` | `#007bc0` / `#ffffff` / `#1f2328` | base colors of the page; page background, lines and secondary text are derived (a dark palette works) |
| `style.mapBackground` / `mapFloor` / `mapWalls` / `mapGrid` | `#dde2e8` / `#ffffff` / `#333d4a` / `#e9ecef` | map colors (walls are drawn into the tiles by the server) |
| `style.staleAmr`, `style.amrColors` | `#9aa3ad`, 10 colors | AMRs without current data; the colors AMRs get (by id, the same in the widget) |

**Styling** in the settings: every color has a color picker and a hex field (`#rrggbb`); changes show
at once as a preview, *Save* keeps them for everyone (app data), *Revert* or closing drops them,
*Default colors* fills in the defaults.

**Simulation** (for demos and tests): the app moves AMRs between random goals (within 25 m) through the
free space of the map (coarse grid, walls kept at a distance, stray lidar points ignored, A* paths, at
most 4 new paths per 200 ms step) and writes them to `fleet/amrs` every 200 ms - same path and
validation as a PLC write. While it runs it overwrites
external writes; switching it off clears the fleet once.

## Web API (`/ctrlx-amr-fleet/api/v1`, token required)

`GET /fleet[?since=<revision>]` (AMRs + status; only the status when nothing changed since that
revision) · `GET /fleet/path?id=<id>` (driven path of one AMR: `points` = `[x, y, age in s]`, oldest
first; 404 for an unknown id) · `GET /view-config` (colors, units, AMR size, refresh, path history - all
the view needs, also for read-only users) · `GET /map` (active map: file, source, error, cell size, extent, tile levels) ·
`GET /map/tiles/<map id>/<level>/<x>/<y>.png[?color=rrggbb]` (tile; the map id is a content hash, so
tiles are cacheable) · plus the template routes (`/status`, `/settings`, `/values`, app-data save/load).
`GET /settings`, `/settings/meta` and `/values` need `ctrlx-amr-fleet.web.all.rw` (`/values` contains
`settings/active`); everything else reads with `.r`. The Data Layer node `settings/active` itself is
governed by the Data Layer permissions of the ctrlX user, not by this app.

## Home screen widget

The app brings a widget for the ctrlX home screen. ctrlX OS puts it on the home screen of every user
who may see it right after the install; otherwise **Home → edit (pencil) → add widget → "AMR Fleet"**
(category AMR). It lists the AMRs with name, state and battery, shows whether the data is live, and *Open* starts
the full view in a new tab. It needs `ctrlx-amr-fleet.web.all.r` or `.rw` (others don't see it in the
catalog).

* Manifest `uiExtensions.dashboard`: `src` `/ctrlx-amr-fleet/widgets.js`, `tag`
  `ctrlx-amr-fleet-widget`, catalog image `/ctrlx-amr-fleet/widget.svg`, size 4×3 (3×2 … 8×8).
* `www/widgets.js` is a plain Web Component with Shadow DOM. It runs inside the ctrlX UI (same origin),
  so it calls `/ctrlx-amr-fleet/api/v1/fleet` with the ctrlX UI's login token from
  `sessionStorage["idm_token"]` (that is where the ctrlX UI and the Bosch widgets keep it, ctrlX OS 4.6).
  Polls every 2 s while visible; AMR colors from `/view-config`.

## Performance (measured 2026-10-23/24, 0.2.2 → 0.2.4)

`scripts/perf_test.py --device NAME --map MAP` (map from `scripts/make_large_map.py`: 103 x 177 m,
5153 x 8853 cells, 184k points); simulation at 5 updates/s; browser = headless Chromium in the emulated
build VM, so browser numbers only compare versions and AMR counts. 0.2.3/0.2.4 brought: map load in
the background, tile levels prepared after loading, fixed 200 ms simulation rhythm, cached map layer
in the browser, smaller AMR markers when zoomed out.

| | COREvirtual (x86, 4 cores) | real ctrlX CORE (ARM, 2 cores) |
|---|---|---|
| Settings save that switches to the big map | 0.84 s → 0.09 s | 5.0 s → 0.11 s |
| Map ready (in the background since 0.2.3) | 0.8 s → 1.2 s incl. all tile levels | 5.0 s → 6.8 s incl. all tile levels |
| First tile of a zoom level | 0.15 s → 0.06-0.08 s | 0.9-1.2 s → 0.07 s |
| Updates/s reached (target 5), 10 / 50 / 200 AMRs | 4.94 / 4.89 / 4.71 → 4.99 / 5.02 / 4.96 | 4.87 / 4.57 / 3.63 → 5.01 / 4.72 / 4.81 |
| App CPU, 10 / 50 / 200 AMRs (% of a core) | 0.6 / 2.0 / 5.9 → 0.5 / 1.7 / 5.8 | 1.7 / 8.8 / 25 → 1.8 / 12 / 37 |
| Memory (RSS) | 57-58 → 60 MB | 49-51 → 62-63 MB (0.2.5 lowers the load peaks, not re-measured) |
| Browser fps overview, 10 / 50 / 200 AMRs | 5.9 / 6.0 / 3.1 → 9.3 / 6.2 / 4.2 | 9.7 / 7.4 / 3.0 → 9.9 / 6.6 / 4.1 |
| Browser fps locked onto an AMR | 3.9 / 3.6 / 2.8 → 3.1 / 3.6 / 1.6 | 4.1 / 3.8 / 2.9 → 4.4 / 3.6 / 2.4 |
| `/fleet` at 200 AMRs | 37 KB, ~80 ms round trip | 37 KB, ~90-100 ms |

**Real CORE with 0.3.0** (2026-10-24, includes the 0.2.5 memory fix and the path history at 5 min):
memory 55-58 MB during the test (0.2.4: 62-63 MB; 0.2.2: 49-51 MB), peak 60 MB (0.2.4: 82 MB), 37 MB
after a start with the bundled map; big map ready 4.9 s in the background (loaded 2.6 s + all tile
levels 2.1 s; 0.2.4: 2.9 s + 3.7 s). App CPU and updates/s as with 0.2.4 (10 / 50 / 200 AMRs: 2.1 / 10 /
40 % of a core, 4.97 / 4.96 / 4.81 updates/s) - the path recording is within the noise. Browser at 200
AMRs: 2.5-2.7 fps in the overview (0.2.4 the day before: 4.1) - the A/B test below shows it was the
VM's speed, not 0.3.0.

**Browser A/B, 0.3.1** (`scripts/browser_ab.py`, 2026-10-24: one local app with the big map and 200
simulated AMRs, the three page versions in turns, medians of 3 rounds; the local simulation shares the
VM, so all fps are low):

| page | overview fps | locked fps | layout ms/s (overview / locked) | style ms/s |
|---|---|---|---|---|
| 0.2.4 | 2.9 | 2.3 | 193 / 136 | 34 / 30 |
| 0.3.0 | 2.7 | 2.2 | 178 / 127 | 34 / 31 |
| 0.3.1 | **3.2** | **3.0** | **18 / 18** | **5 / 10** |

The AMR list rewrote all 200 tiles with every update since 0.1 (0.3.0 did not make it worse); 0.3.1
writes only changes and skips off-screen tiles: layout and style work down ~90 %, +10-35 % frames. What
is left is drawing 200 AMRs with labels (JavaScript ~350 ms/s, a bit more since AMRs keep their full
shape) and software rasterizing in the GPU-less VM - a PC browser has far more room. Locked fps depend on
whether the followed AMR moves (a parked one lets the map layer be reused: one run had 14 fps).

More CPU on the CORE mostly means more simulation steps (5 instead of 3.6 per second at 200 AMRs);
per step it is about the same to ~30 % more (single 20 s samples). At 200 AMRs 78 % of the work per
update is the simulation itself, 17 % reading/checking the fleet JSON and 5 % the `/fleet` answer -
with a real PLC/fleet manager instead of the simulation expect ~5 % of a CORE core at 200 AMRs.
Locked onto an AMR at 200 AMRs the browser did not get faster: the map moves every frame (no cached
layer), and the page now gets 5 instead of 3.6-4.7 updates/s, each rewriting all 200 rows of the AMR
list (not yet measured separately).

## Develop

```bash
python3 -m unittest discover -s tests -t .     # tests, any PC (fake Data Layer)
./run-local.sh                                  # http://127.0.0.1:8080/ctrlx-amr-fleet/ - switch on the simulation in Settings
../../tools/check.sh .
./build-snap-amd64.sh && ../../tools/ctrlx.py install ./*_amd64.snap --service
```

UI check in headless Chromium (one-time setup: `../../tools/setup-ui-test.sh`): `tests/ui_scenario.py`
switches on the simulation, then checks and screenshots the detail tile, crosshair and lock (heading-up,
drag ends it), the styling preview (Revert and closing drop it) and the widget on its own page. As a
reader it checks that there are no settings (button, `#settings`, API 403). `--device` opens the
installed app through the reverse proxy and changes nothing there.

```bash
python3 ../../tools/ui_check.py . --narrow          # admin; screenshots in .ui-check/ - look at them
python3 ../../tools/ui_check.py . --role reader     # read-only user
python3 ../../tools/ui_check.py . --device          # the app on the default device of devices.toml
```

| Path | What |
|---|---|
| `src/app/occupancy_map.py` | map file parser, grid, levels, PNG tiles (pure Python) |
| `src/app/map_source.py` | which map file, reload on change |
| `src/app/fleet.py` | fleet JSON validation, `FleetState` |
| `src/app/simulator.py` | demo fleet: navigation grid, random goals |
| `src/app/datalayer_nodes.py` | nodes; `on_write` may reject a value (fleet JSON) |
| `src/app/web_server.py` | routes incl. `/fleet`, `/map`, `/map/tiles` |
| `www/app.js` | canvas map view: tiles, interpolation, pan/zoom/pinch, lock, minimap, list + details, styling, settings |
| `www/widgets.js`, `www/widget.svg` | ctrlX home screen widget + its catalog image |
| `tests/ui_scenario.py` | UI scenario for `tools/ui_check.py` |
