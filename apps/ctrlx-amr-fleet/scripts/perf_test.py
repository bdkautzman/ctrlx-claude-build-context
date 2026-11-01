#!/usr/bin/env python3
"""Performance test of ctrlx-amr-fleet on a device (devices.toml): a map + 10 ... 200 simulated AMRs.

    python3 scripts/perf_test.py --device virtual --map MAP.txt [--counts 10,25,50,100,200]
                                 [--browser 10,50,200] [--out perf-virtual.json]

Changes on the device (and restores them at the end): uploads MAP.txt into the app's app data folder
(stays there), sets map.file to it and runs the built-in simulation with each AMR count. Measures:
  * map load (settings save = synchronous load on the device), level bitmap + tile generation
  * per AMR count: app process CPU (% of one core) and memory (SSH, /proc), fleet updates per second
    (should be 5 = the simulation's 200 ms rhythm), /fleet size and round trip from here
  * --browser counts: the page in headless Chromium on THIS machine - time until map + AMRs are shown,
    browser CPU per second (CDP Performance metrics), frames per second, then the same locked onto an
    AMR and zoomed in. In an emulated build VM these browser numbers are ~30-50x too slow: compare them
    between AMR counts, not with a real PC. Needs tools/setup-ui-test.sh (re-runs with its venv).

Input modes (setting fleet.input) instead of the simulation:

    python3 scripts/perf_test.py --device core --inputs fleet-json,amr-json,amr-fields
                                 [--input-counts 10,50,200] [--rate 5] [--out perf-inputs-core.json]

Simulation off; per AMR count an AMR list P-001 ... is uploaded; per mode THIS machine writes a fleet
that drives in circles over the map through the device's REST API (PUT /automation/api/v2/bulk,
<= BULK_CHUNK node writes per request, the requests of one cycle in parallel) --rate times per
second: fleet-json = 1 node, amr-json = 1 node per AMR, amr-fields = x, y, rotation, battery, state
per AMR. Measures the app's CPU and memory, the
whole device's CPU (Data Layer broker + REST server work too), the update rate the app reached, the
bulk round trip and failed writes. Checks once that the per-AMR nodes browse, read and describe.
Restores the AMR list and the settings at the end.
"""

import argparse
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(KIT, "tools"))
import ctrlx  # noqa: E402  the kit's REST client: devices.toml, login

SNAP = "ctrlx-amr-fleet"
API = "/" + SNAP + "/api/v1"
DL = "/automation/api/v2"
BULK_CHUNK = 500         # node writes per bulk request


class Device:
    def __init__(self, name):
        self.t = ctrlx.resolve_target(ctrlx.load_devices(ctrlx.DEFAULT_DEVICES_FILE), os.environ, name)
        self.dev = ctrlx.Device(self.t["api_host"], self.t["api_user"], self.t["api_password"], self.t["verify_tls"])

    def api(self, method, path, body=None, timeout=300):
        return self.dev.request(method, API + path, body, timeout=timeout)[2]

    def ssh(self, command, timeout=120):
        t = self.t
        argv = ["ssh", "-p", str(t["ssh_port"]), "-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=no",
                "{}@{}".format(t["ssh_user"], t["ssh_host"]), command]
        env = dict(os.environ)
        if t["ssh_password"] and shutil.which("sshpass"):
            argv = ["sshpass", "-e"] + argv
            env["SSHPASS"] = t["ssh_password"]
        return subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout).stdout

    def app_cpu(self, seconds):
        """CPU of the app's process over `seconds` (% of one core), its memory (MB), the device's cores."""
        out = self.ssh("pid=$(systemctl show -p MainPID --value snap.{0}.service.service); "
                       "a=$(cut -d' ' -f14,15 /proc/$pid/stat); sleep {1}; b=$(cut -d' ' -f14,15 /proc/$pid/stat); "
                       "echo $a $b $(getconf CLK_TCK) $(nproc) $(awk '/VmRSS/ {{print $2}}' /proc/$pid/status)"
                       .format(SNAP, seconds), timeout=seconds + 60).split()
        u0, s0, u1, s1, tck, cores, rss_kb = (float(v) for v in out[-7:])
        return {"cpuPercent": round((u1 + s1 - u0 - s0) / tck / seconds * 100, 1), "rssMB": round(rss_kb / 1024, 1),
                "cores": int(cores)}


    def cpu_both(self, seconds):
        """app_cpu() plus the whole device's CPU over the same seconds (% of all cores, /proc/stat)."""
        out = self.ssh("pid=$(systemctl show -p MainPID --value snap.{0}.service.service); "
                       "a=$(cut -d' ' -f14,15 /proc/$pid/stat); c=$(head -1 /proc/stat | cut -d' ' -f3-10); sleep {1}; "
                       "b=$(cut -d' ' -f14,15 /proc/$pid/stat); d=$(head -1 /proc/stat | cut -d' ' -f3-10); "
                       "echo $a $b $(getconf CLK_TCK) $(nproc) $(awk '/VmRSS/ {{print $2}}' /proc/$pid/status) $c $d"
                       .format(SNAP, seconds), timeout=seconds + 60).split()
        v = [float(x) for x in out[-23:]]
        u0, s0, u1, s1, tck, cores, rss_kb = v[:7]
        c0, c1 = v[7:15], v[15:23]                     # user nice system idle iowait irq softirq steal
        busy = lambda c: sum(c) - c[3] - c[4]          # noqa: E731
        return {"cpuPercent": round((u1 + s1 - u0 - s0) / tck / seconds * 100, 1), "rssMB": round(rss_kb / 1024, 1),
                "cores": int(cores),
                "devicePercent": round((busy(c1) - busy(c0)) / max(1.0, sum(c1) - sum(c0)) * 100, 1)}


def app_log(d, needles, limit=40):
    """The app's newest Logbook lines that contain one of the needles (e.g. its own load timings)."""
    entries = ctrlx._log_entries(d.dev, {"unit": "snap.{}.service.service".format(SNAP), "limit": limit})
    lines = [str(e.get("dynamicDescription") or e.get("mainTitle") or "") for e in entries]
    return [l for l in lines if any(n in l for n in needles)][:4]


def timed(fn):
    t0 = time.monotonic()
    result = fn()
    return result, time.monotonic() - t0


def server_sample(d, seconds=20):
    cpu = {}
    th = threading.Thread(target=lambda: cpu.update(d.app_cpu(seconds)))
    th.start()
    start, t_start = d.api("GET", "/fleet?since=-1"), time.monotonic()
    lat, size = [], 0
    for _ in range(10):
        body, dt = timed(lambda: d.dev.request("GET", API + "/fleet?since=-1", raw=True)[2])
        lat.append(dt * 1000)
        size = len(body)
        time.sleep(0.5)
    th.join()
    end, t_end = d.api("GET", "/fleet?since=-1"), time.monotonic()
    lat.sort()
    states = {}
    for a in end["amrs"]:
        states[a["state"]] = states.get(a["state"], 0) + 1
    return dict(cpu, updatesPerS=round((end["revision"] - start["revision"]) / (t_end - t_start), 2),
                fleetKB=round(size / 1024, 1), fleetMsMedian=round(lat[len(lat) // 2]), fleetMsMax=round(lat[-1]),
                amrs=end["count"], states=states)


def tiles(d):
    info = d.api("GET", "/map")
    out = {"cells": "{} x {}".format(info["width"], info["height"]), "levels": info["levels"], "points": info["points"]}
    top = info["levels"] - 1
    path = "/map/tiles/{}/{}/{}/{}.png".format(info["id"], "{}", "{}", "{}")
    _, dt = timed(lambda: d.dev.request("GET", API + path.format(top, 0, 0), raw=True))
    out["topTileMs"] = round(dt * 1000)
    ntx = (((info["width"] - 1) >> 0) + 1 + 255) // 256
    nty = (((info["height"] - 1) >> 0) + 1 + 255) // 256
    picks = [(x, y) for x in range(0, ntx, 4) for y in range(0, nty, 5)]
    first, again = [], []
    for x, y in picks:
        _, dt = timed(lambda: d.dev.request("GET", API + path.format(0, x, y), raw=True))
        first.append(dt * 1000)
    for x, y in picks:
        _, dt = timed(lambda: d.dev.request("GET", API + path.format(0, x, y), raw=True))
        again.append(dt * 1000)
    out.update(level0FirstTileMs=round(first[0]),            # includes building the level's bitmap
               level0TileMsMedian=round(statistics.median(first[1:] or first)),
               level0CachedMsMedian=round(statistics.median(again)), level0Tiles=ntx * nty, sampledTiles=len(picks))
    return out


BROWSER = r"""
import json, re, sys, time
from playwright.sync_api import sync_playwright
url, shot = sys.argv[1], sys.argv[2]
res = {}
FPS = "() => new Promise(r => { let n = 0; const t0 = performance.now(); (function f() { n++; if (performance.now() - t0 < 10000) requestAnimationFrame(f); else r(n / ((performance.now() - t0) / 1000)); })(); })"
def metrics(cdp):
    return {m["name"]: m["value"] for m in cdp.send("Performance.getMetrics")["metrics"]}
def busy(cdp, seconds, page):
    a = metrics(cdp); fps = page.evaluate(FPS); b = metrics(cdp)
    dt = b["Timestamp"] - a["Timestamp"]
    return {"scriptMsPerS": round((b["ScriptDuration"] - a["ScriptDuration"]) / dt * 1000),
            "taskMsPerS": round((b["TaskDuration"] - a["TaskDuration"]) / dt * 1000), "fps": round(fps, 1),
            "heapMB": round(b["JSHeapUsedSize"] / 1048576, 1)}
with sync_playwright() as pw:
    browser = pw.chromium.launch(args=["--no-proxy-server"])
    ctx = browser.new_context(viewport={"width": 1400, "height": 860}, ignore_https_errors=True)
    ctx.set_default_timeout(300000)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    cdp = ctx.new_cdp_session(page)
    cdp.send("Performance.enable")
    t0 = time.monotonic()
    page.goto(url, wait_until="load")
    page.wait_for_function("document.body.dataset.uiReady === 'true' && /^\\(\\d+\\)$/.test(document.getElementById('amr-count').textContent)")
    res["readySeconds"] = round(time.monotonic() - t0, 1)
    res["overview"] = busy(cdp, 10, page)
    page.screenshot(path=shot + "-overview.png", animations="disabled")
    # ^Follow: the tile head is a button too, and its name contains its buttons' names
    page.locator("#amr-list li").first.get_by_role("button", name=re.compile(r"^Follow ")).click()
    for _ in range(4):
        page.click("#btn-zoom-in")
    t1 = time.monotonic()
    page.wait_for_function("document.body.dataset.uiReady === 'true'")
    res["lockReadySeconds"] = round(time.monotonic() - t1, 1)
    res["locked"] = busy(cdp, 10, page)
    page.screenshot(path=shot + "-locked.png", animations="disabled")
    res["errors"] = errors
    browser.close()
print("RESULT " + json.dumps(res))
"""


def browser_sample(d, shot):
    venv_python = os.path.expanduser("~/.cache/ctrlx-kit/uivenv/bin/python")
    url = "{}/{}/?token={}".format(d.dev.base, SNAP, d.dev.token())
    out = subprocess.run([venv_python, "-c", BROWSER, url, shot], capture_output=True, text=True, timeout=1800)
    for line in out.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[7:])
    errors = [l for l in out.stderr.splitlines() if "Error" in l] or out.stderr.strip().splitlines() or ["no output"]
    return {"error": errors[0][:300]}


def fleet_at(ids, t, box):
    """(id, x, y, rotation in degrees): every AMR on its own ellipse around the map centre."""
    cx, cy, rx, ry = (box[0] + box[1]) / 2, (box[2] + box[3]) / 2, (box[1] - box[0]) * 0.4, (box[3] - box[2]) * 0.4
    out = []
    for i, ident in enumerate(ids):
        a = 2 * math.pi * i / len(ids) + (0.05 + 0.004 * (i % 10)) * t
        out.append((ident, round(cx + rx * math.cos(a) * (0.5 + 0.5 * (i % 3) / 2), 1),
                    round(cy + ry * math.sin(a) * (0.5 + 0.5 * (i % 3) / 2), 1), round(math.degrees(a + math.pi / 2) % 360, 2)))
    return out


def bulk_entries(mode, ids, t, box):
    root, fleet = SNAP + "/fleet/", fleet_at(ids, t, box)
    if mode == "fleet-json":
        amrs = [{"id": i, "x": x, "y": y, "rotation": r, "state": "driving", "battery": 80} for i, x, y, r in fleet]
        return [{"address": root + "amrs", "type": "string", "value": json.dumps({"amrs": amrs})}]
    if mode == "amr-json":
        return [{"address": root + "amr/" + i, "type": "string",
                 "value": json.dumps({"x": x, "y": y, "rotation": r, "state": "driving", "battery": 80})}
                for i, x, y, r in fleet]
    entries = []
    for i, x, y, r in fleet:
        a = root + "amr/" + i + "/"
        entries += [{"address": a + "x", "type": "float64", "value": x}, {"address": a + "y", "type": "float64", "value": y},
                    {"address": a + "rotation", "type": "float64", "value": r},
                    {"address": a + "battery", "type": "float64", "value": 80.0},
                    {"address": a + "state", "type": "string", "value": "driving"}]
    return entries


def write_load(d, mode, ids, box, rate, seconds, stats):
    """Writes the fleet `rate` times per second for `seconds`; the bulk requests of one cycle in parallel
    (one after the other they took longer than a cycle at 1000 writes). No catching up when slower."""
    lat, results, lock = [], {"failed": 0, "writes": 0, "error": ""}, threading.Lock()

    def send(chunk):
        try:
            body, dt = timed(lambda: d.dev.request("PUT", DL + "/bulk?type=write", chunk, timeout=60)[2])
        except SystemExit as exc:                           # ctrlx.Device: HTTP error -> SystemExit
            with lock:
                results["failed"] += len(chunk)
                results["error"] = results["error"] or str(exc)[:200]
            return
        bad = [r for r in body if r.get("result") != "DL_OK"] if isinstance(body, list) else chunk
        with lock:
            lat.append(dt * 1000)
            results["failed"] += len(bad)
            results["writes"] += len(chunk)
            if bad and not results["error"]:
                results["error"] = json.dumps(bad[0])[:200]

    cycles, t0 = 0, time.monotonic()
    next_t = t0
    while time.monotonic() - t0 < seconds:
        entries = bulk_entries(mode, ids, time.monotonic() - t0, box)
        senders = [threading.Thread(target=send, args=(entries[k:k + BULK_CHUNK],))
                   for k in range(0, len(entries), BULK_CHUNK)]
        for th in senders:
            th.start()
        for th in senders:
            th.join()
        cycles += 1
        next_t += 1.0 / rate
        delay = next_t - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        elif delay < -1.0:
            next_t = time.monotonic()
    elapsed = time.monotonic() - t0
    lat.sort()
    stats.update(cyclesPerS=round(cycles / elapsed, 2), writesPerS=round(results["writes"] / elapsed),
                 bulkRequests=len(lat), bulkMsMedian=round(lat[len(lat) // 2]) if lat else None,
                 bulkMsMax=round(lat[-1]) if lat else None, failedWrites=results["failed"],
                 firstError=results["error"])


def node_checks(d, ids, mode):
    """The per-AMR nodes seen through the device's REST API (wildcard provider node: browse, metadata, read)."""
    base = DL + "/nodes/" + SNAP + "/fleet/amr"
    out = {}
    for name, path in (("browseAmr", base + "?type=browse"), ("browseFirst", base + "/" + ids[0] + "?type=browse"),
                       ("metadata", base + "/" + ids[0] + ("/x" if mode == "amr-fields" else "") + "?type=metadata"),
                       ("read", base + "/" + ids[0] + ("/x" if mode == "amr-fields" else ""))):
        try:
            value = d.dev.request("GET", path, timeout=60)[2]
            out[name] = json.dumps(value)[:300]
        except SystemExit as exc:
            out[name] = "ERROR " + str(exc)[:200]
    return out


def input_test(d, args, report):
    modes = args.inputs.split(",")
    counts = [int(c) for c in args.input_counts.split(",")]
    original = d.api("GET", "/settings")
    original_list = d.api("GET", "/amr-list")["ids"]
    info = d.api("GET", "/map")
    box = (info["left"], info["right"], info["bottom"], info["top"]) if info.get("loaded") else (0, 10000, 0, 10000)
    report.update(inputs=[], nodeChecks={}, map=info.get("name"), rate=args.rate)
    settings = json.loads(json.dumps(original))
    settings["simulation"]["enabled"] = False
    try:
        settings["fleet"]["input"] = "fleet-json"
        d.api("PUT", "/settings", settings)
        time.sleep(3)
        report["idle"] = d.cpu_both(15)                     # nothing written
        print(json.dumps({"idle": report["idle"]}), flush=True)
        for n in counts:
            ids = ["P-{:03d}".format(i + 1) for i in range(n)]
            d.api("PUT", "/amr-list", {"ids": ids})
            for mode in modes:
                settings["fleet"]["input"] = mode
                d.api("PUT", "/settings", settings)
                time.sleep(3)                               # the app (un)registers fleet/amr/** within a moment
                stats = {}
                writer = threading.Thread(target=write_load, args=(d, mode, ids, box, args.rate, 35, stats))
                writer.start()
                time.sleep(6)                               # warm-up
                start, t_start = d.api("GET", "/fleet?since=-1"), time.monotonic()
                cpu = d.cpu_both(20)
                end, t_end = d.api("GET", "/fleet?since=-1"), time.monotonic()
                writer.join()
                if mode != "fleet-json" and mode not in report["nodeChecks"]:
                    report["nodeChecks"][mode] = node_checks(d, ids, mode)
                run = dict(mode=mode, amrs=n, writesPerCycle=len(bulk_entries(mode, ids, 0, box)), server=cpu,
                           updatesPerS=round((end["revision"] - start["revision"]) / (t_end - t_start), 2),
                           fleetCount=end["count"], rejected=end["rejected"] - start["rejected"],
                           lastError=end["lastError"][:200], writer=stats)
                report["inputs"].append(run)
                print(json.dumps(run), flush=True)
    finally:
        d.api("PUT", "/amr-list", {"ids": original_list})
        d.api("PUT", "/settings", original)
        report["restored"] = {"fleet.input": original["fleet"]["input"], "simulation": original["simulation"],
                              "amrList": len(original_list)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", required=True)
    ap.add_argument("--map", help="map file for the simulation test (not needed for --inputs)")
    ap.add_argument("--counts", default="10,25,50,100,200")
    ap.add_argument("--browser", default="10,50,200")
    ap.add_argument("--inputs", default="", help="input modes to test instead of the simulation, comma-separated")
    ap.add_argument("--input-counts", default="10,50,200")
    ap.add_argument("--rate", type=float, default=5.0, help="fleet updates per second written in the input test")
    ap.add_argument("--out", default="perf.json")
    args = ap.parse_args()
    if args.inputs:
        d = Device(args.device)
        report = {"device": args.device, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
        try:
            input_test(d, args, report)
        finally:
            with open(args.out, "w") as f:
                json.dump(report, f, indent=1)
            print("report:", args.out)
        return
    if not args.map:
        ap.error("--map is required for the simulation test")
    counts = [int(c) for c in args.counts.split(",")]
    browser_counts = {int(c) for c in args.browser.split(",") if c}
    d = Device(args.device)
    name = os.path.basename(args.map)
    report = {"device": args.device, "map": name, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    original = d.api("GET", "/settings")
    try:
        with open(args.map, "rb") as f:
            data = f.read()
        _, dt = timed(lambda: d.dev.request("PUT", "/solutions/webdav/appdata/{}/{}".format(SNAP, name), data,
                                            headers={"Content-Type": "text/plain"}, raw=True, timeout=600))
        report["uploadSeconds"] = round(dt, 1)
        settings = json.loads(json.dumps(original))
        settings["map"]["file"] = name
        settings["simulation"].update(enabled=True, amrCount=counts[0])
        _, dt = timed(lambda: d.api("PUT", "/settings", settings))
        report["settingsSaveSeconds"] = round(dt, 2)  # since 0.2.3 the map loads in the background
        t0 = time.monotonic() - dt
        info = {}
        while not (info.get("loaded") and info.get("name") == name and not info.get("loading")):
            time.sleep(0.1)
            info = d.api("GET", "/map")
        report["mapLoadSeconds"] = round(time.monotonic() - t0, 2)   # seen from here (+ polling, round trip)
        while not info.get("prepared", True):                         # all tile levels built (0.2.3+)
            time.sleep(0.2)
            info = d.api("GET", "/map")
        report["mapPreparedSeconds"] = round(time.monotonic() - t0, 2)
        report["appLog"] = app_log(d, ("loaded:", "prepared in"))
        report["tiles"] = tiles(d)
        print(json.dumps({k: report[k] for k in ("uploadSeconds", "settingsSaveSeconds", "mapLoadSeconds",
                                                 "mapPreparedSeconds", "appLog", "tiles")}), flush=True)
        report["runs"] = []
        for n in counts:
            settings["simulation"]["amrCount"] = n
            d.api("PUT", "/settings", settings)
            time.sleep(15 + n * 0.06)                 # spawn + all AMRs on their way (4 new paths per step)
            run = {"amrs": n, "server": server_sample(d)}
            if n in browser_counts:
                run["browser"] = browser_sample(d, os.path.splitext(args.out)[0] + "-{}".format(n))
                run["serverWithBrowser"] = server_sample(d)
            report["runs"].append(run)
            print(json.dumps(run), flush=True)
    finally:
        restore = json.loads(json.dumps(original))
        d.api("PUT", "/settings", restore)
        report["restored"] = {"map.file": restore["map"]["file"], "simulation": restore["simulation"]}
        with open(args.out, "w") as f:
            json.dump(report, f, indent=1)
        print("report:", args.out)


if __name__ == "__main__":
    main()
