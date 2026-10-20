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
    flags) → don't use it for core24; see `templates/python-webapp/ci/github-build.yml`.
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

* `tools/ctrlx.py install` (multipart field `file` + `update`, task polling) – built from
  packagemanager OpenAPI 1.15, tested against a mock only.
* `tools/ctrlx.py logs` field names (logbook OpenAPI 2.4).
* `templates/python-webapp/ci/github-build.yml` (SDK zip layout, native arm64 runner).
* `templates/snippets/licensing.py`.
* The `<N>` syslog-priority prefix in `app/logsetup.py` → levels in the ctrlX Logbook
  (relies on systemd `SyslogLevelPrefix=yes`, the default for services).
* The Solutions app sends a token with the app's `.rw` scope (or admin) to save/load commands
  (persistdata.md says it "ensures the required permissions") - if save/load fails with 403,
  log the token scopes and adjust `auth.require` for `/appdata/*`.

## Found during development
<!-- Append here: date - symptom - cause - fix - (check code if you added one) -->
