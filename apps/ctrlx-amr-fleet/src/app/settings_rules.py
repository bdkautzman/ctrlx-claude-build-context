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
  format:   "color" -> the web UI shows a color picker + hex field (strings and arrays of strings)
  unique:   field name that must be unique within an array of objects (also used to match
            elements when restoring secrets)
  Example with all of them: apps/ctrlx-core-monitor/app/settings_rules.py ("cores").

The web UI fetches these rules from GET <api>/settings/meta and renders matching inputs.
When you add a setting: add it to defaults/settings.json AND (if it has limits) here.
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
    "map": {"label": "Map"},
    "map.file": {
        "label": "Map file",
        "pattern": r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}",
        "help": "File name of the map: searched in this app's app data folder first "
                "(Manage app data), then in the maps bundled with the app (HMI_Map.txt).",
    },
    "map.resolution": {
        "label": "Map cell size [map units]",
        "min": 0.0,
        "max": 1.0e6,
        "help": "Size of one map cell. 0 = automatic (greatest common divisor of the coordinates).",
    },
    "map.unitsPerMeter": {
        "label": "Map units per meter",
        "min": 1.0e-6,
        "max": 1.0e6,
        "help": "1000 = map and AMR coordinates in mm. Used for the scale bar, the AMR size "
                "and the simulation speed.",
    },
    "fleet": {"label": "Fleet"},
    "fleet.rotationUnit": {
        "label": "Rotation unit",
        "enum": ["deg", "rad"],
        "help": "Unit of \"rotation\" in the fleet JSON (counter-clockwise from the +x axis).",
    },
    "fleet.staleAfterMs": {
        "label": "Stale after [ms]",
        "min": 500,
        "max": 600000,
        "help": "AMRs are shown greyed out when the fleet data is older than this.",
    },
    "fleet.amrLengthM": {"label": "AMR length [m]", "min": 0.05, "max": 50.0},
    "fleet.amrWidthM": {"label": "AMR width [m]", "min": 0.05, "max": 50.0},
    "display": {"label": "Display"},
    "display.refreshIntervalMs": {
        "label": "Refresh interval [ms]",
        "min": 100,
        "max": 10000,
        "help": "How often the web page fetches the AMR positions (movement is interpolated).",
    },
    "style": {"label": "Styling"},
    "style.accent": dict(COLOR, label="Accent", help="Buttons, selection, the locked AMR, the overview frame."),
    "style.panel": dict(COLOR, label="Panels", help="Top bar, AMR list, tool buttons, settings. Page "
                                                     "background, lines and secondary text are derived."),
    "style.text": dict(COLOR, label="Text"),
    "style.mapBackground": dict(COLOR, label="Outside the map"),
    "style.mapFloor": dict(COLOR, label="Map floor"),
    "style.mapWalls": dict(COLOR, label="Map walls and obstacles"),
    "style.mapGrid": dict(COLOR, label="Grid lines"),
    "style.staleAmr": dict(COLOR, label="AMRs without current data"),
    "style.amrColors": {"label": "AMR colors", "format": "color", "itemPattern": HEX_COLOR, "minItems": 1,
                        "maxItems": 16, "help": "Each AMR gets one of these colors (always the same one for an id)."},
    "simulation": {"label": "Simulation"},
    "simulation.enabled": {
        "label": "Simulate AMRs",
        "help": "Demo/test without a PLC: the app moves AMRs through the free space of the map "
                "and overwrites fleet/amrs every 200 ms.",
    },
    "simulation.amrCount": {"label": "Simulated AMRs", "min": 1, "max": 50},
    "simulation.speedMps": {"label": "Simulation speed [m/s]", "min": 0.05, "max": 10.0},
}
