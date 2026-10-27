"""Readers for Linux /proc: raw bytes with os.open/os.read, no subprocesses (no ps/top in the snap).

What a strict snap may read (docs/ctrlx/gotchas.md #67): stat, status, statm, cgroup, cmdline and
task/<tid>/stat of every process always; smaps_rollup (PSS), io and /proc/pressure only with the
system-observe plug. Every reader returns None for a file that is missing or not readable (process
gone, plug not connected) - callers treat None as "no value", never as an error.
"""

import os
import re
from collections import namedtuple

CLK_TCK = os.sysconf("SC_CLK_TCK")
PF_KTHREAD = 0x00200000          # task flag of kernel threads (stat field 9)
POLICIES = {0: "OTHER", 1: "FIFO", 2: "RR", 3: "BATCH", 5: "IDLE", 6: "DEADLINE"}

Stat = namedtuple("Stat", "comm state ppid flags utime stime nice threads starttime processor "
                          "rt_priority policy")

_STATUS_KEYS = {b"VmRSS": "rss", b"RssAnon": "anon", b"RssFile": "file", b"RssShmem": "shmem",
                b"VmSwap": "swap", b"VmHWM": "hwm"}
_MEMINFO_KEYS = {b"MemTotal", b"MemFree", b"MemAvailable", b"Buffers", b"Cached", b"Shmem",
                 b"SwapTotal", b"SwapFree"}
_SNAP_REVISION = re.compile(rb"/snap/([a-z0-9][a-z0-9-]*)/(x?[0-9]+)/")


def read(path, limit=262144):
    """Whole file (up to limit bytes) or None."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return None
    try:
        chunks = []
        size = 0
        while size < limit:
            want = min(65536, limit - size)
            chunk = os.read(fd, want)
            chunks.append(chunk)
            size += len(chunk)
            if len(chunk) < want:      # /proc files arrive in one read when they fit: no EOF read
                break
        return b"".join(chunks)
    except OSError:
        return None
    finally:
        os.close(fd)


def parse_stat(data):
    """/proc/<pid>/stat or task/<tid>/stat -> Stat. comm may contain spaces and ')'."""
    if not data:
        return None
    try:
        lpar = data.index(b"(")
        rpar = data.rindex(b")")
        f = data[rpar + 2:].split()   # f[i] is field i + 3 of proc(5)
        return Stat(comm=data[lpar + 1:rpar].decode("utf-8", "replace"), state=f[0].decode(),
                    ppid=int(f[1]), flags=int(f[6]), utime=int(f[11]), stime=int(f[12]),
                    nice=int(f[16]), threads=int(f[17]), starttime=int(f[19]), processor=int(f[36]),
                    rt_priority=int(f[37]), policy=int(f[38]))
    except (ValueError, IndexError):
        return None


def parse_status(data):
    """/proc/<pid>/status -> {"rss", "anon", "file", "shmem", "swap", "hwm"} in KB (kernel threads: {})."""
    out = {}
    if not data:
        return out
    for key, name in _STATUS_KEYS.items():
        i = data.find(b"\n" + key + b":")
        if i >= 0:
            j = data.find(b"\n", i + 1)
            parts = data[i + len(key) + 2:j if j > 0 else None].split()
            if parts:
                out[name] = int(parts[0])
    return out


def parse_pss(data):
    """/proc/<pid>/smaps_rollup -> Pss in KB, or None."""
    if not data:
        return None
    for line in data.split(b"\n"):
        if line.startswith(b"Pss:"):
            parts = line.split()
            return int(parts[1]) if len(parts) > 1 else None
    return None


def parse_cgroup(data):
    """/proc/<pid>/cgroup -> the systemd cgroup path ("/system.slice/x.service"), or "".

    ctrlX OS 4.6 runs cgroup v1 (hybrid): the "name=systemd" line carries the unit; on cgroup v2
    there is only the "0::" line.
    """
    if not data:
        return ""
    unified = ""
    for line in data.split(b"\n"):
        parts = line.split(b":", 2)
        if len(parts) != 3:
            continue
        if parts[1] == b"name=systemd":
            return parts[2].decode("utf-8", "replace")
        if parts[0] == b"0" and parts[1] == b"":
            unified = parts[2].decode("utf-8", "replace")
    return unified


def parse_cmdline(data):
    if not data:
        return ""
    return data.rstrip(b"\0").replace(b"\0", b" ").decode("utf-8", "replace")


def snap_revision(cmdline):
    """'/snap/<name>/<revision>/...' in a command line -> (name, revision) or None."""
    m = _SNAP_REVISION.search(cmdline.encode("utf-8", "replace"))
    return (m.group(1).decode(), m.group(2).decode()) if m else None


def parse_proc_stat(data):
    """/proc/stat -> {"all": (total, idle), "cores": [(total, idle), ...], "btime": seconds}.

    idle includes iowait; total sums user..steal (guest time is already part of user).
    """
    out = {"all": None, "cores": [], "btime": 0}
    if not data:
        return out
    for line in data.split(b"\n"):
        if line.startswith(b"cpu"):
            parts = line.split()
            values = [int(v) for v in parts[1:9]]
            values += [0] * (8 - len(values))
            entry = (sum(values), values[3] + values[4])
            if parts[0] == b"cpu":
                out["all"] = entry
            else:
                out["cores"].append(entry)
        elif line.startswith(b"btime"):
            out["btime"] = int(line.split()[1])
    return out


def parse_meminfo(data):
    """/proc/meminfo -> {"MemTotal": kB, ...} (selected keys)."""
    out = {}
    if not data:
        return out
    for line in data.split(b"\n"):
        key, _, value = line.partition(b":")
        if key in _MEMINFO_KEYS:
            parts = value.split()
            if parts:
                out[key.decode()] = int(parts[0])
    return out


def parse_pressure(data):
    """/proc/pressure/<cpu|memory|io> -> "some avg10" in percent, or None."""
    if not data:
        return None
    for line in data.split(b"\n"):
        if line.startswith(b"some"):
            for part in line.split():
                if part.startswith(b"avg10="):
                    return float(part[6:])
    return None
