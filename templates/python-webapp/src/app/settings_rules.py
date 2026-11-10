"""App-specific constraints for settings.json, beyond "same type as the default".

The defaults file (defaults/settings.json) defines the STRUCTURE and TYPES.
This module adds VALUE rules. Keys are dotted paths into the settings object.

Supported rule keys:
  enum:     list of allowed values
  min/max:  numeric bounds (inclusive)
  minLength/maxLength: string length bounds
  pattern:  regular expression the whole string must match
  secret:   true -> value is masked in every API response / Data Layer node; a masked value
            sent back by the UI means "keep the stored value"
  label:    human readable label for the web UI (optional)
  help:     help text for the web UI (optional)
  hidden:   true -> not shown in the web UI (value is preserved)
  items:    for arrays of objects: {"template": {...}, "rules": {field: rule}} - every element
            is normalized against the template (types, missing keys) and checked with the rules
  maxItems/minItems: maximum/minimum array length
  itemPattern: arrays of strings - regular expression every element must match
  format:   "color" -> the web UI shows a color picker + hex field (with pattern HEX_COLOR: the COLOR rule below)
  unique:   field name that must be unique within an array of objects (also used to match
            elements when restoring secrets)
  Example with items, unique, secret, pattern, maxItems: apps/ctrlx-core-connector/src/app/settings_rules.py
  ("cores", the connections to other ctrlX COREs).

The web UI fetches these rules from GET <api>/settings/meta and renders matching inputs.
When you add a setting: add it to defaults/settings.json AND (if it has limits) here.

Checks the rules can't express (syntax of an address list, cross-field checks): write a function
validate_settings(settings) -> [error messages] and pass it to SettingsStore(..., validator=...);
it runs on every validation, messages start with the dotted path. Example:
apps/ctrlx-network-scanner/src/app/settings_rules.py.
"""

HEX_COLOR = r"#[0-9a-fA-F]{6}"
COLOR = {"pattern": HEX_COLOR, "format": "color"}

RULES = {
    # Format version of settings.json. Increase it (and migrate in settings_store) when you make
    # an incompatible change. Hidden in the UI.
    "settingsVersion": {"min": 1, "hidden": True},
    "general.logLevel": {
        "label": "Log level",
        "enum": ["ERROR", "WARNING", "INFO", "DEBUG"],
    },
    "general.heartbeatIntervalMs": {
        "label": "Heartbeat interval [ms]",
        "min": 100,
        "max": 60000,
        "help": "Update interval of the Data Layer heartbeat node.",
    },
    "example.enabled": {"label": "Example enabled",
                        "help": "Start value of the Data Layer node values/enabled; while it is false, the example "
                                "command commands/set-setpoint ends with an error."},
    "example.label": {"label": "Label", "minLength": 0, "maxLength": 80,
                      "help": "Text of the read-only Data Layer node values/label."},
    "example.setpoint": {
        "label": "Initial setpoint",
        "min": -1.0e6,
        "max": 1.0e6,
        "help": "Start value of the Data Layer node values/setpoint after the app starts.",
    },
    # Page colors for every user of the app (GET /status "style" -> kit.js Kit.applyStyle). Page background, lines
    # and secondary text are derived from them (kit.css). Light or dark: each browser's choice (top bar button).
    "style": {"label": "Styling"},
    "style.accent": dict(COLOR, label="Accent", help="Buttons, the brand mark, focus rings - in light and dark mode."),
    "style.panel": dict(COLOR, label="Panels", help="Top bar, cards, settings. Page background, lines and secondary "
                                                     "text are derived from Panels and Text."),
    "style.text": dict(COLOR, label="Text"),
    "style.panelDark": dict(COLOR, label="Panels (dark mode)",
                            help="Panels while the page is in dark mode (the moon/sun button in the top bar)."),
    "style.textDark": dict(COLOR, label="Text (dark mode)"),
}
