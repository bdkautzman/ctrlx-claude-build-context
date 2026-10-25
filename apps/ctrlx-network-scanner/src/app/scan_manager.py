"""Runs scans one at a time and connects them to settings, history, schedules and the Data Layer.

* start(profile_id, trigger, user): a manual / Data Layer start while a scan runs is refused
  (BusyError); a scheduled start is queued instead (one pending start per profile).
* tick(): called from the main loop every ~2 s - fires due schedules, starts queued scans.
* When a scan ends: compare with the previous scan of the same profile, rate it with the current
  lists, store it in the history (one write), publish the result to the Data Layer nodes.
"""

import json
import logging
import secrets
import threading
from datetime import datetime, timezone

from app import rating, scanner
from app.scheduler import Scheduler

log = logging.getLogger("manager")

DL_INDEX_ENTRIES = 20


class BusyError(Exception):
    pass


class UnknownProfileError(Exception):
    pass


def new_scan_id(profile_id, now=None):
    now = now or datetime.now(timezone.utc)
    return "{}-{}-{}".format(now.strftime("%Y%m%d-%H%M%S"), profile_id[:24], secrets.token_hex(2))


def compact_hosts(result):
    """The host list for the Data Layer node last/hosts (short keys, no banners)."""
    out = []
    for h in result.get("hosts", []):
        names = [n["name"] for n in h.get("names", [])]
        out.append({
            "ip": h["ip"], "mac": h.get("mac", ""), "vendor": h.get("vendor", ""),
            "name": names[0] if names else "", "rating": h.get("rating", ""),
            "ports": ["{}/{}".format(p["proto"], p["port"]) for p in h.get("ports", [])],
            "forbidden": ["{}/{}".format(p["proto"], p["port"]) for p in h.get("ports", [])
                          if p.get("rating") == "forbidden"],
        })
    return out


class ScanManager:
    def __init__(self, store, history, registry=None, env=None, scheduler=None):
        self.store = store
        self.history = history
        self.registry = registry          # NodeRegistry of the Data Layer service (or None)
        self.env = env
        self.scheduler = scheduler or Scheduler()
        self._lock = threading.RLock()
        self._scan = None                 # running scanner.Scan
        self._thread = None
        self._queue = []                  # [(profile_id, trigger)]
        self._last_rated = None           # rated result of the newest scan (for nodes/status)
        self._listeners = []

    # -- setup ------------------------------------------------------------------
    def load(self):
        settings = self.store.get()
        self.history.set_limits(settings["history"]["maxScans"], settings["history"]["maxMegabytes"])
        try:
            count = self.history.load()
            log.info("History: %d scans in %s", count, self.history.dir)
        except OSError as exc:
            self.history.error = str(exc)
            log.error("History not available: %s", exc)
        newest = self.history.list()
        if newest:
            result = self.history.get(newest[0]["id"])
            if result is not None:
                self._last_rated = rating.apply(result, rating.Lists.from_settings(settings))
        self.publish()

    def add_listener(self, fn):
        """fn(event: str, data) for "finished" events (tests, future push)."""
        self._listeners.append(fn)

    def on_settings_changed(self, settings):
        self.history.set_limits(settings["history"]["maxScans"], settings["history"]["maxMegabytes"])
        with self._lock:
            if self._last_rated is not None:
                self._last_rated = rating.apply(self._last_rated, rating.Lists.from_settings(settings))
        self.publish()

    # -- profiles ---------------------------------------------------------------
    def profiles(self):
        return self.store.get()["profiles"]

    def profile(self, profile_id):
        for p in self.profiles():
            if p["id"] == profile_id:
                return p
        raise UnknownProfileError("unknown scan profile {!r}".format(profile_id))

    # -- control ----------------------------------------------------------------
    def start(self, profile_id, trigger="manual", user=""):
        """Start a scan now. Returns the scan id. Raises UnknownProfileError, BusyError."""
        profile = self.profile(profile_id)
        settings = self.store.get()
        with self._lock:
            if self._scan is not None:
                raise BusyError("a scan is already running ({})".format(self._scan.profile.get("name")))
            scan = scanner.Scan(new_scan_id(profile_id), profile, settings["scan"]["maxAddresses"],
                                trigger=trigger, user=user, env=self.env)
            self._scan = scan
            self._thread = threading.Thread(target=self._run, args=(scan,), name="scan", daemon=True)
            self._thread.start()
        log.info("Scan %s started (%s, profile %s%s)", scan.id, trigger, profile_id,
                 ", user " + user if user else "")
        self.publish()
        return scan.id

    def abort(self):
        with self._lock:
            scan = self._scan
            self._queue = []
        if scan is None:
            return False
        log.info("Scan %s: abort requested", scan.id)
        scan.cancel()
        self.publish()
        return True

    def tick(self):
        """Fire due schedules; start the next queued scan when idle."""
        try:
            profiles = self.profiles()
            with self._lock:
                busy = {pid for pid, _ in self._queue}
                if self._scan is not None:
                    busy.add(self._scan.profile.get("id"))
            for pid in self.scheduler.due(profiles, self.history.last_started, busy):
                with self._lock:
                    self._queue.append((pid, "schedule"))
                log.info("Schedule: scan of profile %s due", pid)
            with self._lock:
                idle = self._scan is None
                item = self._queue.pop(0) if idle and self._queue else None
            if item:
                try:
                    self.start(item[0], trigger=item[1])
                except (UnknownProfileError, BusyError) as exc:
                    log.warning("Queued scan not started: %s", exc)
            self._publish_schedule(profiles)
        except Exception:
            log.exception("Scheduler tick failed")

    def wait(self, timeout=None):
        """Block until the running scan (if any) has finished - for tests."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    # -- state ----------------------------------------------------------------------
    def current(self, with_hosts=False):
        with self._lock:
            scan = self._scan
            queue = list(self._queue)
        if scan is None:
            return {"running": False, "queued": [p for p, _ in queue]}
        state = {"running": True, "aborting": scan.cancelled, "queued": [p for p, _ in queue],
                 "progress": scan.progress()}
        if with_hosts:
            state["hosts"] = scan.partial()
        return state

    def next_runs(self):
        return self.scheduler.next_runs(self.profiles(), self.history.last_started)

    def last(self):
        with self._lock:
            return self._last_rated

    def rated(self, scan_id):
        """A stored scan rated with the current lists, or None."""
        result = self.history.get(scan_id)
        if result is None:
            return None
        rated = rating.apply(result, rating.Lists.from_settings(self.store.get()))
        rated["ratedWith"] = "current lists"
        return rated

    # -- scan thread -------------------------------------------------------------------
    def _run(self, scan):
        try:
            result = scan.run()
            self._finish(scan, result)
        except Exception:
            log.exception("Scan %s: finishing failed", scan.id)
        finally:
            with self._lock:
                if self._scan is scan:
                    self._scan = None
            self.publish()

    def _finish(self, scan, result):
        lists = rating.Lists.from_settings(self.store.get())
        previous = self.history.latest(result["profile"].get("id"), exclude_id=result["id"])
        if previous is not None and result["status"] == "completed":
            result["changes"] = rating.compare(previous, result)
        rated = rating.apply(result, lists)
        counts = rated["summary"]["rating"]
        result["summary"]["ratingAtScan"] = {k: counts[k] for k in rating.RATINGS}
        rated["summary"]["ratingAtScan"] = dict(result["summary"]["ratingAtScan"])
        try:
            self.history.add(result)
        except (OSError, ValueError) as exc:
            self.history.error = "cannot store scan {}: {}".format(result["id"], exc)
            log.error("%s", self.history.error)
        with self._lock:
            self._last_rated = rated
        log.info("Scan %s %s in %.1f s: %d of %d addresses up, %d open TCP, %d open UDP, %d alarm(s)%s",
                 result["id"], result["status"], result["durationS"], result["summary"]["hostsUp"],
                 result["summary"]["targets"], result["summary"]["openTcp"], result["summary"]["openUdp"],
                 counts["forbidden"], (" - " + result["error"]) if result.get("error") else "")
        if counts["forbidden"]:
            log.warning("Scan %s: %d blacklist finding(s): %s", result["id"], counts["forbidden"],
                        ", ".join(_alarm_text(a) for a in counts["alarms"][:10]))
        for fn in self._listeners:
            try:
                fn("finished", rated)
            except Exception:
                log.exception("listener failed")

    # -- Data Layer --------------------------------------------------------------------
    def _set(self, name, value):
        if self.registry is None:
            return
        try:
            self.registry.set_internal(name, value)
        except (KeyError, ValueError) as exc:
            log.debug("node %s: %s", name, exc)

    def publish(self):
        """Write the current state into the Data Layer node registry."""
        if self.registry is None:
            return
        cur = self.current()
        progress = cur.get("progress") or {}
        if cur["running"]:
            self._set("scan/state", "aborting" if cur.get("aborting") else "running")
        else:
            self._set("scan/state", "idle")
        self._set("scan/id", progress.get("id", ""))
        self._set("scan/profile", progress.get("profile", "") or "")
        self._set("scan/phase", progress.get("phase", "") if cur["running"] else "")
        self._set("scan/progress", float(progress.get("percent", 0.0)) if cur["running"] else 0.0)
        last = self.last()
        if last is not None:
            s = last["summary"]
            r = s.get("rating", {})
            changes = last.get("changes") or {}
            self._set("last/id", last["id"])
            self._set("last/profile", last["profile"].get("id", ""))
            self._set("last/status", last["status"])
            self._set("last/finished-at", last.get("finishedAt") or "")
            self._set("last/duration-s", float(last.get("durationS", 0.0)))
            self._set("last/hosts-up", s.get("hostsUp", 0))
            self._set("last/open-ports", s.get("openTcp", 0) + s.get("openUdp", 0))
            self._set("last/alarms", r.get("forbidden", 0))
            self._set("last/unlisted", r.get("unlisted", 0))
            self._set("last/changes", changes.get("total", 0) or 0)
            self._set("last/summary", json.dumps({
                "id": last["id"], "profile": last["profile"].get("id"), "status": last["status"],
                "startedAt": last.get("startedAt"), "finishedAt": last.get("finishedAt"),
                "durationS": last.get("durationS"), "targets": s.get("targets"), "hostsUp": s.get("hostsUp"),
                "openTcp": s.get("openTcp"), "openUdp": s.get("openUdp"),
                "forbidden": r.get("forbidden", 0), "ok": r.get("ok", 0), "unlisted": r.get("unlisted", 0),
                "changes": changes or None}, separators=(",", ":")))
            self._set("last/hosts", json.dumps(compact_hosts(last), separators=(",", ":")))
            self._set("last/alarm-list", json.dumps(r.get("alarms", []), separators=(",", ":")))
        index = self.history.list()
        self._set("history/count", len(index))
        self._set("history/index", json.dumps(index[:DL_INDEX_ENTRIES], separators=(",", ":")))

    def _publish_schedule(self, profiles):
        if self.registry is None:
            return
        self._set("schedule/next", json.dumps(self.next_runs(), separators=(",", ":")))
        self._set("profiles/list", json.dumps(
            [{"id": p["id"], "name": p["name"], "schedule": p["scheduleMode"]} for p in profiles],
            separators=(",", ":")))


def _alarm_text(alarm):
    if alarm["type"] == "host":
        return "host {}".format(alarm["ip"])
    return "{} {}/{}".format(alarm["ip"], alarm["proto"], alarm["port"])
