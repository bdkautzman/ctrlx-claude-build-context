"""Time series in RAM: raw samples plus 1-minute and 10-minute buckets (min/avg/max), each a ring.

Every tier has ONE time axis (a ring of timestamps) and each series has arrays aligned to it, so a
sample costs one array write per series and tier, and a query reads plain arrays. Values are kept as
integers in "storage units" to keep the RAM small; queries convert them to API units:
  cpu-like metrics: centi-percent (of one core), uint16 'H', 65535 = no value -> up to 655.34 %
  all others:       int32 'i', -1 = no value - memory in KB, counts
Thread series ("thr.") live in the raw and minute tiers only, all others also in the 10-minute tier.

Series ids: <scope>.<parts>.<metric>, scope sys | app | proc | thr, e.g.
  sys.cpu  sys.core0  app.snap:rexroth-automationcore.anon  proc.snap:x.1220-1793113270.cpu
A RAM budget (history.maxMemoryMb, default 28: the app needs ~45 MB without it, the App Guide asks < 75 MB) bounds the arrays: series without new values (ended processes)
are dropped oldest first; if live series alone fill it, no new process/thread series are created.
"""

import logging
import math
import threading
from array import array

log = logging.getLogger("history")

# metric -> (typecode, divisor storage -> API unit, API unit)
_PCT = ("H", 100.0, "%")
_MB = ("i", 1024.0, "MB")
_COUNT = ("i", 1.0, "")
METRICS = {
    "cpu": _PCT, "core": _PCT, "psicpu": _PCT, "psimem": _PCT, "psiio": _PCT,
    "anon": _MB, "file": _MB, "shmem": _MB, "rss": _MB, "pss": _MB, "swap": _MB, "hwm": _MB,
    "memused": _MB, "memavail": _MB, "swapused": _MB,
    "threads": _COUNT, "procs": _COUNT,
}
_NONE = {"H": 65535, "i": -1}
_LIMIT = {"H": 65534, "i": 2 ** 31 - 2}
_ITEMSIZE = {"H": 2, "i": 4}
# scopes that may be refused when the budget is full (system and app series always get a place)
_OPTIONAL = ("proc.", "thr.")


def metric_of(sid):
    metric = sid.rsplit(".", 1)[-1]
    return "core" if metric.startswith("core") else metric


def is_long(sid):
    """Kept in the 10-minute tier?"""
    return not sid.startswith("thr.")


def to_api(sid, value):
    if value is None:
        return None
    _, div, _ = METRICS[metric_of(sid)]
    if div == 1.0:
        return value
    return round(value / div, 3 if div == 1024.0 else 2)


def downsample(times, series, max_points):
    """Merge neighbouring points to at most max_points (min of mins, mean of avgs, max of maxes).

    series: sid -> (mins, avgs, maxs), lists aligned to times, None = no value.
    """
    n = len(times)
    if not max_points or n <= max_points:
        return times, series
    edges = [k * n // max_points for k in range(max_points + 1)]
    new_times = [times[edges[k]] for k in range(max_points)]
    out = {}
    for sid, (mins, avgs, maxs) in series.items():
        n_min, n_avg, n_max = [], [], []
        for k in range(max_points):
            a, b = edges[k], edges[k + 1]
            vals = [v for v in avgs[a:b] if v is not None]
            if not vals:
                n_min.append(None)
                n_avg.append(None)
                n_max.append(None)
                continue
            lo = [v for v in mins[a:b] if v is not None]
            hi = [v for v in maxs[a:b] if v is not None]
            n_avg.append(sum(vals) / len(vals))
            n_min.append(min(lo) if lo else None)
            n_max.append(max(hi) if hi else None)
        out[sid] = (n_min, n_avg, n_max)
    return new_times, out


class _Tier:
    def __init__(self, name, step, capacity, aggregated):
        self.name = name
        self.step = step                  # bucket length in s; 0 = raw samples
        self.aggregated = aggregated      # 3 arrays (min, avg, max) per series instead of 1
        self.capacity = max(1, int(capacity))
        self.times = array("d", bytes(8 * self.capacity))
        self.head = 0                     # next slot to write
        self.count = 0
        self.series = {}                  # sid -> [array] or [min, avg, max]

    def series_bytes(self, sid):
        return self.capacity * _ITEMSIZE[METRICS[metric_of(sid)][0]] * (3 if self.aggregated else 1)

    def order(self):
        if self.count < self.capacity:
            return list(range(self.count))
        return list(range(self.head, self.capacity)) + list(range(self.head))

    def oldest(self):
        if not self.count:
            return None
        return self.times[0 if self.count < self.capacity else self.head]

    def newest(self):
        return self.times[(self.head - 1) % self.capacity] if self.count else None

    def add_series(self, sid):
        code = METRICS[metric_of(sid)][0]
        self.series[sid] = [array(code, [_NONE[code]]) * self.capacity
                            for _ in range(3 if self.aggregated else 1)]

    def push(self, t, values):
        """values: sid -> int (raw) or (min, avg, max); every series must exist already."""
        i = self.head
        self.times[i] = t
        for sid, arrays in self.series.items():
            v = values.get(sid)
            if v is None:
                none = _NONE[arrays[0].typecode]
                for a in arrays:
                    a[i] = none
            elif self.aggregated:
                arrays[0][i], arrays[1][i], arrays[2][i] = v
            else:
                arrays[0][i] = v
        self.head = (i + 1) % self.capacity
        self.count = min(self.count + 1, self.capacity)

    def resize(self, capacity):
        """New capacity; keeps the newest data."""
        capacity = max(1, int(capacity))
        if capacity == self.capacity:
            return
        keep = self.order()[-capacity:]
        times = array("d", bytes(8 * capacity))
        for j, i in enumerate(keep):
            times[j] = self.times[i]
        for sid, arrays in self.series.items():
            resized = []
            for a in arrays:
                b = array(a.typecode, [_NONE[a.typecode]]) * capacity
                for j, i in enumerate(keep):
                    b[j] = a[i]
                resized.append(b)
            self.series[sid] = resized
        self.times = times
        self.capacity = capacity
        self.count = len(keep)
        self.head = len(keep) % capacity

    def read(self, sids, t0, t1):
        """-> times, {sid: (mins, avgs, maxs)} in storage units (None = no value)."""
        idx = [i for i in self.order() if t0 <= self.times[i] <= t1]
        times = [self.times[i] for i in idx]
        out = {}
        for sid in sids:
            arrays = self.series.get(sid)
            if arrays is None:
                continue
            none = _NONE[arrays[0].typecode]
            cols = [[None if a[i] == none else a[i] for i in idx] for a in arrays]
            out[sid] = tuple(cols) if len(cols) == 3 else (cols[0], cols[0], cols[0])
        return times, out


class History:
    def __init__(self, raw_minutes=120, minute_hours=24, ten_minute_days=7, interval_s=5.0,
                 max_memory_mb=28):
        self._lock = threading.RLock()
        self.raw = _Tier("raw", 0, 1, False)
        self.minute = _Tier("minute", 60, 1, True)
        self.ten = _Tier("tenminute", 600, 1, True)
        self.interval_s = interval_s
        self.max_bytes = int(max_memory_mb * 1024 * 1024)
        self.configure(raw_minutes, minute_hours, ten_minute_days, interval_s, max_memory_mb)
        self._acc = {}          # current minute: sid -> [min, sum, n, max]
        self._acc_ten = {}      # current 10 minutes
        self._minute = None     # start (epoch) of the current minute bucket
        self._ten = None
        self._last = {}         # sid -> time of its newest value
        self._first = None      # time of the first sample (tiers' first buckets start before it)
        self._bytes = 0
        self._codes = {}        # sid -> typecode (cache)
        self.refused = 0        # series not created because of the budget (since start)
        self.listeners = []     # fn(minute_start, {sid: (min, avg, max)}) after each minute, storage units

    # -- configuration --------------------------------------------------------
    def configure(self, raw_minutes, minute_hours, ten_minute_days, interval_s, max_memory_mb=None):
        with self._lock:
            self.interval_s = max(0.5, float(interval_s))
            if max_memory_mb is not None:
                self.max_bytes = int(max_memory_mb * 1024 * 1024)
            self.raw.resize(math.ceil(raw_minutes * 60 / self.interval_s))
            self.minute.resize(minute_hours * 60)
            self.ten.resize(ten_minute_days * 144)
            self._bytes = sum(t.series_bytes(s) for t in self._tiers() for s in t.series)

    def _tiers(self):
        return (self.raw, self.minute, self.ten)

    # -- writing --------------------------------------------------------------
    def add(self, t, values):
        """One sample: sid -> value in storage units (rounded and clipped here); None = no value."""
        clean = {}
        codes = self._codes
        for sid, v in values.items():
            if v is None:
                continue
            code = codes.get(sid)
            if code is None:
                code = codes[sid] = METRICS[metric_of(sid)][0]
            clean[sid] = min(max(int(round(v)), 0), _LIMIT[code])
        flushed = None
        with self._lock:
            if self._first is None:
                self._first = t
            bucket = math.floor(t / 60.0) * 60.0
            if self._minute is None:
                self._minute = bucket
            elif bucket > self._minute:
                flushed = self._flush_minute()
                self._minute = bucket
            for sid in list(clean):
                if sid not in self.raw.series and not self._make_room(sid, t):
                    del clean[sid]
            self.raw.push(t, clean)
            for sid, v in clean.items():
                acc = self._acc.get(sid)
                if acc is None:
                    self._acc[sid] = [v, v, 1, v]
                else:
                    if v < acc[0]:
                        acc[0] = v
                    acc[1] += v
                    acc[2] += 1
                    if v > acc[3]:
                        acc[3] = v
                self._last[sid] = t
        if flushed:
            for fn in self.listeners:
                try:
                    fn(*flushed)
                except Exception:
                    log.exception("minute listener failed")

    def _make_room(self, sid, t):
        """Create sid in its tiers if the budget allows it (drops ended series first).

        A series that comes back after a pause may still be in the minute/10-minute tiers.
        """
        tiers = [t for t in (self._tiers() if is_long(sid) else (self.raw, self.minute)) if sid not in t.series]
        need = sum(t.series_bytes(sid) for t in tiers)
        if self._bytes + need > self.max_bytes:
            self._drop_ended(self._bytes + need - self.max_bytes, t)
        if self._bytes + need > self.max_bytes and sid.startswith(_OPTIONAL):
            if self.refused == 0:
                log.warning("History RAM budget (%d MB) full: no new process/thread series",
                            self.max_bytes // (1024 * 1024))
            self.refused += 1
            return False
        for t in tiers:
            t.add_series(sid)
        self._bytes += need
        return True

    def _drop_ended(self, nbytes, now):
        """Drop series without a value in the last two intervals, oldest first, until nbytes are free."""
        ended = sorted((t, sid) for sid, t in self._last.items() if t < now - 2 * self.interval_s)
        freed = 0
        for _, sid in ended:
            if freed >= nbytes:
                break
            freed += self._remove(sid)

    def _remove(self, sid):
        freed = 0
        for t in self._tiers():
            if sid in t.series:
                freed += t.series_bytes(sid)
                del t.series[sid]
        self._last.pop(sid, None)
        self._codes.pop(sid, None)
        self._acc.pop(sid, None)
        self._acc_ten.pop(sid, None)
        self._bytes -= freed
        return freed

    def _flush_minute(self):
        start = self._minute
        acc, self._acc = self._acc, {}
        buckets = {sid: (a[0], int(round(a[1] / a[2])), a[3]) for sid, a in acc.items()}
        self.minute.push(start, buckets)
        ten = math.floor(start / 600.0) * 600.0
        if self._ten is None:
            self._ten = ten
        elif ten > self._ten:
            self._flush_ten()
            self._ten = ten
        for sid, a in acc.items():
            if not is_long(sid):
                continue
            t = self._acc_ten.get(sid)
            if t is None:
                self._acc_ten[sid] = list(a)
            else:
                t[0] = min(t[0], a[0])
                t[1] += a[1]
                t[2] += a[2]
                t[3] = max(t[3], a[3])
        self._prune()
        return start, buckets

    def _flush_ten(self):
        acc, self._acc_ten = self._acc_ten, {}
        self.ten.push(self._ten, {sid: (a[0], int(round(a[1] / a[2])), a[3]) for sid, a in acc.items()})

    def _prune(self):
        """Remove series from tiers that hold none of their values any more."""
        for tier in self._tiers():
            oldest = tier.oldest()
            if oldest is None or tier.count < tier.capacity:
                continue
            step = tier.step or 1.0
            for sid in [s for s in tier.series if math.floor(self._last.get(s, 0) / step) * step < oldest]:
                self._bytes -= tier.series_bytes(sid)
                del tier.series[sid]
        for sid in [s for s in self._last if not any(s in t.series for t in self._tiers())]:
            del self._last[sid]
            self._codes.pop(sid, None)

    # -- reading --------------------------------------------------------------
    def has(self, sid):
        return sid in self._last

    def ids(self, prefix):
        with self._lock:
            return [s for s in self._last if s.startswith(prefix)]

    def entities(self):
        """Series ids without their metric: {"app.snap:x", "proc.snap:x.12-1793", ...}."""
        with self._lock:
            return {sid.rsplit(".", 1)[0] for sid in self._last}

    def last_seen(self, sid):
        return self._last.get(sid)

    def oldest(self):
        """Oldest time any tier still holds."""
        with self._lock:
            times = [t.oldest() for t in self._tiers() if t.count]
            return min(times) if times else None

    def pick_tier(self, sids, t0):
        tiers = [self.raw, self.minute] + ([self.ten] if all(is_long(s) for s in sids) else [])

        def data_from(tier):   # a bucket is stamped with its start, which may be before the first sample
            return max(tier.oldest(), self._first or 0.0)

        for tier in tiers:
            if tier.count and data_from(tier) <= t0:
                return tier
        # no tier reaches back to t0 (the app runs shorter than the range): the finest one, unless a
        # coarser one reaches back further by more than one of its buckets
        best = None
        for tier in tiers:
            if tier.count and (best is None or data_from(tier) < data_from(best) - tier.step):
                best = tier
        return best or self.raw

    def query(self, sids, t0, t1, max_points=None):
        """-> {"tier", "step", "oldest", "times", "series": {sid: (mins, avgs, maxs)}} in API units."""
        with self._lock:
            tier = self.pick_tier(sids, t0)
            times, cols = tier.read(sids, t0, t1)
            info = {"tier": tier.name, "step": tier.step or self.interval_s, "oldest": tier.oldest()}
        times, cols = downsample(times, cols, max_points)
        info["times"] = times
        info["series"] = {sid: tuple([to_api(sid, v) for v in col] for col in c) for sid, c in cols.items()}
        return info

    def usage(self):
        with self._lock:
            return {
                "series": len(self._last),
                "bytes": self._bytes,
                "maxBytes": self.max_bytes,
                "refused": self.refused,
                "tiers": [{"name": t.name, "step": t.step or self.interval_s, "capacity": t.capacity,
                           "count": t.count, "oldest": t.oldest(), "newest": t.newest(),
                           "series": len(t.series)} for t in self._tiers()],
            }
