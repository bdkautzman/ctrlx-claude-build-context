"""Test helpers: a fake /proc tree in a temp folder (what the collector reads on a ctrlX CORE)."""

import json
import os

from app import procfs

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULTS_FILE = os.path.join(HERE, "..", "defaults", "settings.json")
BTIME = 1_700_000_000


def load_defaults():
    with open(DEFAULTS_FILE, encoding="utf-8") as f:
        return json.load(f)


def stat_line(pid, comm, utime=0, stime=0, threads=1, starttime=100, flags=0, nice=0, processor=0,
              rt_priority=0, policy=0, state="S"):
    """A /proc/<pid>/stat line with all 41 fields of proc(5)."""
    f = [pid, "(" + comm + ")", state, 1, pid, pid, 0, -1, flags, 0, 0, 0, 0, utime, stime, 0, 0, 20, nice,
         threads, 0, starttime, 1000000, 100, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 17, processor,
         rt_priority, policy]
    assert len(f) == 41
    return " ".join(str(x) for x in f) + "\n"


class FakeProc:
    """Build and change a /proc tree: processes, threads, memory, system counters."""

    def __init__(self, root, cores=2):
        self.root = root
        self.cores = cores
        self.cpu = [[1000, 0, 500, 10000, 100] for _ in range(cores)]   # user nice system idle iowait
        self.mem = {"MemTotal": 2000000, "MemFree": 800000, "MemAvailable": 1200000, "SwapTotal": 0, "SwapFree": 0}
        self.procs = {}
        os.makedirs(os.path.join(root, "pressure"), exist_ok=True)
        self.write_system()

    def write_system(self):
        lines = []
        total = [sum(c[i] for c in self.cpu) for i in range(5)]
        lines.append("cpu  " + " ".join(str(v) for v in total) + " 0 0 0 0 0")
        for i, c in enumerate(self.cpu):
            lines.append("cpu{} ".format(i) + " ".join(str(v) for v in c) + " 0 0 0 0 0")
        lines.append("btime {}".format(BTIME))
        self._write("stat", "\n".join(lines) + "\n")
        self._write("meminfo", "".join("{}: {} kB\n".format(k, v) for k, v in self.mem.items()))
        for name in ("cpu", "memory", "io"):
            self._write("pressure/" + name, "some avg10=1.50 avg60=0.00 avg300=0.00 total=0\n"
                                            "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")

    def busy(self, ticks_per_core):
        """Let every core work ticks (user) and idle the same amount."""
        for c in self.cpu:
            c[0] += ticks_per_core
            c[3] += ticks_per_core
        self.write_system()

    def add(self, pid, comm, cgroup, cmdline="", anon=1000, file=500, shmem=0, swap=0, pss=None,
            utime=0, stime=0, threads=None, kernel=False, starttime=100):
        """threads: {tid: dict(comm, utime, stime, policy, rt_priority)} - main thread added automatically."""
        p = {"comm": comm, "cgroup": cgroup, "cmdline": cmdline, "anon": anon, "file": file, "shmem": shmem,
             "swap": swap, "pss": pss, "utime": utime, "stime": stime, "kernel": kernel, "starttime": starttime,
             "threads": dict(threads or {})}
        self.procs[pid] = p
        self.write_proc(pid)
        return p

    def update(self, pid, **changes):
        self.procs[pid].update(changes)
        self.write_proc(pid)

    def remove(self, pid):
        self.procs.pop(pid)
        base = os.path.join(self.root, str(pid))
        for dirpath, dirnames, filenames in os.walk(base, topdown=False):
            for f in filenames:
                os.remove(os.path.join(dirpath, f))
            os.rmdir(dirpath)

    def write_proc(self, pid):
        p = self.procs[pid]
        nthreads = 1 + len(p["threads"])
        flags = procfs.PF_KTHREAD if p["kernel"] else 0
        self._write("{}/stat".format(pid), stat_line(pid, p["comm"], p["utime"], p["stime"], nthreads,
                                                     p["starttime"], flags))
        self._write("{}/cgroup".format(pid), "10:freezer:/\n1:name=systemd:{}\n0::/\n".format(p["cgroup"]))
        self._write("{}/cmdline".format(pid), p["cmdline"].replace(" ", "\0") + ("\0" if p["cmdline"] else ""))
        if p["kernel"]:
            self._write("{}/status".format(pid), "Name:\t{}\nThreads:\t1\n".format(p["comm"]))
        else:
            rss = p["anon"] + p["file"] + p["shmem"]
            self._write("{}/status".format(pid), "Name:\t{}\nVmHWM:\t{} kB\nVmRSS:\t{} kB\nRssAnon:\t{} kB\n"
                        "RssFile:\t{} kB\nRssShmem:\t{} kB\nVmSwap:\t{} kB\nThreads:\t{}\n".format(
                            p["comm"], rss + 100, rss, p["anon"], p["file"], p["shmem"], p["swap"], nthreads))
            if p["pss"] is not None:
                self._write("{}/smaps_rollup".format(pid), "00400000-ff [rollup]\nRss: {} kB\nPss: {} kB\n".format(rss, p["pss"]))
        self._write("{}/task/{}/stat".format(pid, pid), stat_line(pid, p["comm"], p["utime"], p["stime"], nthreads,
                                                                   p["starttime"], flags))
        for tid, t in p["threads"].items():
            self._write("{}/task/{}/stat".format(pid, tid), stat_line(
                tid, t.get("comm", "worker"), t.get("utime", 0), t.get("stime", 0), nthreads,
                t.get("starttime", p["starttime"]), flags, processor=t.get("processor", 1),
                rt_priority=t.get("rt_priority", 0), policy=t.get("policy", 0)))

    def _write(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
