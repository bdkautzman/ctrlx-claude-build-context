# Package manifest (`configs/package-assets/<name>.package-manifest.json`)

Full reference: `upstream-sdk-4.6.0/package-assets.md`. Schema: `tools/schemas/package-manifest.v1.7.schema.json`
(validated by `tools/check.sh`; `additionalProperties: false` everywhere → unknown keys are errors).
Template: `templates/python-webapp/configs/package-assets/ctrlx-acme-template.package-manifest.json`.

## Delivery

* File name **must** be `<snap-name>.package-manifest.json`, `id` **must** equal the snap name.
* The `configs` dump part puts it into `$SNAP/package-assets/<name>/`; the `package-assets`
  content slot exposes that folder to ctrlX OS (Device Admin). Same folder: `<name>.fossinfo.json`,
  `i18n/<name>.package-manifest.<lang>.json`.
* `"$schema": "https://json-schema.boschrexroth.com/ctrlx-automation/ctrlx-os/apps/package-manifest/package-manifest.v1.7.schema.json"`

## Sections used by the template

### `scopes-declaration` (permissions)
```json
{"identifier": "<name>.web", "name": "...", "description": "...",
 "scopes": [{"identifier": "<name>.web.all.r",  "name": "View ...",   "description": "..."},
            {"identifier": "<name>.web.all.rw", "name": "Manage ...", "description": "..."}]}
```
Scope id pattern `<id>.<service>.<scope_name>.<access>` with access `r|w|rw|x`. The admin scope
`rexroth-device.all.rwx` must always grant full access. The app **must enforce** scopes itself
(`src/app/auth.py`). Don't copy scope ids from samples (`rexroth-python-webserver.web…` appears in
real apps that were copied from the SDK sample - check `MANIFEST-SCOPES`).

### `services.proxyMapping` (reverse proxy)
```json
{"name": "<name>.web", "url": "/<name>",
 "binding": "unix://{$SNAP_DATA}/package-run/<name>/web.sock",
 "restricted": ["/<name>/api"]}
```
* `name`: lowercase, contains `.web`, unique. `url`: lowercase, starts with `/`, should be `/<name>`.
* `binding`: note the **`{$SNAP_DATA}`** syntax (not `${SNAP_DATA}`). Socket path ≤ 108 chars,
  the part after `/var/snap/<name>/current/` ≤ 50 chars (shorten `package-run`→`run` if needed;
  the folder inside must be the snap name).
* `restricted`: paths for which the proxy validates the bearer token. **All API paths.**
  Object form also allowed: `{"path": "...", "except": [...], "headersForwarding": ["X-Auth-Scopes", ...]}`.
* Optional: `"options": [{"option": "websockets", "value": ""}]` for WebSocket support.

### `menus`
`sidebar`, `settings`, `overview`, `system` arrays. Each entry: `id` (start with the snap name,
unique), `title`, **`items` (required by the schema, use `[]`)**, optional `link`, `target`,
`icon` (`bosch-ic-*`), `permissions` (scope ids), `description`, `i18n`.
* `link` placeholders: `${bearertoken}` (current user token), `${hostname}`.
* `target`: `_blank` (recommended for 3rd-party UIs, new tab) or `embedded` (inside the ctrlX UI;
  only if your page is designed for it) or a window name.
* **`settings` / `system` entries (ctrlX OS 4.6, verified)**: the Settings page builds tiles from the
  entry's URL only - with `target: "_blank"` the tile has **no link** (it cannot open a new tab);
  omit `target` (opens in the same tab) or use `embedded`. Tiles are grouped by **`category`**:
  `0_apps`, `0_device`, `1_system`, `2_security`, `3_network` (shown as Apps, Device, ...); a missing or
  other category is shown as `core.settings.categories.<name>`. Check: `MENU-SETTINGS`.
  The template has no settings entry; its settings are part of its own UI (sidebar/overview).
* Link to your app data in the ctrlX file view: `/solutions/configurations/appdata/content?path=<dir>%2F`
  (pattern used by community apps).

### `commands.activeConfiguration` (app data save/load)
```json
"save": [{"id": "<name>", "subject": "<Title> settings", "url": "/<name>/api/v1/appdata/save"}],
"load": [{"id": "<name>", "subject": "<Title> settings", "url": "/<name>/api/v1/appdata/load"}]
```
URLs starting with `/` are relative to `https://localhost` → they go through the reverse proxy,
so they must be below the proxy url and inside `restricted`. Protocol: `app-data-settings.md`.

### `configuration`
```json
"appDirectories": [{"name": "<name>", "description": "<Title>", "icon": "bosch-ic-components",
                    "copyOnLoad": true, "writeProtected": false}],
"appPrivateFiles": ["^<name>/.*\\.tmp$"]
```
* `name` = folder in the active configuration (lowercase, start with the snap name).
* `copyOnLoad: true` → Solutions copies the folder on load before "prepare".
* `writeProtected` default **true** (users can't edit via WebDAV/UI). Template sets `false`
  so users may edit `settings.json` in "Manage app data"; the app watches the file.
* `appPrivateFiles`: RE2 regexes of files excluded from save/load (temp files, caches).

## Sections you may add later
* `licenses` - not used: this kit builds unofficial, unlicensed apps (no ctrlX license check)
* `certificatestores` + slot `package-certificates` (key/cert management UI)
* `apps.rexroth-setup.settings` (device backup/restore of settings) → `upstream-sdk-4.6.0/persist-device-settings.md`
* `uiExtensions.dashboard` (home screen widgets as Web Components) - how to write one:
  `web-ui-and-reverse-proxy.md` → "Home screen widgets"; example `apps/ctrlx-amr-fleet`
* `documentation` (API docs in the help menu), `dependencies.dependsOn` (informational)
* `i18n` tags + `i18n/<name>.package-manifest.de.json` translations
