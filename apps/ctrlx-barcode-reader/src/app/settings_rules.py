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

import re

from app.decoder import FORMAT_NAMES

PIXEL_FORMATS = ["", "Mono8", "BayerRG8", "BayerGR8", "BayerGB8", "BayerBG8", "RGB8", "BGR8"]
AUTO_MODES = ["Off", "Once", "Continuous"]

RULES = {
    # Format version of settings.json. Increase it (and migrate in settings_store) when you make
    # an incompatible change. Hidden in the UI.
    "settingsVersion": {"min": 1, "hidden": True},
    "general": {"label": "General"},
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
    # -- camera ------------------------------------------------------------------------------
    "camera": {"label": "Camera"},
    "camera.source": {
        "label": "Source",
        "enum": ["gige", "simulated"],
        "help": "gige: a GigE Vision camera (Aravis). simulated: Aravis' built-in fake camera with "
                "generated test codes (or images from the app data folder test-images/).",
    },
    "camera.address": {
        "label": "Camera address",
        "minLength": 0,
        "maxLength": 128,
        "pattern": r"[A-Za-z0-9._:\-]*",
        "help": "IP address (e.g. 192.168.1.99) or Aravis device id from Discover. "
                "Empty: the first camera found.",
    },
    "camera.pixelFormat": {
        "label": "Pixel format",
        "enum": PIXEL_FORMATS,
        "help": "Empty: keep the camera's format. 8-bit formats only. Mono8 is enough for codes and the "
                "fastest; a Bayer format shows a color image.",
    },
    "camera.exposureUs": {"label": "Exposure time [us]", "min": 1.0, "max": 10000000.0},
    "camera.exposureAuto": {"label": "Auto exposure", "enum": AUTO_MODES},
    "camera.gain": {"label": "Gain", "min": 0.0, "max": 1000.0,
                    "help": "In the camera's own unit (STC-MCS500POE: 0-208)."},
    "camera.gainAuto": {"label": "Auto gain", "enum": AUTO_MODES},
    "camera.packetSize": {
        "label": "Packet size [bytes]",
        "min": 0,
        "max": 9000,
        "help": "GigE Vision stream packet size. 0: negotiate the largest size the network passes.",
    },
    "camera.acquisition": {
        "label": "Acquisition",
        "enum": ["singleframe", "trigger"],
        "help": "How a read gets a fresh image. singleframe: one SingleFrame acquisition per read (standard "
                "GenICam, works with the STC-MCS500POE). trigger: acquisition keeps running, a software trigger "
                "per read (faster, for cameras whose software trigger works).",
    },
    "camera.packetDelayUs": {
        "label": "Packet delay [us]",
        "min": -2.0,
        "max": 100000.0,
        "help": "Pause between stream packets; shorter is faster, too short loses images. -2: automatic (6 us on "
                "amd64 devices such as the X5 and X7, 40 us on arm64 such as the X3; raised step by step after "
                "repeated failed images, Reconnect starts over). -1: keep the camera's value. 0 or more: this value.",
    },
    "camera.timeoutMs": {"label": "Image timeout [ms]", "min": 200, "max": 60000,
                         "help": "How long a read waits for the image after the trigger."},
    "camera.buffers": {"label": "Stream buffers", "min": 2, "max": 8,
                       "help": "Frame buffers given to Aravis (one frame each in RAM)."},
    # -- decoding -----------------------------------------------------------------------------
    "decode": {"label": "Decoding"},
    "decode.formats": {
        "label": "Symbologies",
        "minItems": 1,
        "maxItems": len(FORMAT_NAMES),
        "itemPattern": "|".join(re.escape(n) for n in FORMAT_NAMES),
        "help": "Fewer symbologies decode faster.",
    },
    "decode.tryRotate": {"label": "Rotated codes", "help": "Also look for codes rotated by 90/270 degrees."},
    "decode.tryInvert": {"label": "Inverted codes", "help": "Also look for light codes on a dark background."},
    "decode.tryDownscale": {"label": "Downscaled search", "help": "Also search downscaled copies (large codes)."},
    "decode.maxCodes": {"label": "Max. codes per image", "min": 1, "max": 100},
    "decode.roi": {"label": "Decode region"},
    "decode.roi.enabled": {"label": "Only decode the region"},
    "decode.roi.onCamera": {
        "label": "Camera sends only the region",
        "help": "Much faster for a label that stays in one place: less to transfer and decode. The picture then "
                "shows only the region; clear the region to see the full frame and draw a new one.",
    },
    "decode.roi.x": {"label": "X [px]", "min": 0, "max": 100000},
    "decode.roi.y": {"label": "Y [px]", "min": 0, "max": 100000},
    "decode.roi.width": {"label": "Width [px]", "min": 0, "max": 100000},
    "decode.roi.height": {"label": "Height [px]", "min": 0, "max": 100000},
    # -- modes --------------------------------------------------------------------------------
    "live": {"label": "Live preview"},
    "live.intervalMs": {"label": "Interval [ms]", "min": 100, "max": 10000},
    "live.width": {"label": "Image width [px]", "min": 160, "max": 4096},
    "continuous": {"label": "Continuous reading"},
    "continuous.intervalMs": {"label": "Interval [ms]", "min": 10, "max": 3600000,
                              "help": "Time between the starts of two reads (longer if a read takes longer)."},
    # -- images -------------------------------------------------------------------------------
    "images": {"label": "Images in RAM"},
    "images.displayWidth": {"label": "Display width [px]", "min": 320, "max": 4096},
    "images.jpegQuality": {"label": "JPEG quality", "min": 30, "max": 100},
    "images.historySize": {"label": "History entries", "min": 1, "max": 200,
                           "help": "Results kept in RAM with their display image."},
    "storage": {"label": "Image storage"},
    "storage.target": {
        "label": "Store images",
        "enum": ["ram", "medium"],
        "help": "ram: only the history in RAM. medium: also save every read to the removable medium "
                "below (never to the internal flash).",
    },
    "storage.medium": {
        "label": "Medium",
        "maxLength": 64,
        "pattern": r"(?!\.\.?$)[^/\\\x00]*",      # one folder name under /media, not . or ..
        "help": "Folder name under /media (Settings > Storage mounts USB sticks and SD cards there).",
    },
    "storage.save": {"label": "Save", "enum": ["all", "noread"],
                     "help": "all: every read. noread: only reads without a code or with an error."},
    "storage.format": {"label": "File format", "enum": ["jpg", "png"],
                       "help": "png is lossless but ~5x larger and slower."},
    "storage.jpegQuality": {"label": "JPEG quality", "min": 30, "max": 100},
    "storage.maxMb": {"label": "Max. size [MB]", "min": 10, "max": 1000000,
                      "help": "When the app's folder on the medium gets larger, the oldest images are deleted."},
    # -- simulation ---------------------------------------------------------------------------
    "simulation": {"label": "Simulated camera"},
    "simulation.width": {"label": "Width [px]", "min": 256, "max": 4096},
    "simulation.height": {"label": "Height [px]", "min": 256, "max": 4096},
    "simulation.codes": {"label": "Codes per image", "min": 0, "max": 6},
}


def validate_settings(settings):
    """Cross-field checks -> error messages."""
    errors = []
    delay = settings.get("camera", {}).get("packetDelayUs", 0)
    if delay < 0 and delay not in (-1, -2):
        errors.append("camera.packetDelayUs: -2 (automatic), -1 (keep the camera's value) or 0 and more")
    roi = settings.get("decode", {}).get("roi", {})
    if roi.get("enabled") and (roi.get("width", 0) < 16 or roi.get("height", 0) < 16):
        errors.append("decode.roi: the region must be at least 16 x 16 pixels")
    storage = settings.get("storage", {})
    if storage.get("target") == "medium" and not storage.get("medium"):
        errors.append("storage.medium: choose a medium to store images on")
    formats = settings.get("decode", {}).get("formats", [])
    if len(set(formats)) != len(formats):
        errors.append("decode.formats: a symbology is listed twice")
    return errors
