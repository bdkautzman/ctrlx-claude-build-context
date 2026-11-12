# Gotchas (living document - append, never silently delete)

Format: **symptom** → cause → fix. Add the date and the source (build log, device, forum link).
Codes in brackets are `tools/ctrlx_check.py` findings that catch the problem.

**Don't read this whole file** (~17k tokens, and whatever is read stays in the session's context). Instead:
* An error text, result code or checker code at hand: `grep -n '<text>' docs/ctrlx/gotchas.md`.
* Otherwise pick the topic in the index below and read only the entries whose hint fits, e.g. entry 42:
  `sed -n '/^42\. /,/^43\. /p' docs/ctrlx/gotchas.md`.
* A new entry gets the next number at the end of the file and a `#N hint` in its topic here (skill
  `record-gotcha`); `python3 -m unittest discover -s tools/tests` checks that the index lists every entry.

## Index by topic
An entry can be under more than one topic. Kit parts still unverified on a device: "Unverified assumptions" below.

- **Names, manifest, source files** (most have a checker code): #1 CRLF line endings · #2 executable bit lost ·
  #4 manifest file name / `id` · #5 menu entries need `items` · #6 identifiers copied from samples · #8 log files in
  app data · #9 API without a permission check · #10 fossinfo format · #12 socket path too long · #13 `{$SNAP_DATA}`
  in the binding · #20 socket name vs validation · #29 executable bits after a Windows copy · #34 Settings page
  category and tile · #97 Bosch icon names
- **Build and packaging**: #3 core24 + `platforms` · #11 ctrlx-actions outdated · #14 quote `version` · #15 sub-package
  missing in the snap · #16 GLIBC error (Raspberry Pi) · #17 apt can't find ctrlx-datalayer · #19 SDK sample bugs ·
  #30 build VM facts (destructive mode) · #31 new_app copied build folders · #32 snap size (ctrlx-datalayer .deb) ·
  #33 slow checks after a build · #36 arm64 cross-build · #47 builds take ~20 min · #48 `.app` format · #49 one `.app`
  for two architectures · #63 new top-level folder not in the snap · #70 JavaScript library without Node.js · #94 Ubuntu
  packages with Python modules · #95 auto mode blocks downloaded wheels · #96 OpenCV size · #98 numpy 2.4 on the
  COREvirtual · #99 arm64 snap with amd64 packages
- **Device, install, `tools/ctrlx.py`**: #21 build VM proxy, device time-outs · #22 install needs SERVICE · #23 HTTP 502
  right after an install · #26 Logbook text search answers 204 · #27 package version field · #37 real CORE vs
  COREvirtual · #38 every URL shows the ctrlX UI (no proxy route) · #45 the COREvirtual from the LAN · #46 install
  polling disconnects · #65 snap runtime facts (time zone, `_gateway`) · #75 clock days off · #84 package ids
  `snap_<name>` · #85 uninstall keeps app data · #110 "Too many sessions"
- **Web UI, reverse proxy, roles, widget**: #9 permission check · #35 "Set" wrote the old value back · #38 no proxy
  route · #53 widget authentication · #55 read-only users and settings · #57 widget created at install · #91 big
  uploads, drain a rejected body · #102 HTTP 502 for many parallel requests
- **UI tests (Playwright)**: #51 no browser in the build VM · #52 headless Chromium setup · #54 second page in one tab ·
  #56 time-out during a build · #60 page speed in the VM
- **Settings, app data, Solutions**: #7 hard-coded solutions path · #18 app data mounted after the start · #24 load
  refused by Motion · #25 no abort after a refused query · #42 credentials in settings · #82 "Permission denied" at
  the first start · #85 app data kept after uninstall · #89 `PUT /settings` replaces all · #107 copy right after a
  save · #108 load results
- **Data Layer**: #28 CLIENT_NOT_CONNECTED at the first start · #39 sharing = remote connection · #40 remote never comes
  up · #41 remote to the device itself · #43 read-only before type mismatch · #44 ctrlX OS 3.6 node layout · #50 rejected
  write changed the value · #65 command node that rejects a value · #77 remote after a clock change · #78 result
  names differ per API · #79 client: JSON, metadata, bulk · #80 KVD restarts on `nodes.json` changes · #81 WebDAV
  ignores `If-Match` · #83 `array-of-<type>` · #86 ctrlX OS 3.6 without `system/info` · #87 empty KVD value · #88
  wildcard provider node · #90 write cost · #111 PLC types: exact node type needed
- **Sandbox, interfaces, network, USB**: #61 what the `network` plug allows · #62 neighbor table · #64 scan from the
  COREvirtual · #67 installer connects `system-observe`, `removable-media` · #74 cgroup v2 vs v1, no PSI · #76 USB
  stick · #104 network limits in the sandbox
- **CPU, memory, performance**: #58 CORE vs COREvirtual speed · #59 RSS stays up after a load · #60 measuring page
  speed · #66 network scan cost · #68 reading /proc · #69 `array` rings allocate at once · #71 memory growth from
  short-lived processes · #72 what a slow leak looks like · #73 apps get 2 of 4 cores · #90 Data Layer write cost ·
  #92 Python ints in big sets · #105 large images (malloc, resize) · #112 the template's 35 MB, part by part
- **GigE Vision cameras (Aravis)**: #93 through COREvirtual port forwarding · #100 no frame on a software trigger ·
  #101 packet loss, pacing · #103 X3 vs X7 · #104 sandbox network limits · #106 stream port cleared · #109 closing a
  lost camera blocks

## Found while building this kit (2026-10-15, from docs/samples/example repos)

1. **Daemon doesn't start, log shows `/usr/bin/env: 'python3\r': No such file or directory`**
   → files edited/checked out on Windows got CRLF line endings. Fix: LF everywhere
   (`git config --global core.autocrlf input`, `.gitattributes` with `* text=auto eol=lf`,
   `dos2unix`). [SRC-CRLF]
2. **`bin/run.sh: Permission denied`** → the executable bit was lost (Windows copy, zip).
   `chmod +x wrapper/run.sh`; in git: `git update-index --chmod=+x wrapper/run.sh`. [SRC-EXEC-BIT]
3. **Snap built for the wrong ctrlX OS** → `base: core20/core22` from old examples (all three
   community repos in `reference/example-apps` use core20/core22). ctrlX OS 4.x: `core24` +
   `platforms:` instead of `architectures:`. [SNAP-BASE, SNAP-PLATFORMS]
4. **Manifest ignored / menus missing** → manifest file not named `<snap>.package-manifest.json`,
   `id` missing or different from the snap name (seen in sqlite-ctrlx, cam-builder,
   datalayer.ab). [MANIFEST-FILENAME, MANIFEST-ID]
5. **Schema says menu entries are invalid** → every menu entry needs `"items": []`
   (schema `required: [id, title, items]`). Devices seem to tolerate the omission (community
   apps work without it) but the validation schema doesn't. [MANIFEST-SCHEMA]
6. **Copied identifiers from samples**: scope ids `rexroth-python-webserver.web.*`, manifest
   file `sdk-py-webserver.package-manifest.json`, proxy names without `.web` (in the SDK sample
   itself and in cam-builder). Rename everything with `tools/new_app.py`. [MANIFEST-SCOPES, PROXY-NAME]
7. **Hardcoded `/var/snap/rexroth-solutions/common/solutions/...`** (datalayer.ab, sqlite-ctrlx)
   → works only by accident; use `$SNAP_COMMON/solutions/activeConfiguration` via the
   `active-solution` plug. [SRC-SOLUTIONS-PATH]
8. **Log files written into app data** (datalayer.ab, sqlite-ctrlx) → flash wear (App Guide
   6.2.2) and logs get archived into configurations. Log to stdout/journald. [SRC-LOGFILE]
9. **API reachable without permission check** (cam-builder: token check commented out,
   `restricted: []`) → list `/<snap>/api` under `restricted` and check scopes. [PROXY-RESTRICTED]
10. **fossinfo format**: `package-assets.md` shows a `foss_components` example and a
    `snap/package-assets/...` location; the official schema and json-schema README use
    `components` and `package-assets/<snap>/<snap>.fossinfo.json`. Follow the schema. [FOSS-FORMAT]
11. **`ctrlx-actions` GitHub Action is outdated** (last change 2022, focal multiarch, legacy
    flags) → don't use it for core24; the kit builds locally (`build-snap-amd64.sh` / `-arm64.sh`).
12. **Unix socket path too long** → binding path ≤ 108 chars, part after `.../current/` ≤ 50.
    Snap names > 29 chars need `run/<snap>/web.sock`. [PROXY-SOCKET-LEN]
13. **`${SNAP_DATA}` in the manifest binding** doesn't resolve - the manifest syntax is
    `{$SNAP_DATA}`. [PROXY-BINDING]
14. **`version: 1.0` unquoted** is parsed as a float by YAML → always quote or use 3 parts. [SNAP-VERSION]
15. **New Python sub-package missing in the snap** → not listed in `pyproject.toml`
    `[tool.setuptools] packages`. [PY-PACKAGES]
16. **arm64 build on a Raspberry Pi / wrong env: `GLIBC_2.36 not found` against core22**
    (forum thread) → build in the ctrlX App Build Environment with the matching SDK version and
    `ctrlx-datalayer` .deb; don't mix SDK/deb/base versions.
17. **`apt` can't find `ctrlx-datalayer` during the snap build** → the local apt repo from the
    SDK .deb is not set up: `install-ctrlx-datalayer.sh <dir-with-deb>`. [ENV-DATALAYER-DEB]
18. **App starts before app data is mounted** → `$SNAP_COMMON/solutions/activeConfiguration`
    may not exist yet; wait/poll, never crash (template handles it).
19. **SDK sample bugs to not copy**: `samples-python/logbook/snap/snapcraft.yaml` has a list of
    plugs without the `plugs:` key; `appdata` sample's `ensure_storage_location` retry loop can
    never return False; samples decode JWTs without restricting the API paths.
20. **Socket naming vs. validation**: basechecks say "only web sockets (name contains `.web`)
    can be externally accessible"; SDK samples and docs use `web.sock`. Template keeps
    `web.sock` (proven); if the validation team objects, rename to `<short>.web.sock` within
    the 50-char limit and update the manifest binding.

## Unverified assumptions in this kit (confirm on first real use, then move up)

* Browser click-through on the real CORE (its REST/web API passed; the UI is the same files as on the virtual).

Verified on ctrlX COREvirtual 4.6 (2026-10-20, template 0.1.0): `tools/ctrlx.py install`
(multipart `file` + `update`, task polling), `logs` entry field names, the `<N>` syslog prefix
(warnings show as `warning` in the Logbook), and the Solutions token on `/appdata/save|load`
(save and a full load cycle pass `auth.require(write=True)`). Browser click-through by the user
(2026-10-21, 0.1.1/0.1.2): sidebar/overview open the UI with the token, settings and Data Layer
values can be edited and saved, a read-only user has no write access, no Settings-page tile.

## Found during development
<!-- Append here: date - symptom - cause - fix - (check code if you added one) -->

First device test, 2026-10-20 (App Build Environment VM → ctrlX COREvirtual 4.6, port forwarding):

21. **`tools/ctrlx.py`/curl time out against the device** → the App Build Environment sets
    `http(s)_proxy=http://10.0.2.2:3128` (`/etc/environment`); that proxy can't reach the device.
    `ctrlx.py` now ignores proxies; curl needs `--noproxy '*'`. Use `10.0.2.2:8443` (the tool's
    default): the Windows host's LAN IP worked at first, then stopped answering from the VM.
    SSH to the COREvirtual: `ssh -p 8022 <user>@10.0.2.2` (port 10022 is the build VM itself).
22. **Install fails: HTTP 409 `system must be in state 'SERVICE' to execute this task`** → ctrlX OS
    4.6 installs apps only in SERVICE. Writable node `scheduler/admin/state`
    (`{"state": "SERVICE|SETUP|OPERATING"}`). Fix: `tools/ctrlx.py install --service` (switches and
    restores), `tools/ctrlx.py state`.
23. **HTTP 502 Bad Gateway for a few seconds right after an install** (reverse proxy reloads the new
    proxy mapping); reading the error body may even raise `IncompleteRead` → retry; `ctrlx.py`
    does so when it restores the state.
24. **Solutions load refused in phase `query` with 409 from `rexroth-motion`** ("Cannot modify motion
    object for loading configuration in Running State") → loading a configuration needs SERVICE,
    like installing. Not an app problem; our app answered the query with 204. An X3 without Motion loaded in OPERATING (#108).
25. **No `abort` after a refused `query`** (persistdata.md: "the load operation is canceled") → an
    app that pauses its file watcher in `query` must not wait for `abort`. Template: pause from
    `query` expires after 60 s, `prepare`/`load` extend it (`LOAD_QUERY_TIMEOUT_S`). A load that goes
    ahead calls our app with `query, prepare, load, validate, activate`, then `save` (same id).
26. **Logbook `search_text` always answers 204 No Content** (plain and wildcard text) → filter by
    `unit=snap.<snap>.<app>.service` (works) and search client-side: `ctrlx.py logs --snap/--search`.
27. **Package Manager list has no top-level `version`** → it is `release.version`; state in
    `health.status`. Data Layer REST reports `float64` nodes as type `double` (writes with
    `float64` are accepted).
28. **First start: `Data Layer provider start failed: Result.CLIENT_NOT_CONNECTED`** → the content
    interfaces are connected (by Device Admin) after the first start; snapd restarts the service
    and the provider comes up. Also on every SERVICE↔OPERATING switch the provider disconnects
    and reconnects within seconds. Harmless as long as the app retries (template does). Since template 0.3.0 this is
    logged as INFO; ERROR only when the Data Layer is still missing after 60 s (`DownTimeLog`) - before, every kit app
    put an error line into the Logbook at every boot.
29. **`tools/tests` `test_template_passes` fails with `SRC-EXEC-BIT`, `tools/check.sh: Permission denied`**
    after the Windows transfer → `bash tools/fix-permissions.sh` (see #2); in git the restored
    bits show up as mode changes - commit them so a fresh clone keeps them.
30. **Build environment facts**: the App Build Environment sets `SNAPCRAFT_BUILD_ENVIRONMENT=host`
    (destructive mode, no LXD; the warning "destructive mode as a non-super user" is expected);
    that is also why the local `file:` apt repo with `ctrlx-datalayer` works. The python plugin's
    "Python interpreter not found in payload." is normal: `bin/python3` links to core24's
    `/usr/bin/python3.12`. A first build took ~25 min. snapcraft lint warns that `contact` and
    `license` are missing - set them per app.
31. **`tools/new_app.py` after a template build** copied `parts/ stage/ prime/` into the new app →
    fixed (skips build output); new apps also start at version 0.1.0 whatever the template's version.
32. **Snap 35 MB (149 MB unpacked) for a few KB of app code** → the `ctrlx-datalayer` .deb stages
    `libcomm_datalayer` for amd64 AND arm64, a 36 MB static lib, headers and `dl_compliance` /
    `mddb_compiler`; the python plugin adds pip/setuptools/wheel/packaging. The SDK samples ship
    all of it. Template 0.1.1: `prime:` filter + `override-prime` (keeps only
    `usr/lib/${CRAFT_ARCH_TRIPLET_BUILD_FOR}/libcomm_datalayer.so*`) → 9.4 MB, runs unchanged.
    `foss_from_prime.py` listed the app's own package and `packaging` as FOSS components → it now
    skips the app itself and the venv tooling.
33. **`tools/check.sh` / checker self-tests very slow after a build** (self-tests > 2 min instead
    of 20 s) → `parts/ stage/ prime/` hold ~11 000 files; the checker globbed them before
    filtering and the self-test copied them per test. Both now skip build output.

Browser click-through by the user, 2026-10-21 (template 0.1.1):

34. **Settings page shows a category "core.settings.categories.undefined" with a tile that does
    nothing** → (from the device UI code, rexroth-deviceadmin 4.6) the page groups `menus.settings`
    and `menus.system` entries by `category` (missing → "undefined"; translated only: apps, device,
    system, security, network, written `0_apps` ...), and builds tiles from the entry's URL only.
    An entry with `target: "_blank"` gets a click callback instead of a URL, which the sidebar
    handles but the Settings page ignores. Fix: no `target` (same tab) or `embedded`, plus a valid
    `category`; the template dropped its settings entry. [MENU-SETTINGS]
35. **Editing a Data Layer value in the template UI and clicking "Set" restored the old value** →
    `www/app.js` added the Edit handler with `addEventListener` and later set `btn.onclick` for
    "Set"; a click ran both, `startEdit` first, which rebuilt the field with the old value. Enter
    in the field worked, so a headless check that used Enter missed it. Fix: one `onclick` slot,
    and re-check `editing` after the refresh request. Lesson: when testing a UI, click the buttons.

Real ctrlX CORE (arm64, ctrlX OS 4.6, rexroth-arch01-*), 2026-10-21 (template 0.1.2):

36. **arm64 cross-build works as-is** (for Architecture: all stage-packages and pure-Python wheels only - #99) in the App Build Environment (multiarch apt sources from the
    SDK setup script; the `ctrlx-datalayer` .deb is `Architecture: all`; the prime filter keeps
    `usr/lib/aarch64-linux-gnu/libcomm_datalayer.so*`). 8.4 MB; no "Unable to determine library
    dependencies" warnings this time; same `contact`/`license` lint warnings as amd64.
37. **Real-device differences**: `GET /package-manager/api/v1/settings` returns `{}` while "unknown
    sources" is off (the key is omitted, not `false`); the install took 2 min 40 s over the LAN
    (COREvirtual: ~50 s); the Logbook shows "System integrity degraded - Reason: UNSIGNED_APPS" for
    our unsigned snap (expected). The app itself (Data Layer, reconnects) behaves as on the virtual.
38. **App installed fine, Data Layer OK, but every `/<snap>/...` URL returns the ctrlX UI's
    `index.html` (HTTP 200, "Loading...") - no reverse-proxy route** → Device Admin's
    `proxy-monitor` logged `Failed to load caddy config: ... dial unix /tmp/caddy.admin.sock: no such
    file or directory`. The device had been up 33 days; `systemd-tmpfiles` cleaned Device Admin's
    private `/tmp` (`/tmp/snap-private-tmp/snap.rexroth-deviceadmin/tmp/caddy.admin.sock`) that
    morning. Existing routes keep working, new apps get none. A ctrlX OS issue, not the app's.
    Check: `sudo journalctl | grep "Failed to load caddy config"`. Fix: restart
    `rexroth-deviceadmin.proxy` (+ `.proxy-monitor`) or reboot - it's the user's device, ask first.
    Confirmed: after a reboot the socket was back and the app's route worked (no reinstall needed).

App `ctrlx-core-monitor` (Data Layer remote connections), 2026-10-21:

39. **"Shared Data Layer" between devices = Data Layer remote connection** (`devices/remotes/<name>`),
    not `datalayer/reverse-connections` (that one publishes *this* device on the other one). Details
    and verified nodes: `datalayer-python.md` → "Remote connections".
40. **Remote connection never comes up, Logbook: "Data layer remote connection could not be
    established - Connect error: DL_SEC_UNAUTHORIZED"**, although the same user/password works
    for REST and for a direct client from a PC; the other device logs no login at all → the TLS
    check of its self-signed certificate fails. Fix: append `NoCheckCertificate=true` to the
    address (`...?sslport=443&NoCheckCertificate=true`). Not mentioned in the type description
    (`types/datalayer/remoteconfig`), only in Bosch's how-to "Data exchange between controls".
41. **A remote to the device itself (`tcp://...@127.0.0.1`) failed the same way** - tested only
    without `NoCheckCertificate`; use a second device for tests.
42. **Settings with credentials**: the template's generic rules now support `items` (arrays of
    objects, validated per element), `pattern` (regex) and `secret` (masked in every API response
    and Data Layer node; the masked value sent back by the settings form keeps the stored one).
    Never put a default login into the rules (`SRC-CREDENTIALS` also flags a default user name).
43. **Wrong-typed write to a read-only node answered `DL_TYPE_MISMATCH`** - the provider glue
    checked the type first. Now read-only is reported first (`DL_PERMISSION_DENIED`).
44. **Version info missing for a ctrlX OS 3.6 device** (read through a remote connection) → node
    layout differs: 3.6 has no `system/info/ctrlxOsVersion` (`osVersion` = Ubuntu Core "22") and
    names installed apps `system/apps/installed/snap_<name>` (4.6: `<name>`). Read the OS version
    from `ctrlxOsVersion`, else from major.minor of the Device Admin version
    (`.../snap_rexroth-deviceadmin` → `release.version`, e.g. 3.6.8). Metrics and
    `system/info/{hostname,architecture,uptime}` exist on both.
45. **Watching the COREvirtual from a real device on the LAN**: address the Windows host
    (e.g. 192.168.1.211) with `sslport=8443`; ctrlX WORKS' port forwarding also passes the Data
    Layer ports 2069/2070 unchanged, so `tcp://<user>:<pw>@<windows-ip>?sslport=8443&NoCheckCertificate=true`
    works (verified 2026-10-22 from the real CORE). A CORE that was powered off came back with its
    clock 14 h behind (`timedatectl`: NTP inactive, not synchronized); remote connections in both
    directions still worked, but its Logbook timestamps are wrong - set time/NTP in Settings → Date & Time.
46. **`tools/ctrlx.py install` crashed with `http.client.RemoteDisconnected` while polling the
    install task** of an app *update* (the app itself was installed fine) → the reverse proxy
    reloads during the update and drops the connection; only `URLError` was handled. Fixed: such
    errors become a clean error and the task polling retries.
47. **Snap builds take ~20 min in the App Build Environment** → the VM runs as QEMU **TCG** (software
    emulation, CPU "QEMU TCG CPU"; `systemd-detect-virt` = qemu, not kvm): pure-Python work is
    ~30-50x slower (10 M loop 26 s, `python3 -m venv` 96 s). Hardware acceleration was disabled on
    purpose (problems running Claude Code inside the VM with it). More RAM/cores barely help:
    snapcraft runs single-threaded (one core at ~100 %, 2.8 GB of 3.9 GB free).
    Measured with ctrlx-core-monitor (amd64): full build 19 min (clean 1 min, venv + pip ~8 min,
    30-60 s of snapcraft overhead per step for each of the 4 tiny dump parts, xz ~1 min);
    incremental build after touching only `www/style.css`: 12.8 min; incremental after editing
    files that most parts depend on: 21.9 min (slower than clean: re-running a step first cleans
    its old output). Why incremental helps so little: the `app` part's `source: .` is the whole
    app folder, so ANY change rebuilds the Python venv. Fast inner loop: `run-local.sh` against the
    device's Data Layer (seconds, see build-deploy-test.md §1); build snaps to integrate/hand over.
    **Fix (template 0.1.4, measured 2026-10-22 with ctrlx-core-monitor 0.1.2, amd64):** Python package
    in `src/` (part `app`: `source: src`), one dump part `files` instead of four, `python-packages: []`
    (no pip/setuptools/wheel upgrade in the venv, ~2 min), incremental `tools/build-snap.sh`:
    full build 16.7 min (was ~20), nothing changed 3.0 min, UI change 4.6 min (was 12.8),
    Python change 13.3 min (was ~20). A Python rebuild is still dominated by venv + pip (~5 min) and
    re-staging thousands of files (~4 min). Snap content is identical to the old layout.

Packaging, 2026-10-22:

48. **`.app` packages (one file for all architectures)** = uncompressed **tar** with
    `public/snaps/<arch>/release/<snap>_<version>.snap` (+ `<snap>_<version>.signature` for signed
    apps); the architecture is in the path, not in the snap file name (SDK `scripts/app-signing-tool.sh`,
    upstream `appsigning-oem.md`). An **unsigned** `.app` (only the amd64 snap, no `.signature`) was
    accepted by `POST /package-manager/api/v1/packages` on the COREvirtual 4.6 with "unknown sources"
    allowed and updated the app. Two architectures in one file: verified, #49.
49. **One unsigned two-architecture `.app` installs on amd64 and arm64** (verified 2026-10-22,
    ctrlx-core-monitor 0.1.2, `tools/make-app.sh`, 17.5 MB): the same file went through
    `tools/ctrlx.py install --service` on the COREvirtual (update 0.1.2 → 0.1.2, new revision, 35 s)
    and the real CORE (update 0.1.1 → 0.1.2, 1 min 40 s). Each device installed the snap of its own
    architecture: the installed `/var/lib/snapd/snaps/<snap>_x<N>.snap` has the sha256 of the amd64
    resp. arm64 member, `uname -m` x86_64 / aarch64, the app's API worked on both. The package manager
    extracts the matching snap and installs it (`snap changes`: `Install "<snap>" snap from file
    "upload<random>.snap"`; a plain `.snap` upload shows `"upload.snap"`). The whole file is uploaded,
    so a `.app` costs both snaps' upload time.

App `ctrlx-amr-fleet` (AMR positions as a JSON string node, map view), 2026-10-22:

50. **A Data Layer write that the app rejected in `on_write` still changed the node value** (a read
    returned the invalid JSON) → the template's `NodeRegistry.write` stored the value first and called
    `on_write` afterwards, so `on_write` could only react, not veto. Fixed in template 0.1.5: `on_write`
    runs before storing; raising `NodeWriteError` rejects the write (`DL_INVALID_VALUE`) and keeps the
    old value. Apps created earlier (`ctrlx-core-monitor`) keep the old order - fine as long as their
    `on_write` never rejects. Test: `test_on_write_can_reject`.
51. **No browser to test a web UI in the App Build Environment** (no Chrome/Firefox; Playwright's
    Chromium needs atk, cups, gbm, pango, cairo, ... = system packages via sudo apt; Claude in Chrome
    may not be connected). Workaround without system changes: run `www/app.js` in QuickJS
    (`pip install quickjs` in a throw-away venv) with DOM/canvas stubs against the real web server -
    `apps/ctrlx-amr-fleet/tests/ui/`. Caught no layout problems, but every script path ran
    (pan, pinch, lock, settings form). Pitfall: a canvas page that only redraws on change shows no
    draw calls on a virtual clock once the animation has finished - collect them while data arrives.
    **Update 2026-10-23:** replaced by a real headless browser (#52); the QuickJS harness was removed.
    The library list above was a guess - the headless shell only lacks the nine libraries in #52.

UI tests with Playwright, 2026-10-23 (decision: real browser instead of QuickJS, screenshots Claude can read):

52. **Headless Chromium in the App Build Environment** - `tools/setup-ui-test.sh` + `tools/ui_check.py`.
    * Playwright 1.64.1 from PyPI in a venv (`~/.cache/ctrlx-kit/uivenv`, no root), only the headless shell
      (`playwright install --only-shell chromium`, 278 MB in `~/.cache/ms-playwright`, 1.5 min download).
    * `ldd` showed nine missing libraries: libXcomposite, libXdamage, libXfixes, libXrandr, libasound,
      libatk, libatk-bridge, libatspi, libgbm - and the VM had **no fonts** (text = boxes). One
      `sudo apt-get install --no-install-recommends` of ten Ubuntu 24.04 packages (`t64` names!) = 25 packages,
      42 MB. Playwright's own `install-deps chromium` wants 74 packages incl. an X server: not needed headless.
    * Speed (emulated CPU, #47): browser start ~12 s, page load to data 9 s locally / 18 s on the COREvirtual
      through the reverse proxy, one screenshot 4-10 s, a whole scenario ~45-75 s. Too slow for every
      `check.sh` run -> `check.sh --ui` and an explicit step after UI changes.
    * `wait_for_load_state("networkidle")` returned before the map tiles were loaded: a page that polls
      every 250 ms is never idle for 500 ms. Fix: `<body data-ui-ready>` set by the page itself
      (web-ui-and-reverse-proxy.md); `ui_check.py` waits two animation frames, then for "true".
    * A venv's `bin/python` is a symlink to the system interpreter: a "re-exec with the venv's Python"
      guard must compare `sys.prefix` with the venv, not `realpath(sys.executable)` (that never re-executed).
    * Device mode works with the token from the identity manager in the URL (`/<snap>/?token=...`, like the
      menu link) and `ignore_https_errors` for the self-signed certificate; `--no-proxy-server` because the
      VM sets `http(s)_proxy` (#21).

App `ctrlx-amr-fleet` 0.2.0 (styling, read-only users, home screen widget), 2026-10-23:

53. **How does a home screen widget authenticate?** Not in the SDK docs. The ctrlX UI (rexroth-deviceadmin
    4.6) stores the login token in `sessionStorage["idm_token"]`; Bosch's widget bundles
    (`/automationcore/widgets.js`, `/motion/widgets.js`, `/opcua-client/widgets.js`) read it there and send
    `Authorization: Bearer`. Widgets share the ctrlX UI's origin, so the app's restricted `/api` works
    directly. Details: web-ui-and-reverse-proxy.md → "Home screen widgets". (Found by reading the bundles;
    the widget itself was verified on a real home screen later the same day: #57.)
54. **UI test pitfalls with a second page in the same tab**: Playwright `page.set_content()` replaces the
    document but keeps the window - the old page's `setInterval`/`requestAnimationFrame` keep running and
    throw "Cannot read properties of null" on the new document. An SVG document can't be rewritten at
    all ("Only HTML documents support open()"). Use another tab (`ui.new_page()`) and serve the test page
    on the app's origin with `page.route(url, route.fulfill(...))`.
55. **Read-only users and settings**: the template lets `.r` users read `/settings` (shown disabled). An
    app that must hide settings from them also has to close `/settings/meta` and `/values` (the latter
    contains `settings/active`) and give the page what it needs through its own endpoint
    (`ctrlx-amr-fleet`: `/view-config`). The Data Layer node `settings/active` follows the ctrlX user's
    Data Layer permissions, not the app's scopes.

56. **`ui_check.py` failed with "Page.goto: Timeout 30000ms exceeded"** while a snap build ran in the
    background (emulated CPU, #47, both single-threaded on one core) - the same run passed before and after.
    Playwright's default timeout is 30 s; `ui_check.py` now uses 90 s and reports any browser step failure as
    a problem with a `-failure.png` screenshot instead of a traceback.
57. **Home screen widget verified on the COREvirtual (2026-10-23, ctrlx-amr-fleet 0.2.0)**: right after the
    install the widget was already on the user's home screen - ctrlX OS creates new widgets initially
    (manifest `noInitialCreation: true` prevents it). In the widget, `sessionStorage["idm_token"]` was
    set and the API answered; "Open" (`window.open("/<snap>/?token=" + idm_token)`) started the app
    logged in. Testing the real home screen headless in the emulated build VM: the ctrlX UI is a heavy
    Angular page - screenshots need `animations="disabled"` and minutes of timeout, and a widget that
    replaces its rows every poll made Playwright's `locator.wait_for` time out (element detached
    between checks) - wait with `page.wait_for_function` instead, and don't churn the DOM (fixed in
    the widget: rows are rebuilt only when something visible changed).
58. **How much slower is a real ctrlX CORE than the COREvirtual for a Python app?** Measured with
    ctrlx-amr-fleet 0.2.2 (2026-10-23; CORE = ARM, 2 cores for apps (4, see #73), rexroth-arch01; COREvirtual on a Windows PC,
    4 cores): parsing a 184k-line map 5.0 s vs 0.8 s (~6x), CPU for the same simulation load 25 % vs 5 %
    of a core (~5x). The emulated build VM is another ~6x slower than the CORE (~35x vs the COREvirtual).
    Plan CPU-heavy work (big files, per-cycle loops) for the CORE, not for the COREvirtual. SSH CPU/RSS of
    an app: `systemctl show -p MainPID --value snap.<snap>.<app>.service` (the template's app is called
    `service`: `snap.<snap>.service.service`), then `/proc/<pid>/stat` fields 14+15 and `VmRSS`;
    `apps/ctrlx-amr-fleet/scripts/perf_test.py` does it.

59. **A Python app's memory (RSS) stays up after a big load** (ctrlx-amr-fleet 0.2.4 on a CORE: 62-63 MB
    instead of 49-51 MB after a faster map parser, 2026-10-24). Python keeps the memory of millions of
    short-lived small objects (lists of tuples/strings/floats) for reuse - RSS does not go back down. Keep
    the *peak* low: parse big files in chunks (a regex `findall` over a 184k-line file peaked at 39 MB,
    256 KB chunks at 7 MB), work on `bytes`/`array` instead of sets of tuples. Measuring: `tracemalloc`
    shows peaks per allocation but slows allocation-heavy code ~20x (51 s instead of 2.3 s) - time
    without it; `f.read(limit)` appears in tracemalloc as the full `limit` (64 MB) although on Linux only
    the bytes read become resident - check the size with `os.fstat(f.fileno()).st_size` instead. Compare
    on the device: `VmRSS` before/after (`scripts/perf_test.py` in ctrlx-amr-fleet), `VmHWM` = peak.
    Result on the CORE (0.2.5 fixes, in 0.3.0): 55-58 MB instead of 62-63, peak 60 MB instead of 82 MB.
60. **Measuring a web page's speed in the emulated build VM: single runs mislead** (2026-10-24,
    ctrlx-amr-fleet). Headless Chromium there has no GPU and shares the TCG-emulated CPUs: the same
    page gave 4.1 fps one day and 2.5 the next, and a "locked onto an AMR" view 1.0 / 2.4 / 14 fps
    depending on whether that AMR was moving (a still view reuses the cached map layer). A regression
    blamed on 0.3.0 from such runs turned out to be the VM. Compare page versions A/B in alternating
    rounds in one session against the same data (`apps/ctrlx-amr-fleet/scripts/browser_ab.py`: serves
    each version's www/ via Playwright `page.route`, reads CDP `Performance.getMetrics`) and look at the
    browser's own times (`LayoutDuration`, `RecalcStyleDuration`, `ScriptDuration`), not only fps. Found
    that way: a list rewritten on every poll costs layout even when the values are the same - write only
    changes and give off-screen items `content-visibility: auto` (layout 190 -> 18 ms/s at 200 rows).

App `ctrlx-network-scanner` (network inventory: discovery, ports, services, PDF reports), 2026-10-25:

61. **What a strict snap with only the `network` plug can do on the network** (read from the AppArmor and
    seccomp profiles of an installed kit app on the COREvirtual 4.6, `/var/lib/snapd/apparmor/profiles/snap.<snap>.*`
    and `/var/lib/snapd/seccomp/bpf/*.src`): AppArmor allows `network inet/inet6 stream|dgram` and
    `network netlink raw|dgram`; seccomp allows `socket AF_NETLINK - NETLINK_ROUTE`. So TCP connects, UDP
    (also multicast/broadcast sends) and **rtnetlink dumps** work: interfaces/addresses (RTM_GETADDR), routes
    (RTM_GETROUTE) and the kernel's **neighbor (ARP) table** (RTM_GETNEIGH) - stdlib `socket.AF_NETLINK`,
    `apps/ctrlx-network-scanner/src/app/netinfo.py`. Not possible: raw and packet sockets (no `network inet raw`,
    no `network packet` rule; `network-control` is denied for ctrlX apps, reserved-interfaces doc), and
    **unprivileged ICMP ping**: `net.ipv4.ping_group_range` is `1 0` on the COREvirtual AND the real CORE, which
    excludes the snap daemon's gid 0. Consequences for a scanner: no ping, no SYN scan, no OS fingerprinting.
    What works instead: host discovery by sending one empty UDP datagram per address (the kernel ARPs for it)
    and reading the neighbor table (directly connected networks), TCP connect "pings" (accepted or refused =
    up), and a *connected* UDP socket reports an ICMP port unreachable as `ECONNREFUSED` (UDP "closed").
62. **Filling the kernel's neighbor table**: every address probed on a directly connected network gets an
    entry (INCOMPLETE -> FAILED for silent ones); `net.ipv4.neigh.default.gc_thresh3` is 1024 on ctrlX OS. Above
    it new entries fail ("neighbour table overflow") - for the whole device, also real-time traffic of other
    apps. The scanner probes local networks in batches of 128 and waits ~2 s per batch so the kernel resolves
    or fails them before the next batch.
63. **A new top-level folder of an app is not in the snap** unless it is in the `stage:` list of the `files`
    dump part (here `data/oui.tsv.gz`, the MAC vendor list - the lookup would have failed silently on the
    device). Check: `PART-STAGE-DIR` (warning). `tools/ui_check.py` copies `data/` for local runs now.
64. **What a scan from inside the COREvirtual sees** (ctrlX WORKS, QEMU user networking; verified 2026-10-25):
    `10.0.2.2` (gateway) is the Windows PC - TCP connections to it land on the PC's own services, including
    the ports the PC forwards back into the COREvirtual (2069/2070 Data Layer, 8443 web with the
    COREvirtual's certificate), so the device partly sees itself there. `10.0.2.3` (QEMU DNS) answers ARP
    but never sends ICMP port unreachable: every UDP port of it is "open|filtered". A second address on
    eth0 (192.168.2.19/24 here) has nobody else on its network. The device's own addresses answer SSDP
    (its UPnP "DeviceDiscovery" service) but never appear in its own neighbor table - mark them as up directly.
    Test real discovery on a real CORE/LAN.
65. **Small runtime facts of a strict snap on ctrlX OS 4.6**: the snap sees the device's time zone
    (`datetime.astimezone()` gave CDT, like the host - `/etc/localtime` comes from the host); reverse DNS of
    the default gateway returns systemd-resolved's synthetic name `_gateway` (filter names starting with `_`);
    a Data Layer string node used as a command (`scan/start`) that rejects a value in `on_write` answers a REST
    write with HTTP 400 `DL_INVALID_VALUE` and keeps its old value, as designed (#50).
66. **Cost of a network scan on a real ctrlX CORE** (arm64, 2 cores for apps (4, see #73); ctrlx-network-scanner 0.1.0, 2026-10-25):
    "Quick inventory" of a /24 (254 addresses, 4 hosts up, ~75 common TCP ports, 100 attempts/s, 32 parallel)
    took 15 s and 588 connection attempts: neighbor-table discovery ~10 s (2 ARP batches of 128 + 2 s wait
    each), port scan ~5 s. App process: CPU max 9.7 %, average 4.7 % of one core; RSS 38.7 -> 42.2 MB.
    No "neighbour table overflow" in dmesg; the neighbor table was back to its 3 entries right after. The
    same profile on the COREvirtual without the "no TCP ping for addresses that did not answer ARP" rule
    took 77 s for 508 addresses (2.4 k TCP attempts in discovery alone), with it 20 s (767 attempts, same
    4 hosts): on directly connected networks ARP decides, TCP pings are only for routed targets. History: ~1.5 KB per scan (gzip JSON).

Probe for `ctrlx-system-analyzer` (per-app/process/thread CPU and memory), 2026-10-27:

67. **The ctrlX installer connects `system-observe` and `removable-media` of an unsigned strict snap itself**
    (COREvirtual, ctrlX OS 4.6, snapd 2.72; throwaway probe snap installed with `tools/ctrlx.py install`,
    i.e. the package-manager REST API the web UI uses). `snap connections` shows both as `manual`, like the
    content interfaces of the other apps; no `snap connect` needed. Neither is in the installer's plug
    denylist (`/snap/rexroth-deviceadmin/current/denylist.json`, 78 entries). Order: the daemon starts with
    both plugs NOT connected, ~3 s later the installer connects them and the service is restarted - so an
    app must not cache "no access" from its first seconds. What a strict root daemon reads from `/proc` of
    *other* processes (automationcore, PID 1), without -> with `system-observe`: always readable: the `/proc`
    PID list, `status` (VmRSS/RssAnon/VmHWM/Threads), `stat`, `statm`, `cgroup` (`name=systemd` line =
    `snap.<snap>.<app>.service`), `cmdline`, `task/` + `task/<tid>/stat`, `fd/` count, `/proc/stat`,
    `/proc/meminfo`; only with the plug: `smaps_rollup` (PSS), `io`, `attr/current` (AppArmor label),
    `schedstat`, `/proc/pressure/*`. `/media` (removable media mount points) needs `removable-media`.
    ctrlX OS 4.6 runs cgroup v1 (hybrid); the Data Layer has only system-wide metrics
    (`framework/metrics/system/*`, `system/resources/{cpu,memory}`) and the automationcore's own threads
    (`framework/metrics/cpu-load`, last 50 s).

App `ctrlx-system-analyzer` (CPU/memory history per app, process and thread; leak analysis), 2026-10-27:

68. **Cost of reading /proc for every process and thread in Python** (collector of ctrlx-system-analyzer
    0.1.0, sample = /proc/stat + meminfo + pressure, per process stat + status, per thread task/<tid>/stat):
    COREvirtual with 177-179 processes / 744-755 threads: 29-36 ms CPU per sample (0.6 % of one core at
    5 s); the emulated build VM with 154 processes / 300 threads: 250-500 ms. `smaps_rollup` (PSS) is the
    expensive file - the kernel walks the process's page tables (123 ms of a 400 ms sample in the build
    VM for 22 processes) - so PSS is read only every 30 s per process. Read /proc files with one
    `os.read` (stop when a read returns less than asked: a second read just to see EOF doubled the
    syscalls) and look keys up with `bytes.find` instead of splitting `status` into lines.
    Real CORE (arm64, 215 processes / 431 threads, 2026-10-30): 123-136 ms CPU per sample = 2.7 % of
    one core at 5 s - ~4x the COREvirtual.
69. **Python `array` rings allocate their full capacity at once** → a history of ~760 series (raw 2 h at
    5 s + 1-minute 24 h + 10-minute 7 days, min/avg/max) is 22 MB right after the start, and the app's
    RSS is 60 MB from the first minutes (ctrlx-system-analyzer on the COREvirtual). App Guide 6.2.1 asks
    for < 75 MB RAM: the arrays have a budget setting (`history.maxMemoryMb`, default 28 MB: the app
    itself needs ~45 MB RSS; measured flat at 68.5 MB with 23.5 MB of history after 40 min) that drops
    ended processes' series first. Short-lived processes would otherwise grow it without bound.
70. **Vendoring a JavaScript library without Node.js** (the build VM has no `node`/`npm`): the npm
    registry is reachable through the VM's proxy - `curl https://registry.npmjs.org/<pkg>` gives the
    versions, publish dates and the tarball's `dist.integrity` (sha512, base64); download the tarball,
    compare the hash, copy `dist/*.min.js` + CSS into the app's `www/vendor/` and add a fossinfo entry
    with the LICENSE text (uPlot 1.6.32, MIT, 51 KB, for ctrlx-system-analyzer). The static file
    server serves subfolders of `www/` as they are.
71. **Short-lived processes made the analyzer's own memory grow 55 MB/h** (ctrlx-system-analyzer 0.1.0
    before the fix, COREvirtual, 2026-10-27): a test loop started `sleep 10` every 10 s; each lived
    through 2 samples (5 s interval) and got its own history series - 6 metrics x ~35 KB of
    preallocated rings (#69) = ~210 KB per `sleep`. The leak analysis flagged the analyzer itself as
    "growing" (anon 15 -> 61 MB in 30 min) - correct, the budget would have stopped it at 40 MB. Fix: own
    series only for processes/threads that have run for >= 60 s (shorter ones count for their app);
    ended processes with too little data are left out of the leak table (there were 118 `sleep` rows).
72. **What a real slow leak looks like in RssAnon** (test on the COREvirtual: a bash loop appending 20 KB
    to a string every 10 s): not a straight line and not a regular saw tooth - steady growth (0.4 ->
    17.5 MB in 30 min, ~33 MB/h) with single samples far below (6.5 / 4.7 MB) when glibc hands freed
    memory back. A rolling-minimum envelope followed those dips (fit R^2 0.74, only "possible"); a
    rolling 10th percentile ignores them (0.97) but is worse for regular GC saw teeth (0.76 vs 0.996).
    The analysis now fits both and uses the steadier one (ctrlx-system-analyzer `analysis.steadiest`).
    At 1-minute resolution (read back from a USB stick on the real CORE, 2026-10-30) that was not
    enough: every minute's MINIMUM catches the dip of that minute, a dip about every 10 min, and a 30-
    point range leaves a 2-point rolling window (fit 0.48). Fix: a downward-only Hampel filter first
    (value < median - 3 MAD of its 7 neighbours -> median; `analysis.despike`): 0.93; regular saw teeth,
    restart drops and flat series are unchanged.

Real ctrlX CORE (arm64, rexroth-arch01, ctrlX OS 4.6.5/4.6.6), ctrlx-system-analyzer 0.1.0/0.1.1, 2026-10-30:

73. **The CORE has 4 cores; apps get 2 of them.** Kernel command line `irqaffinity=0 rcu_nocbs=2-3
    nohz_full=2-3`; every normal process runs in the cpuset 0-1 (`Cpus_allowed_list: 0-1`, so `nproc` in an
    SSH shell says 2, `nproc --all` 4). Cores 2-3 belong to the automationcore's real-time threads
    (sysHwWdg FIFO 90, schedMain FIFO 89, SystemHandlerHP RR 91, ...). `/proc/stat` lists all 4 cores, so a
    "CPU %" of the whole device understates how busy the app cores are: watch cores 0-1.
74. **Real CORE vs COREvirtual kernel**: the CORE runs cgroup v2 (`systemd.unified_cgroup_hierarchy=1`,
    `/sys/fs/cgroup` is cgroup2fs; `/proc/<pid>/cgroup` has only the `0::/system.slice/<unit>` line), the
    COREvirtual cgroup v1 hybrid (`1:name=systemd:` line) - read both. The CORE's kernel has no pressure
    stall information at all (`/proc/pressure` missing; the COREvirtual has it). Both: snapd connects
    `system-observe` / `removable-media` of an unsigned snap through the installer (#67 holds for arm64).
75. **The CORE's clock can be days off** (NTP inactive: 7 days behind on 2026-10-30, 14 h on 2026-10-22,
    #45). A web page that computes "the last hour" from the browser's clock and asks the app for
    device-stamped data gets an empty range. Let the server say what time it is (`serverTime` in the
    status response) and compute ranges from that (ctrlx-system-analyzer 0.1.1); show times as the
    device's and say how far its clock is off.
76. **A USB stick on the real CORE** (FAT32, mounted in Settings > Storage, 2026-10-30): it appears as
    `/media/sda1` - named after the block device, not the stick's label - with `vfat` options
    `fmask=0022,dmask=0022` and owner root: only root may write, which the snap daemons are.
    `os.path.ismount("/media/sda1")` is true inside a strict snap with `removable-media`, and
    `os.statvfs` gives size/free. Appending CSV rows once a minute (ctrlx-system-analyzer, 58 files)
    worked; FAT32 rounds every file up to its cluster size (`du` 1.3 MB for 0.6 MB written).


App `ctrlx-core-connector` 0.2.0 (formerly `ctrlx-core-monitor`: Data Layer browser, KVD nodes, widget), 2026-10-31:

77. **A Data Layer remote stays at `SEC_INVALID_SESSION` after the other device's clock was set.** The real
    CORE's clock was 7 days behind; the user set it (Logbook "Time settings changed successfully", 16:24:18
    UTC) and from that second every read through the COREvirtual's remote `cm-real-core` answered
    `SEC_INVALID_SESSION` (REST: 401 `DL_SEC_INVALIDSESSION`) - still 9 min later. Deleting
    `devices/remotes/cm-real-core` and creating it again (the app's reconcile does that within 30 s) fixed it
    at once. ctrlx-core-connector now recreates a remote by itself on `SEC_INVALID_SESSION` /
    `SEC_INVALID_TOKEN_CONTENT` / `SEC_NO_TOKEN` (at most every 30 s).
78. **Result names differ between the APIs.** REST and the C library say `DL_INVALID_ADDRESS`,
    `DL_SEC_INVALIDSESSION`; Python's `ctrlxdatalayer.variant.Result.name` is `INVALID_ADDRESS`,
    `SEC_INVALID_SESSION` (and `SEC_NO_TOKEN` is spelled `sec_noSEC_NO_TOKEN_token` in the 3.6 enum).
    ctrlx-core-monitor compared `result.name` with `"DL_..."`, so its error texts ("not connected", "login
    rejected") never showed on a device - only the raw name. Normalize (strip `DL_`) before comparing.
79. **Data Layer client: JSON read/write, metadata and bulk** (ctrlx-datalayer 3.5 client, ctrlX OS 4.6,
    measured from the build VM through the COREvirtual's remote to the real CORE):
    * `read_json_sync(conv, addr, -1)` gives `{"type":"int32","value":77}` for simple types and arrays, but
      the plain object for flatbuffers (`{"hostname": ...}` for `system/info`, no wrapper).
    * `write_json_sync` wants the same forms: `{"type":"int32","value":12}` (a bare `13` → `FAILED`
      "1: 2: error: declaration expected"); flatbuffers as the plain object (a `{"type":"object","value":{...}}`
      wrapper → `FAILED` "unknown field: type"). The `error` Variant carries that text.
    * Metadata as JSON: `metadata_sync` + `conv.get_schema(C_DLR_SCHEMA.METADATA)` +
      `conv.converter_generate_json_complex(data, schema, -1)`. Through a remote every reference
      `targetAddress` carries the remote prefix (`devices/remotes/cm-x/types/datalayer/int32`); the remote's
      root `devices/remotes/cm-x` has the metadata of the remote connection itself (Resource), not of a folder.
    * `ctrlxdatalayer.bulk.Bulk(client)` `.metadata(addrs)` / `.browse(addrs)` / `.read([BulkReadRequest(a)])`
      then `.get_response()` (use the responses inside the `with Bulk(...)` block; they are freed on close).
      60 nodes: bulk read 55-72 ms, bulk metadata 150-160 ms; 60 single `read_json_sync` 1.6-1.8 s (~7 ms
      each, metadata ~10 ms). The broker's limit is `maxBulkRequest` 1000 (server settings).
    * The operating state is `scheduler/admin/state` → `{"state": "OPERATING"}`, readable through remotes.
80. **KVD (`rexroth-kvd` 4.6.3 COREvirtual / 4.6.5 real CORE) watches its `nodes.json` - and restarts for
    every change.** An entry added to or removed from `<active configuration>/kvd/nodes.json` (Solutions
    WebDAV `PUT /solutions/webdav/appdata/kvd/nodes.json`, or the file itself, also written via temp file +
    rename) is applied without a load command - but KVD logs `restarting ... stopped ... running`: for ~1 s
    **all** its nodes (and its own `kvd/...` root) are gone; an add through ctrlx-core-connector takes ~1.8 s.
    The other nodes keep their current values (a written 12 stayed 12). Three traps, seen 2026-10-31:
    * A change made **during** such a restart is missed: after 8 deletes in a row on the real CORE the file
      was clean, but KVD still showed `cctest/folder` until the file was saved again (unchanged).
    * An entry KVD can't convert **makes the KVD process exit**: a `uint64` initial value of 2^64-1 (KVD reads
      numbers as float64: `1.8446744073709552e+19 ... value not aUINT64 (DL_FAILED)`) → "failed to connect",
      exit 1, systemd restarts it 10 s later (`restart-delay: 10s`) and it exits again while the entry is in
      the file. Keep 64-bit initial values within ±2^53; larger values can be written once the node exists.
    * Checking "is the node gone?" right after a change hits the restart window and says yes too early.
    * KVD keeps the values of deleted nodes in its database: a node created again at the same address
      starts with its last value, not with the initial value of the new entry (`kvd/value-count` counts them).
    ctrlx-core-connector therefore changes one entry at a time per core, waits until KVD runs again (`kvd`
    root back) and shows the change, saves once more if KVD runs but missed it, and takes an entry out at
    once when KVD exited. KVD's own nodes `kvd/db`, `kvd/value-count`, `kvd/verbose` show that it runs.
    Its manifest also registers `/kvd/api/v1/load|save` as Solutions commands - not needed for this.
    Entry schema: `/snap/rexroth-kvd/current/configs/schemas/node.v1.0.schema.json` on the device (types incl.
    `ar*` arrays, `metadata.nodeClass` Variable/Resource/Collection/Folder, `metadata.operations.write`,
    `mode`, `volatile`).
81. **Solutions WebDAV ignores `If-Match`**: a PUT of `appdata/kvd/nodes.json` with a wrong ETag in
    `If-Match` still answered 201 (and set a new ETag). No optimistic locking: serialize your own edits and
    re-read the file right before changing it.
82. **First start after the install: the app data folder is there, but "Permission denied".** ctrlx-core-connector
    0.2.0 waited for `$SNAP_COMMON/solutions/activeConfiguration` (visible after 12 s), then `os.makedirs` of
    its own folder raised `PermissionError` and the process crashed; systemd started it again 1 s later and
    then it worked (content interface connected while the app already ran, like #28; the AppArmor rules come
    after the mount). Harmless for the template (it creates the file on the next start) but an app that
    does something once on the first start (here: import another app's settings) must not decide
    "nothing to import" on that error: treat `OSError` as "not ready yet" and try again
    (`apps/ctrlx-core-connector/src/app/main.py` `_load_settings`).
83. **Array types are named `array-of-<type>` in the type tree**: metadata of an `arint16` node references
    `types/datalayer/array-of-int16` (also `array-of-double`, `array-of-string`, `array-of-timestamp`), while
    KVD's nodes.json, REST values and `read_json` say `arint16`. Map one to the other.
84. **Package ids in the package-manager API are `snap_<name>`**: `GET /package-manager/api/v1/packages` lists
    `{"id": "snap_ctrlx-core-monitor", "name": "ctrlx-core-monitor", ...}` (also `snap_rexroth-automationcore`).
    A check `"ctrlx-core-monitor" in ids` is always false - compare `name` (`tools/ctrlx.py packages` prints it).
85. **Uninstalling an app keeps its app data**: the uninstall is a package-manager task with the `snap_` id
    (#84; the OpenAPI example's `rexroth-firewall` has no prefix), done in SERVICE state like an install (not tried
    in OPERATING): `tools/ctrlx.py state SERVICE`, then `tools/ctrlx.py api POST /package-manager/api/v1/tasks
    --data '{"action": "uninstall", "parameters": {"id": "snap_<name>"}}'` (201 with the task `id`; poll
    `GET .../tasks/<id>` until `done`), then `state OPERATING`. Seen 2026-10-31 (ctrlX OS 4.6): the app's Data
    Layer root is gone, but **its folder in the active configuration (`<snap>/settings.json`) stays** - incl.
    plain-text passwords - and is saved with every configuration. Delete it in Settings → Manage app data, or
    `tools/ctrlx.py api DELETE /solutions/webdav/appdata/<snap>/` (204; the folder with its files).
    Seen again 2026-11-12 on the X3, whose apps had been uninstalled since 2026-11-05: a fresh install **loaded the
    old `settings.json` at once** (Barcode Reader on its simulated camera, Core Connector with its old connection) -
    "installed fresh" does not mean default settings. What the snap keeps itself (`$SNAP_COMMON` / `$SNAP_DATA`, e.g.
    the Network Scanner's scan history) was gone. The scopes granted to users stayed: the test users still had
    `ctrlx-acme-template`'s three scopes when the template was installed again.
86. **A ctrlX OS 3.6 CORE without `system/info/hostname`, `uptime`, `architecture` and `system/apps`** (seen
    2026-10-31 through a remote from the COREvirtual, 192.168.1.10): its `system/info` held only `ledDevice` and
    `setup-in-progress` (= false); `framework/metrics/system/*` and `scheduler/admin/state` were there. #44 saw
    those `system/info` nodes on a 3.6 device - don't rely on them. A read of a missing node through a remote
    answers `INVALID_ADDRESS`, the same as a read through a remote that is not mounted, so an app that requires
    one such node reports a working connection as "not connected". A failed login and a failed certificate check
    answer `SEC_UNAUTHORIZED` instead (tested with a made-up user on 3.6 and 4.6). The user's plain
    `tcp://user:pw@ip` remote connected to that 3.6 core; the app's `...?sslport=443&NoCheckCertificate=true`
    form connected to the same device on 2026-10-22.
87. **A KVD collection reads OK with an empty value** (ctrlX OS 4.6, KVD 4.6.5 sample `samples/kvd/collection`):
    REST answers `""`, `read_json_sync` returns OK with an empty string - `json.loads("")` then raised and the
    browser's node details failed with HTTP 400 "Expecting value". Treat an empty JSON text as "no value".
    KVD's node list (`nodes.json`) also holds patterns like `samples/kvd/implicit/**` and `.../*` - not addresses.
88. **A node set that changes at runtime: one wildcard provider node instead of hundreds** (verified on the
    COREvirtual 4.6, 2026-11-01, ctrlx-amr-fleet 0.4.0: `fleet/amr/<id>/<value>` for an AMR list). `register_node`
    takes wildcards ("wildcards allowed", SDK sample `datalayer.provider.sub`): one `ProviderNode` registered at
    `<path>/**` gets browse / read / write / metadata for EVERY address below `<path>` - its callbacks see the full
    address and decide: `on_browse` answers the children (`Variant.set_array_string`), `on_metadata` a Folder or a
    Variable with `types/datalayer/<type>`, an unknown address `Result.INVALID_ADDRESS` (REST: 404). The ctrlX
    broker then shows it like registered nodes: the parent lists `<path>`'s name, REST browse / metadata / read /
    write work, `fleet/amr/AMR-09/x` for an id not in the list is 404. The app also registered `<path>` itself (a
    second node) for the folder; whether `/**` alone covers `<path>` was not tested. Unregistering removes the whole
    subtree at once. Cost: one node and a few metadata variants instead of 1400 registrations (200 AMRs x 7 values).
89. **The template's `PUT /settings` replaces ALL settings: missing keys get the defaults, not the current values.**
    A UI test cleanup that sent `{"fleet": {"input": "fleet-json"}}` switched the app's simulation off (its default)
    and a later step waited 90 s for AMRs. Tools and tests: `GET /settings`, change the copy, `PUT` the whole object
    (`apps/ctrlx-amr-fleet/scripts/perf_test.py` does). The web UI always sends the whole form.
90. **Each Data Layer write into a Python provider costs ~0.24 ms CPU on a real CORE** (~0.05 ms on the COREvirtual;
    measured 2026-11-01 with ctrlx-amr-fleet 0.4.0, writes from the build VM through REST
    `PUT /automation/api/v2/bulk?type=write`, up to 500 per request). The app's own Python per write was ~0.03 ms of
    it; the rest is the `ctrlxdatalayer` callback path (ctypes, Variant). So 1000 writes/s = ~24 % of a CORE core in
    the app alone, and 200 AMRs x 5 values x 5/s (5000 writes/s) did not get through at all (2100/s, whole device
    31 % of 2 cores incl. the REST server). One write of a 200-AMR JSON (5/s) cost 7 %. Design PLC interfaces with
    few large writes (a JSON or flatbuffers node), or values written only on change. REST bulk: per entry its
    own `result` (`DL_OK`); 500 writes took ~70-170 ms on the COREvirtual, ~160-390 ms on the CORE.
91. **The reverse proxy passes big uploads to an app; the stdlib server must read a rejected body to the end**
    (verified on the COREvirtual 4.6, 2026-11-02, ctrlx-amr-fleet 0.5.0 "Upload map…"): an 8.7 MB map file went
    through `PUT /<snap>/api/v1/maps/<name>` in one request (7.3 s from the build VM, XMLHttpRequest with upload
    progress). A header-only probe (`Content-Length` + `Expect: 100-continue`, no body) got `100 Continue` up to
    100 MB, so there is no small proxy body limit. Stream the body into `<name>.tmp` in the app data (manifest
    `appPrivateFiles`) and rename; never hold it in memory. When the app answers early (409 exists, 400 name) without
    reading the body, `http.server` closes the socket and the client sees a reset / `EOF occurred in violation of
    protocol` instead of the answer: read the rest first (`_drain` in the app's `web_server.py`). Probing limits:
    use the header-only probe - a real oversized test body to a writable route can get stored (it happened once with
    `/amr-list`: a body of `#` characters was a valid, empty list).
92. **Python ints in sets / lists / dicts cost ~50-90 bytes each - use `bytearray` / `array` for grids.** The AMR
    simulator's navigation grid of a 208 x 356 m map (320k cells, 167k free) kept 15 MB (peak 24 MB) as a set +
    list + `Counter`; as `bytearray` masks + `array("i")` it keeps 1.0 MB (peak 1.7 MB), the same speed for the
    A* lookups (`mask[i]` instead of `i in set`). Measure with `tracemalloc` (slows Python ~5x - time separately).

App `ctrlx-barcode-reader` (GigE Vision camera, Aravis + OpenCV + zxing-cpp), 2026-11-03:

93. **A GigE Vision camera and the ctrlX COREvirtual with port forwarding: commands yes, images no.** The
    COREvirtual (and the build VM) sit behind QEMU user networking (10.0.2.15, NAT through the Windows PC). A GVCP
    discovery sent to the camera's IP (UDP 3956) is answered, Aravis opens the camera by IP and reads/writes its
    features (`arv-tool-0.8 -a 192.168.1.99 control ...`). But the image stream is sent by the camera to the address
    the host writes into `GevSCDA` - Aravis writes its interface address 10.0.2.15, which the camera can't reach, so
    every grab times out (no packets at all). Broadcast discovery finds nothing through the NAT. Test image
    acquisition on a real CORE in the camera's network; on the COREvirtual use a simulated camera (Aravis' "Fake"
    interface: `Aravis.enable_interface("Fake")`, device id `Fake_1`, software trigger works) or give it a network
    adapter in the camera's network. Both devices: `net.core.rmem_max` 212992, MTU 1500, ephemeral ports
    32768-60999 (which include the reserved 49250-50250 - an app that binds UDP port 0 can get one).
94. **Ubuntu packages with Python modules in a core24 snap** (stage-packages `python3-gi`, `gir1.2-aravis-0.8`,
    `libaravis-0.8-0`): snapcraft stages them for the build-for architecture (the VM's multiarch sources, #36) and
    does not stage `python3`/`python3.12` (core24 has them). The module lands in `$SNAP/usr/lib/python3/dist-packages`,
    which is not on the python plugin venv's `sys.path`, and GObject typelibs in
    `$SNAP/usr/lib/<triplet>/girepository-1.0`, which gi doesn't search: add both at runtime before `import gi`
    (`apps/ctrlx-barcode-reader/src/app/camera.py::load_aravis`; `GI_TYPELIB_PATH` is read when gi loads its first
    typelib, the libraries are found through snapcraft's `LD_LIBRARY_PATH`). `libaravis-0.8-0` depends on
    `libgstreamer1.0-0` (+ `libunwind8`, `libdw1t64`) only for its GStreamer plugin: prime them out. core24 has GLib/GIO
    but no libxml2, ICU (~30 MB unpacked), libusb, libgirepository.
95. **Claude Code's auto mode blocked running native code from downloaded PyPI wheels** ("Code from External")
    while Ubuntu archive packages extracted with `dpkg-deb -x` ran. Ask the user before running wheels (OpenCV,
    numpy, ...) in the build VM; after the user agreed in the conversation the same commands ran.
96. **OpenCV 4.14 (opencv-python-headless 4.14.0.94) as a snap dependency**: the x86_64 wheel is 57 MB (210 MB
    unpacked with numpy, ~62 MB in the xz snap), aarch64 35 MB (~38 MB in the snap with numpy). It bundles FFmpeg
    (LGPL-2.1+), OpenSSL 1.1.1k (end of life), OpenBLAS, libgfortran (GPL-3.0 + GCC exception), libvpx/libaom/dav1d/
    libavif and statically libtiff, OpenJPEG, OpenEXR, protobuf, Intel IPP (x86_64) / KleidiCV + carotene (aarch64):
    `cv2.getBuildInformation()` lists them. The wheel's LICENSE-3RD-PARTY.txt misses libtiff and the GCC Runtime
    Library Exception text (numpy's LICENSE.txt has it), zxing-cpp's wheel misses nanobind. `import numpy, cv2,
    zxingcpp` = 46 MB RSS on the build VM. OpenCV's own decoders read only QR and EAN-8/13, UPC-A/E (its
    `BarcodeDetector` is an EAN/UPC decoder); zxing-cpp 3.1.1 (Apache-2.0, 1 MB wheel) reads 19 symbologies. A noisy
    EAN-13 tilted by 6 degrees gave zxing-cpp a second, wrong EAN-13 with a valid check digit.
97. **Bosch icon names for menus**: the ctrlX UI's stylesheet on the device lists them -
    `curl -sk https://<device>/` → `styles-*.css` → `grep -o 'bosch-ic-[a-z0-9-]*'` (ctrlX OS 4.6: e.g.
    `bosch-ic-barcode-scan`, `bosch-ic-camera`, `bosch-ic-qr-code`). An unknown name shows an empty icon.
98. **numpy >= 2.4 does not import on the COREvirtual - and then the Data Layer library fails too.** The
    COREvirtual (ctrlX WORKS, 2026-11-03) reports the host CPU (i7-11850H) with SSE3, AVX, AVX2, AVX-512F but
    without SSSE3, SSE4.1, SSE4.2, POPCNT in `/proc/cpuinfo` (the build VM's "QEMU TCG CPU" has them). numpy 2.4+
    x86_64 wheels need x86-64-v2 and raise `RuntimeError: NumPy was built with baseline optimizations: (X86_V2) but
    your machine doesn't support: (X86_V2)` at import. `flatbuffers` (used by `ctrlxdatalayer`) imports numpy when it
    is installed and only catches `ImportError`, so `import ctrlxdatalayer` failed and the app ran without Data Layer.
    numpy 2.3.x (baseline SSE3) works; check `grep flags /proc/cpuinfo` on the device before pinning C extensions with
    a raised x86 baseline. OpenCV 4.14 wheels: baseline SSE3 (`cv2.getBuildInformation()`). The real CORE is arm64.
99. **An arm64 snap built on the amd64 VM contained amd64 Ubuntu packages** (snapcraft 8, destructive mode,
    `--build-for=arm64`): `stage-packages: [python3-gi, libaravis-0.8-0]` staged
    `_gi.cpython-312-x86_64-linux-gnu.so` and `usr/lib/x86_64-linux-gnu/...` - the build and the snap linter only
    warned about "unused libraries". Only "Architecture: all" packages (like `ctrlx-datalayer`, #36) are right
    without care. With `libfoo:${CRAFT_ARCH_BUILD_FOR}` snapcraft fetches arm64 packages, but then its filter of
    packages that the base already has no longer matches and it stages `python3.12`, `libpython3.12`, `libffi8`,
    `libselinux1`, `debconf`, ... too (a staged `$SNAP/usr/bin/python3` would come first in the snap's PATH, before
    the venv's Python). The same goes for C-extension wheels from the python plugin's pip (x86_64 wheels in an
    arm64 snap). Fix in ctrlx-barcode-reader: part `debs` (`apt-get download <pkg>:$CRAFT_ARCH_BUILD_FOR` of exactly
    the packages core24 lacks, `dpkg-deb -x`, versions into `usr/share/<snap>/debs.txt`) and part `wheels`
    (`pip install --platform manylinux_2_28_<arch> ... --target`, sha256 pins). Check an arm64 snap with
    `file prime/**/*.so | grep -v aarch64`. Also: an incremental build that reprimes a part after its `prime:`
    filter changed keeps the files primed before - build `--clean` after filter changes. core24 20260824 has
    glib 2.80.0-6ubuntu3.8, noble's `gir1.2-glib-2.0` is 2.80.0-6ubuntu3.10 (same API, works).
100. **Omron Sentech STC-MCS500POE (firmware 20.50) + Aravis 0.8.30: no frame on a software trigger** (real CORE,
    2026-11-03). With TriggerSelector FrameStart, TriggerMode On, TriggerSource Software (unlocked per the camera's
    GenICam XML: `TriggerSoftwareEnabled` = 0), acquisition running (`AcquisitionEnabledReg` 1), burst count 1, neither
    `TriggerSoftware`, a direct write of `TriggerSoftwareReg` nor `SoftwareSignalPulse` (source SoftwareSignal0) made
    the camera send a single stream packet; free run streamed at once. One **SingleFrame acquisition per read**
    (AcquisitionStart → one frame → AcquisitionStop) works and is plain GenICam: ~208 ms per 5 MP frame incl. the
    exposure. To see what a camera does: run a small script in the app's own sandbox,
    `echo <base64> | base64 -d | sudo snap run --shell <snap>.<app> -c 'python3 -'` (same Python, libraries,
    confinement; the app must release the camera first - one controlling application per camera), with
    `ARV_DEBUG=stream:2` for Aravis' stream statistics; GVCP READREG of the bootstrap registers (0x0A00 CCP, 0x0D00 SCP0,
    0x0D04 SCPS0, 0x0D18 SCDA0) works from a second application without control.
101. **GigE Vision images on a ctrlX CORE lose packets unless the camera paces them.** `net.core.rmem_max` is 212992
    (an app can't raise it), i.e. ~2.5 ms of a 1 Gbit/s burst. STC-MCS500POE, 5 MP BayerRG8, 1444-1520 B packets:
    inter-packet delay 6 µs (camera default 6000 ticks) and 20 µs lost whole frames (2/6, 3/6, thousands of resends);
    40 µs (`arv_camera_gv_set_packet_delay(40000)` - Aravis converts ns to the camera's ticks) gave 14 of 14 complete
    frames, ~208 ms each. Aravis' packet-size negotiation chose 1520 B on the CORE (MTU 1500!) and those frames
    arrived. No firewall is in the way: iptables (nft and legacy) is empty, policy ACCEPT. Cost on the CORE (cv2 2
    threads): debayer 5 MP 15 ms (cv2) vs ~200 ms with numpy slicing; resize 2448→1280 INTER_AREA ~170 ms, INTER_LINEAR
    ~50 ms, halving with INTER_AREA fast; zxing-cpp 19 symbologies on 5 MP ~330 ms; first `import cv2` ~3 s.
102. **HTTP 502 from the reverse proxy when a page fires many requests at once** (real CORE, ctrlx-barcode-reader:
    the history table loaded 50 thumbnails in parallel; one answered 502, nothing in the app log). Python's
    `socketserver` listens with a backlog of 5 (`request_queue_size`): extra connections of the proxy to the Unix
    socket were refused. Template 0.1.8: backlog 64; and pages should limit parallel requests (the app: 3 at a time).
103. **A fast UDP stream that a CORE X3 can't take is no problem on an X7** (ctrlx-barcode-reader, 2026-11-04, GigE Vision, 5 MP, UDP
    with Aravis). X7: no lost frames up to wire speed, 6 ms CPU per frame. X3: frames lost at most packet delays below 30 µs, 19-92 ms CPU per frame;
    98-99.6 % of the loss is `RcvbufErrors` (the receiving thread doesn't empty the socket), the NIC's counters stay clean; the loss is not
    linear with the rate (10-20 µs worst). Tune and measure network values per device model; a value found on one says little
    about the other. Details: the app's `KNOWN-ISSUES.md` 22.
104. **What a strict snap can't do for network speed** (measured in the sandbox with `network` + `network-bind`, as root, X7 and X3):
    `AF_PACKET` EACCES, `SO_RCVBUFFORCE` EPERM, `SO_RCVBUF` capped at 2 x `rmem_max` (425984). `os.setpriority` on a thread gives EPERM too. `/proc/net/snmp` isn't readable either: read
    the UDP counters over SSH. `net.core.rmem_max` = 212992 on both, not changeable from an app.
105. **Large images in Python: two cheap wins** (ctrlx-barcode-reader, X3, 2026-11-04). glibc raises its mmap threshold after freeing a large block, so
    frames of 5-15 MB then come from the heap and RSS keeps growing (121 MB after 60 reads); `MALLOC_MMAP_THRESHOLD_=1048576` for the service keeps
    them in mmap (85 MB, ~5 % slower), `MALLOC_ARENA_MAX=1` didn't help. `cv2.resize` to an exact half with `INTER_AREA` is 4-8 x faster than a slightly
    different width (gray 5 MP: 2.8 ms for 1224 wide, 22.5 ms for 1280). A worker thread for display images gained little and raised packet loss on
    the X3's 2 app CPUs.
106. **Aravis: dropping a stream object clears the camera's stream port** (ctrlx-barcode-reader, 2026-11-04). With two `ArvGvStream` objects on one camera, releasing the older one sets `GevSCPHostPort` to 0: the camera
    sends nothing and reads time out, with no error and no packet counted. Release the old stream before creating the next one.
107. **Solutions REST tasks: a `copy` right after `save_async` gives an incomplete configuration** (X3, 2026-11-05). `POST /solutions/api/v1/tasks` answers 201 with an empty body and the id in `Location`, so a script that
    doesn't look there won't wait. Our copy lacked `datalayer/`, `scheduler/`, `script/` and `trace/`, which the save had not written yet. We deleted it unloaded, so what loading it does isn't known. Wait for `state` = `done`
    (recipe: `app-data-settings.md`). Also: the first save on a device adds the system settings files to the active configuration (35 on that X3, 2.4 s); `GET .../configurations/appdata/archive` answers 400 "No downloadable
    appdata found"; stored configurations are reachable under `/solutions/webdav/<name>/`.
108. **Load results** (template mechanism, ctrlx-barcode-reader 0.2.0, X3, 2026-11-05): a load runs in OPERATING when no app refuses it (no Motion on that X3, #24). An app answering 400 in phase `load` makes the task `failed`,
    and its phases end `validate, abort, save`: the app stays on its settings, and that save replaces the invalid file in the active configuration. No folder for the app in the configuration: reset to defaults, the file comes back with the same
    save, which matters for a default that reaches for hardware (`camera.source`). Other apps didn't restart and their settings files stayed the same over 11 loads.
109. **A GigE camera that is gone: closing it blocks** (Aravis 0.8.30, X7, 2026-11-05). Dropping the `ArvCamera` after `control-lost` took about 8 s, and a read during that time waits. Set a status such as `camera/state` before
    closing, not after, or it reports `connected` all that time. The heartbeat does notice a pulled cable while idle.
110. **"Too many sessions": the device refuses logins of the devices.toml user** (X3, ctrlX OS 4.6, 2026-11-07). `POST
    /identity-manager/api/v2/auth/token` answered HTTP 400 `"dynamicDescription":"Too many sessions"` (diagnosis 080E0200 /
    0C7A0202) after a day of tests: `tools/ctrlx.py` logged in on every call and never logged out, and every
    `ui_check.py --device` run added one more session - the identity manager limits sessions per user
    (`GET/PUT /identity-manager/api/v2/settings/session`: `maxSessionsPerUser`, `sessionTimeout`, `tokenValidity`;
    open ones: `GET /sessions`, `DELETE /sessions/{id}`). The same user's browser login is then refused too. Fix:
    both tools end their session with `DELETE /identity-manager/api/v2/auth/token` (`Device.logout()`). Scripts that
    log in themselves must log out as well. Old sessions end only with their timeout. The X3's values (2026-11-08):
    `maxSessionsPerUser` 100, `tokenValidity` 28800 s, `sessionTimeout` 0 - a session without logout lives 8 h, so
    100 tool calls within 8 h lock the user out for up to 8 h. Logins worked again the next day.
111. **A PLC-typed write to a template node: `DL_TYPE_MISMATCH`** (X3, ctrlX OS 4.6, template 0.3.0, 2026-11-07, REST
    writes with `tools/ctrlx.py dl-write`). The Data Layer hands the writer's type to the provider unchanged: float32,
    int16, int32, int64, uint8, uint32 and bool8 written to the float64 node `values/setpoint` were all refused, only
    float64 was taken; the bool8 node `values/enabled` refused int16 1 and uint8 1. A ctrlX PLC writes with its
    variable's type (REAL = float32), so a node must have that type - template 0.4.0 has every scalar type and
    `array-of-<type>` (table: `datalayer-python.md` "Node types for PLC variables"). REST names differ between
    reads and writes: a float64 node reads as `"type": "double"` (2026-11-08), the writes above used `float64`.
112. **Where a kit app's RAM goes: ~34.6 MB RSS / 23.5 MB PSS for the template alone** (X3, ctrlX OS 4.6, template
    0.4.0 idle, 2026-11-08; the app's start-up step by step in one process inside the snap, own Data Layer root and
    socket; `/proc/<pid>/smaps`). File pages 18.9 MB: python3.12 binary 5.6, libcrypto 3.0, libstdc++ 1.6, libc 1.5
    (core24, shared with every Python snap: PSS a third of that) and **libcomm_datalayer.so 6.6 - each snap maps its
    own copy, so it is not shared between kit apps**. Anonymous 15.8 MB: interpreter 2.9, standard-library imports
    7.9 (logging 1.2, http.client 1.2 incl. email, json 1.1, dataclasses 1.0 incl. inspect, ssl 1.1, http.server 0.8,
    socketserver 0.6, mimetypes 0.5, typing 0.4), ctrlxdatalayer + 54 comm/flatbuffers modules 3.4, provider running
    0.5 (+4 threads), the app's own modules, settings and five web requests ~1.0. Levers tried: no `ssl`
    (`sys.modules["ssl"] = None` before http.server) -1.0 MB RSS only (libcomm_datalayer loads libcrypto anyway);
    `PYTHONOPTIMIZE=2` +3.6 MB and 4.4 s instead of 0.9 s to start (no .opt-2.pyc in the snap: everything is compiled
    at start); `MALLOC_ARENA_MAX=2` no change; the `DataLayerClient` +0.16 MB. Not tried: a dict instead of
    mimetypes (~0.5 MB), a plain class instead of dataclasses (<= 1 MB, if nothing else imports inspect). So ~31 MB
    is the price of Python + the Data Layer library; an app has ~40 MB RSS for its own work under the 75 MB target.
