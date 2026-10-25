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

validate_settings() below adds the checks rules can't express (target/port/schedule syntax);
SettingsStore runs it after the rules passed.
"""

from app import ports, rating, scheduler, targets

PROFILE_TEMPLATE = {
    "id": "", "name": "", "targets": "auto", "exclude": "", "tcpPorts": "common", "udpPorts": "",
    "discoverNeighbors": True, "discoverTcp": True, "discoverMulticast": True, "assumeAlive": False,
    "serviceDetection": True, "identifyAutomation": False, "resolveNames": True,
    "timeoutMs": 1000, "retries": 0, "concurrency": 32, "ratePerSecond": 100,
    "scheduleMode": "off", "scheduleIntervalMinutes": 60, "scheduleTimes": "02:00", "scheduleDays": "",
}
PROFILE_RULES = {
    "id": {"pattern": r"[a-z0-9][a-z0-9-]{0,31}", "label": "ID",
           "help": "Short name used in the Data Layer (scan/start) - lowercase letters, digits, '-'"},
    "name": {"minLength": 1, "maxLength": 60, "label": "Name"},
    "targets": {"minLength": 1, "maxLength": 2000, "label": "Targets"},
    "exclude": {"maxLength": 2000, "label": "Exclude"},
    "tcpPorts": {"maxLength": 500, "label": "TCP ports"},
    "udpPorts": {"maxLength": 500, "label": "UDP ports"},
    "timeoutMs": {"min": 100, "max": 10000, "label": "Timeout [ms]"},
    "retries": {"min": 0, "max": 3, "label": "Retries"},
    "concurrency": {"min": 1, "max": 256, "label": "Parallel attempts"},
    "ratePerSecond": {"min": 1, "max": 2000, "label": "Max. attempts per second"},
    "scheduleMode": {"enum": list(scheduler.MODES), "label": "Schedule"},
    "scheduleIntervalMinutes": {"min": 5, "max": 10080, "label": "Interval [min]"},
    "scheduleTimes": {"maxLength": 200, "label": "Times"},
    "scheduleDays": {"maxLength": 60, "label": "Days"},
}
LIST_ENTRY_TEMPLATE = {"hosts": "", "ports": "", "note": ""}
LIST_ENTRY_RULES = {
    "hosts": {"maxLength": 1000},
    "ports": {"maxLength": 500},
    "note": {"maxLength": 120},
}

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
    "scan.maxAddresses": {
        "label": "Max. addresses per scan",
        "min": 1,
        "max": 65536,
        "help": "A scan whose targets cover more addresses is refused before it starts.",
    },
    "history.maxScans": {"label": "Keep at most [scans]", "min": 1, "max": 1000},
    "history.maxMegabytes": {"label": "Keep at most [MB]", "min": 1, "max": 200},
    "profiles": {
        "label": "Scan profiles",
        "minItems": 1,
        "maxItems": 20,
        "unique": "id",
        "items": {"template": PROFILE_TEMPLATE, "rules": PROFILE_RULES},
    },
    "lists.whitelist": {
        "label": "Whitelist (expected)",
        "maxItems": 500,
        "items": {"template": LIST_ENTRY_TEMPLATE, "rules": LIST_ENTRY_RULES},
    },
    "lists.blacklist": {
        "label": "Blacklist (forbidden)",
        "maxItems": 500,
        "items": {"template": LIST_ENTRY_TEMPLATE, "rules": LIST_ENTRY_RULES},
    },
}


def validate_settings(settings):
    """Syntax of targets, ports, schedules and list entries -> error messages."""
    errors = []
    for i, p in enumerate(settings.get("profiles", [])):
        where = "profiles[{}]".format(i)
        errors += ["{}.targets: {}".format(where, e) for e in targets.check_spec(p["targets"])]
        errors += ["{}.exclude: {}".format(where, e)
                   for e in targets.check_spec(p["exclude"], allow_auto=False, allow_empty=True)]
        errors += ["{}.tcpPorts: {}".format(where, e) for e in ports.check_spec(p["tcpPorts"], "tcp")]
        errors += ["{}.udpPorts: {}".format(where, e) for e in ports.check_spec(p["udpPorts"], "udp")]
        errors += ["{}.{}".format(where, e) for e in scheduler.check_profile(p)]
    for kind in ("whitelist", "blacklist"):
        for i, entry in enumerate(settings.get("lists", {}).get(kind, [])):
            errors += ["lists.{}[{}]: {}".format(kind, i, e) for e in rating.check_entry(entry)]
    return errors
