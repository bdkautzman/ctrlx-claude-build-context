"""Answers for the web API: series, leak analysis, events and exports for a time range.

The RAM history answers by default. When it doesn't reach back to the start of the range and the
external medium has older data, the medium answers instead (minute buckets, read from its CSV files).
"""

import csv
import io
import json
import time

from app import analysis
from app.history import downsample
from app.medium import APP_METRICS, PROC_METRICS, SYSTEM_METRICS, THREAD_METRICS, local_time, metric_columns

RESTART_TYPES = ("restarted", "started", "stopped", "analyzer")   # analyzer: its own (re)start
MAX_EXPORT_CELLS = 2_000_000


class QueryError(ValueError):
    pass


class Queries:
    def __init__(self, history, collector, events, medium, store):
        self.history = history
        self.collector = collector
        self.events_log = events
        self.medium = medium
        self.store = store
        self.started = time.time()

    # -- series ---------------------------------------------------------------
    def series(self, sids, t0, t1, points=None):
        ram = self.history.query(sids, t0, t1, points)
        ram["source"] = "ram"
        if self.medium.available() and (ram["oldest"] is None or t0 < ram["oldest"] - 60):
            times, cols = self.medium.read(sids, t0, t1)
            if times and (ram["oldest"] is None or times[0] < ram["oldest"]):
                times, cols = downsample(times, cols, points)
                return {"tier": "minute", "source": "medium", "step": 60, "oldest": times[0],
                        "times": times, "series": cols}
        return ram

    # -- events ---------------------------------------------------------------
    def events(self, t0, t1):
        out = self.events_log.between(t0, t1)
        if self.medium.available() and t0 < self.started:
            out = [e for e in self.medium.events(t0, min(t1, self.started - 1))] + out
        return sorted(out, key=lambda e: e["time"])

    def add_marker(self, text, t=None):
        text = (text or "").strip()
        if not text:
            raise QueryError("marker text is empty")
        return self.events_log.add(time.time() if t is None else t, "marker", text[:200])

    # -- leak analysis --------------------------------------------------------
    def _entities(self, level, t0, t1, app=None):
        prefix = "app." if level == "apps" else "proc."
        if app and level == "processes":
            prefix = "proc.{}.".format(app)
        found = {}
        for entity in self.history.entities():
            if entity.startswith(prefix) and (level != "apps" or entity.count(".") == 1):
                found[entity] = self.collector.describe(entity) or {}
        if self.medium.available() and (self.history.oldest() is None or t0 < self.history.oldest()):
            if level == "processes" and not app and t1 - t0 > 3 * 86400:
                raise QueryError("process level from the external medium: choose an app or at most 3 days")
            for entity, info in self.medium.entities("apps" if level == "apps" else "processes", t0, t1, app).items():
                found.setdefault(entity, info)
        return found

    def analysis(self, t0, t1, level="apps", metric=None, app=None):
        if level not in ("apps", "processes"):
            raise QueryError("level must be apps or processes")
        rules = self.store.get()["leak"]
        metric = metric or rules["metric"]
        if metric not in ("anon", "rss", "pss"):
            raise QueryError("metric must be anon, rss or pss")
        entities = self._entities(level, t0, t1, app)
        sids = []
        for entity in entities:
            sids += [entity + "." + metric, entity + ".threads"]
        data = self.series(sids, t0, t1, points=2000)
        snap = self.collector.snapshot()
        available = snap["system"]["memAvailableMb"] if snap else None
        restarts = {}
        for e in self.events(t0, t1):
            if e["type"] in RESTART_TYPES and e["app"]:
                restarts.setdefault(e["app"], []).append(e["time"])
        rows = []
        for entity, info in entities.items():
            col = data["series"].get(entity + "." + metric)
            if not col or not any(v is not None for v in col[0]):
                continue
            key = entity.split(".")[1]
            th = data["series"].get(entity + ".threads")
            result = analysis.assess(data["times"], col[0], rules, available,
                                     restarts.get(key, ()) if level == "apps" else (),
                                     (data["times"], th[0]) if th else None)
            last = next((v for v in reversed(col[1]) if v is not None), None)
            last_seen = self.history.last_seen(entity + "." + metric)
            ended = level == "processes" and last_seen is not None and last_seen < time.time() - 3 * self.history.interval_s
            if ended and result["verdict"] == "insufficient":
                continue                       # a short-lived process: nothing to see
            result.update({"entity": entity, "app": key, "name": info.get("appName") or info.get("name") or key,
                           "process": info.get("name") if level == "processes" else None,
                           "pid": info.get("pid"), "service": info.get("service", ""), "currentMb": last,
                           "ended": ended})
            rows.append(result)
        order = {"growing": 0, "possible": 1, "stable": 2, "insufficient": 3}
        rows.sort(key=lambda r: (order[r["verdict"]], -(r["growthMbPerHour"] or 0)))
        return {"from": t0, "to": t1, "level": level, "metric": metric, "source": data["source"],
                "tier": data["tier"], "rules": rules, "availableMb": available, "results": rows}

    # -- export ---------------------------------------------------------------
    def export(self, t0, t1, level, fmt):
        kinds = {"system": ("sys", SYSTEM_METRICS), "apps": ("app", APP_METRICS),
                 "processes": ("proc", PROC_METRICS), "threads": ("thr", THREAD_METRICS)}
        if level not in kinds:
            raise QueryError("level must be system, apps, processes or threads")
        if fmt not in ("csv", "json"):
            raise QueryError("format must be csv or json")
        scope, metrics = kinds[level]
        if level == "system":
            entities = {"sys": {}}
            cores = sorted({s.split(".")[1] for s in self.history.ids("sys.core")}, key=lambda m: int(m[4:]))
            metrics = SYSTEM_METRICS[:1] + tuple(cores) + SYSTEM_METRICS[1:]
        elif level == "threads":
            entities = {e: self.collector.describe(e) or {} for e in self.history.entities() if e.startswith("thr.")}
        else:
            entities = self._entities(level, t0, t1)
        sids = [e + "." + m for e in entities for m in metrics]
        data = self.series(sids, t0, t1, points=None)
        if len(data["times"]) * max(1, len(sids)) > MAX_EXPORT_CELLS:
            raise QueryError("too much data for one export: choose a shorter range or another level")
        if fmt == "json":
            body = {"from": t0, "to": t1, "level": level, "tier": data["tier"], "source": data["source"],
                    "step": data["step"], "times": data["times"], "entities": entities,
                    "series": {sid: {"min": c[0], "avg": c[1], "max": c[2]} for sid, c in data["series"].items()}}
            return json.dumps(body, separators=(",", ":")).encode("utf-8"), "application/json"
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        ident = {"system": [], "apps": ["app", "name"], "processes": ["app", "process", "pid", "name", "service"],
                 "threads": ["app", "thread", "pid", "tid", "name"]}[level]
        w.writerow(["epoch", "time"] + ident + metric_columns(metrics))
        for i, t in enumerate(data["times"]):
            for entity, info in entities.items():
                cells = []
                for m in metrics:
                    c = data["series"].get(entity + "." + m)
                    cells += ["" if c is None or c[k][i] is None else c[k][i] for k in range(3)]
                if not any(x != "" for x in cells):
                    continue
                parts = entity.split(".")
                ids = {"system": [], "apps": [parts[-1], info.get("name", "")],
                       "processes": [parts[1], parts[-1], info.get("pid", ""), info.get("name", ""),
                                     info.get("service", "")],
                       "threads": [parts[1], parts[-1], info.get("pid", ""), info.get("tid", ""),
                                   info.get("name", "")]}[level]
                w.writerow([int(t), local_time(t)] + ids + cells)
        return buf.getvalue().encode("utf-8"), "text/csv"
