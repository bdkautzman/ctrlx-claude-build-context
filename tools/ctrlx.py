#!/usr/bin/env python3
"""Small ctrlX OS REST client for the develop-deploy-test loop (stdlib only).

Devices: <kit>/devices.toml (git-ignored; template devices.example.toml) holds address, API and SSH
credentials of each test device. Pick one with --device NAME (or CTRLX_DEVICE; else its `default`).

Connection precedence: --host/--user/--password > --device NAME > CTRLX_* variables >
CTRLX_DEVICE / default device of devices.toml > built-in defaults (10.0.2.2:8443, boschrexroth):
    CTRLX_HOST      host[:port]  e.g. 10.0.2.2:8443 (COREvirtual port forwarding seen from the
                    App Build Environment), 192.168.1.1 (real CORE); without a port: CTRLX_SSL_PORT
    CTRLX_USER / CTRLX_PASSWORD               REST API login
    CTRLX_SSH_USER / CTRLX_SSH_PASSWORD / CTRLX_SSH_PORT
    CTRLX_DEVICES_FILE  other devices file;  CTRLX_VERIFY_TLS=1  verify the device certificate
    http(s)_proxy is ignored: the device is always reached directly.

Commands:
    ctrlx.py devices                          list the devices of devices.toml (no passwords)
    ctrlx.py ssh [command ...]                SSH to the device (sshpass when a password is set)
    ctrlx.py env                              print CTRLX_* exports, e.g. for run-local.sh:
                                              eval "$(../../tools/ctrlx.py --device virtual env)"
    ctrlx.py packages                         list installed apps
    ctrlx.py state [SERVICE|SETUP|OPERATING]  show / set the device operating state
    ctrlx.py install <file.snap|file.app> [--allow-unknown] [--service]
                                              upload + install/update, wait for the task
                                              (--service: switch to SERVICE state and back;
                                              refuses a snap built for another architecture and
                                              a .app without the device's architecture)
    ctrlx.py dl-read <path>                   read a Data Layer node (REST)
    ctrlx.py dl-browse <path>                 list child nodes
    ctrlx.py dl-write <path> <json-value> --type float64
    ctrlx.py api <METHOD> <path> [--data JSON] [--out FILE]
                                              any REST path with the login token, e.g. an app's own API:
                                              api GET /ctrlx-network-scanner/api/v1/status
    ctrlx.py appdata <relative-path>          read a file of the active configuration (WebDAV),
                                              e.g. ctrlx-acme-demo/settings.json
    ctrlx.py logs [--snap NAME] [--search TEXT] [--limit N]
                                              recent Logbook entries (newest first)

API sources: reference/rest-api/*.openapi.json (package manager 1.15, identity manager 2.10,
Data Layer 2.1.5, logbook 2.4). Built from the OpenAPI specs - if a call fails on your
device version, fix it here and note it in docs/ctrlx/gotchas.md.
"""

import argparse
import http.client
import json
import os
import re
import shlex
import shutil
import ssl
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DEVICES_FILE = os.path.join(KIT, "devices.toml")


def load_devices(path):
    """Devices file as dict ({} if it does not exist)."""
    if not os.path.isfile(path):
        return {}
    try:
        import tomllib  # Python >= 3.11
    except ImportError:
        raise SystemExit("{} needs Python >= 3.11 (tomllib) - or use the CTRLX_* variables".format(path))
    with open(path, "rb") as f:
        try:
            return tomllib.load(f)
        except tomllib.TOMLDecodeError as exc:
            raise SystemExit("{}: {}".format(path, exc))


def device_names(devices):
    return [k for k, v in devices.items() if isinstance(v, dict)]


def _with_port(host, port):
    if not host or ":" in host or not port or int(port) == 443:
        return host
    return "{}:{}".format(host, port)


def resolve_target(devices, env, device=None, host=None, user=None, password=None):
    """Connection settings from (in this order) explicit options, --device, CTRLX_* variables,
    the default device of the devices file and built-in defaults."""
    name = device or env.get("CTRLX_DEVICE") or devices.get("default")
    d = {}
    if name:
        d = devices.get(name)
        if not isinstance(d, dict):
            raise SystemExit("Unknown device '{}' - devices file has: {}".format(name, ", ".join(device_names(devices)) or "nothing"))
    use_env = not device  # an explicit --device wins over CTRLX_* variables
    e = env if use_env else {}
    env_host = _with_port(e.get("CTRLX_HOST", ""), e.get("CTRLX_SSL_PORT"))
    dev_host = _with_port(d.get("host", ""), d.get("https_port"))
    api_host = host or env_host or dev_host or "10.0.2.2:8443"
    t = {
        "name": name or "",
        "api_host": api_host,
        "api_user": user or e.get("CTRLX_USER") or d.get("api_user") or "boschrexroth",  # ctrlx-check: ignore
        "api_password": password or e.get("CTRLX_PASSWORD") or d.get("api_password") or "boschrexroth",  # ctrlx-check: ignore
        "ssh_host": api_host.rsplit(":", 1)[0] if (host or env_host) else (d.get("host") or "10.0.2.2"),
        "ssh_port": int(e.get("CTRLX_SSH_PORT") or d.get("ssh_port") or (8022 if api_host == "10.0.2.2:8443" else 22)),
        "ssh_user": e.get("CTRLX_SSH_USER") or d.get("ssh_user") or "",
        "ssh_password": e.get("CTRLX_SSH_PASSWORD") or d.get("ssh_password") or "",
        "arch": d.get("arch", ""),
        "verify_tls": env.get("CTRLX_VERIFY_TLS") == "1" or bool(d.get("verify_tls")),
    }
    return t


def snap_arch(snap_file):
    """'arm64' for name_1.0.0_arm64.snap ('' if the name has no architecture)."""
    base = os.path.basename(snap_file)
    return base[:-5].rsplit("_", 1)[-1] if base.endswith(".snap") and base.count("_") >= 2 else ""


APP_SNAP = re.compile(r"(?:\./)?public/snaps/([^/]+)/release/[^/]+\.snap$")


def app_archs(app_file):
    """Architectures in a .app package: an uncompressed tar with public/snaps/<arch>/release/<snap>_<ver>.snap
    (tools/make-app.sh, SDK scripts/app-signing-tool.sh)."""
    try:
        with tarfile.open(app_file, "r:") as tar:
            names = tar.getnames()
    except (tarfile.TarError, OSError) as exc:
        raise SystemExit("{} is not a .app package (uncompressed tar): {}".format(app_file, exc))
    archs = sorted({m.group(1) for m in map(APP_SNAP.match, names) if m})
    if not archs:
        raise SystemExit("{} has no public/snaps/<arch>/release/*.snap - not a .app package".format(app_file))
    return archs


class Device:
    def __init__(self, host, user, password, verify):
        self.base = "https://" + host
        self.user, self.password = user, password
        self.ctx = ssl.create_default_context()
        if not verify:
            self.ctx.check_hostname = False
            self.ctx.verify_mode = ssl.CERT_NONE
        # The device is on the LAN / behind port forwarding: never route it through http(s)_proxy
        # (the App Build Environment sets one that cannot reach the device).
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                                  urllib.request.HTTPSHandler(context=self.ctx))
        self._token = None

    def request(self, method, path, body=None, headers=None, auth=True, raw=False, timeout=60):
        url = path if path.startswith("http") else self.base + path
        hdrs = dict(headers or {})
        data = None
        if body is not None and not isinstance(body, (bytes, bytearray)):
            data = json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
        elif body is not None:
            data = bytes(body)
        if auth:
            hdrs["Authorization"] = "Bearer " + self.token()
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with self.opener.open(req, timeout=timeout) as res:
                content = res.read()
                if raw:
                    return res.status, res.headers, content
                return res.status, res.headers, (json.loads(content) if content.strip()[:1] in (b"{", b"[") else content.decode(errors="replace"))
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode(errors="replace")
            except Exception:  # e.g. IncompleteRead on a 502 while the proxy reloads
                detail = ""
            raise SystemExit("HTTP {} {} {}\n{}".format(exc.code, method, path, detail[:2000]))
        except urllib.error.URLError as exc:
            raise SystemExit("Cannot reach {}: {} (CTRLX_HOST correct? COREvirtual running?)".format(url, exc.reason))
        except (http.client.HTTPException, ConnectionError, TimeoutError) as exc:
            # e.g. RemoteDisconnected while the reverse proxy reloads during an app install
            raise SystemExit("Connection to {} lost: {}".format(url, exc or type(exc).__name__))

    def token(self):
        if self._token is None:
            _, _, body = self.request("POST", "/identity-manager/api/v2/auth/token",
                                      {"name": self.user, "password": self.password}, auth=False)
            self._token = body["access_token"]
        return self._token


def cmd_packages(dev, args):
    _, _, pkgs = dev.request("GET", "/package-manager/api/v1/packages")
    for p in sorted(pkgs, key=lambda p: p.get("name", "")):
        # ctrlX OS 4.6: version is in release.version, state in health.status (no top-level "version")
        version = (p.get("release") or {}).get("version") or p.get("version", "")
        health = (p.get("health") or {}).get("status", "")
        print("{:32} {:12} {:8} {}".format(p.get("name", ""), str(version), health, p.get("title", "")))


def _multipart(fields, file_field, filename, file_bytes):
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append('--{}\r\nContent-Disposition: form-data; name="{}"\r\n\r\n{}\r\n'.format(boundary, k, v).encode())
    parts.append('--{}\r\nContent-Disposition: form-data; name="{}"; filename="{}"\r\n'
                 'Content-Type: application/octet-stream\r\n\r\n'.format(boundary, file_field, filename).encode())
    parts.append(file_bytes)
    parts.append("\r\n--{}--\r\n".format(boundary).encode())
    return b"".join(parts), "multipart/form-data; boundary=" + boundary


STATE_NODE = "scheduler/admin/state"  # writable: SERVICE / SETUP / OPERATING (ctrlX OS 4.6)


def _get_state(dev):
    """Operating state of the device, or None if the device has no scheduler node."""
    try:
        _, _, body = dev.request("GET", _node_path(STATE_NODE))
    except SystemExit:
        return None
    return (body.get("value") or {}).get("state") if isinstance(body, dict) else None


def _set_state(dev, state, timeout=120):
    """Request a state and wait for it. Retries: right after an install the reverse proxy reloads
    and answers 502 for a few seconds."""
    print("Switching device to", state, "...")
    deadline = time.time() + timeout
    while True:
        try:
            if _get_state(dev) == state:
                return
            dev.request("PUT", _node_path(STATE_NODE), {"type": "object", "value": {"state": state}})
        except SystemExit as exc:
            if time.time() > deadline:
                raise SystemExit("Could not switch the device to {}: {}".format(state, exc))
        if time.time() > deadline:
            raise SystemExit("Device did not reach state {} within {} s".format(state, timeout))
        time.sleep(2)


def cmd_state(dev, args):
    if args.state:
        _set_state(dev, args.state.upper())
    print(_get_state(dev))


def check_arch(package, target):
    """Refuse a package that has nothing for the device's architecture (devices.toml `arch`)."""
    want, base = target["arch"], os.path.basename(package)
    if package.endswith(".app"):
        archs = app_archs(package)
        print("{} contains {}".format(base, ", ".join(archs)))
        if want and want not in archs:
            raise SystemExit("{} contains only {}, but device '{}' is {} - package both architectures with "
                             "tools/make-app.sh".format(base, ", ".join(archs), target["name"], want))
        return
    arch = snap_arch(package)
    if arch and want and arch != want:
        raise SystemExit("{} is built for {}, but device '{}' is {} - build with ./build-snap-{}.sh".format(
            base, arch, target["name"], want, want))


def cmd_install(dev, args):
    check_arch(args.snap, args.target)
    # ctrlX OS 4.6 rejects installs outside SERVICE with HTTP 409 "system must be in state 'SERVICE'"
    previous = _get_state(dev)
    if previous not in (None, "SERVICE"):
        if not args.service:
            raise SystemExit("The device is in state {}; apps can only be installed in SERVICE. Switch it in the "
                             "ctrlX UI, or re-run with --service (switches to SERVICE and back).".format(previous))
        _set_state(dev, "SERVICE")
        try:
            _install(dev, args)
        finally:
            _set_state(dev, previous)
    else:
        _install(dev, args)


def _install(dev, args):
    snap = args.snap
    name = os.path.basename(snap).split("_")[0]
    _, _, settings = dev.request("GET", "/package-manager/api/v1/settings")
    if isinstance(settings, dict) and not settings.get("allowUnknownApps", False):
        if args.allow_unknown:
            dev.request("PUT", "/package-manager/api/v1/settings", dict(settings, allowUnknownApps=True))
            print("Enabled 'allow installation from unknown sources'")
        else:
            raise SystemExit("The device does not allow apps from unknown sources (unsigned snaps). Enable it in "
                             "Settings > Apps > Settings, or re-run with --allow-unknown.")
    _, _, pkgs = dev.request("GET", "/package-manager/api/v1/packages")
    update = any(p.get("name") == name for p in pkgs)
    with open(snap, "rb") as f:
        body, ctype = _multipart({"update": "true" if update else "false"}, "file", os.path.basename(snap), f.read())
    print("{} {} ({:.1f} MB) ...".format("Updating" if update else "Installing", name, len(body) / 1e6))
    status, headers, _ = dev.request("POST", "/package-manager/api/v1/packages", body,
                                     headers={"Content-Type": ctype}, timeout=600)
    loc = headers.get("Location", "")
    if not loc:
        print("Accepted (HTTP {}), no task location returned".format(status))
        return
    task_path = loc if loc.startswith("/package-manager") else "/package-manager/api/v1" + (loc if loc.startswith("/") else "/" + loc)
    last = None
    for _ in range(600):
        try:
            _, _, task = dev.request("GET", task_path)
        except SystemExit as exc:  # the proxy reloads while the app is installed: retry
            print("  (retrying: {})".format(str(exc).splitlines()[0][:120]))
            time.sleep(2)
            continue
        state = task.get("state")
        line = "{} {}%".format(state, task.get("progress", ""))
        if line != last:
            print("  task:", line)
            last = line
        if state in ("done", "failed"):
            if state == "failed":
                raise SystemExit("Installation failed: " + json.dumps(task.get("result", task), indent=1))
            print("Installed", name)
            return
        time.sleep(2)
    raise SystemExit("Timeout waiting for " + task_path)


def _node_path(path):
    return "/automation/api/v2/nodes/" + "/".join(urllib.parse.quote(p, safe="") for p in path.strip("/").split("/"))


def cmd_dl_read(dev, args):
    _, _, body = dev.request("GET", _node_path(args.path))
    print(json.dumps(body, indent=1))


def cmd_dl_browse(dev, args):
    _, _, body = dev.request("GET", _node_path(args.path) + "?type=browse")
    print(json.dumps(body, indent=1))


def cmd_dl_write(dev, args):
    value = json.loads(args.value)
    _, _, body = dev.request("PUT", _node_path(args.path), {"type": args.type, "value": value})
    print(json.dumps(body, indent=1) if body else "OK")


def cmd_api(dev, args):
    """Any REST path of the device with the login token, e.g. an app's own API through the reverse proxy."""
    body = json.loads(args.data) if args.data is not None else None
    status, headers, content = dev.request(args.method.upper(), args.path, body, raw=True)
    if args.out:
        with open(args.out, "wb") as f:
            f.write(content)
        print("HTTP {} {} - {} bytes -> {}".format(status, headers.get("Content-Type", ""), len(content), args.out))
        return
    if content.strip()[:1] in (b"{", b"["):
        print(json.dumps(json.loads(content), indent=1))
    else:
        sys.stdout.write(content.decode(errors="replace") or "HTTP {}\n".format(status))


def cmd_appdata(dev, args):
    _, _, content = dev.request("GET", "/solutions/webdav/appdata/" + args.path.lstrip("/"), raw=True)
    sys.stdout.write(content.decode(errors="replace"))


def _log_entries(dev, q):
    _, _, body = dev.request("GET", "/logbook/api/v2/entries?" + urllib.parse.urlencode(q))
    if isinstance(body, dict):
        return body.get("entries", [])
    return []  # 204 No Content = no matching entries


def cmd_logs(dev, args):
    # ctrlX OS 4.6: the search_text parameter always answers 204 (tried plain and wildcard text), so
    # --search filters client-side. The unit filter works: snap services log as snap.<snap>.<app>.service.
    limit = 1000 if args.search else args.limit
    entries = []
    if args.snap:
        _, _, units = dev.request("GET", "/logbook/api/v2/units")
        names = {u for u in json.dumps(units).split('"') if u.startswith("snap.{}.".format(args.snap))}
        if not names:
            raise SystemExit("No Logbook unit for snap {} (installed? logged anything yet?)".format(args.snap))
        for unit in sorted(names):
            entries += _log_entries(dev, {"limit": limit, "unit": unit})
        entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    else:
        entries = _log_entries(dev, {"limit": limit})
    if args.search:
        needle = args.search.lower()
        entries = [e for e in entries if needle in json.dumps(e).lower()]
    for e in entries[:args.limit]:
        print("{} {:8} {:30} {}".format(e.get("timestamp", ""), str(e.get("logLevel", "")),
                                        str(e.get("systemUnit") or e.get("entity") or "")[:30],
                                        str(e.get("dynamicDescription") or e.get("mainTitle") or "")))


def cmd_devices(dev, args):
    devices = args.devices
    if not devices:
        print("No devices file ({}) - copy devices.example.toml to devices.toml".format(args.devices_file))
        return
    for n in device_names(devices):
        d = devices[n]
        mark = "*" if n == devices.get("default") else " "
        print("{} {:10} {:6} {:20} ssh {}, port {:<5}  {}".format(
            mark, n, d.get("arch", ""), _with_port(d.get("host", ""), d.get("https_port")),
            d.get("ssh_user") or "-", d.get("ssh_port", 22), d.get("description", "")))


def cmd_ssh(dev, args):
    t = args.target
    if not t["ssh_user"]:
        raise SystemExit("No SSH user for device '{}' (ssh_user in devices.toml or CTRLX_SSH_USER)".format(t["name"]))
    argv = ["ssh", "-p", str(t["ssh_port"]), "-o", "StrictHostKeyChecking=accept-new",
            "{}@{}".format(t["ssh_user"], t["ssh_host"])] + list(args.command)
    env = dict(os.environ)
    if t["ssh_password"]:
        if shutil.which("sshpass"):
            argv = ["sshpass", "-e"] + argv  # password via $SSHPASS, not visible in the process list
            env["SSHPASS"] = t["ssh_password"]
        else:
            print("(sshpass not installed - ssh will ask for the password)", file=sys.stderr)
    os.execvpe(argv[0], argv, env)


def cmd_env(dev, args):
    t = args.target
    host, _, port = t["api_host"].partition(":")
    for k, v in (("CTRLX_HOST", host), ("CTRLX_SSL_PORT", port or "443"),
                 ("CTRLX_USER", t["api_user"]), ("CTRLX_PASSWORD", t["api_password"])):
        print("export {}={}".format(k, shlex.quote(v)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", help="device name in devices.toml (default: CTRLX_DEVICE or the file's `default`)")
    ap.add_argument("--host", help="host[:port] of the REST API")
    ap.add_argument("--user")
    ap.add_argument("--password")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("devices").set_defaults(fn=cmd_devices)
    p = sub.add_parser("ssh")
    p.add_argument("command", nargs=argparse.REMAINDER, help="remote command (none: interactive shell)")
    p.set_defaults(fn=cmd_ssh)
    sub.add_parser("env").set_defaults(fn=cmd_env)
    sub.add_parser("packages").set_defaults(fn=cmd_packages)
    p = sub.add_parser("state")
    p.add_argument("state", nargs="?", choices=["SERVICE", "SETUP", "OPERATING", "service", "setup", "operating"])
    p.set_defaults(fn=cmd_state)
    p = sub.add_parser("install")
    p.add_argument("snap", metavar="file", help="<snap>_<ver>_<arch>.snap or <snap>_<ver>.app (tools/make-app.sh)")
    p.add_argument("--allow-unknown", action="store_true")
    p.add_argument("--service", action="store_true",
                   help="switch the device to SERVICE for the install, then back to its previous state")
    p.set_defaults(fn=cmd_install)
    p = sub.add_parser("dl-read")
    p.add_argument("path")
    p.set_defaults(fn=cmd_dl_read)
    p = sub.add_parser("dl-browse")
    p.add_argument("path")
    p.set_defaults(fn=cmd_dl_browse)
    p = sub.add_parser("dl-write")
    p.add_argument("path")
    p.add_argument("value", help="JSON value, e.g. 1.5 or '\"text\"' or true")
    p.add_argument("--type", required=True, help="Data Layer type, e.g. float64, string, bool8")
    p.set_defaults(fn=cmd_dl_write)
    p = sub.add_parser("api")
    p.add_argument("method", help="GET, POST, PUT, DELETE")
    p.add_argument("path", help="e.g. /ctrlx-network-scanner/api/v1/status")
    p.add_argument("--data", help="JSON body")
    p.add_argument("--out", help="write the response body to this file (e.g. a PDF)")
    p.set_defaults(fn=cmd_api)
    p = sub.add_parser("appdata")
    p.add_argument("path")
    p.set_defaults(fn=cmd_appdata)
    p = sub.add_parser("logs")
    p.add_argument("--snap", help="only entries of this snap's services")
    p.add_argument("--search", help="case-insensitive text filter (client-side)")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(fn=cmd_logs)
    args = ap.parse_args()
    args.devices_file = os.environ.get("CTRLX_DEVICES_FILE", DEFAULT_DEVICES_FILE)
    args.devices = load_devices(args.devices_file)
    args.target = resolve_target(args.devices, os.environ, args.device, args.host, args.user, args.password)
    t = args.target
    dev = Device(t["api_host"], t["api_user"], t["api_password"], t["verify_tls"])  # logs in on first request
    args.fn(dev, args)


if __name__ == "__main__":
    main()
