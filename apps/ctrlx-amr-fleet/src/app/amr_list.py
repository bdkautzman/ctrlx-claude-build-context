"""The AMR list: the AMRs the per-AMR input modes offer Data Layer nodes for (setting fleet.input).

A text file in the app's app data folder, so it is saved and loaded with ctrlX configurations:

    # comment
    AMR-01
    AMR-02

One AMR id per line; blank lines and lines starting with '#' are ignored; in a line with ',', ';' or a
tab only the first field counts (a CSV export works). An id has 1..64 characters A-Z a-z 0-9 _ - (it
is part of Data Layer addresses: fleet/amr/<id>), ids are unique, at most MAX_IDS.

Created from defaults/amr-list.txt when it is missing. The web UI replaces it (PUT /amr-list: single
ids added/removed or an uploaded file); a change from outside (Manage app data, a loaded
configuration) is picked up by poll(), which only stat()s the file - no cyclic flash writes.
"""

import logging
import os
import re
import threading

from app.fleet import MAX_AMRS

log = logging.getLogger("amr-list")

FILE_NAME = "amr-list.txt"
MAX_IDS = MAX_AMRS
ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}"
_ID = re.compile(ID_PATTERN)
_FIELD_SPLIT = re.compile(r"[,;\t]")
HEADER = ("# AMR list of ctrlx-amr-fleet: one AMR id per line (A-Z a-z 0-9 _ -, up to 64 characters).\n"
          "# Used by the per-AMR input modes (setting fleet.input): Data Layer nodes fleet/amr/<id>.\n")


class AmrListError(ValueError):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


def parse_list(text):
    """Ids in file order, or AmrListError naming every bad line (at most 20)."""
    ids, seen, errors = [], set(), []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip().lstrip("﻿")
        if not line or line.startswith("#"):
            continue
        ident = _FIELD_SPLIT.split(line, 1)[0].strip().strip('"')
        if not _ID.fullmatch(ident):
            errors.append('line {}: "{}" is not a valid id (1..64 characters A-Z a-z 0-9 _ -)'.format(n, ident[:70]))
        elif ident in seen:
            errors.append('line {}: "{}" is listed twice'.format(n, ident))
        else:
            seen.add(ident)
            ids.append(ident)
    if len(ids) > MAX_IDS:
        errors.append("more than {} AMRs".format(MAX_IDS))
    if errors:
        raise AmrListError(errors[:20] + (["... {} more".format(len(errors) - 20)] if len(errors) > 20 else []))
    return ids


def format_list(ids):
    return HEADER + "".join(ident + "\n" for ident in ids)


class AmrList:
    def __init__(self, path, default_path):
        self._path = path
        self._default_path = default_path
        self._lock = threading.Lock()
        self._ids = self._read_default()
        self._error = None
        self._last_mtime_ns = None
        self._last_bad_mtime_ns = None
        self._listeners = []

    def _read_default(self):
        with open(self._default_path, "r", encoding="utf-8") as f:
            return parse_list(f.read())

    def ids(self):
        with self._lock:
            return list(self._ids)

    def status(self):
        with self._lock:
            return {"file": FILE_NAME, "count": len(self._ids), "error": self._error}

    def add_listener(self, callback):
        """callback(ids) after every change (outside the lock)."""
        self._listeners.append(callback)

    def save(self, ids):
        """Validate and store a new list (web UI). Raises AmrListError (nothing is written then)."""
        ids = parse_list("\n".join(ids))
        os.makedirs(os.path.dirname(self._path), mode=0o700, exist_ok=True)
        with self._lock:
            self._write_atomic(format_list(ids))
            changed = ids != self._ids
            self._ids, self._error = ids, None
        if changed:
            self._notify()
        return ids

    def poll(self, storage_available=True, paused=False):
        """Call periodically: creates the file when missing, reloads it when changed from outside.
        paused: a ctrlX configuration is being loaded - the file may be half copied."""
        if not storage_available or paused:
            return
        try:
            mtime_ns = os.stat(self._path).st_mtime_ns
        except FileNotFoundError:
            self._create_default()
            return
        except OSError:
            return
        if mtime_ns in (self._last_mtime_ns, self._last_bad_mtime_ns):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                ids = parse_list(f.read())
        except (OSError, UnicodeError, AmrListError) as exc:
            self._last_bad_mtime_ns = mtime_ns           # log once per bad version, keep the last good list
            with self._lock:
                self._error = "{}: {}".format(FILE_NAME, exc)
            log.error("Ignoring invalid %s (keeping %d AMRs): %s", FILE_NAME, len(self._ids), exc)
            return
        with self._lock:
            self._last_mtime_ns = mtime_ns
            changed = ids != self._ids
            self._ids, self._error = ids, None
        if changed:
            log.info("%s loaded: %d AMRs", FILE_NAME, len(ids))
            self._notify()

    def _create_default(self):
        """No list in the app data (first start, or a configuration without one): the default list."""
        try:
            ids = self._read_default()
            os.makedirs(os.path.dirname(self._path), mode=0o700, exist_ok=True)
            with self._lock:
                self._write_atomic(format_list(ids))
                changed = ids != self._ids
                self._ids, self._error = ids, None
        except (OSError, AmrListError) as exc:
            log.error("Cannot create %s: %s", self._path, exc)
            return
        log.info("Created %s with %d AMRs", self._path, len(ids))
        if changed:
            self._notify()

    def _write_atomic(self, text):   # with self._lock; *.tmp is in the manifest's appPrivateFiles
        tmp = self._path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        os.replace(tmp, self._path)
        self._last_mtime_ns = os.stat(self._path).st_mtime_ns

    def _notify(self):
        ids = self.ids()
        for callback in self._listeners:
            try:
                callback(ids)
            except Exception:
                log.exception("AMR list listener failed")
