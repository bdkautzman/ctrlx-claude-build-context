# apps

Create apps here with:

    python3 tools/new_app.py --name ctrlx-<company>-<app> --title "<Title>" --author "<Company>" --url <url>

Each app folder can also become its own git repository.

| App | What | Status |
|---|---|---|
| `ctrlx-core-monitor` | Status of other ctrlX COREs (host name, IP, firmware, CPU, memory) via Data Layer remote connections; connections in app data, "+ Add core" dialog | 0.1.1 - verified 2026-10-22: amd64 on the COREvirtual (watching a 4.6 and a 3.6 CORE), arm64 on the real CORE (watching the COREvirtual); UI checked in the browser |
