#!/usr/bin/env python3
"""Look at an app's web UI in a real browser (headless Chromium) and take screenshots.

    python3 tools/ui_check.py apps/<app>                     the app started locally (like run-local.sh)
    python3 tools/ui_check.py apps/<app> --device virtual    the app installed on a device (devices.toml),
                                                             opened through the reverse proxy with a login token
    options: --out DIR (default <app>/.ui-check/)  --narrow (also a 390 px wide screenshot)
             --no-scenario (only the start page)
             --role reader|writer   local only: as a user with only <snap>.web.all.r / .rw (default: admin,
                                    auth off); screenshots get the role in their name

One-time setup: tools/setup-ui-test.sh (Playwright + Chromium in ~/.cache, pinned; prints one sudo apt line).
This script re-runs itself with that venv's Python, so plain `python3` works.

What it does
  1. local: copies src/ www/ defaults/ of the app to a temp folder and starts `python3 -m app.main` there
     on a free port (APP_DEV_NO_AUTH=1, own app data, Data Layer off) - the app folder is not touched.
     --device: logs in with the devices.toml credentials and opens /<snap>/?token=... like the ctrlX menu.
  2. opens the page (1400 x 860) and waits until <body data-ui-ready="true">: index.html starts with
     data-ui-ready="false", www/app.js sets "true" when its data is shown (and "false" while it loads
     more). Pages without the attribute: load event + 3 s.
  3. runs run(ui) from the app's tests/ui_scenario.py (prepare(ui) before the page opens), see the template.
  4. exit 1 on JavaScript errors, console errors, HTTP responses >= 400 (unless the scenario allowed them)
     or a failing scenario. Prints the screenshots - LOOK at them: layout problems don't fail the run.
In the App Build Environment (emulated CPU) a run takes about a minute; browser start alone ~12 s.
Scenarios must not change a device unless asked: check `ui.local` before writes.
"""

import argparse
import base64
import importlib.util
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
VENV = os.environ.get("CTRLX_UI_VENV", os.path.expanduser("~/.cache/ctrlx-kit/uivenv"))
VIEWPORT = {"width": 1400, "height": 860}
TIMEOUT_MS = 90000   # Playwright's 30 s default is too short in an emulated build VM under load (snap build)
NARROW = {"width": 390, "height": 844}
READY = 'body[data-ui-ready="true"]'

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    venv_python = os.path.join(VENV, "bin", "python")
    # (the venv's python is a symlink to the system one: compare the prefix, not the executable)
    if os.path.exists(venv_python) and os.path.realpath(sys.prefix) != os.path.realpath(VENV):
        os.execv(venv_python, [venv_python, os.path.abspath(__file__)] + sys.argv[1:])
    sys.exit("Playwright is not set up: run tools/setup-ui-test.sh (once)")


def snap_name(app_dir):
    with open(os.path.join(app_dir, "snap", "snapcraft.yaml"), encoding="utf-8") as f:
        m = re.search(r"^name:\s*([a-z0-9-]+)\s*$", f.read(), re.M)
    if not m:
        sys.exit("no name: in {}/snap/snapcraft.yaml".format(app_dir))
    return m.group(1)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def unsigned_token(scopes, name):
    """A JWT without signature - enough for the local dev server, which (like the app behind the ctrlX
    reverse proxy) only decodes the scopes; the proxy is what checks signatures on a device."""
    def b64(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    return "{}.{}.unsigned".format(b64({"alg": "none", "typ": "JWT"}),
                                   b64({"name": name, "scope": scopes, "exp": int(time.time()) + 3600}))


class LocalApp:
    """The app's web server from a temp copy (no .dev/ in the app folder, Data Layer off)."""

    def __init__(self, app_dir, snap, role="admin"):
        self.tmp = tempfile.mkdtemp(prefix="ui-check-")
        for part in ("src", "www", "defaults"):
            if os.path.isdir(os.path.join(app_dir, part)):
                shutil.copytree(os.path.join(app_dir, part), os.path.join(self.tmp, part))
        self.port = free_port()
        env = dict(os.environ, APP_DEV_HTTP_PORT=str(self.port), PYTHONPATH=os.path.join(self.tmp, "src"))
        env.pop("CTRLX_HOST", None)
        self.token = None
        if role == "admin":
            env["APP_DEV_NO_AUTH"] = "1"
        else:   # real scope checks with a token like the ctrlX menu link gives
            env.pop("APP_DEV_NO_AUTH", None)
            suffix = {"reader": ".web.all.r", "writer": ".web.all.rw"}[role]
            self.token = unsigned_token([snap + suffix], "ui-check-" + role)
        self.log_path = os.path.join(self.tmp, "app.log")
        self.log = open(self.log_path, "w")
        python3 = shutil.which("python3", path=os.environ.get("PATH")) or "python3"  # system Python, like run-local.sh
        self.proc = subprocess.Popen([python3, "-m", "app.main"], cwd=self.tmp, env=env,
                                     stdout=self.log, stderr=subprocess.STDOUT)
        self.base = "http://127.0.0.1:{}/{}".format(self.port, snap)
        self.page_url = self.base + "/" + ("?token=" + self.token if self.token else "")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + 90
        while True:
            if self.proc.poll() is not None:
                raise SystemExit("the app stopped:\n" + self.log_tail())
            try:
                opener.open(self.page_url, timeout=5).read()
                break
            except (urllib.error.URLError, ConnectionError, OSError):
                if time.monotonic() > deadline:
                    raise SystemExit("the app did not answer within 90 s:\n" + self.log_tail())
                time.sleep(0.5)

    def log_tail(self, lines=15):
        self.log.flush()
        with open(self.log_path) as f:
            return "".join(f.readlines()[-lines:])

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.log.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


class DeviceApp:
    """The installed app on a device from devices.toml, through the reverse proxy."""

    def __init__(self, device, snap):
        sys.path.insert(0, HERE)
        import ctrlx  # the kit's REST client: devices.toml, login
        target = ctrlx.resolve_target(ctrlx.load_devices(ctrlx.DEFAULT_DEVICES_FILE), os.environ, device)
        dev = ctrlx.Device(target["api_host"], target["api_user"], target["api_password"], target["verify_tls"])
        self.token = dev.token()
        self.verify_tls = target["verify_tls"]
        self.base = "{}/{}".format(dev.base, snap)
        self.page_url = "{}/?token={}".format(self.base, self.token)   # what the ctrlX menu link does
        self.name = device or "default device"

    def stop(self):
        pass


class UI:
    """What a scenario gets: the page, helpers for screenshots, waiting and API calls."""

    def __init__(self, page, app, local, out, prefix, role, attach=None):
        self.page, self.app, self.local, self.out, self.prefix, self.role = page, app, local, out, prefix, role
        self._attach = attach
        self.shots = []
        self.allowed_http = set()
        self.api_prefix = "/api/v1"

    def wait_ready(self, timeout_s=60):
        """Wait two animation frames (let the page react), then for data-ui-ready="true" if the page uses it."""
        self.page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
        if self.page.evaluate("() => document.body.dataset.uiReady !== undefined"):
            self.page.wait_for_selector(READY, state="attached", timeout=timeout_s * 1000)
        else:
            self.page.wait_for_timeout(3000)

    def shot(self, name, locator=None):
        """Screenshot of the page (or of one element: locator, e.g. a widget)."""
        self.wait_ready()
        path = os.path.join(self.out, "{}-{}.png".format(self.prefix, name))
        (locator or self.page).screenshot(path=path)
        self.shots.append(path)
        return path

    def new_page(self, url=None):
        """Another tab of the same browser (own JavaScript, same origin storage rules), errors reported like
        the main page's - e.g. to show a widget on its own. Becomes ui.page until ui.page is set back."""
        page = self.page.context.new_page()
        if self._attach:
            self._attach(page)
        if url:
            page.goto(url, wait_until="load")
        return page

    def allow_http(self, *statuses):
        """HTTP error statuses the next steps provoke on purpose (e.g. 400 for an invalid setting)."""
        self.allowed_http.update(statuses)

    def api(self, method, path, body=None, as_admin=False):
        """Call the app's API (path below /api/v1) with the page's token (local admin: none needed).
        as_admin=True (local only): as an admin, e.g. to set up a test for a reader."""
        headers = {"Content-Type": "application/json"}
        if as_admin:
            if not self.local:
                raise RuntimeError("as_admin is for local runs only")
            headers["Authorization"] = "Bearer " + unsigned_token(["rexroth-device.all.rwx"], "ui-check-admin")
        elif self.app.token:
            headers["Authorization"] = "Bearer " + self.app.token
        req = urllib.request.Request(self.app.base + self.api_prefix + path, method=method, headers=headers,
                                     data=None if body is None else json.dumps(body).encode())
        handlers = [urllib.request.ProxyHandler({})]
        if not self.local and not self.app.verify_tls:
            handlers.append(urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
        with urllib.request.build_opener(*handlers).open(req, timeout=30) as res:
            raw = res.read()
        return json.loads(raw) if raw else None


def load_scenario(app_dir):
    path = os.path.join(app_dir, "tests", "ui_scenario.py")
    if not os.path.exists(path):
        return None
    spec = importlib.util.spec_from_file_location("ui_scenario", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("app", help="app folder, e.g. apps/ctrlx-acme-demo or templates/python-webapp")
    ap.add_argument("--device", nargs="?", const="", default=None,
                    help="test the installed app on this device of devices.toml (no name: the default device)")
    ap.add_argument("--out", help="screenshot folder (default <app>/.ui-check)")
    ap.add_argument("--narrow", action="store_true", help="also a 390 px wide (phone/HMI) screenshot")
    ap.add_argument("--no-scenario", action="store_true", help="ignore tests/ui_scenario.py")
    ap.add_argument("--role", choices=("admin", "writer", "reader"), default="admin",
                    help="local only: user role (reader = only <snap>.web.all.r); default admin, auth off")
    args = ap.parse_args()

    app_dir = os.path.abspath(args.app)
    snap = snap_name(app_dir)
    local = args.device is None
    if not local and args.role != "admin":
        sys.exit("--role works locally only (on a device the devices.toml user decides)")
    out = os.path.abspath(args.out or os.path.join(app_dir, ".ui-check"))
    os.makedirs(out, exist_ok=True)
    prefix = ("local" if local else "device") + ("" if args.role == "admin" else "-" + args.role)
    for old in os.listdir(out):  # keep the folder about this run only (and this role)
        if re.fullmatch(re.escape(prefix) + r"-(?!reader-|writer-)[^/]*\.png", old):
            os.remove(os.path.join(out, old))
    scenario = None if args.no_scenario else load_scenario(app_dir)

    started = time.monotonic()
    timings = []

    def mark(what):
        timings.append((what, time.monotonic() - started))

    print("== {} {}{} ({})".format(snap, "local" if local else "on device " + (args.device or "(default)"),
                                   "" if args.role == "admin" else " as " + args.role,
                                   "scenario tests/ui_scenario.py" if scenario else "start page only"))
    app = LocalApp(app_dir, snap, args.role) if local else DeviceApp(args.device or None, snap)
    mark("app ready" if local else "logged in")
    problems = []
    ui = None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--no-proxy-server"])
            mark("browser started")
            context = browser.new_context(viewport=VIEWPORT, ignore_https_errors=not local)
            context.set_default_timeout(TIMEOUT_MS)
            context.set_default_navigation_timeout(TIMEOUT_MS)
            page = context.new_page()

            def on_console(msg):
                if msg.type == "error" and not msg.text.startswith("Failed to load resource"):
                    problems.append("console error: " + msg.text)

            def on_response(res):
                if res.status >= 400 and res.status not in ui.allowed_http:
                    problems.append("HTTP {} {} {}".format(res.status, res.request.method,
                                                           res.url.split("?")[0]))
            def attach(p):
                p.on("pageerror", lambda exc: problems.append("JavaScript error: {}".format(exc)))
                p.on("console", on_console)
                p.on("response", on_response)
            attach(page)
            ui = UI(page, app, local, out, prefix, args.role, attach)

            step = "prepare"
            try:   # any failure: report it with the page as it is, keep the screenshots so far
                if scenario and hasattr(scenario, "prepare"):
                    scenario.prepare(ui)
                step = "start page"
                page.goto(app.page_url, wait_until="load")
                ui.shot("start")
                mark("start page shown")
                if scenario and hasattr(scenario, "run"):
                    step = "scenario"
                    scenario.run(ui)
                    mark("scenario done")
                if args.narrow:
                    step = "narrow view"
                    page.set_viewport_size(NARROW)
                    page.reload(wait_until="load")
                    ui.shot("narrow")
                    mark("narrow screenshot")
            except Exception as exc:
                problems.append("{} failed: {}: {}".format(step, type(exc).__name__, (str(exc).splitlines() or [""])[0]))
                try:
                    ui.page.screenshot(path=os.path.join(out, prefix + "-failure.png"))
                    ui.shots.append(os.path.join(out, prefix + "-failure.png"))
                except Exception:
                    pass
            browser.close()
    finally:
        app_log = app.log_tail() if local and problems else ""
        app.stop()
    if app_log:
        print("-- app log (last lines):\n" + app_log.rstrip())
    for what, t in timings:
        print("   {:22s} {:6.1f} s".format(what, t))
    print("== screenshots (look at them):")
    for path in (ui.shots if ui else []):
        rel = os.path.relpath(path)
        print("   " + (path if rel.startswith("..") else rel))
    if problems:
        print("== PROBLEMS:")
        for p in problems:
            print("   " + p)
        print("== UI CHECK: FAILED")
        return 1
    print("== UI CHECK: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
