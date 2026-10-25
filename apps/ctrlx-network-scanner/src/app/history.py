"""History of finished scans on flash: $SNAP_COMMON/history/<id>.json.gz + index.json.

Not in app data on purpose (user's choice 2026-10-25): history would make every configuration
save/backup bigger and is device-specific anyway. $SNAP_COMMON survives app updates.

Flash wear: exactly two writes per finished scan (the scan file, then the small index), each
atomic (tmp + fsync + rename). Nothing is written while a scan runs. The size is capped by
history.maxScans and history.maxMegabytes; the oldest scans are deleted first.
"""

import gzip
import json
import logging
import os
import re
import threading

log = logging.getLogger("history")

ID_PATTERN = re.compile(r"[0-9A-Za-z][0-9A-Za-z_-]{0,79}")
INDEX_FILE = "index.json"


def index_entry(result, size):
    summary = result.get("summary", {})
    rating = summary.get("ratingAtScan", {})
    changes = result.get("changes") or {}
    profile = result.get("profile", {})
    return {
        "id": result["id"], "profileId": profile.get("id", ""), "profileName": profile.get("name", ""),
        "trigger": result.get("trigger", ""), "user": result.get("user", ""), "status": result.get("status", ""),
        "startedAt": result.get("startedAt"), "finishedAt": result.get("finishedAt"),
        "durationS": result.get("durationS", 0), "targets": summary.get("targets", 0),
        "hostsUp": summary.get("hostsUp", 0), "openTcp": summary.get("openTcp", 0),
        "openUdp": summary.get("openUdp", 0), "alarmsAtScan": rating.get("forbidden", 0),
        "changes": changes.get("total") if changes else None, "bytes": size,
    }


class History:
    def __init__(self, directory, max_scans=100, max_megabytes=20):
        self.dir = directory
        self.max_scans = max_scans
        self.max_bytes = int(max_megabytes * 1024 * 1024)
        self._lock = threading.RLock()
        self._index = []   # newest first
        self.error = None

    # -- setup ------------------------------------------------------------------
    def set_limits(self, max_scans, max_megabytes):
        with self._lock:
            self.max_scans = max_scans
            self.max_bytes = int(max_megabytes * 1024 * 1024)
            self._enforce_limits()

    def load(self):
        """Read the index (rebuild it from the scan files if it is missing or does not match)."""
        with self._lock:
            os.makedirs(self.dir, exist_ok=True)
            files = {f[:-len(".json.gz")] for f in os.listdir(self.dir) if f.endswith(".json.gz")}
            index = None
            try:
                with open(os.path.join(self.dir, INDEX_FILE), encoding="utf-8") as f:
                    index = json.load(f)
            except (OSError, ValueError):
                pass
            if not isinstance(index, list) or {e.get("id") for e in index if isinstance(e, dict)} != files:
                log.info("Rebuilding the history index from %d scan files", len(files))
                index = []
                for scan_id in files:
                    result = self._read(scan_id)
                    if result is not None:
                        index.append(index_entry(result, self._size(scan_id)))
                self._index = sorted(index, key=lambda e: e.get("startedAt") or "", reverse=True)
                self._write_index()
            else:
                self._index = sorted(index, key=lambda e: e.get("startedAt") or "", reverse=True)
            self._enforce_limits()
            return len(self._index)

    # -- access -------------------------------------------------------------------
    def list(self):
        with self._lock:
            return [dict(e) for e in self._index]

    def usage(self):
        with self._lock:
            return {"count": len(self._index), "bytes": sum(e.get("bytes", 0) for e in self._index),
                    "maxScans": self.max_scans, "maxBytes": self.max_bytes, "error": self.error}

    def get(self, scan_id):
        if not ID_PATTERN.fullmatch(scan_id or ""):
            return None
        with self._lock:
            if not any(e["id"] == scan_id for e in self._index):
                return None
            return self._read(scan_id)

    def latest(self, profile_id, exclude_id=None, status="completed"):
        """The newest scan of a profile (full result) or None."""
        with self._lock:
            for e in self._index:
                if e.get("profileId") == profile_id and e["id"] != exclude_id and e.get("status") == status:
                    return self._read(e["id"])
        return None

    def last_started(self, profile_id):
        """startedAt (ISO, UTC) of the newest scan of a profile, any status - for schedules."""
        with self._lock:
            for e in self._index:
                if e.get("profileId") == profile_id:
                    return e.get("startedAt")
        return None

    # -- changes --------------------------------------------------------------------
    def add(self, result):
        """Store a finished scan. Returns its index entry. Raises OSError."""
        scan_id = result["id"]
        if not ID_PATTERN.fullmatch(scan_id):
            raise ValueError("invalid scan id " + scan_id)
        data = gzip.compress(json.dumps(result, separators=(",", ":")).encode("utf-8"), compresslevel=6, mtime=0)
        with self._lock:
            os.makedirs(self.dir, exist_ok=True)
            self._write_atomic(os.path.join(self.dir, scan_id + ".json.gz"), data)
            entry = index_entry(result, len(data))
            self._index = [e for e in self._index if e["id"] != scan_id]
            self._index.insert(0, entry)
            self._index.sort(key=lambda e: e.get("startedAt") or "", reverse=True)
            self._enforce_limits(write=False)
            self._write_index()
            self.error = None
            return dict(entry)

    def delete(self, scan_id):
        if not ID_PATTERN.fullmatch(scan_id or ""):
            return False
        with self._lock:
            if not any(e["id"] == scan_id for e in self._index):
                return False
            self._index = [e for e in self._index if e["id"] != scan_id]
            self._remove_file(scan_id)
            self._write_index()
            return True

    # -- internals ------------------------------------------------------------------
    def _enforce_limits(self, write=True):
        removed = False
        while self._index and (len(self._index) > self.max_scans
                               or sum(e.get("bytes", 0) for e in self._index) > self.max_bytes):
            if len(self._index) == 1:
                break   # always keep the newest scan
            old = self._index.pop()
            self._remove_file(old["id"])
            removed = True
            log.info("History limit: deleted scan %s", old["id"])
        if removed and write:
            self._write_index()

    def _path(self, scan_id):
        return os.path.join(self.dir, scan_id + ".json.gz")

    def _size(self, scan_id):
        try:
            return os.path.getsize(self._path(scan_id))
        except OSError:
            return 0

    def _read(self, scan_id):
        try:
            with gzip.open(self._path(scan_id), "rt", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError, EOFError) as exc:
            log.warning("Cannot read scan %s: %s", scan_id, exc)
            return None

    def _remove_file(self, scan_id):
        try:
            os.unlink(self._path(scan_id))
        except FileNotFoundError:
            pass

    def _write_index(self):
        data = json.dumps(self._index, separators=(",", ":")).encode("utf-8")
        self._write_atomic(os.path.join(self.dir, INDEX_FILE), data)

    @staticmethod
    def _write_atomic(path, data):
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        os.replace(tmp, path)
