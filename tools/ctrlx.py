#!/usr/bin/env python3
"""Small ctrlX OS REST client for the develop-deploy-test loop (stdlib only).

Connection (environment variables or options):
    CTRLX_HOST      host[:port]  e.g. 10.0.2.2:8443 (COREvirtual port forwarding seen from the
                    App Build Environment), localhost:8443 (from the Windows host), 192.168.1.1
    CTRLX_USER      default boschrexroth
    CTRLX_PASSWORD  default boschrexroth
    CTRLX_VERIFY_TLS=1  verify the device certificate (default: off - devices use self-signed certs)

Commands:
    ctrlx.py packages                         list installed apps
    ctrlx.py install <file.snap> [--allow-unknown]
                                              upload + install/update, wait for the task
    ctrlx.py dl-read <path>                   read a Data Layer node (REST)
    ctrlx.py dl-browse <path>                 list child nodes
    ctrlx.py dl-write <path> <json-value> --type float64
    ctrlx.py appdata <relative-path>          read a file of the active configuration (WebDAV),
                                              e.g. ctrlx-acme-demo/settings.json
    ctrlx.py logs [--search TEXT] [--limit N] recent Logbook entries

API sources: reference/rest-api/*.openapi.json (package manager 1.15, identity manager 2.10,
Data Layer 2.1.5, logbook 2.4). Built from the OpenAPI specs - if a call fails on your
device version, fix it here and note it in docs/ctrlx/gotchas.md.
"""

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


class Device:
    def __init__(self, host, user, password, verify):
        self.base = "https://" + host
        self.user, self.password = user, password
        self.ctx = ssl.create_default_context()
        if not verify:
            self.ctx.check_hostname = False
            self.ctx.verify_mode = ssl.CERT_NONE
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
            with urllib.request.urlopen(req, context=self.ctx, timeout=timeout) as res:
                content = res.read()
                if raw:
                    return res.status, res.headers, content
                return res.status, res.headers, (json.loads(content) if content.strip()[:1] in (b"{", b"[") else content.decode(errors="replace"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise SystemExit("HTTP {} {} {}\n{}".format(exc.code, method, path, detail[:2000]))
        except urllib.error.URLError as exc:
            raise SystemExit("Cannot reach {}: {} (CTRLX_HOST correct? COREvirtual running?)".format(url, exc.reason))

    def token(self):
        if self._token is None:
            _, _, body = self.request("POST", "/identity-manager/api/v2/auth/token",
                                      {"name": self.user, "password": self.password}, auth=False)
            self._token = body["access_token"]
        return self._token


def cmd_packages(dev, args):
    _, _, pkgs = dev.request("GET", "/package-manager/api/v1/packages")
    for p in sorted(pkgs, key=lambda p: p.get("name", "")):
        print("{:40} {:20} {}".format(p.get("name", ""), str(p.get("version", "")), p.get("title", "")))


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


def cmd_install(dev, args):
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
        _, _, task = dev.request("GET", task_path)
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


def cmd_appdata(dev, args):
    _, _, content = dev.request("GET", "/solutions/webdav/appdata/" + args.path.lstrip("/"), raw=True)
    sys.stdout.write(content.decode(errors="replace"))


def cmd_logs(dev, args):
    q = {"limit": args.limit}
    if args.search:
        q["search_text"] = args.search
    _, _, body = dev.request("GET", "/logbook/api/v2/entries?" + urllib.parse.urlencode(q))
    entries = body.get("entries", body) if isinstance(body, dict) else body
    for e in entries if isinstance(entries, list) else []:
        print("{} {:8} {:30} {}".format(e.get("timestamp", ""), str(e.get("logLevel", "")),
                                        str(e.get("systemUnit") or e.get("entity") or "")[:30],
                                        str(e.get("dynamicDescription") or e.get("mainTitle") or "")))
    if not isinstance(entries, list):
        print(json.dumps(body, indent=1)[:4000])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=os.environ.get("CTRLX_HOST", "10.0.2.2:8443"))
    ap.add_argument("--user", default=os.environ.get("CTRLX_USER", "boschrexroth"))  # ctrlx-check: ignore
    ap.add_argument("--password", default=os.environ.get("CTRLX_PASSWORD", "boschrexroth"))  # ctrlx-check: ignore
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("packages").set_defaults(fn=cmd_packages)
    p = sub.add_parser("install")
    p.add_argument("snap")
    p.add_argument("--allow-unknown", action="store_true")
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
    p = sub.add_parser("appdata")
    p.add_argument("path")
    p.set_defaults(fn=cmd_appdata)
    p = sub.add_parser("logs")
    p.add_argument("--search")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(fn=cmd_logs)
    args = ap.parse_args()
    dev = Device(args.host, args.user, args.password, os.environ.get("CTRLX_VERIFY_TLS") == "1")
    args.fn(dev, args)


if __name__ == "__main__":
    main()
