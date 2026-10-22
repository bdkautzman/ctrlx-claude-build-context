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
