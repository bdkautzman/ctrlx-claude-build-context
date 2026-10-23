# apps

Create apps here with:

    python3 tools/new_app.py --name ctrlx-<company>-<app> --title "<Title>" --author "<Company>" --url <url>

Each app folder can also become its own git repository.

| App | What | Status |
|---|---|---|
| `ctrlx-core-monitor` | Status of other ctrlX COREs (host name, IP, firmware, CPU, memory) via Data Layer remote connections; connections in app data, "+ Add core" dialog | 0.1.1 - verified 2026-10-22: amd64 on the COREvirtual (watching a 4.6 and a 3.6 CORE), arm64 on the real CORE (watching the COREvirtual); UI checked in the browser |
| `ctrlx-amr-fleet` | Live map of AMRs: positions/headings written as one JSON string to the Data Layer node `ctrlx-amr-fleet/fleet/amrs`; occupancy map (bundled `HMI_Map.txt` or app data) as tiles; pan, zoom, pinch, lock (heading-up), minimap, AMR list with detail tiles; styling settings; read-only users without settings; ctrlX home screen widget; simulation with collision avoidance | 0.2.1 - 2026-10-23: installed on the COREvirtual (amd64): app, ui_check --device and the home screen widget (appeared by itself; token + Open work) verified; simulated AMRs keep moving (90 s sample: all 4 moved, 12-15 trips each, never more than 1 s all stuck); arm64 not built |
