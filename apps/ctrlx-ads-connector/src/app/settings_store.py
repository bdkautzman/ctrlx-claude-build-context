"""Settings persisted as JSON in the ctrlX app data (Solutions "activeConfiguration").

Layout on the device:
    $SNAP/defaults/settings.json                                    shipped defaults (read-only)
    $SNAP_COMMON/solutions/activeConfiguration/<snap>/settings.json  active settings (app data)

Why app data and not $SNAP_COMMON directly: files in the active configuration are saved
into and loaded from ctrlX configurations ("Manage app data", backup/restore of projects).

Rules implemented here (see docs/ctrlx/app-data-settings.md):
  * Structure and types come from the defaults file. Missing keys are filled from the
    defaults, unknown keys are kept (forward compatibility), wrong types are rejected.
  * Value rules (enum/min/max/pattern/...) come from app/settings_rules.py, including arrays
    of objects (`items`) and `secret` values that are masked for the UI and the Data Layer.
  * Writes are atomic (write *.tmp, fsync, rename) and only happen on a real change
    (user action, Solutions save). Never write cyclically: flash wear.
  * The file may be edited externally (writeProtected=false: WebDAV, "Manage app data"),
    so poll_external_change() reloads it when its mtime changes.

This module has no ctrlX dependencies and is fully unit-testable on any PC.
"""

import copy
import json
import logging
import math
import os
import re
import threading
import time

log = logging.getLogger("settings")

LOAD_OPERATION_TIMEOUT_S = 600
# After "query" Solutions sends nothing (no abort) if another participant refuses the load, so the
# pause started there must expire soon; "prepare" follows within seconds when the load goes ahead.
LOAD_QUERY_TIMEOUT_S = 60
# Shown instead of `secret` values; sent back unchanged by the UI = keep the stored value.
SECRET_MASK = "********"


class SettingsError(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = list(errors)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _type_name(value):
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if value is None:
        return "null"
    return type(value).__name__


def _normalize(candidate, default, path, errors):
    """Return candidate coerced to the type of default; append problems to errors."""
    where = path or "<root>"
    if isinstance(default, dict):
        if not isinstance(candidate, dict):
            errors.append("{}: expected object, got {}".format(where, _type_name(candidate)))
            return copy.deepcopy(default)
        result = {}
        for key, default_value in default.items():
            sub_path = key if not path else path + "." + key
            if key in candidate:
                result[key] = _normalize(candidate[key], default_value, sub_path, errors)
            else:
                result[key] = copy.deepcopy(default_value)
        for key, value in candidate.items():
            if key not in default:
                result[key] = copy.deepcopy(value)  # keep unknown keys
        return result
    if isinstance(default, bool):
        if isinstance(candidate, bool):
            return candidate
        errors.append("{}: expected boolean, got {}".format(where, _type_name(candidate)))
        return default
    if isinstance(default, int):
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            return candidate
        if isinstance(candidate, float) and candidate.is_integer():
            return int(candidate)
        errors.append("{}: expected integer, got {}".format(where, _type_name(candidate)))
        return default
    if isinstance(default, float):
        if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
            value = float(candidate)
            if math.isfinite(value):
                return value
            errors.append("{}: must be a finite number".format(where))
            return default
        errors.append("{}: expected number, got {}".format(where, _type_name(candidate)))
        return default
    if isinstance(default, str):
        if isinstance(candidate, str):
            return candidate
        errors.append("{}: expected string, got {}".format(where, _type_name(candidate)))
        return default
    if isinstance(default, list):
        if isinstance(candidate, list):
            return copy.deepcopy(candidate)
        errors.append("{}: expected array, got {}".format(where, _type_name(candidate)))
        return copy.deepcopy(default)
    return copy.deepcopy(candidate)


def type_map(defaults, path=""):
    """{dotted_path: json type name} for all leaf values - used by the web UI."""
    result = {}
    for key, value in defaults.items():
        full = key if not path else path + "." + key
        if isinstance(value, dict):
            result.update(type_map(value, full))
        else:
            result[full] = _type_name(value)
    return result


def get_path(obj, dotted):
    for part in dotted.split("."):
        if not isinstance(obj, dict) or part not in obj:
            raise KeyError(dotted)
        obj = obj[part]
    return obj


def set_path(obj, dotted, value):
    parts = dotted.split(".")
    for part in parts[:-1]:
        obj = obj[part]
    obj[parts[-1]] = value


def _check_value(value, rule, where, errors):
    if "enum" in rule and value not in rule["enum"]:
        errors.append("{}: must be one of {}".format(where, rule["enum"]))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "min" in rule and value < rule["min"]:
            errors.append("{}: must be >= {}".format(where, rule["min"]))
        if "max" in rule and value > rule["max"]:
            errors.append("{}: must be <= {}".format(where, rule["max"]))
    if isinstance(value, str):
        if "minLength" in rule and len(value) < rule["minLength"]:
            errors.append("{}: must have at least {} characters".format(where, rule["minLength"]))
        if "maxLength" in rule and len(value) > rule["maxLength"]:
            errors.append("{}: must have at most {} characters".format(where, rule["maxLength"]))
        if "pattern" in rule and not re.fullmatch(rule["pattern"], value):
            errors.append("{}: invalid format".format(where))
    if isinstance(value, list) and "maxItems" in rule and len(value) > rule["maxItems"]:
        errors.append("{}: at most {} entries".format(where, rule["maxItems"]))
    if isinstance(value, list) and "minItems" in rule and len(value) < rule["minItems"]:
        errors.append("{}: at least {} entries".format(where, rule["minItems"]))
    if isinstance(value, list) and "itemPattern" in rule:   # arrays of strings
        for i, item in enumerate(value):
            if not isinstance(item, str) or not re.fullmatch(rule["itemPattern"], item):
                errors.append("{}[{}]: invalid format".format(where, i))


def _normalize_items(value, rule, where, errors):
    """Normalize every element of an array of objects against rule["items"]."""
    template, item_rules = rule["items"].get("template", {}), rule["items"].get("rules", {})
    result, seen, unique = [], set(), rule.get("unique")
    for i, item in enumerate(value):
        item_where = "{}[{}]".format(where, i)
        item = _normalize(item, template, item_where, errors)
        for key, item_rule in item_rules.items():
            if key in item:
                _check_value(item[key], item_rule, item_where + "." + key, errors)
        if unique and isinstance(item, dict):
            key = item.get(unique)
            if key in seen:
                errors.append("{}.{}: duplicate value {!r}".format(item_where, unique, key))
            seen.add(key)
        result.append(item)
    return result


def _apply_rules(settings, rules, errors):
    for dotted, rule in rules.items():
        try:
            value = get_path(settings, dotted)
        except KeyError:
            continue
        _check_value(value, rule, dotted, errors)
        if "items" in rule and isinstance(value, list):
            set_path(settings, dotted, _normalize_items(value, rule, dotted, errors))


def _secret_fields(rule):
    return [k for k, r in rule.get("items", {}).get("rules", {}).items() if r.get("secret")]


def redact(settings, rules):
    """Copy of settings with every `secret` value replaced by SECRET_MASK (empty stays empty)."""
    result = copy.deepcopy(settings)
    for dotted, rule in rules.items():
        try:
            value = get_path(result, dotted)
        except KeyError:
            continue
        if rule.get("secret") and value:
            set_path(result, dotted, SECRET_MASK)
        for field in _secret_fields(rule) if isinstance(value, list) else []:
            for item in value:
                if isinstance(item, dict) and item.get(field):
                    item[field] = SECRET_MASK
    return result


def restore_secrets(candidate, current, rules):
    """Replace SECRET_MASK in candidate by the stored value (array elements are matched by
    rule["unique"]). A mask without a stored value becomes "" so validation rejects it."""
    if not isinstance(candidate, dict):
        return candidate
    result = copy.deepcopy(candidate)
    for dotted, rule in rules.items():
        try:
            value = get_path(result, dotted)
        except KeyError:
            continue
        if rule.get("secret") and value == SECRET_MASK:
            try:
                set_path(result, dotted, get_path(current, dotted))
            except KeyError:
                set_path(result, dotted, "")
        fields, unique = _secret_fields(rule), rule.get("unique")
        if not fields or not isinstance(value, list):
            continue
        try:
            stored = {i.get(unique): i for i in get_path(current, dotted) if isinstance(i, dict)} if unique else {}
        except KeyError:
            stored = {}
        for item in value:
            if not isinstance(item, dict):
                continue
            for field in fields:
                if item.get(field) == SECRET_MASK:
                    item[field] = stored.get(item.get(unique), {}).get(field, "")
    return result


def validate(candidate, defaults, rules, validator=None):
    """Return (normalized_settings, errors). errors == [] means valid.

    validator: optional fn(normalized) -> [error messages] for checks the generic rules can't
    express (e.g. the syntax of a target list). It always runs, so the user sees all problems at
    once: every value has the type of its default (wrong types were replaced by the default),
    but values may break the rules. Messages should start with the dotted path
    ("profiles[0].targets: ...")."""
    errors = []
    if not isinstance(candidate, dict):
        return copy.deepcopy(defaults), ["settings must be a JSON object (not {})".format(_type_name(candidate))]
    normalized = _normalize(candidate, defaults, "", errors)
    _apply_rules(normalized, rules, errors)
    if validator is not None:
        errors.extend(validator(normalized))
    return normalized, errors


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class SettingsStore:
    def __init__(self, settings_file, defaults_file, active_configuration_dir, rules, validator=None):
        self._file = settings_file
        self._validator = validator
        self._dir = os.path.dirname(settings_file)
        self._active_configuration_dir = active_configuration_dir
        self._rules = rules
        with open(defaults_file, "r", encoding="utf-8") as f:
            defaults = json.load(f)
        normalized, errors = validate(defaults, defaults, rules, validator)
        if errors:
            raise SettingsError(["defaults/settings.json violates settings_rules.py: " + e for e in errors])
        self._defaults = normalized
        self._current = copy.deepcopy(normalized)
        self._lock = threading.RLock()
        self._listeners = []
        self._last_mtime_ns = None
        self._last_bad_mtime_ns = None
        self._load_deadline = None  # monotonic time until which a Solutions load pauses polling
        self._status = {"source": "defaults", "persisted": False, "error": None}

    # -- read access -------------------------------------------------------
    def get(self):
        with self._lock:
            return copy.deepcopy(self._current)

    def get_redacted(self):
        """Settings for the outside world (web API, Data Layer): `secret` values masked."""
        return redact(self.get(), self._rules)

    def defaults(self):
        return copy.deepcopy(self._defaults)

    def rules(self):
        return copy.deepcopy(self._rules)

    def types(self):
        return type_map(self._defaults)

    def status(self):
        with self._lock:
            status = dict(self._status)
        status["storageAvailable"] = self.storage_available()
        status["file"] = self._file
        return status

    def add_listener(self, callback):
        """callback(settings_dict) is called after every applied change (outside the lock)."""
        self._listeners.append(callback)

    def storage_available(self):
        """True when the Solutions app has mounted the active configuration (active-solution plug)."""
        return os.path.isdir(self._active_configuration_dir)

    # -- changes -----------------------------------------------------------
    def update(self, candidate):
        """Validate, persist and apply new settings. Raises SettingsError.
        Masked `secret` values in candidate keep their stored value."""
        candidate = restore_secrets(candidate, self.get(), self._rules)
        normalized, errors = validate(candidate, self._defaults, self._rules, self._validator)
        if errors:
            raise SettingsError(errors)
        with self._lock:
            if self.storage_available():
                self._write_atomic(normalized)
                persisted = True
            else:
                log.warning("App data storage not available - settings are applied but NOT persisted")
                persisted = False
            self._set(normalized, "user", persisted)
        self._notify()
        return copy.deepcopy(normalized)

    def reset_to_defaults(self, persist):
        with self._lock:
            persisted = False
            if persist and self.storage_available():
                self._write_atomic(self._defaults)
                persisted = True
            self._set(copy.deepcopy(self._defaults), "defaults", persisted)
        self._notify()

    def load_from_disk(self, create_if_missing):
        """(Re)load settings.json from the active configuration.

        Returns True on success. On a missing file: writes the current settings when
        create_if_missing, otherwise resets to defaults (Solutions "load" semantics:
        no data to load -> initial state). Raises SettingsError on invalid content
        (current settings stay active).
        """
        if not self.storage_available():
            with self._lock:
                self._status["error"] = "app data storage not available (active-solution not connected yet)"
            return False
        if not os.path.isfile(self._file):
            if create_if_missing:
                with self._lock:
                    self._write_atomic(self._current)
                    self._status.update({"persisted": True, "error": None})
                log.info("Created %s", self._file)
                return True
            log.info("No settings in the loaded configuration - resetting to defaults")
            self.reset_to_defaults(persist=False)
            return True
        try:
            mtime_ns = os.stat(self._file).st_mtime_ns
            with open(self._file, "r", encoding="utf-8") as f:
                candidate = json.load(f)
        except (OSError, ValueError) as exc:
            err = "cannot read {}: {}".format(self._file, exc)
            with self._lock:
                self._status["error"] = err
            raise SettingsError([err])
        normalized, errors = validate(candidate, self._defaults, self._rules, self._validator)
        if errors:
            with self._lock:
                self._status["error"] = "invalid settings.json: " + "; ".join(errors)
            raise SettingsError(errors)
        with self._lock:
            self._last_mtime_ns = mtime_ns
            self._set(normalized, "appdata", True)
        self._notify()
        return True

    def save_to_disk(self):
        """Write the active settings into the active configuration (Solutions "save")."""
        if not self.storage_available():
            raise SettingsError(["app data storage not available (active-solution not connected)"])
        with self._lock:
            self._write_atomic(self._current)
            self._status.update({"persisted": True, "error": None})

    def begin_load_operation(self, timeout_s=LOAD_OPERATION_TIMEOUT_S):
        """Solutions load started or progressed (phase query/prepare/load): pause polling.

        While Solutions copies/removes files we must neither recreate nor half-read them.
        Every call (re)sets the pause to `timeout_s` from now.
        """
        with self._lock:
            self._load_deadline = time.monotonic() + timeout_s

    def end_load_operation(self):
        """Solutions load finished (phase activate/abort): resume polling."""
        with self._lock:
            self._load_deadline = None

    def _load_in_progress(self):
        with self._lock:
            if self._load_deadline is None:
                return False
            if time.monotonic() > self._load_deadline:
                log.info("Solutions load operation ended without activate/abort - resuming settings polling")
                self._load_deadline = None
                return False
            return True

    def poll_external_change(self):
        """Call periodically. Reloads settings.json if someone else changed it.

        Only stat() is used for polling, which does not write to flash.
        """
        if not self.storage_available() or self._load_in_progress():
            return
        if not os.path.isfile(self._file):
            # first time the storage appears (or file deleted): create it from current values
            try:
                self.load_from_disk(create_if_missing=True)
            except (SettingsError, OSError) as exc:
                log.error("Cannot create settings file: %s", exc)
            return
        try:
            mtime_ns = os.stat(self._file).st_mtime_ns
        except OSError:
            return
        if mtime_ns in (self._last_mtime_ns, self._last_bad_mtime_ns):
            return
        log.info("settings.json changed externally - reloading")
        try:
            self.load_from_disk(create_if_missing=False)
        except SettingsError as exc:
            self._last_bad_mtime_ns = mtime_ns  # log once per bad version
            log.error("Ignoring invalid external settings change: %s", exc)

    # -- internals ---------------------------------------------------------
    def _set(self, settings, source, persisted):
        self._current = settings
        self._status = {"source": source, "persisted": persisted, "error": None}

    def _notify(self):
        snapshot = self.get()
        for callback in self._listeners:
            try:
                callback(snapshot)
            except Exception:  # a listener must never break the store
                log.exception("Settings listener failed")

    def _write_atomic(self, settings):
        os.makedirs(self._dir, mode=0o700, exist_ok=True)
        tmp = self._file + ".tmp"  # matched by appPrivateFiles in the package manifest
        data = json.dumps(settings, indent=2, sort_keys=False) + "\n"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        os.replace(tmp, self._file)
        self._last_mtime_ns = os.stat(self._file).st_mtime_ns
