# Changelog

Versions follow semantic versioning and must match `version:` in `snap/snapcraft.yaml`.

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
