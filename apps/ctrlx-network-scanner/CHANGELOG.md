# Changelog

## Unreleased

- **UI feedback overlay, dev only** (2026-11-09, from the kit template, user's request): `./run-local.sh` sets
  `APP_DEV_FEEDBACK` to the kit's `tools/ui-feedback/overlay.js`; the dev server (`src/app/dev_feedback.py`, hooks in
  `web_server.py` and `config.py`) adds it to the HTML pages and keeps the notes in `.dev/feedback.json`. Alt+click an
  element, type a note; skill `/ui-feedback` works through the notes. Only with `APP_DEV_NO_AUTH=1` outside the snap;
  `APP_DEV_FEEDBACK=0 ./run-local.sh` turns it off. Tests: `tests/test_dev_feedback.py`. No snap built with it
  (nothing in the snap changes behaviour).

## 0.1.0 (2026-10-25)

First version (from the kit template 0.1.6).

* Scan engine: neighbor-table (ARP), TCP and mDNS/SSDP/WS-Discovery host discovery; TCP connect and
  UDP scans with load limits; service identification (banners, HTTP, TLS certificates) and optional
  automation device identification (Modbus, OPC UA, EtherNet/IP, ISO-on-TCP); names (DNS,
  NetBIOS, mDNS), MAC vendors (IEEE listing).
* Scan profiles in app data (targets, exclusions, ports, discovery, load limits, schedule: interval or
  daily times/weekdays); three defaults (Quick inventory, Automation devices, Deep).
* Whitelist/blacklist rating (rating only, never limits scanning), change detection against the
  previous scan of the profile, history on flash (capped), PDF/CSV/JSON downloads.
* Data Layer: start/abort nodes for PLCs, progress, results of the newest scan as counts and JSON.
* Web UI: overview with live progress, results with filters and list buttons, history with chart,
  profile editor with size estimate, list editor, settings and node table.

Brought back into the kit: settings validator hook + `minItems`/`itemPattern` (template), `tools/pdf_check.py`,
`tools/ui_check.py` copies `data/`, `tools/new_app.py` skips `.craft/`.

Verified 2026-10-25:
* COREvirtual (amd64, test build of 0.1.0): install, Data Layer (28 nodes; scan started by writing
  `scan/start`, unknown profile rejected with `DL_INVALID_VALUE`), scans of its 2 networks (quick: 4 hosts,
  22 open ports; automation: EtherNet/IP ListIdentity answered over TCP and UDP), interval schedule fired
  and restored, PDF from the device, UI through the reverse proxy (`ui_check --device`). CPU <= 4.7 %, RSS 45 MB.
* Real CORE (arm64, final 0.1.0): install (`--service`, back to OPERATING), quick scan of 192.168.1.0/24 in
  15 s (4 hosts with Bosch/HP vendors, 30 open ports), CPU max 9.7 %, RSS 42 MB, PDF, UI on the device
  (also 390 px wide). Numbers: gotchas #66.
* Locally: 96 unit tests, `ui_check` admin/narrow/reader (scan, list buttons, downloads), `pdf_check`.

