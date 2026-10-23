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
| Select an AMR | click it on the map or in the list | |
| **Lock to an AMR** | lock button in the list, or double-click the AMR | `Esc` unlocks |
| Overview | minimap (bottom right): click/drag to move the view | |

* **Lock (follow)**: the AMR stays in the centre and the map moves around it. With *Rotate map with AMR*
  (on by default) the map also turns, so the AMR always points up and appears completely stationary.
  Zooming keeps the lock; dragging the map ends it.
* Positions are fetched every `display.refreshIntervalMs` (250 ms) and **interpolated** between
  updates, so motion is smooth even with slower writers. The status pill shows `live` or `no update for
  N s` (greyed AMRs) after `fleet.staleAfterMs`.
* Labels, minimap and the AMR list can be switched off (remembered per browser).
* AMR size on the map: `fleet.amrLengthM` × `fleet.amrWidthM`; tiny AMRs are drawn at a minimum size
  when zoomed out.

## Settings (app data, `settings.json`)

| Setting | Default | |
|---|---|---|
| `map.file` | `HMI_Map.txt` | map file name (app data first, then bundled maps) |
| `map.resolution` | `0` | cell size in map units, 0 = automatic |
| `map.unitsPerMeter` | `1000` | map units per meter (1000 = mm): scale bar, AMR size, simulation speed |
| `fleet.rotationUnit` | `deg` | `deg` or `rad` |
| `fleet.staleAfterMs` | `5000` | grey out the AMRs when the data is older |
| `fleet.amrLengthM` / `amrWidthM` | `1.0` / `0.7` | AMR footprint in m |
| `display.refreshIntervalMs` | `250` | how often the page fetches positions |
| `simulation.enabled` | `false` | built-in demo fleet (no PLC needed) |
| `simulation.amrCount` / `speedMps` | `4` / `0.8` | |

**Simulation** (for demos and tests): the app moves AMRs between random goals through the free space of
the map (coarse grid, walls kept at a distance, stray lidar points ignored) and writes them to
`fleet/amrs` every 200 ms - same path and validation as a PLC write. While it runs it overwrites
external writes; switching it off clears the fleet once.

## Web API (`/ctrlx-amr-fleet/api/v1`, token required)

`GET /fleet[?since=<revision>]` (AMRs + status; only the status when nothing changed since that
revision) · `GET /map` (active map: file, source, error, cell size, extent, tile levels) ·
`GET /map/tiles/<map id>/<level>/<x>/<y>.png[?color=rrggbb]` (tile; the map id is a content hash, so
tiles are cacheable) · plus the template routes (`/status`, `/settings`, `/values`, app-data save/load).

## Develop

```bash
python3 -m unittest discover -s tests -t .     # tests, any PC (fake Data Layer)
./run-local.sh                                  # http://127.0.0.1:8080/ctrlx-amr-fleet/ - switch on the simulation in Settings
../../tools/check.sh .
./build-snap-amd64.sh && ../../tools/ctrlx.py install ./*_amd64.snap --service
```

UI check in headless Chromium (one-time setup: `../../tools/setup-ui-test.sh`): `tests/ui_scenario.py`
switches on the simulation, then takes screenshots of the map, the lock (heading-up) and the settings drawer
and checks that dragging ends the lock. `--device` opens the installed app through the reverse proxy and
changes nothing there.

```bash
python3 ../../tools/ui_check.py . --narrow      # screenshots in .ui-check/ - look at them
python3 ../../tools/ui_check.py . --device      # the app on the default device of devices.toml
```

| Path | What |
|---|---|
| `src/app/occupancy_map.py` | map file parser, grid, levels, PNG tiles (pure Python) |
| `src/app/map_source.py` | which map file, reload on change |
| `src/app/fleet.py` | fleet JSON validation, `FleetState` |
| `src/app/simulator.py` | demo fleet: navigation grid, random goals |
| `src/app/datalayer_nodes.py` | nodes; `on_write` may reject a value (fleet JSON) |
| `src/app/web_server.py` | routes incl. `/fleet`, `/map`, `/map/tiles` |
| `www/app.js` | canvas map view: tiles, interpolation, pan/zoom/pinch, lock, minimap, list, settings |
| `tests/ui_scenario.py` | UI scenario for `tools/ui_check.py` |
