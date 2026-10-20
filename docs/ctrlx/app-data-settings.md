# Settings in ctrlX app data (Solutions)

Upstream spec: `upstream-sdk-4.6.0/persistdata.md`. Implementation: `app/settings_store.py`,
`app/appdata_commands.py`, `app/settings_rules.py`, `defaults/settings.json`.

## Model

| What | Where |
|---|---|
| defaults (structure + types) | `defaults/settings.json` → `$SNAP/defaults/settings.json` |
| value rules (enum/min/max/labels/help/hidden) | `app/settings_rules.py` (`RULES`, dotted paths) |
| active settings | `$SNAP_COMMON/solutions/activeConfiguration/<name>/settings.json` |
| archived configurations | managed by the Solutions app - never touch |

`activeConfiguration` is a symlink maintained by the Solutions app, visible through the
`active-solution` content plug. It may appear **after** the daemon started → the app starts
with defaults, waits up to 30 s, and keeps polling (`main.py`).

## Behaviour implemented

* **Validation**: candidate is normalized against the defaults (missing keys filled, unknown
  keys preserved for forward compatibility, wrong types rejected, ints accepted for floats),
  then `RULES` are applied. Invalid content never replaces the active settings.
* **Write**: atomic (`settings.json.tmp` → fsync → rename), mode 0600, only on real changes.
  `*.tmp` is excluded from save/load via `appPrivateFiles`.
* **External edits** (`writeProtected: false`): `poll_external_change()` stat()s the file every
  2 s and reloads on mtime change; invalid edits are logged once and ignored.
* **First start / file deleted**: current settings are written to create the file.
* **Listeners**: `store.add_listener(fn)` → called after every applied change (log level,
  Data Layer nodes, …). Put "apply settings" logic there, never read the file elsewhere.

## Solutions save/load protocol (manifest `commands.activeConfiguration`)

POST body `{"configurationPath": "...", "id": "282xy5", "phase": "..."}`, token in header.

| Operation | Phase | Template does | Answer |
|---|---|---|---|
| save | `save` | write active settings to the file | 204 |
| load | `query` | can we load now? pause polling | 204 (or 409 + mainDiagnosisCode `080F0E00` if a device state is required) |
| load | `prepare` | pause polling | 204 |
| load | `load` | Solutions already copied the files (copyOnLoad) → reload; no file → reset to defaults | 204 / 400 Problem |
| load | `validate` | – | 204 |
| load | `activate` / `abort` | resume polling | 204 |
| any | unknown phase | – | **204** (mandatory forward compatibility) |

* After every load, Solutions triggers a save with the same id (since XCR-V-0120).
* Failures: Problem JSON with `title`, `detail`, `dynamicDescription` ending in `[<id>]`.
* Long operations (> 1 min) must use the async protocol: 201 + `Location`, then
  `{"state": "pending|running|done|failed"}` on GET.
* Endpoints must check the token scopes (template: `.rw` or admin).

## Adding a setting (checklist)

1. Add key + default value (with the right JSON type: `1.0` for float, `1` for int) to `defaults/settings.json`.
2. If it has limits/choices/label/help: add an entry in `app/settings_rules.py`.
3. Apply it in a listener (`main.py` / `DataLayerService.on_settings_changed`).
4. Incompatible change (rename, meaning change)? Bump `settingsVersion` and migrate old files
   in `SettingsStore.load_from_disk` before validation. Never break older files: configurations
   are archived and loaded years later.
5. Run tests: `tests/test_settings.py` checks that every rule has a default.
The web form is generated automatically from defaults + rules (`/api/v1/settings/meta`).

## Not app data

Device-specific data (certificates, network settings, credentials) does **not** belong in a
configuration. Use `$SNAP_COMMON` (across revisions) / `$SNAP_DATA` (per revision) for those,
and the Setup backup mechanism (`apps.rexroth-setup.settings` in the manifest,
`upstream-sdk-4.6.0/persist-device-settings.md`) if they must be in device backups.
Secrets: never plain text in app data; use `encryptedFile` setup settings or the certificate store.
