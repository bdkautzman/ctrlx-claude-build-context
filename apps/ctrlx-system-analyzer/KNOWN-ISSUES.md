# Known issues and caveats - ctrlx-system-analyzer

Working document: everything that does not work as expected, works only with a workaround, is a caveat for users
or maintainers, or was not tested, with the evidence, what was tried and what is still open. Keep it current: add
new findings, mark fixed ones (don't delete them - the history helps the next analysis), and date every entry.
Generic lessons also go to `docs/ctrlx/gotchas.md` (numbers in brackets).

Written 2026-11-04, after the fact, from the app's README and CHANGELOG, the gotchas, the kit `README.md`,
`apps/README.md` and the development notes of the sessions. Nothing was re-measured or re-checked on a device for
this document. **"Not recorded" means the records don't say** - it may have been done or not.

## Test setup

| Device | Facts as recorded |
|---|---|
| **Real ctrlX CORE** | 0.1.1, 2026-10-30; 0.2.0 since 2026-11-12 (roles and UI only, "Not tested yet"). arm64, rexroth-arch01, ctrlX OS 4.6.5/4.6.6 (automationcore 4.6.5), 4 cores with apps on 0-1 [#73], cgroup v2, no `/proc/pressure` [#74], NTP inactive, clock 7 days behind [#75]. 213-215 processes, 425-437 threads. USB stick (FAT32) at `/media/sda1` [#76] |
| **ctrlX COREvirtual** | 0.1.0, 2026-10-27; 0.2.0 since 2026-11-12 (roles and UI only, "Not tested yet"). amd64, 4 cores, ctrlX OS 4.6, snapd 2.72, cgroup v1 hybrid. 176-180 processes, 744-1100 threads. It "runs 4 h at a time" |
| **Build VM** | emulated CPU [#47], 154 processes, 300 threads, 250-500 ms CPU per sample. Local runs and the unit tests |

Last recorded state. The state today was not checked.

* Real CORE: recording switched off after the test. **60 test files (2.3 MB) are left on the user's USB stick.**
* COREvirtual: last recorded version 0.1.0. Its saved settings still have the old history budget of 40 MB. The probe
  snap `ctrlx-plug-probe` from #67 was still installed (recorded 2026-10-30).

## Overview

| # | Issue | Status | Workaround / fix |
|---|---|---|---|
| 1 | Leak verdicts only from 30-60 min runs with a synthetic leak | **open** | see "Not tested yet" |
| 2 | An SD card as medium is not tested (a USB stick was) | **open** | - |
| 3 | The CORE's kernel has no pressure stall information | by design of the kernel [#74] | overview says so (0.1.1) |
| 4 | Device clock days off: every preset range was empty | fixed in 0.1.1 [#75] | "now" from the device; remaining caveat below |
| 5 | "CPU %" of the whole device understates how busy the CORE's app cores are | caveat [#73] | watch cores 0-1 |
| 6 | A sample costs ~4x more CPU on the CORE than on the COREvirtual | caveat [#68] | `collector.threads` off, longer interval |
| 7 | The analyzer's own RSS grows until its history budget is full | by design [#69] | `history.maxMemoryMb` |
| 8 | Short-lived processes made the analyzer grow 55 MB/h | fixed in 0.1.0 [#71] | own series only after >= 60 s |
| 9 | A test leak with deep dips was only "possible" | fixed in 0.1.0 [#72] | envelope with the steadier fit |
| 10 | Read back at 1-minute minimums the same leak was "possible" again (fit 0.48) | fixed in 0.1.1 [#72] | Hampel filter (0.93) |
| 11 | The analyzer's own leak row counted 0 restarts after two | fixed in 0.1.1 | own start event counted |
| 12 | `/proc/<pid>/smaps` is not readable by a strict snap | by design | `sudo cat /proc/<pid>/smaps` over SSH |
| 13 | Without `system-observe`: no PSS, I/O, pressure, AppArmor labels | caveat [#67] | the installer connects the plug, ~3 s after the start |
| 14 | PSS only every 30 s per process | by design [#68] | - |
| 15 | "Tasks" are Linux threads, no scheduler task timing | by design (user, 2026-10-27) | - |
| 16 | History is in RAM by default | by design (user, 2026-10-27) | optional recording on a medium |
| 17 | Recording: ~33 KB/min with threads; FAT32 rounds files up to its clusters | caveat [#76] | `recording.threads` off |
| 18 | `ctrlx-bmg-umc` 5.7.0 on the COREvirtual adds ~4 threads/min | finding in another app, follow-up not recorded | - |
| 19 | uPlot was vendored by hand (no Node.js in the build VM) | maintenance caveat [#70] | see below |
| 20 | Not tested yet (list below) | open | - |

## Details

### 1. Leak verdicts so far

The purpose of the app is to see a **slow** leak over a long range. All recorded verdicts are from short runs with a
test leak far above the threshold (1 MB/h):

| Device | Test leak | Verdict |
|---|---|---|
| COREvirtual, 0.1.0, 2026-10-27 | bash process, ~33 MB/h | "growing" after 30 min, process and app level, `analysis/growing` |
| Real CORE, 0.1.1, 2026-10-30 | bash process, ~34 MB/h | "growing" (fit 0.98) after 30 min, process and app level, `analysis/growing` |
| Real CORE, read back from the USB stick | the same leak | "possible" (fit 0.48), then "growing" (0.93) with the 0.1.1 filter |

### 3. Kernel differences [#74]

`/proc/pressure` is missing on the CORE's kernel; the COREvirtual has it. 0.1.1 says so on the overview instead of blaming
the `system-observe` plug (`collector.pressure`: ok / missing / unreadable).

The CORE runs cgroup v2 (`/proc/<pid>/cgroup` has only the `0::/system.slice/<unit>` line), the COREvirtual cgroup v1 hybrid
(`1:name=systemd:` line). The app has to read both.

### 4. Device clock [#75, #45]

NTP inactive: 7 days behind on 2026-10-30, 14 h on 2026-10-22. A page that computes "the last hour" from the browser's
clock gets an empty range from device-stamped data. 0.1.1: `serverTime` in `/status`, ranges from that, a note says how far
the clock is off. **Remaining caveat:** samples, the date folders and the `time` column on the medium carry the device's
clock, so they are as wrong as the clock is. The clock being set while the analyzer runs: see "Not tested yet".

### 5. Cores on the real CORE [#73]

Kernel command line `irqaffinity=0 rcu_nocbs=2-3 nohz_full=2-3`. Every normal process runs in the cpuset 0-1
(`Cpus_allowed_list: 0-1`, `nproc` says 2, `nproc --all` 4). Cores 2-3 belong to the automationcore's real-time threads
(sysHwWdg FIFO 90, schedMain FIFO 89, SystemHandlerHP RR 91, ...). `/proc/stat` lists all 4.

### 6. Cost of a sample [#68]

| Device | Processes / threads | CPU per sample | at 5 s |
|---|---|---|---|
| Real CORE, 0.1.1 | 213-215 / 425-437 | 122-136 ms (#68: 123-136 ms at 215 / 431) | 2.4-2.7 % of one core |
| COREvirtual, 0.1.0 | 176-180 / 744-1100 | 29-36 ms | 0.6 % |
| Build VM | 154 / 300 | 250-500 ms | - |

`smaps_rollup` (PSS) is the expensive file: the kernel walks the process's page tables (123 ms of a 400 ms sample in the
build VM for 22 processes), hence every 30 s. With `collector.threads` off a sample reads only the processes; that cost
is not recorded. The app shows its own cost (*Sampling* pill, `collector/sample-cpu-ms`). For the code: read `/proc` files
with one `os.read` (a second read just to see EOF doubled the syscalls) and look keys up with `bytes.find`.

### 7. The analyzer's own memory [#69]

`array` rings allocate their full capacity at once: ~760 series are 22 MB right after the start. The app needs ~45 MB RSS
without its history.

| Device | Budget | RSS |
|---|---|---|
| COREvirtual, 0.1.0 | 40 MB | 60 MB after 2 min, 68.5 MB from 40 min on (23.5 MB history, 906 series), flat for the last 25 min of the test |
| Real CORE, 0.1.1 | not recorded (default 28 MB) | 52 MB after 2 min, 57 MB after 35 min |

The default budget is 28 MB so that ~45 + 28 = ~73 MB stays below the App Guide's 75 MB. That sum is an estimate: **no
recorded test reached a full budget**. The COREvirtual's saved 40 MB would allow ~85 MB by the same sum, also not
measured. The leak table says "this app · history fills its budget" for its own row.

### 8. Short-lived processes [#71]

COREvirtual, 2026-10-27, before the fix: a test loop started `sleep 10` every 10 s. Each lived through 2 samples and got its
own series: 6 metrics x ~35 KB of rings = ~210 KB per `sleep`. The analysis flagged the analyzer itself as "growing" (anon
15 → 61 MB in 30 min), correctly; the budget would have stopped it at 40 MB. Fix: own series only for processes and threads
that have run for >= 60 s; ended processes with too little data are left out of the leak table (there were 118 `sleep` rows).

### 9-10. The envelope of the leak analysis [#72]

Test: a bash loop appending 20 KB to a string every 10 s. Not a straight line: steady growth (0.4 → 17.5 MB in 30 min,
~33 MB/h) with single samples far below (6.5 / 4.7 MB) when glibc hands freed memory back.

| Method | Data | Fit |
|---|---|---|
| rolling minimum | this leak, COREvirtual | 0.74, only "possible" |
| rolling 10th percentile | this leak, COREvirtual | 0.97 |
| rolling 10th percentile | regular GC saw teeth | 0.76 (rolling minimum: 0.996) |
| both, the steadier one (0.1.0) | 1-minute minimums read back from the stick on the real CORE, 30 points | 0.48 |
| + downward-only Hampel filter (0.1.1) | the same | 0.93 |

Hampel: value < median - 3 MAD of its 7 neighbours → median (`analysis.despike`). Saw teeth, restart drops and flat series
are unchanged. Other leak shapes: not tested.

### 12-13. What the plugs give [#67]

Tested 2026-10-27 with a throwaway probe snap on the COREvirtual, and the installer's connecting confirmed on arm64
[#74]. `snap connections` shows both plugs as `manual`, no `snap connect` needed (the user's condition for building the
app); neither is in the installer's denylist (`/snap/rexroth-deviceadmin/current/denylist.json`, 78 entries).

**Order**: the daemon starts with both plugs NOT connected; ~3 s later the installer connects them and restarts the service.
The app must not cache "no access" from its first seconds.

Readable from `/proc` of other processes without the plug: the PID list, `status`, `stat`, `statm`, `cgroup`, `cmdline`,
`task/` + `task/<tid>/stat`, `fd/` count, `/proc/stat`, `/proc/meminfo`. Only with the plug: `smaps_rollup`, `io`,
`attr/current`, `schedstat`, `/proc/pressure/*`. `/media` needs `removable-media`.

The README says the app still works without `system-observe`. The probe supports that; running the app itself on a device
without the plug is not recorded.

### 17. Recording on a USB stick [#76]

Real CORE, 2026-10-30. The stick (FAT32, mounted in Settings > Storage) appears as `/media/sda1`, named after the block
device, not the label, with `vfat` options `fmask=0022,dmask=0022`, owner root: only root may write, which snap daemons are.
`os.path.ismount` is true inside the strict snap with `removable-media`; `os.statvfs` gives size and free.

Measured with threads: ~33 KB per minute, ~47 MB per day (extrapolated). FAT32 rounds each file up to a cluster: `du` 1.3 MB
for 0.6 MB written (58 files, #76). The notes give 60 files, 2.3 MB at the end of the test. All file kinds were written;
after an app restart the range before it came back from the stick (charts, leak table, events).

The user's words for the path: `<medium root>/ctrlx-system-analyzer/`. Keep it.

### 18. `ctrlx-bmg-umc` 5.7.0 on the COREvirtual

Found on the way, 2026-10-27: ~4 threads per minute (290 → 767 threads in 2 h). This is not a problem of the analyzer, it is
something the analyzer showed. Whether it was followed up is not recorded.

### 19. uPlot [#70]

The build VM has no `node` or `npm`. uPlot 1.6.32 (MIT, 51 KB) came from the npm registry: `curl
https://registry.npmjs.org/<pkg>` gives the versions and the tarball's `dist.integrity` (sha512, base64); download the
tarball, compare the hash, copy `dist/*.min.js` + CSS into `www/vendor/`, and add a fossinfo entry with the LICENSE text. An
update of uPlot means repeating that.

### Test note

0.1.1 changed the UI scenario: on a device an app's chart may be empty.

## Not tested yet

* **0.2.0 on a device** (kit UI, three roles): **amd64 done on the COREvirtual 2026-11-12**: settings and plugs (system-observe, removable-media)
  unchanged by the update, Data Layer and log clean, 61.7 MB a few minutes after start; roles through the API with
  kit-view / kit-operate / kit-configure (settings 403/403/200, marker 403/200/200 through `/events` and through
  `PUT /values/events/marker` - four test markers "role test ..." are in the event list); `ui_check --device virtual` as
  admin and the three test users OK. The RAM history starts again at the update (RAM only, as designed).
  **arm64 on the real CORE X3 2026-11-12**: the X3 had none of the apps any more (installed fresh, `--service`, about
  200 s each; the app data of 2026-11-05 was still there and loaded); plugs connected by the installer, Data Layer and
  log clean, the same role checks through the API as on the COREvirtual all as expected, `ui_check --device x3` as admin
  and the three test users OK. `system-observe` and `removable-media` connected by the installer; 64.0 MB a few minutes after
  the start, 70.0 MB after about 50 min (history filling its budget, 7); markers per role through `/events` and
  `PUT /values/events/marker`.
* **Multi-hour and multi-day ranges**: verdicts come from 30-60 min runs; the longest figure recorded is a thread count over
  2 h on the COREvirtual. Not tested: the 1-minute and 10-minute tiers over their full lengths (24 h, 7 days), the 30-day
  range, a verdict from them.
* **A slow leak** near the threshold (1 MB/h), or a leak of a real app: the test leaks grew ~33-34 MB/h.
* **An SD card** as medium.
* **Long recordings**: 60 files in a test. Day rollover (new date folder), a stick filling up, the ~47 MB per day: not
  measured over a day.
* **A full history budget** on either device, so RSS at the default 28 MB (see 7).
* **0.1.1 on the COREvirtual**: last recorded there is 0.1.0.
* **The device clock being set while it samples or records** (date folders, ranges): not recorded. A clock set on the
  real CORE on 2026-10-31 hit a Data Layer remote (#77); the analyzer was not part of that record.
* **Read-back after a reboot**: the README says yes, the recorded test is an app restart.
* **Medium problems**: pulled or not yet mounted (retried every minute, up to 60 minutes wait in RAM): not recorded on a
  device.
* **Settings**: `collector.threads` off, `recording.threads` off, `leak.metric` rss or pss on a device: not recorded.
* **Update events** (a snap whose processes run from a new revision): not recorded on a device. Restarts were seen.
* **`events/marker` from a PLC**: Data Layer nodes checked, a PLC write not recorded.
* **Read-only user on a device**: not recorded.
* **The app without `system-observe`** (see 12-13).

## Diagnostics

From the kit root (where only the file or the value was recorded, the command is the usual form):

```bash
python3 tools/ui_check.py apps/ctrlx-system-analyzer --device x3
tools/ctrlx.py --device x3 dl-read ctrlx-system-analyzer/collector/sample-cpu-ms     # own cost per sample
tools/ctrlx.py --device x3 dl-read ctrlx-system-analyzer/analysis/growing            # growing apps, last leak.nodeWindowMinutes
tools/ctrlx.py --device x3 dl-read ctrlx-system-analyzer/recording/state             # off, waiting, recording, error
tools/ctrlx.py --device x3 dl-read ctrlx-system-analyzer/apps/summary
tools/ctrlx.py --device x3 api GET /ctrlx-system-analyzer/api/v1/status              # serverTime
tools/ctrlx.py --device x3 ssh 'snap connections ctrlx-system-analyzer'              # both plugs "manual" [#67]
tools/ctrlx.py --device x3 ssh 'nproc; nproc --all; grep Cpus_allowed_list /proc/self/status; cat /proc/cmdline'   # [#73]
tools/ctrlx.py --device x3 ssh 'stat -fc %T /sys/fs/cgroup; ls /proc/pressure; timedatectl'    # cgroup2fs, PSI, clock [#74, #75]
tools/ctrlx.py --device x3 ssh 'sudo cat /proc/<pid>/smaps'                          # where inside a leaking process memory grows
tools/ctrlx.py --device x3 ssh 'du -sh /media/sda1/ctrlx-system-analyzer'            # recording on the stick [#76]
```

Test leaks (the exact scripts are not recorded; run them on a test device only):

* **Slow leak** [#72]: a bash loop appending 20 KB to a string every 10 s. Expect ~33 MB/h in RssAnon.
* **Process churn** [#71]: a loop starting `sleep 10` every 10 s.

Locally:

```bash
cd apps/ctrlx-system-analyzer && APP_DEV_MEDIA_ROOT=/tmp/media ./run-local.sh    # folders in /tmp/media count as media
```
