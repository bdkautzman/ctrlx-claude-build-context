"""App-specific constraints for settings.json, beyond "same type as the default".

The defaults file (defaults/settings.json) defines the STRUCTURE and TYPES.
This module adds VALUE rules. Keys are dotted paths into the settings object.

Supported rule keys:
  enum:     list of allowed values
  min/max:  numeric bounds (inclusive)
  minLength/maxLength: string length bounds
  label:    human readable label for the web UI (optional)
  help:     help text for the web UI (optional)
  hidden:   true -> not shown in the web UI (value is preserved)

The web UI fetches these rules from GET <api>/settings/meta and renders matching inputs.
When you add a setting: add it to defaults/settings.json AND (if it has limits) here.
"""

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
    "example.enabled": {"label": "Example enabled"},
    "example.label": {"label": "Label", "minLength": 0, "maxLength": 80},
    "example.setpoint": {
        "label": "Initial setpoint",
        "min": -1.0e6,
        "max": 1.0e6,
        "help": "Start value of the Data Layer node values/setpoint after the app starts.",
    },
}
