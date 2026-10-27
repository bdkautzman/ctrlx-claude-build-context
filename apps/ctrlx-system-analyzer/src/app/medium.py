"""Optional long-term recording on an external medium (USB stick / SD card) - never the internal flash.

ctrlX OS mounts media under /media/<name> (Settings > Storage, removable-media plug). When the setting
recording.medium names one, every minute's buckets (min/avg/max) are appended as CSV to

    <medium>/ctrlx-system-analyzer/<YYYY-MM-DD>/system.csv            one row per minute
                                               /events.csv            restarts, updates, markers
                                               /apps/<app>.csv        one row per minute
                                               /processes/<app>.csv   one row per process and minute
                                               /threads/<app>.csv     per thread and minute (recording.threads)

Dates and the `time` column are the device's local time, `epoch` is UTC seconds. Files are only
appended, once a minute, by a thread of their own. A medium that disappears is retried every minute;
up to MAX_PENDING minutes wait for it. The same files answer queries older than the RAM history
(MediumRecorder.read) - unmount the medium in Settings > Storage before pulling it out.
"""

import csv
import datetime
import io
import logging
import os
import queue
import threading
import time

from app import grouping
from app.history import METRICS, to_api

log = logging.getLogger("medium")

MAX_PENDING = 60
AGGS = ("min", "avg", "max")
APP_METRICS = ("procs", "threads", "cpu", "anon", "file", "shmem", "rss", "pss", "swap")
PROC_METRICS = ("cpu", "anon", "rss", "pss", "swap", "threads")
THREAD_METRICS = ("cpu",)
SYSTEM_METRICS = ("cpu", "memused", "memavail", "swapused", "psicpu", "psimem", "psiio")
_UNITS = {"%": "pct", "MB": "mb", "": ""}
_KIND_COLUMNS = {
    "apps": ("epoch", "time", "app", "name"),
    "processes": ("epoch", "time", "app", "process", "pid", "name", "service"),
    "threads": ("epoch", "time", "app", "thread", "pid", "tid", "name"),
}
EVENT_COLUMNS = ("epoch", "time", "type", "app", "service", "text")


def column(metric, agg):
    unit = _UNITS[METRICS["core" if metric.startswith("core") else metric][2]]
    return "{}_{}_{}".format(metric, unit, agg) if unit else "{}_{}".format(metric, agg)


def metric_columns(metrics):
    return [column(m, a) for m in metrics for a in AGGS]


def local_time(epoch):
    return datetime.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


def day_of(epoch):
    return datetime.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d")


def days_between(t0, t1):
    d = datetime.datetime.fromtimestamp(t0).date()
    end = datetime.datetime.fromtimestamp(t1).date()
    out = []
    while d <= end and len(out) < 400:
        out.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return out


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


class MediumRecorder:
    def __init__(self, root, folder, describe, require_mount=True):
        self.root = root
        self.folder = folder
        self.describe = describe              # entity -> info dict (Collector.describe)
        self.require_mount = require_mount
        self.medium = ""
        self.threads = False
        self._queue = queue.Queue()
        self._pending = []                    # batches [(path, header, [rows])] not written yet
        self._lock = threading.Lock()
        self._status = {"state": "off", "error": None, "lastWrite": None, "bytes": 0, "pending": 0}
        self._stop = threading.Event()
        self._thread = None

    # -- control --------------------------------------------------------------
    def configure(self, settings):
        r = settings["recording"]
        self.medium = r["medium"]
        self.threads = bool(r["threads"])
        with self._lock:
            if not self.medium:
                self._pending = []
                self._status.update(state="off", error=None, pending=0)
            elif self._status["state"] == "off":
                self._status.update(state="waiting", error=None)

    def start(self):
        self._thread = threading.Thread(target=self._run, name="medium", daemon=True)
        self._thread.start()

    def stop(self, timeout=5.0):
        self._stop.set()
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout)

    def base_dir(self):
        """<medium>/<folder> if the configured medium is mounted, else None."""
        if not self.medium:
            return None
        path = os.path.join(self.root, self.medium)
        if not os.path.isdir(path) or (self.require_mount and not os.path.ismount(path)):
            return None
        return os.path.join(path, self.folder)

    def status(self):
        with self._lock:
            s = dict(self._status)
        s["medium"] = self.medium
        s["threads"] = self.threads
        base = self.base_dir()
        s["path"] = base
        return s

    # -- listeners (collector thread) ----------------------------------------
    def on_minute(self, start, buckets):
        if self.medium:
            self._queue.put(("minute", start, buckets))

    def on_event(self, event):
        if self.medium:
            self._queue.put(("event", event))

    # -- writer thread --------------------------------------------------------
    def _run(self):
        while not self._stop.is_set():
            item = self._queue.get()
            if item is None:
                break
            try:
                batch = self._rows_for(item)
                with self._lock:
                    self._pending.append(batch)
                    del self._pending[:-MAX_PENDING]
                self._write_pending()
            except Exception as exc:
                log.exception("recording failed")
                with self._lock:
                    self._status.update(state="error", error=str(exc))

    def _rows_for(self, item):
        if item[0] == "event":
            e = item[1]
            return [("{}/events.csv".format(day_of(e["time"])), EVENT_COLUMNS,
                     [[int(e["time"]), local_time(e["time"]), e["type"], e["app"], e["service"], e["text"]]])]
        _, start, buckets = item
        day = day_of(start)
        stamp = [int(start), local_time(start)]
        by_entity = {}
        for sid, value in buckets.items():
            entity, _, metric = sid.rpartition(".")
            by_entity.setdefault(entity, {})[metric] = value
        files = {}

        def add(path, header, row):
            files.setdefault(path, (header, []))[1].append(row)

        def cells(entity, metrics, values):
            out = []
            for m in metrics:
                v = values.get(m)
                for k in range(3):
                    out.append("" if v is None else to_api(entity + "." + m, v[k]))
            return out

        system = by_entity.pop("sys", None)
        if system:
            cores = sorted((m for m in system if m.startswith("core")), key=lambda m: int(m[4:]))
            metrics = SYSTEM_METRICS[:1] + tuple(cores) + SYSTEM_METRICS[1:]
            add("{}/system.csv".format(day), ("epoch", "time") + tuple(metric_columns(metrics)),
                stamp + cells("sys", metrics, system))
        for entity, values in sorted(by_entity.items()):
            scope, _, rest = entity.partition(".")
            info = self.describe(entity) or {}
            if scope == "app":
                kind, metrics, ident = "apps", APP_METRICS, [rest, info.get("name", rest)]
                key = rest
            elif scope == "proc":
                key, _, pkey = rest.rpartition(".")
                kind, metrics = "processes", PROC_METRICS
                ident = [key, pkey, info.get("pid", ""), info.get("name", ""), info.get("service", "")]
            elif scope == "thr" and self.threads:
                key, _, tkey = rest.rpartition(".")
                kind, metrics = "threads", THREAD_METRICS
                ident = [key, tkey, info.get("pid", ""), info.get("tid", ""), info.get("name", "")]
            else:
                continue
            path = "{}/{}/{}.csv".format(day, kind, grouping.file_for(key))
            add(path, _KIND_COLUMNS[kind] + tuple(metric_columns(metrics)), stamp + ident + cells(entity, metrics, values))
        return [(path, header, rows) for path, (header, rows) in files.items()]

    def _write_pending(self):
        """Write the waiting batches (one per minute or event) in order; failed ones stay pending."""
        base = self.base_dir()
        with self._lock:
            if not self.medium:
                self._pending = []
                return
            if base is None:
                self._status.update(state="waiting", error="medium '{}' is not mounted".format(self.medium),
                                    pending=len(self._pending))
                return
            pending, self._pending = self._pending, []
        written = 0
        failed = []
        err = None
        for batch in pending:
            if err is None:
                try:
                    written += self._write_batch(base, batch)
                    continue
                except OSError as exc:
                    err = "{}: {}".format(base, exc.strerror or exc)
            failed.append(batch)
        with self._lock:
            self._pending = failed + self._pending
            del self._pending[:-MAX_PENDING]
            self._status["bytes"] += written
            self._status["pending"] = len(self._pending)
            if err:
                self._status.update(state="error", error=err)
                log.warning("Recording failed: %s", err)
            else:
                self._status.update(state="recording", error=None, lastWrite=time.time())

    @staticmethod
    def _write_batch(base, batch):
        written = 0
        for path, header, rows in batch:
            full = os.path.join(base, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            buf = io.StringIO()
            w = csv.writer(buf, lineterminator="\n")
            if not os.path.exists(full):
                w.writerow(header)
            w.writerows(rows)
            data = buf.getvalue().encode("utf-8")
            with open(full, "ab") as f:
                f.write(data)
            written += len(data)
        return written

    # -- reading --------------------------------------------------------------
    def available(self):
        return self.base_dir() is not None

    def oldest(self):
        """Epoch of the first recorded minute, or None."""
        base = self.base_dir()
        if base is None:
            return None
        try:
            days = sorted(d for d in os.listdir(base) if len(d) == 10 and d[4] == "-")
        except OSError:
            return None
        for day in days:
            rows = self._rows(os.path.join(base, day, "system.csv"), None, None)
            if rows:
                return rows[0][0]
        return None

    def _rows(self, path, t0, t1, match=None):
        """-> [(epoch, {column: value})] of one file within [t0, t1]; match: (column, value) filter."""
        try:
            with open(path, newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                if not header:
                    return []
                mi = header.index(match[0]) if match and match[0] in header else None
                out = []
                for row in reader:
                    if not row or (mi is not None and (len(row) <= mi or row[mi] != match[1])):
                        continue
                    try:
                        epoch = int(row[0])
                    except ValueError:
                        continue
                    if (t0 is not None and epoch < t0) or (t1 is not None and epoch > t1):
                        continue
                    out.append((epoch, dict(zip(header, row))))
                return out
        except (OSError, ValueError, csv.Error):
            return []

    def read(self, sids, t0, t1):
        """Series from the medium -> (times, {sid: (mins, avgs, maxs)}) in API units (minute buckets)."""
        base = self.base_dir()
        if base is None:
            return [], {}
        # which file holds which series: (kind, group key, row filter) -> [(sid, metric)]
        wanted = {}
        for sid in sids:
            parts = sid.split(".")
            if parts[0] == "sys":
                wanted.setdefault(("system", None, None), []).append((sid, parts[1]))
            elif parts[0] == "app" and len(parts) == 3:
                wanted.setdefault(("apps", parts[1], None), []).append((sid, parts[2]))
            elif parts[0] == "proc" and len(parts) == 4:
                wanted.setdefault(("processes", parts[1], ("process", parts[2])), []).append((sid, parts[3]))
            elif parts[0] == "thr" and len(parts) == 4:
                wanted.setdefault(("threads", parts[1], ("thread", parts[2])), []).append((sid, parts[3]))
        values = {}   # sid -> {epoch: (min, avg, max)}
        epochs = set()
        for day in days_between(t0, t1):
            for (kind, key, match), metrics in wanted.items():
                name = "system.csv" if kind == "system" else "{}/{}.csv".format(kind, grouping.file_for(key))
                for epoch, row in self._rows(os.path.join(base, day, name), t0, t1, match):
                    epochs.add(epoch)
                    for sid, metric in metrics:
                        triple = tuple(_num(row.get(column(metric, a))) for a in AGGS)
                        if triple[1] is not None:
                            values.setdefault(sid, {})[epoch] = triple
        times = sorted(epochs)
        series = {}
        for sid, by_time in values.items():
            cols = ([], [], [])
            for t in times:
                v = by_time.get(t, (None, None, None))
                for k in range(3):
                    cols[k].append(v[k])
            series[sid] = cols
        return [float(t) for t in times], series

    def entities(self, kind, t0, t1, app=None):
        """Apps or processes recorded in [t0, t1] -> {entity: info} (from the CSV rows)."""
        base = self.base_dir()
        out = {}
        if base is None:
            return out
        for day in days_between(t0, t1):
            folder = os.path.join(base, day, kind)
            try:
                files = sorted(os.listdir(folder))
            except OSError:
                continue
            for name in files:
                if app is not None and name != grouping.file_for(app) + ".csv":
                    continue
                for _, row in self._rows(os.path.join(folder, name), t0, t1):
                    if kind == "apps":
                        out.setdefault("app." + row.get("app", ""), {"name": row.get("name", ""),
                                                                    "app": row.get("app", "")})
                    else:
                        out.setdefault("proc.{}.{}".format(row.get("app", ""), row.get("process", "")),
                                       {"pid": _num(row.get("pid")), "name": row.get("name", ""),
                                        "service": row.get("service", ""), "app": row.get("app", "")})
                    if kind == "apps":
                        break       # one row tells the app
        return out

    def events(self, t0, t1):
        base = self.base_dir()
        out = []
        if base is None:
            return out
        for day in days_between(t0, t1):
            for epoch, row in self._rows(os.path.join(base, day, "events.csv"), t0, t1):
                out.append({"time": epoch, "type": row.get("type", ""), "text": row.get("text", ""),
                            "app": row.get("app", ""), "appName": "", "service": row.get("service", "")})
        return out


def _num(text):
    if text is None or text == "":
        return None
    try:
        v = float(text)
    except ValueError:
        return None
    return int(v) if v.is_integer() and "." not in text else v
