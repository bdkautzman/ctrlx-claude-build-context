"""RAM history (tiers, aggregation, budget) and the leak analysis."""

import math
import unittest

from app import analysis
from app.history import History, downsample

RULES = {"metric": "anon", "minMbPerHour": 1.0, "minFit": 0.8, "minSpanMinutes": 30, "nodeWindowMinutes": 60}


def history(**kw):
    args = dict(raw_minutes=10, minute_hours=2, ten_minute_days=1, interval_s=10, max_memory_mb=40)
    args.update(kw)
    return History(**args)


class HistoryTest(unittest.TestCase):
    def test_raw_values_in_api_units(self):
        h = history()
        h.add(1000.0, {"sys.cpu": 1234.4, "app.snap:x.anon": 2048, "app.snap:x.threads": 7, "sys.memused": None})
        q = h.query(["sys.cpu", "app.snap:x.anon", "app.snap:x.threads", "sys.memused"], 900, 1100)
        self.assertEqual(q["tier"], "raw")
        self.assertEqual(q["times"], [1000.0])
        self.assertEqual(q["series"]["sys.cpu"][1], [12.34])            # centi-percent -> %
        self.assertEqual(q["series"]["app.snap:x.anon"][1], [2.0])      # KB -> MB
        self.assertEqual(q["series"]["app.snap:x.threads"][1], [7])
        self.assertNotIn("sys.memused", q["series"])

    def test_minute_and_ten_minute_buckets(self):
        h = history()
        seen = []
        h.listeners.append(lambda start, buckets: seen.append((start, buckets)))
        for i in range(61):                       # 0..600 s, one sample every 10 s
            h.add(i * 10.0, {"app.snap:x.anon": 100 + i, "thr.snap:x.1-2-3.cpu": 50})
        self.assertEqual(len(seen), 10)
        start, buckets = seen[0]
        self.assertEqual(start, 0.0)
        self.assertEqual(buckets["app.snap:x.anon"], (100, 102, 105))   # min, avg (rounded), max
        q = h.query(["app.snap:x.anon"], -1, 700, None)
        self.assertEqual(q["tier"], "raw")
        self.assertEqual(h.minute.count, 10)
        self.assertEqual(h.ten.count, 0)          # the first 10-minute bucket closes with the 11th minute
        for i in range(61, 75):
            h.add(i * 10.0, {"app.snap:x.anon": 100 + i})
        self.assertEqual(h.ten.count, 1)
        ten = h.ten.read(["app.snap:x.anon"], 0, 0)[1]["app.snap:x.anon"]
        self.assertEqual((ten[0][0], ten[2][0]), (100, 159))
        self.assertNotIn("thr.snap:x.1-2-3.cpu", h.ten.series)  # threads: raw + minute only

    def test_pick_tier(self):
        h = history(raw_minutes=1, interval_s=10)     # raw keeps 6 samples
        for i in range(30):
            h.add(i * 10.0, {"sys.cpu": 100})
        self.assertEqual(h.pick_tier(["sys.cpu"], 250).name, "raw")
        self.assertEqual(h.pick_tier(["sys.cpu"], 30).name, "minute")
        # no tier reaches back: raw unless a coarser tier reaches further by more than a bucket
        h2 = history()
        for i in range(8):
            h2.add(1000.0 + i * 10, {"sys.cpu": 100})
        self.assertEqual(h2.pick_tier(["sys.cpu"], 0).name, "raw")
        # seen on the COREvirtual: the 10-minute bucket is stamped 600 s, the data starts at 1000 s - a
        # range from 700 s must not get the 10-minute tier
        h3 = history(raw_minutes=30)              # raw keeps all 130 samples
        for i in range(130):
            h3.add(1000.0 + i * 10, {"sys.cpu": 100})
        self.assertEqual(h3.ten.oldest(), 600.0)
        self.assertEqual(h3.pick_tier(["sys.cpu"], 700).name, "raw")

    def test_downsample_keeps_extremes(self):
        times = list(range(10))
        t, s = downsample(times, {"a": ([1] * 10, [2] * 9 + [None], [3] * 9 + [99])}, 2)
        self.assertEqual(t, [0, 5])
        self.assertEqual(s["a"], ([1, 1], [2.0, 2.0], [3, 99]))

    def test_resize_keeps_newest(self):
        h = history(raw_minutes=1, interval_s=10)
        for i in range(6):
            h.add(i * 10.0, {"sys.cpu": i * 100})
        h.configure(1, 2, 1, 20)                    # 3 raw slots now
        q = h.query(["sys.cpu"], 0, 100)
        self.assertEqual(q["times"], [30.0, 40.0, 50.0])
        self.assertEqual(q["series"]["sys.cpu"][1], [3.0, 4.0, 5.0])

    def test_budget_refuses_optional_series_and_drops_ended(self):
        h = history(max_memory_mb=0.05)            # ~52 KB
        h.add(0.0, {"sys.cpu": 1, "app.snap:x.anon": 1})
        for i in range(40):
            h.add(10.0, {"proc.snap:x.{}-1.anon".format(i): 1})
        self.assertGreater(h.refused, 0)
        self.assertTrue(h.has("sys.cpu"))
        self.assertLessEqual(h.usage()["bytes"], h.max_bytes)
        before = h.refused
        h.add(40.0, {"proc.snap:x.new-1.anon": 1})  # ended series are dropped to make room
        self.assertEqual(h.refused, before)
        self.assertTrue(h.has("proc.snap:x.new-1.anon"))

    def test_returning_series_keeps_its_minute_data(self):
        h = history(raw_minutes=1, interval_s=10)
        for i in range(7):
            h.add(i * 10.0, {"thr.snap:x.1-2-3.cpu": 500, "sys.cpu": 1})
        for i in range(7, 20):                     # the thread idles: no values, falls out of raw
            h.add(i * 10.0, {"sys.cpu": 1})
        self.assertNotIn("thr.snap:x.1-2-3.cpu", h.raw.series)
        h.add(200.0, {"thr.snap:x.1-2-3.cpu": 700, "sys.cpu": 1})
        minute = h.minute.read(["thr.snap:x.1-2-3.cpu"], 0, 0)[1]["thr.snap:x.1-2-3.cpu"]
        self.assertEqual(minute[1], [500])

    def test_usage(self):
        h = history()
        h.add(0.0, {"sys.cpu": 1})
        u = h.usage()
        self.assertEqual(u["series"], 1)
        self.assertEqual([t["name"] for t in u["tiers"]], ["raw", "minute", "tenminute"])
        self.assertGreater(u["bytes"], 0)


class AnalysisTest(unittest.TestCase):
    def series(self, hours, fn, step=60):
        times = [i * step for i in range(int(hours * 3600 / step))]
        return times, [fn(t) for t in times]

    def test_fit_line(self):
        b, a, r2 = analysis.fit_line([0, 1, 2, 3], [1, 3, 5, 7])
        self.assertAlmostEqual(b, 2.0)
        self.assertAlmostEqual(a, 1.0)
        self.assertAlmostEqual(r2, 1.0)
        self.assertEqual(analysis.fit_line([5], [1])[0], 0.0)

    def test_sawtooth_leak_is_growing(self):
        # 2 MB/h underneath a garbage collector's 20 MB saw tooth (period 10 min)
        times, mins = self.series(3, lambda t: 100 + 2 * t / 3600 + 20 * ((t % 600) / 600))
        r = analysis.assess(times, mins, RULES, available_mb=1000)
        self.assertEqual(r["verdict"], "growing")
        self.assertAlmostEqual(r["growthMbPerHour"], 2.0, delta=0.3)
        self.assertGreater(r["fit"], 0.9)
        self.assertAlmostEqual(r["hoursUntilFull"], 1000 / r["growthMbPerHour"], delta=1)

    def test_growth_with_single_deep_dips_is_growing(self):
        # like a bash process on the COREvirtual: grows 30 MB/h, now and then a single sample far lower
        times, mins = self.series(0.6, lambda t: (0.4 + 30 * t / 3600) * (0.3 if t % 330 == 0 and t else 1), step=5)
        r = analysis.assess(times, mins, RULES)
        self.assertEqual(r["verdict"], "growing")
        self.assertAlmostEqual(r["growthMbPerHour"], 30, delta=4)
        self.assertLess(r["startMb"], r["endMb"])

    def test_flat_is_stable_and_noise_is_not_growing(self):
        times, mins = self.series(2, lambda t: 50.0)
        self.assertEqual(analysis.assess(times, mins, RULES)["verdict"], "stable")
        times, mins = self.series(2, lambda t: 50 + 30 * math.sin(t / 500.0))
        self.assertNotEqual(analysis.assess(times, mins, RULES)["verdict"], "growing")

    def test_restart_starts_the_fit_anew(self):
        # grows 10 MB/h for 2 h, restarts (drop), then flat for 1 h
        times, mins = self.series(3, lambda t: 100 + 10 * t / 3600 if t < 7200 else 80.0)
        r = analysis.assess(times, mins, RULES, restart_times=[7200])
        self.assertEqual(r["verdict"], "stable")
        self.assertEqual(r["fittedFrom"], 7200)
        self.assertEqual(r["restarts"], 1)
        r = analysis.assess(times[:120], mins[:120], RULES)
        self.assertEqual(r["verdict"], "growing")

    def test_insufficient_and_threads(self):
        times, mins = self.series(0.25, lambda t: 1.0)
        self.assertEqual(analysis.assess(times, mins, RULES)["verdict"], "insufficient")
        times, mins = self.series(2, lambda t: 10.0)
        r = analysis.assess(times, mins, RULES, threads=(times, [10 + int(t / 600) for t in times]))
        self.assertAlmostEqual(r["threadsPerHour"], 6.0, delta=0.5)

    def test_baseline_takes_bucket_minimum(self):
        t, v = analysis.baseline(list(range(10)), [5, 1, 5, 5, 5, 5, 5, 2, 5, 5], points=2)
        self.assertEqual(v, [1, 2])


if __name__ == "__main__":
    unittest.main()
