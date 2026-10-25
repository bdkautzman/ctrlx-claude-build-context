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
"""

import argparse
import json
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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", required=True)
    ap.add_argument("--map", required=True)
    ap.add_argument("--counts", default="10,25,50,100,200")
    ap.add_argument("--browser", default="10,50,200")
    ap.add_argument("--out", default="perf.json")
    args = ap.parse_args()
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
