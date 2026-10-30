"""Memory leak analysis: how steadily does an app's (or process's) memory grow over a time range?

1. Baseline: the lower envelope of the series. Garbage collectors and caches make memory go up and
   down in a saw tooth; a leak lifts the bottom of the teeth, so the bottoms are what is fitted: the
   per-bucket MINIMUM (merged into at most BASELINE_POINTS equal time buckets, again by minimum), then
   a rolling window over ENVELOPE_PARTS of the range (a 3 h range: 15 min - longer than most teeth):
   its minimum (regular saw teeth) and its 10th percentile (growth with single deep dips, e.g. a
   process that sometimes hands memory back - measured on the COREvirtual: R^2 0.74 with the minimum,
   0.97 with the percentile). The steadier of the two (higher R^2) is used. Before that, single dips
   are filled in (Hampel filter, downward only): at 1-minute resolution every minute's minimum
   catches a dip, and a 30-point range leaves the rolling window too short to skip them (measured on
   the real CORE from the USB stick: fit 0.48 -> 0.93). Saw-tooth bottoms repeat, so they stay.
2. Least-squares line through the baseline -> growth in MB/h and the fit quality R^2 (1 = a straight
   line, 0 = no trend).
3. Verdict: "growing" when growth >= leak.minMbPerHour AND R^2 >= leak.minFit over at least
   leak.minSpanMinutes; "possible" when only the growth threshold is reached; else "stable".
   For an app, a restart (restarted/stopped/started event) inside the range starts the fit anew after
   the last one - the restart itself would otherwise look like a big drop.
4. Time until full: available system memory / growth (only for "growing").
"""

BASELINE_POINTS = 240
ENVELOPE_PARTS = 12


def baseline(times, mins, points=BASELINE_POINTS, t_from=None):
    """-> (times, values) of the per-bucket minimum, gaps (None) skipped."""
    pairs = [(t, v) for t, v in zip(times, mins) if v is not None and (t_from is None or t >= t_from)]
    if len(pairs) <= points:
        return [p[0] for p in pairs], [p[1] for p in pairs]
    t0, t1 = pairs[0][0], pairs[-1][0]
    width = (t1 - t0) / points or 1.0
    buckets = {}
    for t, v in pairs:
        k = min(int((t - t0) / width), points - 1)
        b = buckets.get(k)
        if b is None or v < b[1]:
            buckets[k] = (t0 + (k + 0.5) * width, v) if b is None else (b[0], v)
    keys = sorted(buckets)
    return [buckets[k][0] for k in keys], [buckets[k][1] for k in keys]


def envelope(ts, vs, parts=ENVELOPE_PARTS, quantile=0.0):
    """Trailing rolling minimum (quantile 0) or low percentile over len/parts points (complete windows)."""
    k = max(1, len(vs) // parts)
    if k == 1:
        return ts, vs
    if quantile <= 0:
        return ts[k - 1:], [min(vs[i - k + 1:i + 1]) for i in range(k - 1, len(vs))]
    pick = int(quantile * (k - 1) + 0.5)
    return ts[k - 1:], [sorted(vs[i - k + 1:i + 1])[pick] for i in range(k - 1, len(vs))]


def despike(vs, half=3, k=3.0):
    """Replace values far BELOW the median of their neighbours (> k median deviations) by that median."""
    out = list(vs)
    for i in range(len(vs)):
        window = sorted(vs[max(0, i - half):i + half + 1])
        med = window[len(window) // 2]
        mad = sorted(abs(x - med) for x in window)[len(window) // 2]
        if vs[i] < med - k * max(mad, 0.01 * abs(med)):
            out[i] = med
    return out


def steadiest(ts, vs):
    """Fit both envelopes -> (slope, intercept, r2, times, values) of the one with the higher R^2."""
    best = None
    for q in (0.0, 0.1):
        et, ev = envelope(ts, vs, quantile=q)
        slope, intercept, r2 = fit_line(et, ev)
        if best is None or r2 > best[2]:
            best = (slope, intercept, r2, et, ev)
    return best


def fit_line(ts, vs):
    """Least squares v = a + b * t -> (b per second, a, r2). t is shifted to start at 0."""
    n = len(ts)
    if n < 2:
        return 0.0, (vs[0] if vs else 0.0), 0.0
    t0 = ts[0]
    xs = [t - t0 for t in ts]
    mx = sum(xs) / n
    my = sum(vs) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return 0.0, my, 0.0
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, vs))
    b = sxy / sxx
    a = my - b * mx
    ss_tot = sum((y - my) ** 2 for y in vs)
    ss_res = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, vs))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return b, a, max(0.0, r2)


def assess(times, mins, rules, available_mb=None, restart_times=(), threads=None):
    """times/mins: memory series in MB (per-bucket minimum); threads: (times, values) or None.

    rules: settings["leak"]. -> dict with growth, fit, verdict, ... (MB, MB/h, hours)
    """
    t_from = None
    restarts = sorted(t for t in restart_times if times and times[0] <= t <= times[-1])
    if restarts:
        t_from = restarts[-1]
    bt, bv = baseline(times, mins, t_from=t_from)
    span_s = (bt[-1] - bt[0]) if len(bt) >= 2 else 0.0
    if restarts and span_s < rules["minSpanMinutes"] * 60:
        bt, bv = baseline(times, mins)            # too little after the restart: whole range
        span_s = (bt[-1] - bt[0]) if len(bt) >= 2 else 0.0
        t_from = None
    result = {"points": len(bt), "spanMinutes": round(span_s / 60.0, 1), "restarts": len(restarts),
              "fittedFrom": t_from, "startMb": None, "endMb": None, "growthMbPerHour": None,
              "fit": None, "verdict": "insufficient", "hoursUntilFull": None, "threadsPerHour": None,
              "line": None}
    if len(bt) < 6 or span_s < rules["minSpanMinutes"] * 60:
        return result
    slope, intercept, r2, et, ev = steadiest(bt, despike(bv))
    growth = slope * 3600.0
    result.update({"startMb": round(ev[0], 2), "endMb": round(ev[-1], 2), "growthMbPerHour": round(growth, 3),
                   "fit": round(r2, 3),
                   # the fitted line, for the chart: [[t, MB], [t, MB]]
                   "line": [[et[0], round(intercept, 3)], [et[-1], round(intercept + slope * (et[-1] - et[0]), 3)]]})
    if growth >= rules["minMbPerHour"] and r2 >= rules["minFit"]:
        result["verdict"] = "growing"
        if available_mb is not None and growth > 0:
            result["hoursUntilFull"] = round(available_mb / growth, 1)
    elif growth >= rules["minMbPerHour"]:
        result["verdict"] = "possible"
    else:
        result["verdict"] = "stable"
    if threads:
        tt, tv = baseline(threads[0], threads[1], t_from=t_from)
        if len(tt) >= 6:
            tslope = steadiest(tt, tv)[0]
            result["threadsPerHour"] = round(tslope * 3600.0, 2)
    return result
