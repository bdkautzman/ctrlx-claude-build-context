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
  wide:     true -> the field takes the whole row of the settings form (long texts, long drop-down entries)
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

import re

from app.ads_types import valid_net_id
from app.datalayer_nodes import SUPPORTED_TYPES
from app.plc_nodes import valid_address

HEX_COLOR = r"#[0-9a-fA-F]{6}"
COLOR = {"pattern": HEX_COLOR, "format": "color"}
NET_ID = r"\d{1,3}(\.\d{1,3}){5}"

CONTROLLER_TEMPLATE = {
    "name": "",
    "host": "",
    "amsNetId": "",
    "amsPort": 851,
    "pollIntervalMs": 100,
    "enabled": True,
}
NODE_TEMPLATE = {
    "controller": "",
    "symbol": "",
    "address": "",
    "type": "",
    "writable": False,
    "description": "",
    "unit": "",
}
CONTROLLER_NAME = r"[A-Za-z0-9][A-Za-z0-9_-]{0,23}"
MAX_CONTROLLERS = 32
MAX_NODES = 2000

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
        "help": "Update interval of the Data Layer node status/heartbeat.",
    },
    "ads": {"label": "ADS"},
    "ads.localNetId": {
        "label": "Local AMS Net ID",
        "pattern": "(" + NET_ID + ")?",
        "help": "The AMS Net ID of this device toward the controllers. Empty: its IP address toward the first "
                "controller + .1.1. Every controller needs a route to this Net ID (tile: Route). Going back to "
                "empty takes effect after a restart of the app.",
    },
    "ads.timeoutMs": {
        "label": "Timeout [ms]",
        "min": 100,
        "max": 10000,
        "help": "How long the app waits for a controller's answer to one ADS request (reads, writes, symbol "
                "upload). A Data Layer write waits up to three of them (symbol handle, write, release).",
    },
    # Read by the browser page (browse.js via GET /status) - not used by the server.
    "browser": {"label": "Browser"},
    "browser.refreshMs": {
        "label": "Read interval [ms]",
        "min": 250,
        "max": 60000,
        "help": "How often the browser page reads the values it shows you while it is open (the visible values "
                "in the tree and the selected variable). Not the Data Layer nodes: their values are read at the "
                "poll interval of each controller (Edit on the controller's tile).",
    },
    # The TwinCAT controllers: added / edited in the web UI (+ Add controller), hidden in the settings form.
    "controllers": {
        "hidden": True,
        "maxItems": MAX_CONTROLLERS,
        "unique": "name",
        "items": {
            "template": CONTROLLER_TEMPLATE,
            "rules": {
                # also the Data Layer folder connections/<name>/ of its nodes
                "name": {"label": "Name", "pattern": CONTROLLER_NAME,
                         "help": "Letters, digits, '_' and '-', max. 24 characters."},
                "host": {"label": "IP address / host name", "pattern": r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}"},
                "amsNetId": {"label": "AMS Net ID", "pattern": "(" + NET_ID + ")?"},
                "amsPort": {"label": "AMS port", "min": 1, "max": 65535},
                "pollIntervalMs": {"label": "Poll interval [ms]", "min": 20, "max": 60000},
                "enabled": {"label": "Enabled"},
            },
        },
    },
    # The Data Layer nodes of PLC variables (connections/<controller>/<address>): added in the browser, hidden here.
    "nodes": {
        "hidden": True,
        "maxItems": MAX_NODES,
        "items": {
            "template": NODE_TEMPLATE,
            "rules": {
                "controller": {"pattern": CONTROLLER_NAME},
                "symbol": {"minLength": 1, "maxLength": 512},
                "address": {"minLength": 1, "maxLength": 512},
                "type": {"enum": list(SUPPORTED_TYPES)},
                "description": {"maxLength": 1000},
                "unit": {"maxLength": 32},
            },
        },
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


def validate_settings(settings):
    """Cross-field checks (SettingsStore validator): Net IDs, names that differ only in case, nodes of unknown
    controllers, addresses that are taken or are a folder of another node."""
    errors = []
    def bad_range(net_id):    # the format is the rule's pattern; here only the 0-255 of every part
        return bool(net_id) and re.fullmatch(NET_ID, net_id) is not None and not valid_net_id(net_id)

    if bad_range(settings["ads"]["localNetId"]):
        errors.append("ads.localNetId: each part must be 0-255")
    names, net_ids = {}, {}
    for i, c in enumerate(settings["controllers"]):
        where = "controllers[{}]".format(i)
        lower = c["name"].lower()
        if lower in names and names[lower] != c["name"]:
            errors.append("{}.name: differs only in case from {}".format(where, names[lower]))
        names.setdefault(lower, c["name"])
        if c["amsNetId"]:
            if bad_range(c["amsNetId"]):
                errors.append("{}.amsNetId: each part must be 0-255".format(where))
            elif c["amsNetId"] in net_ids:
                errors.append("{}.amsNetId: already used by {} (one controller entry per AMS Net ID)".format(
                    where, net_ids[c["amsNetId"]]))
            net_ids.setdefault(c["amsNetId"], c["name"])
    seen = {}
    for i, n in enumerate(settings["nodes"]):
        where = "nodes[{}]".format(i)
        if n["controller"] not in {c["name"] for c in settings["controllers"]}:
            errors.append("{}.controller: no controller {}".format(where, n["controller"]))
        if not valid_address(n["address"]):
            errors.append("{}.address: letters, digits, '_' and '-' per level (max. 64), levels separated by '/'"
                          .format(where))
            continue
        key = (n["controller"], n["address"].lower())
        if key in seen:
            errors.append("{}.address: {} is already a node ({})".format(where, n["address"], seen[key]))
        seen.setdefault(key, n["symbol"])
    keys = {(c, a) for c, a in seen}
    for c, a in keys:
        parts = a.split("/")
        for k in range(1, len(parts)):
            if (c, "/".join(parts[:k])) in keys:
                errors.append("nodes: {}/{} is a node, so it can't be a folder of {}/{}".format(
                    c, "/".join(parts[:k]), c, a))
    return errors
