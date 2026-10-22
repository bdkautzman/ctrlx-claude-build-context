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
  hidden:   true -> not shown in the generic settings form (value is preserved)
  items:    for arrays of objects: {"template": {...}, "rules": {field: rule}} - every element
            is normalized against the template (types, missing keys) and checked with the rules
  maxItems: maximum array length
  unique:   field name that must be unique within an array of objects (also used to match
            elements when restoring secrets)

The web UI fetches these rules from GET <api>/settings/meta and renders matching inputs.
When you add a setting: add it to defaults/settings.json AND (if it has limits) here.
"""

CORE_TEMPLATE = {
    "name": "",
    "host": "",
    "user": "",
    "password": "",
    "sslPort": 443,
    "skipCertificateCheck": True,
}

RULES = {
    # Format version of settings.json. Increase it (and migrate in settings_store) when you make
    # an incompatible change. Hidden in the UI.
    "settingsVersion": {"min": 1, "hidden": True},
    "general.logLevel": {
        "label": "Log level",
        "enum": ["ERROR", "WARNING", "INFO", "DEBUG"],
    },
    "general.pollIntervalMs": {
        "label": "Poll interval [ms]",
        "min": 500,
        "max": 60000,
        "help": "How often the status of the connected cores is read.",
    },
    # Connections to other ctrlX COREs. Edited with the + dialog of the web UI (or by hand in
    # "Manage app data"); hidden in the generic settings form.
    "cores": {
        "hidden": True,
        "maxItems": 32,
        "unique": "name",
        "items": {
            "template": CORE_TEMPLATE,
            "rules": {
                # becomes the Data Layer remote "devices/remotes/cm-<name>" (one path segment)
                "name": {"label": "Name", "pattern": r"[a-z0-9][a-z0-9-]{0,23}",
                         "help": "Lowercase letters, digits and '-', max. 24 characters."},
                "host": {"label": "IP address / host name", "pattern": r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}"},
                "user": {"label": "User", "pattern": r"[A-Za-z0-9._-]{1,64}"},
                "password": {"label": "Password", "secret": True, "minLength": 1, "maxLength": 128},
                "sslPort": {"label": "HTTPS port", "min": 1, "max": 65535},
                "skipCertificateCheck": {"label": "Skip certificate check"},
            },
        },
    },
}
