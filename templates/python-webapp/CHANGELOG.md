# Changelog

Versions follow semantic versioning and must match `version:` in `snap/snapcraft.yaml`.
Every release needs a higher version than the previous one (ctrlX validation rule).

## Unreleased

- **UI feedback overlay, dev only** (2026-11-09, user's request): `./run-local.sh` sets `APP_DEV_FEEDBACK` to the kit's
  `tools/ui-feedback/overlay.js`; the dev server (`src/app/dev_feedback.py`) then adds it to its HTML pages and keeps
  the notes in `.dev/feedback.json` (`/<snap>/__dev/feedback[.js]`). Alt+click an element, type a note; skill
  `/ui-feedback` works through the notes. Only with `APP_DEV_NO_AUTH=1` outside the snap; `ui_check.py` removes the
  variable. `APP_DEV_FEEDBACK=0 ./run-local.sh` turns it off. Tests: `tests/test_dev_feedback.py`.
  No snap built with it (nothing in the snap changes behaviour).

## 0.4.0

- **Node types for PLC variables** (user's choice 2026-11-08): every scalar type of the Data Layer (bool8, int8 ..
  uint64, float32, float64, string) and `array-of-<type>`; each node accepts only its own type, nothing is converted.
  A PLC REAL needs a float32 node, DINT int32, ... (table: `docs/ctrlx/datalayer-python.md`). Before, only
  float64/int64/uint64 nodes existed and the X3 refused every PLC-typed write to them with `DL_TYPE_MISMATCH`
  (gotchas #111). float32 nodes hold the float32 a REAL holds; arrays are tuples, `NodeSpec.max_items`;
  `minimum`/`maximum`/`max_length` apply to each element. Example node `values/limits` (array-of-float32).
  The page shows float32 values with the fewest digits that give the same float32 and edits arrays as JSON.
- **`DataLayerClient`** (`datalayer_client.py`, from core-connector's `ClientAdapter`): read / read_typed /
  read_json / write (exact type) / write_json / browse / metadata / remove and bulk read / browse / metadata;
  `DataLayerService(..., on_client=callback)` opens it on the provider's connection (opt-in).
- `tests/ui_scenario.py`: the 403 check for settings also runs on a device (a GET); with `tools/ui_check.py --device
  NAME --role reader|operator|writer` it runs as the device's test users (`tools/ctrlx.py test-users`).
- Verified 2026-11-08 on the ctrlX CORE X3 (arm64, incremental build 8.5 MB): `DataLayerClient` inside the snap with
  the real library (read, read_typed, read_json, write, write_json, browse, metadata, the three bulk calls, result
  names; float32 into float64 and int32 into float64 refused); REST writes to `values/limits` as `arfloat32` / `arfloat`
  taken, `arfloat64` / `ardouble` refused; `ui_check.py --device x3` as admin and as the test users kit-view,
  kit-operate, kit-configure (first role check on a device with real ctrlX users); RAM 34.6 MB RSS / 23.5 MB PSS idle,
  as 0.3.0 (gotchas #112). Also on 0.3.0 that day: a start while busy is refused (`DL_INVALID_VALUE`) and the running
  command still ends with done. **Not tested**: a real ctrlX PLC writing the nodes; a scalar float32 node on a device.

## 0.3.0

- **PLC commands** with the PLCopen handshake Execute / Busy / Done / Error (+ error-text, parameters):
  `PlcCommand(path, run, params=[...])` in `datalayer_nodes.py`, the kit's standard (user's choice 2026-11-07).
  Example `commands/set-setpoint` (copies `target` into `values/setpoint` after 1 s; error while `values/enabled`
  is false), unit tests, UI-scenario step, tutorial step. Before, four apps built four different trigger schemes.
- `Commands` + `bind()`: nodes that call the app's worker, which is created after the registry (`build_node_specs(...,
  commands)`, `DataLayerService(..., commands)`); three apps had built this by hand.
- Data Layer not there at the start or after a SERVICE <-> OPERATING switch: INFO instead of ERROR, ERROR only when it
  lasts over 60 s, then every 10 min (`DownTimeLog`, gotchas #28) - every app logged an error at every boot (X3 Logbook).
- `NodeSpec.group`: an object with `bind(registry)` that made the node (used by `PlcCommand`).
- Verified 2026-11-07 on the ctrlX CORE X3 (arm64, incremental build 8.5 MB): 13 nodes; the handshake over the Data Layer
  REST API (as a PLC writes): target + execute true -> done and values/setpoint set, error with error-text while
  values/enabled is false, true written again ignored, execute false resets; the start-up log has no error line
  (an update: the Data Layer was there at once). Locally: unit tests, `ui_check.py` as admin, reader, operator.
  **Not tested on a device**: a start while busy (unit tests only - each REST call from the build VM takes ~1 s, longer
  than the example's work), the log levels after a reboot, `ui_check.py --device` for 0.3.0 (the X3 refused logins
  of the test user at that point, gotchas #110), a real ctrlX PLC writing the nodes.

## 0.2.0

- Roles: **view / operate / configure** (scopes `<snap>.web.all.r`, new `<snap>.web.operate.x`, `<snap>.web.all.rw`;
  each includes the one before). `auth.require(headers, auth.VIEW|OPERATE|CONFIGURE)` instead of `write=True/False`;
  `GET /status` answers `role`, `canOperate`, `canConfigure`, `user` (replaces `canWrite`). Writable Data Layer values
  need operate (`NodeSpec.write_role`), settings and app data commands configure - reading the settings too
  (view/operate users don't see them, as in ctrlx-amr-fleet and ctrlx-core-connector). `settings/active` is listed
  in `GET /values` for configure only (`NodeSpec.view_role`). Menus and widget list all three scopes. The log line of
  a settings change names the user. User's request (review of the five apps, 2026-11-07).
- Web UI split: `www/kit.js` + `www/kit.css` hold what every app needs (token, `Kit.api` with at most 4 requests at a
  time - gotchas #102, `Kit.download`, `Kit.upload`, status/role handling with `data-role` elements, the settings
  form), `app.js` / `style.css` only the app's page. Settings moved into a **drawer** (slides in from the right) and
  the page got a **help menu with a tutorial** (`Kit.setupHelp(steps)`) - both from ctrlx-amr-fleet /
  ctrlx-core-connector, where each was built separately.
- **Home screen widget** `ctrlx-acme-template-widget` (`www/widgets.js`, `www/widget.svg`, manifest `uiExtensions`,
  `noInitialCreation: true`): the app's state and a few values, "Open" button. From ctrlx-amr-fleet /
  ctrlx-core-connector.
- Web server helpers for app routes: `_query()`, `_send_bytes(..., filename=...)` (downloads, `Content-Disposition`
  also for non-ASCII names), `_receive_file(path, max_bytes, check)` (upload streamed into `<path>.tmp` in the app data,
  fsync, rename; `UploadError` rejects it after reading the rest of the body - gotchas #91). From ctrlx-amr-fleet,
  ctrlx-network-scanner, ctrlx-system-analyzer, ctrlx-barcode-reader.
- Data Layer: JSON nodes - `NodeSpec(..., "string", obj, json=True)`: `set_internal` takes the object (compact JSON),
  `get_json()`, writes must be valid JSON and `on_write` gets the parsed object. `settings/active` uses it. Every app
  of the kit published JSON strings with its own `json.dumps`.
- `tests/ui_scenario.py`: roles (`tools/ui_check.py --role reader|operator|writer`), drawer, tutorial, widget.
- Build scripts name the right COREs (arm64: X2, X3; amd64: X5, X7, COREvirtual).
- Verified 2026-11-07 on the ctrlX CORE X3 (arm64, ctrlX OS 4.6; update from 0.1.2, full build 8.5 MB): the device
  lists the three scopes with their names (`GET /identity-manager/api/v2/scopes`), the widget with all three scopes and
  `noInitialCreation` (`GET /package-manager/api/v1/manifests`), the app kept its settings, Data Layer 7 nodes incl. the
  JSON node `settings/active`, `GET /status` answers role `configure` for the admin, `ui_check.py --device x3`
  (page + widget through the reverse proxy) OK. Locally: `ui_check.py` as admin, reader, operator.
  **Not tested on a device**: a ctrlX user with only the view or only the operate scope (needs such users on the
  device), the widget on a real home screen, an upload through the reverse proxy with `_receive_file` (the same
  pattern passed in ctrlx-amr-fleet 0.5.0, gotchas #91).

## 0.1.8

- First start after an install: when the app data folder is there but not writable yet (gotchas #82), settings
  loading retries for up to 30 s instead of crashing (systemd restarted the app, the log showed a traceback).
  `tests/test_main.py`. From apps/ctrlx-barcode-reader.
- Web server: listen backlog 64 (`request_queue_size`) instead of socketserver's 5 - the reverse proxy answered
  502 when a page opened ~50 requests at once (gotchas #102). From apps/ctrlx-barcode-reader.

## 0.1.7

- Settings: `SettingsStore(..., validator=fn)` for checks the generic rules can't express (`fn(settings)`
  returns error messages starting with the dotted path); it runs on every validation together with the
  rules, so a save shows all problems at once. New rule keys `minItems` and `itemPattern` (arrays of
  strings). From apps/ctrlx-network-scanner (target/port syntax) and apps/ctrlx-amr-fleet (color lists).

## 0.1.6

- UI check in a real browser: `tests/ui_scenario.py` (run by `tools/ui_check.py`, headless Chromium via
  Playwright; one-time setup `tools/setup-ui-test.sh`) clicks Edit -> Set on a Data Layer value and
  saves a valid and an invalid setting. `index.html` starts with `<body data-ui-ready="false">`,
  `app.js` sets it to "true" once the data is shown - screenshots wait for it.
- `.ui-check/` (screenshots) is git-ignored.

## 0.1.5

- Data Layer: `NodeSpec.on_write` runs before the value is stored and can reject it by raising
  `NodeWriteError` (the write then fails with e.g. `DL_INVALID_VALUE` and the node keeps its old
  value). Before, a rejected value stayed in the node. From apps/ctrlx-amr-fleet (JSON input node).

## 0.1.4

- Layout: the Python package moved to `src/` (`src/pyproject.toml`, `src/app/`); the snap part
  `app` builds only that folder, so UI/config changes no longer rebuild the venv.
- The four dump parts (wrapper, configs, www, defaults) are one part `files` (same snap layout).
- `python-packages: []`: the venv's pip/setuptools/wheel are no longer upgraded during the build.
- Build scripts call `tools/build-snap.sh`: incremental by default, `--clean` for full builds.

## 0.1.3

- Settings rules: `items` (arrays of objects), `maxItems`, `unique`, `pattern` and `secret`
  (masked in API responses and the Data Layer node `settings/active`; a masked value sent back
  keeps the stored one). From apps/ctrlx-core-monitor (now ctrlx-core-connector).
- Data Layer: a wrong-typed write to a read-only node answers `DL_PERMISSION_DENIED`
  (was `DL_TYPE_MISMATCH`).

## 0.1.2

- Web UI: clicking "Set" after editing a Data Layer value wrote the old value back (the Edit
  button kept its first click handler); a refresh that was running when an edit started could
  discard the edit field.
- Removed the Settings-page menu entry: ctrlX OS 4.6 shows `_blank` settings tiles without a link
  and without a `category` under "core.settings.categories.undefined".

## 0.1.1

- Snap ~100 MB smaller: prime only the target architecture's `libcomm_datalayer.so*`; drop the
  static lib, headers and build tools of the ctrlx-datalayer .deb, and the venv tooling
  (pip, setuptools, wheel, packaging). fossinfo versions filled from the build.
- Solutions load: the polling pause started in the "query" phase expires after 60 s (Solutions
  sends no "abort" when another app refuses the load in "query").

## 0.1.0

- Initial version from the ctrlX Python web-app template.
