"""A/B test of the web page in headless Chromium: one local app (a map + simulated AMRs) and several
versions of www/ - e.g. an older one from a .snap - measured in turns, so that the speed swings of
the emulated build VM hit every version alike. It measures what the page costs the browser, not the
app (perf_test.py measures the app on a device).

    unsquashfs -d /tmp/s024 ctrlx-amr-fleet_0.2.4_amd64.snap www       # an older page
    ~/.cache/ctrlx-kit/uivenv/bin/python scripts/browser_ab.py --map BIG.txt --amrs 200 \\
        --www 0.2.4=/tmp/s024/www --www new=www [--rounds 3] [--out ab.json]

Per version and round, 10 s each in the overview (whole map) and locked onto an AMR (4x zoomed in):
frames per second and the browser's busy time per second - JavaScript, style, layout, all tasks
(CDP Performance metrics). Prints the medians. Every page version must suit the app's API.
"""

import argparse
import json
import os
import re
import shutil
import statistics
import sys
import time
import urllib.request
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(APP_DIR, "..", "..", "tools"))
import ui_check  # noqa: E402  LocalApp: the app's web server from a temp copy

from playwright.sync_api import sync_playwright  # noqa: E402

FPS = ("() => new Promise(r => { let n = 0; const t0 = performance.now(); (function f() { n++;"
       " if (performance.now() - t0 < 10000) requestAnimationFrame(f);"
       " else r(n / ((performance.now() - t0) / 1000)); })(); })")
READY = ("n => document.body.dataset.uiReady === 'true'"
         " && document.getElementById('amr-count').textContent === '(' + n + ')'")
KEYS = ("fps", "scriptMsPerS", "styleMsPerS", "layoutMsPerS", "taskMsPerS")


def metrics(cdp):
    return {m["name"]: m["value"] for m in cdp.send("Performance.getMetrics")["metrics"]}


def sample(cdp, page):
    a = metrics(cdp)
    fps = page.evaluate(FPS)
    b = metrics(cdp)
    dt = b["Timestamp"] - a["Timestamp"]
    per_s = lambda key: round((b[key] - a[key]) / dt * 1000)  # noqa: E731
    return {"fps": round(fps, 1), "scriptMsPerS": per_s("ScriptDuration"), "styleMsPerS": per_s("RecalcStyleDuration"),
            "layoutMsPerS": per_s("LayoutDuration"), "taskMsPerS": per_s("TaskDuration")}


def run_page(browser, app, snap, www, amrs):
    ctx = browser.new_context(viewport={"width": 1400, "height": 860})
    ctx.set_default_timeout(300000)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    def serve(route):   # the static files of this page version; the API stays the app's
        rel = urlsplit(route.request.url).path[len("/" + snap + "/"):] or "index.html"
        full = os.path.join(www, rel)
        if os.path.isfile(full):
            route.fulfill(path=full)
        else:
            route.continue_()
    page.route(re.compile(r"^[^?]*/" + re.escape(snap) + r"/(?!api/)[^/?]*(\?.*)?$"), serve)
    cdp = ctx.new_cdp_session(page)
    cdp.send("Performance.enable")
    page.goto(app.page_url, wait_until="load")
    page.wait_for_function(READY, arg=amrs)
    result = {"overview": sample(cdp, page)}
    page.locator("#amr-list li").first.get_by_role("button", name=re.compile(r"^Follow ")).click()
    for _ in range(4):
        page.click("#btn-zoom-in")
    page.wait_for_function("document.body.dataset.uiReady === 'true'")
    result["locked"] = sample(cdp, page)
    result["errors"] = errors
    ctx.close()
    return result


def api(app, method, path, body=None):
    req = urllib.request.Request(app.base + "/api/v1" + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=120) as res:
        return json.loads(res.read() or b"null")


def fmt(m):
    return "{fps:>4} fps  js {scriptMsPerS:>3}  style {styleMsPerS:>3}  layout {layoutMsPerS:>3}  busy {taskMsPerS:>4}".format(**m)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--map", required=True, help="map file (copied into the local app data)")
    ap.add_argument("--amrs", type=int, default=200, help="simulated AMRs")
    ap.add_argument("--www", action="append", required=True, help="NAME=DIR of a page version (two or more)")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--out", default="", help="JSON with all samples")
    args = ap.parse_args()
    variants = [(v.split("=", 1)[0], os.path.abspath(v.split("=", 1)[1])) for v in args.www]
    snap = ui_check.snap_name(APP_DIR)
    map_name = os.path.basename(args.map)
    app = ui_check.LocalApp(APP_DIR, snap)          # as admin, Data Layer off
    try:
        appdata = os.path.join(app.tmp, ".dev", "solutions", "activeConfiguration", snap)
        os.makedirs(appdata, exist_ok=True)
        shutil.copy(args.map, appdata)
        api(app, "PUT", "/settings", {"map": {"file": map_name},
                                      "simulation": {"enabled": True, "amrCount": args.amrs}})
        deadline = time.monotonic() + 600
        while True:
            info, fleet = api(app, "GET", "/map"), api(app, "GET", "/fleet")
            if info.get("loaded") and info.get("file") == map_name and not info.get("loading") \
                    and fleet.get("count") == args.amrs:
                break
            if time.monotonic() > deadline:
                raise SystemExit("map / simulation not ready after 600 s: " + json.dumps(info)[:300])
            time.sleep(2)
        print("app ready: {} with {} simulated AMRs".format(map_name, args.amrs), flush=True)
        results = {name: [] for name, _ in variants}
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--no-proxy-server"])
            for r in range(args.rounds):
                shift = r % len(variants)                   # a different order each round
                for name, www in variants[shift:] + variants[:shift]:
                    res = run_page(browser, app, snap, www, args.amrs)
                    results[name].append(res)
                    print("round {} {:>8}  overview {}  |  locked {}{}".format(
                        r + 1, name, fmt(res["overview"]), fmt(res["locked"]),
                        "  ERRORS " + json.dumps(res["errors"]) if res["errors"] else ""), flush=True)
            browser.close()
        print("\nmedians of {} rounds".format(args.rounds))
        summary = {}
        for name, _ in variants:
            summary[name] = {mode: {k: statistics.median(s[mode][k] for s in results[name]) for k in KEYS}
                             for mode in ("overview", "locked")}
            print("{:>8}  overview {}  |  locked {}".format(name, fmt(summary[name]["overview"]),
                                                            fmt(summary[name]["locked"])))
        if args.out:
            with open(args.out, "w") as f:
                json.dump({"map": map_name, "amrs": args.amrs, "summary": summary, "samples": results}, f, indent=1)
    finally:
        app.stop()


if __name__ == "__main__":
    main()
