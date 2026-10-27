# Gotchas (living document - append, never silently delete)

Format: **symptom** → cause → fix. Add the date and the source (build log, device, forum link).
Codes in brackets are `tools/ctrlx_check.py` findings that catch the problem.

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
    like installing. Not an app problem; our app answered the query with 204.
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
    and reconnects within seconds. Harmless as long as the app retries (template does).
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

36. **arm64 cross-build works as-is** in the App Build Environment (multiarch apt sources from the
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
    ctrlx-amr-fleet 0.2.2 (2026-10-23; CORE = ARM, 2 cores, rexroth-arch01; COREvirtual on a Windows PC,
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
66. **Cost of a network scan on a real ctrlX CORE** (arm64, 2 cores; ctrlx-network-scanner 0.1.0, 2026-10-25):
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
    Real CORE (arm64) not measured yet: expect ~6x the COREvirtual (#58).
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
