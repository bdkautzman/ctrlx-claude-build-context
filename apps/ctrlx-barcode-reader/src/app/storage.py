"""Optional image storage on a removable medium (USB stick / SD card) - never the internal flash.

ctrlX OS mounts media under /media/<name> (Settings > Storage, removable-media plug). With
storage.target = medium every read (or only the no-reads, storage.save) is written to

    <medium>/ctrlx-barcode-reader/<YYYY-MM-DD>/<HHMMSS>-<sequence>-<ok|noread>.<jpg|png>
                                              /results.csv      one row per saved image

by a thread of its own. Dates and times are the device's local time. The app's folder is kept below
storage.maxMb by deleting the oldest images. A full queue (slow medium) drops the image and counts it.
"""

import csv
import io
import json
import logging
import os
import queue
import threading
import time

from app import imaging

log = logging.getLogger("storage")

QUEUE_SIZE = 2           # frames waiting for the medium (5 MB each for a 5 MP camera)
CSV_COLUMNS = ("time", "sequence", "source", "requestId", "ok", "count", "formats", "texts", "durationMs", "error", "file")


def list_media(root, require_mount=True):
    """Mounted media under root -> [{"name", "path", "totalMb", "freeMb", "writable"}], error or None."""
    try:
        names = sorted(os.listdir(root))
    except OSError as exc:
        return [], "cannot read {}: {}".format(root, exc.strerror or exc)
    media = []
    for name in names:
        path = os.path.join(root, name)
        if not os.path.isdir(path) or (require_mount and not os.path.ismount(path)):
            continue
        try:
            st = os.statvfs(path)
            total = st.f_blocks * st.f_frsize / 1048576.0
            free = st.f_bavail * st.f_frsize / 1048576.0
        except OSError:
            total = free = None
        media.append({"name": name, "path": path, "totalMb": None if total is None else round(total, 1),
                      "freeMb": None if free is None else round(free, 1), "writable": os.access(path, os.W_OK)})
    return media, None


class MediumWriter:
    def __init__(self, root, folder, require_mount=True):
        self.root = root
        self.folder = folder
        self.require_mount = require_mount
        self.settings = {"target": "ram", "medium": "", "save": "all", "format": "jpg", "jpegQuality": 90, "maxMb": 1024}
        self._queue = queue.Queue(QUEUE_SIZE)
        self._lock = threading.Lock()
        self._status = {"state": "off", "error": None, "lastFile": None, "saved": 0, "dropped": 0, "bytes": None}
        self._files = None         # [(path, size)] oldest first, scanned on the first write
        self._stop = threading.Event()
        self._thread = None

    def configure(self, storage_settings):
        with self._lock:
            changed = (storage_settings["medium"] != self.settings["medium"])
            self.settings = dict(storage_settings)
            if changed:
                self._files = None
            if self.settings["target"] != "medium":
                self._status.update(state="off", error=None)
            elif self._status["state"] == "off":
                self._status.update(state="waiting", error=None)

    def start(self):
        self._thread = threading.Thread(target=self._run, name="storage", daemon=True)
        self._thread.start()

    def stop(self, timeout=5.0):
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread:
            self._thread.join(timeout)

    def base_dir(self):
        """<medium>/<folder> if the configured medium is mounted, else None."""
        medium = self.settings["medium"]
        if not medium:
            return None
        path = os.path.join(self.root, medium)
        if not os.path.isdir(path) or (self.require_mount and not os.path.ismount(path)):
            return None
        return os.path.join(path, self.folder)

    def status(self):
        with self._lock:
            s = dict(self._status)
            s.update(target=self.settings["target"], medium=self.settings["medium"], save=self.settings["save"])
        s["path"] = self.base_dir()
        s["queued"] = self._queue.qsize()
        return s

    def wants(self, result):
        s = self.settings
        return s["target"] == "medium" and bool(s["medium"]) and (s["save"] == "all" or not result["ok"])

    def submit(self, frame, result):
        """Queue the frame of a result (called by the reader; never blocks)."""
        if not self.wants(result):
            return False
        try:
            self._queue.put_nowait((frame, dict(result)))
            return True
        except queue.Full:
            with self._lock:
                self._status["dropped"] += 1
            log.warning("Medium too slow - image of result %s not saved", result["sequence"])
            return False

    # -- writer thread ------------------------------------------------------------------------
    def _run(self):
        while not self._stop.is_set():
            item = self._queue.get()
            if item is None:
                break
            frame, result = item
            try:
                self._write(frame, result)
            except Exception as exc:  # noqa: BLE001 - keep the thread alive, show the error
                log.warning("Saving image %s failed: %s", result.get("sequence"), exc)
                with self._lock:
                    self._status.update(state="error", error=str(exc))

    def _write(self, frame, result):
        s = self.settings
        base = self.base_dir()
        if base is None:
            with self._lock:
                self._status.update(state="waiting", error="medium {} is not mounted".format(s["medium"]))
                self._status["dropped"] += 1
            return
        t = time.localtime(result["epoch"])
        day = os.path.join(base, time.strftime("%Y-%m-%d", t))
        os.makedirs(day, exist_ok=True)
        ext = "png" if s["format"] == "png" else "jpg"
        name = "{}-{:06d}-{}.{}".format(time.strftime("%H%M%S", t), result["sequence"],
                                        "ok" if result["ok"] else "noread", ext)
        img = imaging.to_bgr(frame)
        data = imaging.png(img) if ext == "png" else imaging.jpeg(img, s["jpegQuality"])
        path = os.path.join(day, name)
        with open(path, "wb") as f:
            f.write(data)
        self._append_csv(day, result, name)
        self._account(base, path, len(data))
        with self._lock:
            self._status.update(state="ok", error=None, lastFile=path)
            self._status["saved"] += 1

    @staticmethod
    def _append_csv(day, result, name):
        path = os.path.join(day, "results.csv")
        new = not os.path.exists(path)
        buf = io.StringIO()
        w = csv.writer(buf)
        if new:
            w.writerow(CSV_COLUMNS)
        codes = result.get("codes") or []
        w.writerow([result["timestamp"], result["sequence"], result["source"], result["requestId"],
                    int(bool(result["ok"])), len(codes), " ".join(c["format"] for c in codes),
                    json.dumps([c["text"] for c in codes], ensure_ascii=False), result["durationMs"],
                    result.get("error") or "", name])
        with open(path, "a", encoding="utf-8", newline="") as f:
            f.write(buf.getvalue())

    def _account(self, base, path, size):
        """Track the folder size; delete the oldest images above storage.maxMb."""
        if self._files is None:
            files = []
            for dirpath, _dirs, names in os.walk(base):
                for n in names:
                    if n.endswith((".jpg", ".png")):
                        p = os.path.join(dirpath, n)
                        try:
                            files.append((p, os.path.getsize(p)))
                        except OSError:
                            pass
            files.sort()
            self._files = files
        else:
            self._files.append((path, size))
        limit = self.settings["maxMb"] * 1048576
        total = sum(sz for _p, sz in self._files)
        while total > limit and len(self._files) > 1:
            old, sz = self._files.pop(0)
            try:
                os.unlink(old)
            except OSError:
                pass
            total -= sz
            day = os.path.dirname(old)
            if day != os.path.dirname(path) and not any(p.startswith(day + os.sep) for p, _ in self._files[:1]):
                # the oldest day has no images left: its results.csv goes too
                try:
                    os.unlink(os.path.join(day, "results.csv"))
                    os.rmdir(day)
                except OSError:
                    pass
        with self._lock:
            self._status["bytes"] = total
