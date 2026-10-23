# apps

Create apps here with:

    python3 tools/new_app.py --name ctrlx-<company>-<app> --title "<Title>" --author "<Company>" --url <url>

Each app folder can also become its own git repository.

| App | What | Status |
|---|---|---|
| `ctrlx-core-monitor` | Status of other ctrlX COREs (host name, IP, firmware, CPU, memory) via Data Layer remote connections; connections in app data, "+ Add core" dialog | 0.1.1 - verified 2026-10-22: amd64 on the COREvirtual (watching a 4.6 and a 3.6 CORE), arm64 on the real CORE (watching the COREvirtual); UI checked in the browser |
| `ctrlx-amr-fleet` | Live map of AMRs: positions/headings written as one JSON string to the Data Layer node `ctrlx-amr-fleet/fleet/amrs`; occupancy map from a point-list file (bundled `HMI_Map.txt` or app data) served as tiles; pan, zoom, pinch, lock to an AMR (optionally heading-up), minimap, AMR list; built-in simulation | 0.1.0 - 2026-10-23: installed on the COREvirtual (amd64): map loads in 48 ms, simulation, Data Layer nodes, tiles and token checks through the proxy OK, `/fleet` poll ~47 ms round trip; UI checked by the user and with `tools/ui_check.py` (local + device); arm64 not built. Source is at 0.1.1 (not built: simulator heading fix, `data-ui-ready`) |
