# Changelog

Versions follow semantic versioning and must match `version:` in `snap/snapcraft.yaml`.

## 0.2.0

- **Kit UI** (template 0.5.0, user's request 2026-11-10): the page runs on the kit's `www/kit.js` / `kit.css`
  (API calls, roles, settings drawer, help menu + tutorial, About, light/dark) instead of its own copies; app code stays
  in `app.js` / `style.css`.
- **Three roles** view / operate / configure (new scope `ctrlx-system-analyzer.web.operate.x`, manifest names and
  descriptions for all three). View: every page, exports (`/status` `leakMetric` for the Leaks page). Operate: event
  **markers**. Configure: settings incl. the recording medium. Before: `.r` / `.rw`.
- The **Settings tab is gone**: the settings are in the kit's drawer, the analyzer's status card is on the Overview;
  the medium field has a refresh icon (looks for media again) instead of "Look for media" (user's note 2026-11-11).
- **Charts** (user's notes 2026-11-11): the legend shows a solid circle per line; legend values keep their width, so a
  card no longer grows while the cursor shows time and values; **a click on a chart opens it large** in a dialog (it
  follows the data, drag zooms, Esc closes). Chart colors follow light/dark mode.
- **Sortable tables**: the Apps and Leaks column titles sort high → low, low → high, unsorted (text columns A → Z
  first); the sorted column has an arrow and a bold title.
- **Settings drawer** (kit): help texts as ⓘ tooltips next to the labels, true/false as switches, *Save* stores and
  closes the drawer.
- **Styling** settings (new, as in the template): `style.accent`, `panel`, `text`, `panelDark`, `textDark`; page
  background, lines and secondary text are derived. **Light / dark** button in the top bar (per browser).
- **About** in the help menu: name, version, author, license (MIT), AI note (`GET /status` `about`; `config.py`
  `APP_AUTHOR`, `APP_URL`, `APP_LICENSE`, `APP_AI_NOTE`; `snapcraft.yaml` `license: MIT`).
- `/status` answers `role`, `canOperate`, `canConfigure`, `style`, `about`; `GET /values` lists the nodes for view
  (`NodeSpec.view_role`: `settings/active` only for configure), `PUT /values/<node>` needs operate
  (`NodeSpec.write_role`).
- Tests: `tests/test_about.py`, the role split in `tests/test_web_and_nodes.py`, UI scenario as reader and operator.
- **UI feedback overlay, dev only** (2026-11-09, from the kit template, user's request): `./run-local.sh` sets
  `APP_DEV_FEEDBACK` to the kit's `tools/ui-feedback/overlay.js`; the dev server (`src/app/dev_feedback.py`, hooks in
  `web_server.py` and `config.py`) adds it to the HTML pages and keeps the notes in `.dev/feedback.json`. Alt+click an
  element, type a note; skill `/ui-feedback` works through the notes. Only with `APP_DEV_NO_AUTH=1` outside the snap;
  `APP_DEV_FEEDBACK=0 ./run-local.sh` turns it off. Tests: `tests/test_dev_feedback.py`. No snap built with it
  (nothing in the snap changes behaviour).

## 0.1.1

Found on the real ctrlX CORE (arm64, 2026-10-30):
- The page takes "now" from the device (`serverTime` in `/status`): the CORE's clock was 7 days behind
  (NTP off), so every preset range showed an empty week-old window. A note in the range bar and the
  status list says how far the device clock is off; all times shown are the device's.
- The CORE's kernel has no pressure stall information (`/proc/pressure` missing): the overview says so
  instead of blaming the `system-observe` plug (`collector.pressure`: ok / missing / unreadable).
- UI scenario: on a device an app's chart may be empty.
- The analyzer's own start event belongs to its app: its leak-table row now counts its restarts
  (on the CORE it said 0 after two restarts) and fits only the data since the last one.
- Leak analysis fills in single dips far below their neighbours (Hampel filter) before fitting: read
  back from the USB stick (1-minute minimums) the CORE's test leak had a dip every ~10 min and was only
  "possible" (fit 0.48); with the filter "growing" (0.93). Saw teeth and restart drops are unchanged.

Verified on the real CORE (arm64, ctrlX OS with automationcore 4.6.5, 2026-10-30): the installer
connects both plugs; 213-215 processes / 425-437 threads at 122-136 ms CPU per 5 s sample (2.4-2.7 %
of one core); RSS 52 MB after 2 min, 57 MB after 35 min; RT threads of the automationcore with
policy/priority/core (cores 2-3); cgroup v2 grouping; UI through the reverse proxy (ui_check --device
core); Data Layer nodes. Leak test: a bash process growing ~34 MB/h rated "growing" (fit 0.98) after
30 min at process and app level and in `analysis/growing`. Recording to a USB stick (FAT32,
`/media/sda1`): all file kinds written, ~33 KB/min with threads; after an app restart the range
before it came back from the stick (charts, leak table, events).

## 0.1.0

First version (2026-10-27), from the user's design decisions:
- strict confinement + `system-observe` (not devmode; classic is impossible on Ubuntu Core) - the
  ctrlX installer connects it (probe snap, gotchas #67);
- history in RAM by default; optional long-term recording to an external medium the user selects,
  in `<medium root>/ctrlx-system-analyzer/` (`removable-media` plug);
- "tasks" = Linux threads (the scheduler's task timing nodes need a write to start - not included);
- charts with uPlot 1.6.32 (MIT, bundled).

Contents: collector for system / apps / processes / threads from `/proc`, RAM history in three
tiers with a memory budget, leak analysis, restart/update events and markers, CSV recording and
read-back on the medium, CSV/JSON export, web UI (overview, apps, leaks, events, settings), Data
Layer nodes (summary, growing apps, marker, recording state).
Chosen without asking: CSV files per day and app on the medium; PSS read every 30 s; process and
thread series only for processes/threads that have run for a minute; CPU in % of one core.

Verified on the COREvirtual (amd64, 2026-10-27): the installer connects both plugs; 176-180
processes / 744-1100 threads at 29-36 ms CPU per 5 s sample (0.6 % of one core); RSS 60 MB after
2 min, flat at 68.5 MB from 40 min on (40 MB budget then; default now 28 MB); UI through the reverse
proxy (ui_check --device), Data Layer nodes. Leak test: a bash process growing ~33 MB/h was rated
"growing" (process and app level, Data Layer `analysis/growing`) after 30 min - that needed two
fixes found in the test: the envelope with the steadier fit (single deep dips, gotchas #72) and own
series only for processes older than a minute (`sleep` churn, gotchas #71). Found on the way:
`ctrlx-bmg-umc` 5.7.0 on that COREvirtual adds ~4 threads per minute (290 -> 767 threads in 2 h).
Not tested: the real CORE (arm64 snap built, not installed), a real USB stick / SD card (recording
tested locally and in unit tests only), multi-hour ranges (the COREvirtual runs 4 h at a time).
