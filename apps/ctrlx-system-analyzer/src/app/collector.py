"""Samples CPU and memory of the system, every app (snap), process and thread from /proc.

One sample every collector.intervalSeconds:
  /proc/stat, /proc/meminfo, /proc/pressure/*          system CPU per core, memory, pressure stall
  /proc/<pid>/stat                                      CPU time, threads, scheduling of each process
  /proc/<pid>/status (+ smaps_rollup if collector.pss)  memory: RSS split anon/file/shmem, swap, PSS
                                                        (PSS every PSS_EVERY_S, the last value in between)
  /proc/<pid>/task/<tid>/stat                           CPU of each thread (collector.threads)
cgroup and cmdline are read once per process. CPU is percent of ONE core (like top); app values are
sums over their processes. Summed RSS/file/shmem count shared pages once per process; PSS doesn't.

Events: a service whose processes are all replaced between two samples "restarted"; one that loses
all processes "stopped", a new one "started"; a snap whose processes run from a new revision "updated".
"""

import logging
import os
import threading
import time
from collections import deque

from app import grouping, procfs

log = logging.getLogger("collector")

MIN_SAMPLES_FOR_SERIES = 2   # processes/threads get their own series from their 2nd sample ...
MIN_AGE_FOR_SERIES_S = 60.0  # ... and once they live for a minute: helpers like `sleep 10` count for their app only
PSS_EVERY_S = 30.0           # smaps_rollup makes the kernel walk the page tables: PSS only every 30 s
KB = 1024.0


class EventLog:
    """Events in RAM (newest MAX_EVENTS); listeners get every new event (external medium)."""
    MAX_EVENTS = 5000

    def __init__(self):
        self._events = deque(maxlen=self.MAX_EVENTS)
        self._lock = threading.Lock()
        self.listeners = []

    def add(self, t, etype, text, group=None, service=""):
        event = {"time": t, "type": etype, "text": text,
                 "app": group.key if group else "", "appName": group.name if group else "", "service": service}
        with self._lock:
            self._events.append(event)
        for fn in self.listeners:
            try:
                fn(event)
            except Exception:
                log.exception("event listener failed")
        return event

    def between(self, t0, t1):
        with self._lock:
            return [e for e in self._events if t0 <= e["time"] <= t1]


class _Proc:
    __slots__ = ("pid", "start", "start_epoch", "key", "group", "comm", "cmdline", "kernel", "revision",
                 "ticks", "samples", "threads", "pss", "pss_time")

    def __init__(self, pid, st, start_epoch, group, cmdline, kernel):
        self.pid = pid
        self.start = st.starttime
        self.start_epoch = start_epoch
        self.key = "{}-{}".format(pid, int(start_epoch))
        self.group = group
        self.comm = st.comm
        self.cmdline = cmdline
        self.kernel = kernel
        rev = procfs.snap_revision(cmdline) if group.kind == "snap" else None
        self.revision = rev[1] if rev and rev[0] == group.name else None
        self.ticks = None
        self.samples = 0
        self.threads = {}     # tid -> [starttime, ticks, samples]
        self.pss = None
        self.pss_time = None


def _pct(delta_ticks, dt):
    return max(0.0, delta_ticks / procfs.CLK_TCK / dt * 100.0)


def _busy(prev, cur):
    total = cur[0] - prev[0]
    idle = cur[1] - prev[1]
    return max(0.0, min(100.0, (total - idle) * 100.0 / total)) if total > 0 else 0.0


class Collector:
    def __init__(self, history, events, proc_root="/proc", clock=time.time):
        self.history = history
        self.events = events
        self.root = proc_root
        self.clock = clock
        self.interval = 5.0
        self.threads_on = True
        self.pss_on = True
        self.on_sample = []          # fn(snapshot) after each sample (Data Layer nodes)
        self._procs = {}
        self._prev_stat = None
        self._prev_time = None
        self._services = {}          # (group key, service) -> set of process keys
        self._revisions = {}         # group key -> revision
        self._known = {}             # entity ("app.<g>", "proc.<g>.<p>", "thr.<g>.<t>") -> info
        self._lock = threading.Lock()
        self._snapshot = None
        self._stats = {"samples": 0, "lastSampleMs": 0.0, "lastCpuMs": 0.0, "avgCpuMs": 0.0,
                       "pssAvailable": None, "pressureAvailable": None, "error": None}
        self._cpu_ms = deque(maxlen=12)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self.own_pid = os.getpid()

    # -- control --------------------------------------------------------------
    def configure(self, settings):
        c = settings["collector"]
        self.interval = float(c["intervalSeconds"])
        self.threads_on = bool(c["threads"])
        self.pss_on = bool(c["pss"])
        self._wake.set()

    def start(self):
        self._thread = threading.Thread(target=self._run, name="collector", daemon=True)
        self._thread.start()

    def stop(self, timeout=10.0):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)

    def _run(self):
        next_at = time.monotonic()
        while not self._stop.is_set():
            try:
                self.sample()
            except Exception as exc:
                log.exception("sample failed")
                self._stats["error"] = str(exc)
            next_at += self.interval
            now = time.monotonic()
            if next_at < now:                      # fell behind (or interval changed): don't catch up
                next_at = now + self.interval
            self._wake.clear()
            self._wake.wait(next_at - now)
            if self._wake.is_set() and not self._stop.is_set():
                next_at = time.monotonic() + self.interval

    # -- reading --------------------------------------------------------------
    def snapshot(self):
        with self._lock:
            return self._snapshot

    def status(self):
        s = dict(self._stats)
        s.update({"intervalSeconds": self.interval, "threads": self.threads_on, "pss": self.pss_on})
        snap = self.snapshot()
        if snap:
            s["processes"] = snap["counts"]["processes"]
            s["threadCount"] = snap["counts"]["threads"]
        return s

    def describe(self, entity):
        """Last known names of an app/process/thread entity (also after it ended), or None."""
        return self._known.get(entity)

    # -- sampling -------------------------------------------------------------
    def sample(self, now=None):
        t_wall = time.monotonic()
        t_cpu = time.thread_time()
        now = self.clock() if now is None else now
        root = self.root
        ps = procfs.parse_proc_stat(procfs.read(root + "/stat"))
        mem = procfs.parse_meminfo(procfs.read(root + "/meminfo"))
        psi = {k: procfs.parse_pressure(procfs.read(root + "/pressure/" + k)) for k in ("cpu", "memory", "io")}
        btime = ps["btime"]
        dt = (now - self._prev_time) if self._prev_time is not None else None
        if dt is not None and dt <= 0:
            dt = None                               # clock jumped back: no CPU values this time

        values = {}
        system = self._system(ps, mem, psi, values)
        groups = {}
        seen = set()
        pss_ok = None
        counts = {"processes": 0, "threads": 0}
        cur_services = {}
        cur_revisions = {}

        try:
            names = os.listdir(root)
        except OSError as exc:
            raise RuntimeError("cannot list {}: {}".format(root, exc))
        for name in names:
            if not name.isdigit():
                continue
            pid = int(name)
            base = root + "/" + name
            st = procfs.parse_stat(procfs.read(base + "/stat"))
            if st is None:
                continue                            # ended meanwhile
            proc = self._procs.get(pid)
            if proc is None or proc.start != st.starttime:
                proc = self._new_proc(pid, st, base, btime)
                self._procs[pid] = proc
            seen.add(pid)
            proc.samples += 1
            proc.comm = st.comm
            g = proc.group
            counts["processes"] += 1
            counts["threads"] += st.threads

            ticks = st.utime + st.stime
            cpu = None
            if dt:
                if proc.ticks is not None:
                    cpu = _pct(ticks - proc.ticks, dt)
                elif proc.start_epoch >= self._prev_time - 1.0:   # started during this interval
                    cpu = _pct(ticks, dt)
            proc.ticks = ticks

            memory = {}
            if not proc.kernel:
                memory = procfs.parse_status(procfs.read(base + "/status"))
                if self.pss_on and memory:
                    if proc.pss_time is None or now - proc.pss_time >= PSS_EVERY_S or now < proc.pss_time:
                        proc.pss = procfs.parse_pss(procfs.read(base + "/smaps_rollup"))
                        proc.pss_time = now
                    if proc.pss is not None:
                        memory["pss"] = proc.pss
                        pss_ok = True
                    elif pss_ok is None:
                        pss_ok = False

            threads = []
            if self.threads_on and not proc.kernel and st.threads > 1:
                threads = self._threads(proc, base, dt, btime, values, now)
            elif proc.threads:
                proc.threads = {}

            pinfo = self._proc_info(proc, st, cpu, memory, threads)
            agg = groups.get(g.key)
            if agg is None:
                agg = groups[g.key] = {"group": g, "procs": 0, "threads": 0, "cpu": None, "pss_missing": False,
                                       "anon": 0, "file": 0, "shmem": 0, "rss": 0, "pss": 0, "swap": 0,
                                       "processes": [], "self": False, "services": set()}
            agg["procs"] += 1
            agg["threads"] += st.threads
            if cpu is not None:
                agg["cpu"] = (agg["cpu"] or 0.0) + cpu
            for k in ("anon", "file", "shmem", "rss", "swap"):
                agg[k] += memory.get(k, 0)
            if "pss" in memory:
                agg["pss"] += memory["pss"]
            elif not proc.kernel:
                agg["pss_missing"] = True
            agg["processes"].append(pinfo)
            if pid == self.own_pid:
                agg["self"] = True
            if g.service:
                agg["services"].add(g.service)

            # own series once the process has lived for a moment (short-lived helpers only count for the app)
            if proc.samples >= MIN_SAMPLES_FOR_SERIES and now - proc.start_epoch >= MIN_AGE_FOR_SERIES_S:
                prefix = "proc.{}.{}.".format(g.key, proc.key)
                if cpu is not None and (not proc.kernel or cpu > 0 or self.history.has(prefix + "cpu")):
                    values[prefix + "cpu"] = cpu * 100
                if memory:
                    for k in ("anon", "rss", "pss", "swap"):
                        if k in memory:
                            values[prefix + k] = memory[k]
                    values[prefix + "threads"] = st.threads
                self._known[prefix[:-1]] = {"pid": pid, "name": proc.comm, "service": g.service, "app": g.key,
                                            "appName": g.name, "file": g.file, "seen": now}
            if g.unit:
                cur_services.setdefault((g.key, g.service), set()).add(proc.key)
            if proc.revision:
                cur_revisions.setdefault(g.key, {}).setdefault(proc.revision, 0)
                cur_revisions[g.key][proc.revision] += 1

        for pid in [p for p in self._procs if p not in seen]:
            del self._procs[pid]

        for key, agg in groups.items():
            prefix = "app.{}.".format(key)
            if agg["cpu"] is not None:
                values[prefix + "cpu"] = agg["cpu"] * 100
            values[prefix + "procs"] = agg["procs"]
            values[prefix + "threads"] = agg["threads"]
            if agg["group"].kind != "kernel":
                for k in ("anon", "file", "shmem", "rss", "swap"):
                    values[prefix + k] = agg[k]
                if not agg["pss_missing"]:
                    values[prefix + "pss"] = agg["pss"]
            self._known[prefix[:-1]] = {"name": agg["group"].name, "kind": agg["group"].kind,
                                        "file": agg["group"].file, "seen": now}

        if self._stats["samples"] > 0:
            self._detect_events(now, cur_services, cur_revisions, groups)
        self._services = cur_services
        for key, revs in cur_revisions.items():
            self._revisions[key] = max(revs, key=revs.get)

        self.history.add(now, values)
        self._prev_stat = ps["all"] and ps
        self._prev_time = now

        snapshot = self._build_snapshot(now, system, groups, counts)
        with self._lock:
            self._snapshot = snapshot
        cpu_ms = (time.thread_time() - t_cpu) * 1000.0
        self._cpu_ms.append(cpu_ms)
        self._stats.update({
            "samples": self._stats["samples"] + 1,
            "lastSampleMs": round((time.monotonic() - t_wall) * 1000.0, 1),
            "lastCpuMs": round(cpu_ms, 1),
            "avgCpuMs": round(sum(self._cpu_ms) / len(self._cpu_ms), 1),
            "pssAvailable": pss_ok if self.pss_on else None,
            "pressureAvailable": psi["memory"] is not None,
            "error": None,
        })
        if self._stats["samples"] % 120 == 0:
            self._forget_ended()
        for fn in self.on_sample:
            try:
                fn(snapshot)
            except Exception:
                log.exception("sample listener failed")
        return snapshot

    def _system(self, ps, mem, psi, values):
        cores = []
        cpu = None
        prev = self._prev_stat
        if prev and ps["all"]:
            cpu = _busy(prev["all"], ps["all"])
            values["sys.cpu"] = cpu * 100
            for i, core in enumerate(ps["cores"]):
                if i < len(prev["cores"]):
                    c = _busy(prev["cores"][i], core)
                    cores.append(round(c, 1))
                    values["sys.core{}".format(i)] = c * 100
        total = mem.get("MemTotal", 0)
        avail = mem.get("MemAvailable", mem.get("MemFree", 0))
        swap_used = mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)
        if total:
            values["sys.memused"] = total - avail
            values["sys.memavail"] = avail
            values["sys.swapused"] = swap_used
        for name, key in (("cpu", "sys.psicpu"), ("memory", "sys.psimem"), ("io", "sys.psiio")):
            if psi[name] is not None:
                values[key] = psi[name] * 100
        return {"cpu": None if cpu is None else round(cpu, 1), "cores": cores,
                "coreCount": len(ps["cores"]),
                "memTotalMb": round(total / KB, 1), "memUsedMb": round((total - avail) / KB, 1),
                "memAvailableMb": round(avail / KB, 1), "swapTotalMb": round(mem.get("SwapTotal", 0) / KB, 1),
                "swapUsedMb": round(swap_used / KB, 1), "pressure": psi}

    def _new_proc(self, pid, st, base, btime):
        kernel = bool(st.flags & procfs.PF_KTHREAD)
        group = grouping.classify(procfs.parse_cgroup(procfs.read(base + "/cgroup")), kernel)
        cmdline = "" if kernel else procfs.parse_cmdline(procfs.read(base + "/cmdline", 4096))
        return _Proc(pid, st, btime + st.starttime / procfs.CLK_TCK, group, cmdline, kernel)

    def _threads(self, proc, base, dt, btime, values, now):
        try:
            tids = os.listdir(base + "/task")
        except OSError:
            return []
        out = []
        cache = {}
        g = proc.group
        for name in tids:
            st = procfs.parse_stat(procfs.read("{}/task/{}/stat".format(base, name)))
            if st is None:
                continue
            tid = int(name)
            prev = proc.threads.get(tid)
            ticks = st.utime + st.stime
            cpu = None
            if prev is not None and prev[0] == st.starttime:
                samples = prev[2] + 1
                if dt:
                    cpu = _pct(ticks - prev[1], dt)
            else:
                samples = 1
                if dt and self._prev_time is not None and btime + st.starttime / procfs.CLK_TCK >= self._prev_time - 1.0:
                    cpu = _pct(ticks, dt)
            cache[tid] = [st.starttime, ticks, samples]
            start_epoch = btime + st.starttime / procfs.CLK_TCK
            key = "{}-{}-{}".format(proc.pid, tid, int(start_epoch))
            entity = "thr.{}.{}".format(g.key, key)
            if (cpu is not None and samples >= MIN_SAMPLES_FOR_SERIES and now - start_epoch >= MIN_AGE_FOR_SERIES_S
                    and (cpu > 0 or self.history.has(entity + ".cpu"))):
                values[entity + ".cpu"] = cpu * 100
                self._known[entity] = {"pid": proc.pid, "tid": tid, "name": st.comm, "app": g.key,
                                       "appName": g.name, "file": g.file, "seen": now}
            out.append({"key": key, "tid": tid, "name": st.comm, "cpu": None if cpu is None else round(cpu, 2),
                        "policy": procfs.POLICIES.get(st.policy, str(st.policy)), "rtPriority": st.rt_priority,
                        "nice": st.nice, "core": st.processor, "state": st.state})
        proc.threads = cache
        return out

    def _proc_info(self, proc, st, cpu, memory, threads):
        mb = lambda k: round(memory[k] / KB, 2) if k in memory else None  # noqa: E731
        return {"key": proc.key, "pid": proc.pid, "name": proc.comm, "service": proc.group.service,
                "cmdline": proc.cmdline[:300], "state": st.state, "started": round(proc.start_epoch),
                "kernel": proc.kernel, "cpu": None if cpu is None else round(cpu, 2),
                "rssMb": mb("rss"), "anonMb": mb("anon"), "fileMb": mb("file"), "shmemMb": mb("shmem"),
                "pssMb": mb("pss"), "swapMb": mb("swap"), "hwmMb": mb("hwm"), "threads": st.threads,
                "policy": procfs.POLICIES.get(st.policy, str(st.policy)), "rtPriority": st.rt_priority,
                "nice": st.nice, "core": st.processor, "revision": proc.revision,
                "self": proc.pid == self.own_pid, "threadList": threads}

    def _detect_events(self, now, cur_services, cur_revisions, groups):
        group_of = {key: agg["group"] for key, agg in groups.items()}
        for (key, service) in set(self._services) | set(cur_services):
            prev = self._services.get((key, service), set())
            cur = cur_services.get((key, service), set())
            g = group_of.get(key) or grouping.Group(key, (self._known.get("app." + key) or {}).get("name", key),
                                                    "", service, "", True)
            if prev and cur and not prev & cur:
                self.events.add(now, "restarted", "{} restarted".format(service), g, service)
            elif prev and not cur:
                self.events.add(now, "stopped", "{} stopped".format(service), g, service)
            elif cur and not prev:
                self.events.add(now, "started", "{} started".format(service), g, service)
        for key, revs in cur_revisions.items():
            new = max(revs, key=revs.get)
            old = self._revisions.get(key)
            if old and new != old:
                self.events.add(now, "updated", "revision {} -> {}".format(old, new), group_of.get(key))

    def _build_snapshot(self, now, system, groups, counts):
        out = {}
        for key, agg in groups.items():
            g = agg["group"]
            mb = lambda k: round(agg[k] / KB, 2)  # noqa: E731
            kernel = g.kind == "kernel"
            out[key] = {
                "key": key, "name": g.name, "kind": g.kind, "self": agg["self"],
                "services": sorted(agg["services"]), "processes": agg["procs"], "threads": agg["threads"],
                "cpu": None if agg["cpu"] is None else round(agg["cpu"], 2),
                "anonMb": None if kernel else mb("anon"), "fileMb": None if kernel else mb("file"),
                "shmemMb": None if kernel else mb("shmem"), "rssMb": None if kernel else mb("rss"),
                "pssMb": None if kernel or agg["pss_missing"] else mb("pss"),
                "swapMb": None if kernel else mb("swap"),
                "revision": self._revisions.get(key),
                "processList": sorted(agg["processes"], key=lambda p: -(p["rssMb"] or 0) - (p["cpu"] or 0)),
            }
        return {"time": now, "interval": self.interval, "system": system, "groups": out, "counts": counts}

    def _forget_ended(self):
        """Names of ended processes/threads are kept while the history still has their series."""
        alive = self.history.entities()
        for entity in [e for e in self._known if e not in alive]:
            del self._known[entity]
