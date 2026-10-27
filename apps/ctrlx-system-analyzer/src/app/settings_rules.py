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
  unique:   field name that must be unique within an array of objects (also used to match
            elements when restoring secrets)
  Example with all of them: apps/ctrlx-core-monitor/app/settings_rules.py ("cores").

The web UI fetches these rules from GET <api>/settings/meta and renders matching inputs.
When you add a setting: add it to defaults/settings.json AND (if it has limits) here.

Checks the rules can't express (syntax of an address list, cross-field checks): write a function
validate_settings(settings) -> [error messages] and pass it to SettingsStore(..., validator=...);
it runs on every validation, messages start with the dotted path. Example:
apps/ctrlx-network-scanner/src/app/settings_rules.py.
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
    "collector": {"label": "Sampling"},
    "collector.intervalSeconds": {
        "label": "Sample interval [s]",
        "min": 1,
        "max": 60,
        "help": "How often /proc is read. Shorter = finer CPU curves, more load (the analyzer shows its own CPU).",
    },
    "collector.threads": {
        "label": "Threads",
        "help": "CPU per thread of every multi-threaded process (the ctrlX real-time tasks are threads).",
    },
    "collector.pss": {
        "label": "PSS (proportional memory)",
        "help": "Read each process's PSS every 30 s: shared memory counted once in total. Costs a little CPU.",
    },
    "history": {"label": "History in RAM"},
    "history.rawMinutes": {
        "label": "Every sample for [min]",
        "min": 10,
        "max": 360,
    },
    "history.minuteHours": {
        "label": "1-minute min/avg/max for [h]",
        "min": 1,
        "max": 72,
    },
    "history.tenMinuteDays": {
        "label": "10-minute min/avg/max for [days]",
        "min": 1,
        "max": 30,
        "help": "Threads are kept at 1-minute resolution only.",
    },
    "history.maxMemoryMb": {
        "label": "RAM budget [MB]",
        "min": 10,
        "max": 200,
        "help": "Upper limit for the history arrays (the app itself needs ~40 MB more; the App Guide asks for "
                "< 75 MB in total). When full, ended processes go first; then no new process/thread series start.",
    },
    "leak": {"label": "Leak detection"},
    "leak.metric": {
        "label": "Memory metric",
        "enum": ["anon", "rss", "pss"],
        "help": "anon = heap and other private memory (where leaks show), rss = all resident memory, "
                "pss = resident with shared pages split between the processes.",
    },
    "leak.minMbPerHour": {
        "label": "Growing from [MB/h]",
        "min": 0.01,
        "max": 10000,
    },
    "leak.minFit": {
        "label": "Minimum fit (R²)",
        "min": 0,
        "max": 1,
        "help": "How straight the growth must be: 1 = a perfect line, 0 = no trend.",
    },
    "leak.minSpanMinutes": {
        "label": "Minimum time span [min]",
        "min": 5,
        "max": 10080,
    },
    "leak.nodeWindowMinutes": {
        "label": "Data Layer window [min]",
        "min": 10,
        "max": 1440,
        "help": "Time span of the Data Layer node analysis/growing.",
    },
    "recording": {"label": "External medium"},
    "recording.medium": {
        "label": "Medium",
        "maxLength": 64,
        "pattern": r"(?!\.\.?$)[^/\\\x00]*",      # one folder name, not . or ..
        "hidden": True,
        "help": "Folder name under /media - chosen in the Recording card.",
    },
    "recording.threads": {
        "label": "Record threads",
        "help": "Also write one row per thread and minute (larger files).",
    },
}
