---
name: add-app-setting
description: Add or change a setting of a ctrlX app stored in settings.json in ctrlX app data (defaults, validation rules, UI, applying it). Use when the user wants a new configuration option.
---

# Add a setting

Read `docs/ctrlx/app-data-settings.md` first.

1. `defaults/settings.json`: add the key with a default of the right JSON type (`1.0` float, `1` int,
   `true`, `"text"`, nested objects for groups). This defines structure and type.
2. `src/app/settings_rules.py`: `"group.key": {"label": "...", "help": "...", "enum": [...] | "min"/"max" | "minLength"/"maxLength", "hidden": bool}`.
3. Apply it: in a listener (`store.add_listener(fn)` in `main.py` or `DataLayerService.on_settings_changed`)
   - never re-read the file elsewhere. Settings changes must be cheap and safe at runtime.
4. Compatibility: old `settings.json` files without the key still load (defaults fill it). Renames or
   meaning changes need a `settingsVersion` bump + migration in `SettingsStore.load_from_disk` + a test.
5. Tests: `tests/test_settings.py` (validation of the new rule; the "every rule has a default" test must pass).
6. The web form updates automatically (`/api/v1/settings/meta`). Check `./run-local.sh` → Settings card.
