# ctrlx-system-analyzer — System Analyzer for ctrlX OS 4.6

A diagnostic app to install **temporarily**: it samples CPU and memory of the whole system and of
every app (snap), process and thread from Linux `/proc`, keeps them as time series and shows them
over a selectable time range - more detail than the ctrlX OS performance view, and with history, so
that a slow memory leak becomes visible. A leak analysis ranks apps and processes by steady memory
growth (MB/h); restarts and updates are marked in the charts; data can be exported as CSV/JSON.

Built from the kit template (`templates/python-webapp`), Python standard library only. Charts:
uPlot 1.6.32 (MIT, bundled in `www/vendor/`, listed in `fossinfo.json`).

## Where the data comes from

| What | Source | Note |
|---|---|---|
| CPU per core, system memory, swap | `/proc/stat`, `/proc/meminfo` | |
| Pressure stall (CPU/memory/I/O waiting) | `/proc/pressure/*` | needs `system-observe` |
| App of a process | cgroup (`/system.slice/snap.<snap>.<app>.service`) | snap services, snapd-started commands/hooks, system services, user sessions, kernel threads |
| CPU per process | `/proc/<pid>/stat` utime+stime | % of **one** core (like `top`); an app = sum of its processes |
| Memory per process | `/proc/<pid>/status`: RSS = anon + file + shmem, swap, peak (VmHWM) | |
| PSS | `/proc/<pid>/smaps_rollup`, every 30 s per process | needs `system-observe`; shared pages split between processes |
| CPU per thread, scheduling | `/proc/<pid>/task/<tid>/stat`: policy (FIFO/RR = real-time), RT priority, nice, last core | the ctrlX real-time tasks (ctrlXAutomation, motionHighPrep, ...) are threads of the automationcore |
| Restarts / updates | a service whose processes are all replaced = restarted; a snap whose processes run from a new `/snap/<name>/<revision>/` = updated | events in RAM (and on the medium) |

**Confinement**: strict, with the plugs `system-observe` (read-only `/proc` of all processes) and
`removable-media` (optional recording). The ctrlX installer connects both by itself - no
`snap connect` needed (tested 2026-10-27, gotchas #67). Without `system-observe` the app still works:
the process list, CPU, RSS split and threads are readable by every strict snap; only PSS, I/O,
pressure and the AppArmor labels need the plug. The per-region memory map (`/proc/<pid>/smaps`) is
not readable by a strict snap at all - to see *where* inside a leaking process memory grows, use
`sudo cat /proc/<pid>/smaps` over SSH.

**Why "anon" for leaks**: `RssAnon` is heap and other private memory - where a leak shows. `RssFile`
(mapped files, libraries) goes up and down with the page cache, `shmem` is shared memory. The
metric is a setting (`leak.metric`: anon, rss, pss).

## History

| Tier | Resolution | Default length | Setting |
|---|---|---|---|
| every sample | 5 s (`collector.intervalSeconds`, 1-60) | 2 h | `history.rawMinutes` (10-360) |
| 1-minute | min / avg / max | 24 h | `history.minuteHours` (1-72) |
| 10-minute | min / avg / max | 7 days | `history.tenMinuteDays` (1-30) - not for threads |

All in RAM, bounded by `history.maxMemoryMb` (default 28 MB: the app needs ~45 MB RSS without its history, the App Guide asks for < 75 MB): when full, series of ended processes
go first, then no new process/thread series are created (apps and system always). Processes and
threads get their own series once they have run for a minute (short-lived helpers only count for their
app); idle threads get none until they use CPU. Min/max per bucket keep short CPU peaks and the
bottom of memory saw teeth visible at any zoom. A query picks the finest tier that reaches back to
the start of the range. The analyzer's own memory therefore grows over the first hours (more
threads get series, ended processes keep theirs until they age out) until the budget is full - its
leak-table row says so ("this app · history fills its budget"); measured on the COREvirtual (40 MB
budget): 60 MB RSS after 2 min, 68.5 MB from 40 min on (23.5 MB history, 906 series) - flat for the
last 25 min of the test.

**External medium** (setting `recording.medium`, chosen in *Settings → Recording*): ctrlX OS mounts
USB sticks and SD cards under `/media/<name>` (*Settings → Storage*). Every minute the app appends
CSV rows (min/avg/max per metric) to

    <medium>/ctrlx-system-analyzer/<YYYY-MM-DD>/system.csv
                                               /events.csv
                                               /apps/<app>.csv          one row per minute
                                               /processes/<app>.csv     one row per process and minute
                                               /threads/<app>.csv       only with recording.threads

(dates and `time` local, `epoch` UTC seconds; units in the column names: `_mb_`, `_pct_`). Files are
only appended; nothing is written to the internal flash (App Guide 6.2.2). A medium that is not
mounted (yet) is retried every minute, up to 60 minutes wait in RAM. Ranges older than the RAM
history are read back from these files (1-minute resolution), also after a restart or reboot.
Unmount the medium in *Settings → Storage* before pulling it out.

## Leak analysis

For each app (or process) over the selected range: the lower envelope of the memory metric gets a
least-squares line → growth in MB/h and the fit R² (1 = perfectly steady). The envelope: per-bucket
minimum, then over a rolling window of 1/12 of the range either the minimum (the bottoms of
garbage-collector saw teeth) or the 10th percentile (growth with single deep dips, as a process that
sometimes hands memory back) - whichever gives the steadier line.
*growing* = growth ≥ `leak.minMbPerHour` (1 MB/h) and fit ≥ `leak.minFit` (0.8) over at least
`leak.minSpanMinutes` (30 min); *possible* = growth only. A restart inside the range starts the fit
anew after it. "Full in" = available system memory / growth. Thread count growth (threads/h) is
shown too (thread leaks). The Data Layer node `analysis/growing` has the *growing* apps of the last
`leak.nodeWindowMinutes` (60 min).

## Web UI

| Tab | |
|---|---|
| Overview | tiles (CPU, memory, pressure, apps, the analyzer's own load), CPU per core, system memory, the 8 largest / busiest apps, pressure, growing apps |
| Apps | table of all apps (sortable; CPU, anon, RSS, PSS, swap, growth over the range); an app: memory/CPU/count charts (optional min/max), its processes; a process: memory/CPU charts, threads with policy/priority/core, up to 8 threads charted |
| Leaks | ranked growth table (apps or processes, metric, app filter); a row: lowest values, average and the fitted trend |
| Events | restarts, stops, starts, updates, markers; add a marker |
| Settings | recording medium, status (sampling cost, RAM history, tiers), all settings |

Range bar above all tabs: 15 min … 30 days, custom from/to, drag across a chart to zoom; presets
follow "now" (refresh every 5 s for 15 min … every 4 min for 24 h). Export: apps, processes,
threads or system for the range as CSV or JSON.

## Data Layer (`ctrlx-system-analyzer/...`)

| Node | Type | |
|---|---|---|
| `status/version`, `status/state`, `status/heartbeat`, `settings/active` | | template |
| `collector/samples`, `collector/sample-cpu-ms` | uint64, float64 | the analyzer's own cost per sample |
| `system/cpu-percent` | float64 | all cores, last interval |
| `apps/summary` | string (JSON) | per app: cpu, anonMb, rssMb, pssMb, threads, processes - every sample |
| `analysis/growing` | string (JSON) | apps growing over the last `leak.nodeWindowMinutes` - every minute |
| `events/marker` | string, writable | write a text (e.g. from the PLC: "test started") to mark the charts and the recording |
| `recording/state` | string | off, waiting, recording, error |

## Cost

On the COREvirtual and the build VM see the CHANGELOG; the app shows its own CPU per sample
(*Sampling* pill, Data Layer `collector/sample-cpu-ms`). PSS is read only every 30 s because the
kernel walks the page tables for it. With threads off (`collector.threads`) a sample reads only
the processes.

## Develop

    ./run-local.sh                      # http://127.0.0.1:8080/ctrlx-system-analyzer/ - analyzes this machine
    APP_DEV_MEDIA_ROOT=/tmp/media ./run-local.sh   # folders in /tmp/media count as media
    ../../tools/check.sh .
    python3 ../../tools/ui_check.py . --narrow
    ./build-snap-amd64.sh               # / ./build-snap-arm64.sh
