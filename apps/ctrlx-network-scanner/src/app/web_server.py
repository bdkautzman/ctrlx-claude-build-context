"""HTTP server behind the ctrlX reverse proxy (stdlib only, no web framework).

In the snap it listens on the Unix socket
    $SNAP_DATA/package-run/<snap>/web.sock
which the reverse proxy reaches via the package-manifest proxyMapping "binding".
Outside the snap (development) it listens on http://127.0.0.1:8080 with the same paths.

The proxy forwards the FULL path, so every route starts with config.URL_PREFIX.

Routes
  GET  /<snap>/                      index.html (static, unrestricted, no data!)
  GET  /<snap>/<file>                static assets from www/
  GET  /<snap>/api/v1/status         read   (app, settings, Data Layer, scan state, next runs, history usage)
  GET  /<snap>/api/v1/network        read   (this device's interfaces and gateways)
  POST /<snap>/api/v1/estimate       read   ({targets, exclude, tcpPorts, udpPorts} -> sizes / errors)
  POST /<snap>/api/v1/scans          write  ({"profile": id} -> 202 {"id"}; 409 while a scan runs)
  GET  /<snap>/api/v1/scans          read   (history index + usage)
  GET  /<snap>/api/v1/scans/current  read   (progress; ?hosts=1 with the hosts found so far)
  POST /<snap>/api/v1/scans/current/abort  write
  GET  /<snap>/api/v1/scans/<id>     read   (result rated with the current lists)
  GET  /<snap>/api/v1/scans/<id>/report.pdf | export.json | export.csv   read (downloads)
  DELETE /<snap>/api/v1/scans/<id>   write
  GET  /<snap>/api/v1/settings       read
  GET  /<snap>/api/v1/settings/meta  read   (defaults + rules for the UI)
  GET  /<snap>/api/v1/profiles       view   the scan profiles (the run card; the settings are configure-only)
  PUT  /<snap>/api/v1/settings       write  (validate, persist to app data, apply)
  POST /<snap>/api/v1/settings/reset write
  GET  /<snap>/api/v1/values         read   (Data Layer nodes of this app)
  PUT  /<snap>/api/v1/values/<name>  write  body {"value": ...}
  POST /<snap>/api/v1/appdata/save   Solutions save command
  POST /<snap>/api/v1/appdata/load   Solutions load command
All /api paths are "restricted" in the manifest -> the proxy validates the token.
Dev only (run-local.sh, never in the snap): /<snap>/__dev/feedback[.js] for the UI feedback overlay, dev_feedback.py.
"""

import csv
import http.server
import io
import json
import logging
import mimetypes
import os
import socketserver
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit

from app import appdata_commands, auth, config, dev_feedback, netinfo, probes, report_pdf
from app import ports as portlib
from app import targets as targetlib
from app.datalayer_nodes import NodeWriteError
from app.history import ID_PATTERN
from app.scan_manager import BusyError, UnknownProfileError
from app.settings_store import SettingsError

log = logging.getLogger("web")

MAX_BODY_BYTES = 1024 * 1024
STATIC_TYPES = {".html", ".js", ".css", ".svg", ".png", ".ico", ".json", ".woff2", ".map"}
_START_TIME = time.monotonic()


class _UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def get_request(self):
        request, _ = super().get_request()
        return request, ("local", 0)  # BaseHTTPRequestHandler expects a (host, port) tuple


class _TCPHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True


class WebServer:
    def __init__(self, store, datalayer, manager):
        self.store = store
        self.datalayer = datalayer
        self.manager = manager
        self._server = None
        self._thread = None

    def start(self):
        handler = _make_handler(self.store, self.datalayer, self.manager)
        if config.IN_SNAP:
            os.makedirs(config.SOCKET_DIR, exist_ok=True)
            try:
                os.unlink(config.SOCKET_PATH)  # stale socket from a previous run
            except FileNotFoundError:
                pass
            self._server = _UnixHTTPServer(config.SOCKET_PATH, handler)
            os.chmod(config.SOCKET_PATH, 0o660)
            log.info("Web server listening on unix:%s", config.SOCKET_PATH)
        else:
            self._server = _TCPHTTPServer((config.DEV_HTTP_HOST, config.DEV_HTTP_PORT), handler)
            log.info("DEV web server: http://%s:%d%s/", config.DEV_HTTP_HOST, config.DEV_HTTP_PORT,
                     config.URL_PREFIX)
            if config.DEV_NO_AUTH:
                log.warning("APP_DEV_NO_AUTH=1: all requests are treated as admin (dev only)")
            if dev_feedback.enabled():
                log.info("UI feedback overlay on: Alt+click an element of the page -> %s (skill /ui-feedback)",
                         config.DEV_FEEDBACK_FILE)
        self._thread = threading.Thread(target=self._server.serve_forever, name="web", daemon=True)
        self._thread.start()

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if config.IN_SNAP:
            try:
                os.unlink(config.SOCKET_PATH)
            except OSError:
                pass


def estimate(body, local_networks):
    """Sizes of a (possibly unsaved) profile for the editor: addresses and ports, or errors."""
    body = body if isinstance(body, dict) else {}
    errors = []
    tgt, exc = str(body.get("targets", "")), str(body.get("exclude", ""))
    errors += ["targets: " + e for e in targetlib.check_spec(tgt)]
    errors += ["exclude: " + e for e in targetlib.check_spec(exc, allow_auto=False, allow_empty=True)]
    result = {"addresses": None, "excluded": None, "tcpPorts": None, "udpPorts": None}
    if not errors:
        result["addresses"] = targetlib.count(tgt, local_networks)
        result["excluded"] = targetlib.count(exc, []) if exc.strip() else 0
    for proto in ("tcp", "udp"):
        try:
            result[proto + "Ports"] = len(portlib.parse_spec(str(body.get(proto + "Ports", "")), proto))
        except portlib.PortSpecError as e:
            errors.append("{}Ports: {}".format(proto, e))
    result["errors"] = errors
    return result


CSV_COLUMNS = ["scan", "ip", "mac", "vendor", "names", "host_rating", "proto", "port", "state", "service",
               "details", "port_rating"]


def export_csv(result):
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for h in result.get("hosts", []):
        names = " ".join(n["name"] for n in h.get("names", []))
        base = [result.get("id", ""), h["ip"], h.get("mac", ""), h.get("vendor", ""), names, h.get("rating", "")]
        ports = h.get("ports", [])
        if not ports:
            writer.writerow(base + ["", "", "", "", "", ""])
        for p in ports:
            writer.writerow(base + [p["proto"], p["port"], p.get("state", "open"),
                                    p.get("service") or p.get("name") or "", probes.product_line(p),
                                    p.get("rating", "")])
    return ("\ufeff" + out.getvalue()).encode("utf-8")   # BOM: Excel opens UTF-8 correctly


def _make_handler(store, datalayer, manager):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = config.SNAP_NAME
        sys_version = ""

        # -- plumbing -----------------------------------------------------
        def log_message(self, fmt, *args):  # route http.server logs to DEBUG
            log.debug("%s - " + fmt, self.command, *args)

        def _send_bytes(self, status, data, ctype, filename=None):
            self.send_response(status)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            if filename:
                self.send_header("Content-Disposition", 'attachment; filename="{}"'.format(filename))
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, status, body):
            data = b"" if body is None else json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Cache-Control", "no-store")
            if body is not None:
                self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if data:
                self.wfile.write(data)

        def _problem(self, status, title, detail=None, errors=None):
            body = {"type": "about:blank", "title": title, "status": status}
            if detail:
                body["detail"] = detail
            if errors:
                body["errors"] = errors
            self._send_json(status, body)

        def _read_body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                raise ValueError("request body too large")
            return self.rfile.read(length) if length > 0 else b""

        def _read_json(self):
            raw = self._read_body()
            try:
                return json.loads(raw.decode("utf-8") or "null")
            except (ValueError, UnicodeError):
                raise ValueError("body is not valid JSON")

        def _path(self):
            return unquote(urlsplit(self.path).path)

        # -- dispatch -----------------------------------------------------
        def do_GET(self):
            self._dispatch("GET")

        def do_PUT(self):
            self._dispatch("PUT")

        def do_POST(self):
            self._dispatch("POST")

        def do_DELETE(self):
            self._dispatch("DELETE")

        def _dispatch(self, method):
            path = self._path()
            try:
                if path.startswith(config.API_PREFIX + "/") or path == config.API_PREFIX:
                    self._api(method, path[len(config.API_PREFIX):])
                elif path == config.URL_PREFIX:
                    self.send_response(301)
                    self.send_header("Location", config.URL_PREFIX + "/")
                    self.end_headers()
                elif path.startswith(config.URL_PREFIX + dev_feedback.ROUTE) and dev_feedback.enabled():
                    self._dev_feedback(method, path[len(config.URL_PREFIX + dev_feedback.ROUTE):])
                elif path.startswith(config.URL_PREFIX + "/") and method == "GET":
                    self._static(path[len(config.URL_PREFIX) + 1:])
                else:
                    self._problem(404, "Not found")
            except auth.AuthError as exc:
                self._problem(exc.status, exc.title)
            except ValueError as exc:
                self._problem(400, "Bad request", str(exc))
            except Exception:
                log.exception("Unhandled error for %s %s", method, path)
                self._problem(500, "Internal error")

        # -- static files -------------------------------------------------
        def _static(self, rel):
            rel = rel or "index.html"
            base = os.path.realpath(config.WWW_DIR)
            full = os.path.realpath(os.path.join(base, rel))
            ext = os.path.splitext(full)[1].lower()
            if not full.startswith(base + os.sep) or ext not in STATIC_TYPES or not os.path.isfile(full):
                self._problem(404, "Not found")
                return
            with open(full, "rb") as f:
                data = f.read()
            if ext == ".html" and dev_feedback.enabled():
                data = dev_feedback.inject(data)
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ext in (".js", ".json", ".svg"):
                ctype += "; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        # -- dev only: UI feedback overlay (dev_feedback.py) ----------------
        def _dev_feedback(self, method, rest):
            if rest == "feedback.js" and method == "GET":
                try:
                    with open(config.DEV_FEEDBACK_JS, "rb") as f:
                        data = f.read()
                except OSError as exc:
                    return self._problem(404, "Overlay script not found", str(exc))
                self.send_response(200)
                self.send_header("Content-Type", "text/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return self.wfile.write(data)
            if rest == "feedback" and method == "GET":
                return self._send_json(200, dev_feedback.load())
            if rest == "feedback" and method == "POST":
                return self._send_json(201, dev_feedback.add(self._read_json()))
            if rest.startswith("feedback/") and method == "DELETE":
                try:
                    item_id = int(rest[len("feedback/"):])
                except ValueError:
                    return self._problem(404, "Not found")
                if not dev_feedback.remove(item_id):
                    return self._problem(404, "No such note")
                return self._send_json(204, None)
            self._problem(404, "Not found")

        # -- API ----------------------------------------------------------
        def _api(self, method, route):
            if route == "/appdata/save" and method == "POST":
                auth.require(self.headers, auth.CONFIGURE)
                status, body = appdata_commands.handle_save(self._read_body(), store)
                return self._send_json(status, body)
            if route == "/appdata/load" and method == "POST":
                auth.require(self.headers, auth.CONFIGURE)
                status, body = appdata_commands.handle_load(self._read_body(), store)
                return self._send_json(status, body)

            if route == "/status" and method == "GET":
                scopes = auth.require(self.headers, auth.VIEW)
                last = manager.last()
                return self._send_json(200, {
                    "app": config.SNAP_NAME,
                    "title": config.APP_TITLE,
                    "version": config.app_version(),
                    "uptimeSeconds": int(time.monotonic() - _START_TIME),
                    "settings": store.status(),
                    "datalayer": datalayer.status(),
                    "user": auth.user_name(self.headers),
                    "role": auth.role_of(scopes),           # view | operate | configure
                    "canOperate": auth.allows(scopes, auth.OPERATE),
                    "canConfigure": auth.allows(scopes, auth.CONFIGURE),
                    "maxAddresses": store.get()["scan"]["maxAddresses"],   # the run card's estimate (settings: configure)
                    "style": store.get().get("style"),      # page colors for every role (kit.js Kit.applyStyle)
                    "about": {"author": config.APP_AUTHOR, "url": config.APP_URL,       # help menu -> About
                              "license": config.APP_LICENSE, "ai": config.APP_AI_NOTE},
                    "user": auth.user_name(self.headers),
                    "scan": manager.current(),
                    "nextRuns": manager.next_runs(),
                    "history": manager.history.usage(),
                    "last": None if last is None else {
                        "id": last["id"], "status": last["status"], "profile": last["profile"].get("id"),
                        "profileName": last["profile"].get("name"), "finishedAt": last.get("finishedAt"),
                        "summary": last["summary"], "changes": last.get("changes")},
                })
            if route == "/network" and method == "GET":
                auth.require(self.headers, auth.VIEW)
                return self._send_json(200, netinfo.snapshot())
            if route == "/estimate" and method == "POST":
                auth.require(self.headers, auth.VIEW)
                try:
                    nets = netinfo.local_networks(netinfo.interfaces())
                except (netinfo.NetinfoError, OSError):
                    nets = []
                return self._send_json(200, estimate(self._read_json(), nets))

            if route == "/scans" and method == "POST":
                auth.require(self.headers, auth.OPERATE)
                body = self._read_json()
                profile_id = body.get("profile") if isinstance(body, dict) else None
                if not isinstance(profile_id, str):
                    return self._problem(400, "Body must be {\"profile\": \"<profile id>\"}")
                try:
                    scan_id = manager.start(profile_id, trigger="manual", user=auth.user_name(self.headers))
                except UnknownProfileError as exc:
                    return self._problem(404, "Unknown profile", str(exc))
                except BusyError as exc:
                    return self._problem(409, "Scan running", str(exc))
                return self._send_json(202, {"id": scan_id})
            if route == "/scans" and method == "GET":
                auth.require(self.headers, auth.VIEW)
                return self._send_json(200, {"scans": manager.history.list(), "usage": manager.history.usage()})
            if route == "/scans/current" and method == "GET":
                auth.require(self.headers, auth.VIEW)
                query = parse_qs(urlsplit(self.path).query)
                return self._send_json(200, manager.current(with_hosts=query.get("hosts") == ["1"]))
            if route == "/scans/current/abort" and method == "POST":
                auth.require(self.headers, auth.OPERATE)
                if not manager.abort():
                    return self._problem(409, "No scan running")
                log.info("Scan abort requested via web UI by %s", auth.user_name(self.headers) or "?")
                return self._send_json(200, manager.current())
            if route.startswith("/scans/"):
                parts = route[len("/scans/"):].split("/")
                scan_id = parts[0]
                if not ID_PATTERN.fullmatch(scan_id) or len(parts) > 2:
                    return self._problem(404, "Unknown scan")
                if method == "DELETE" and len(parts) == 1:
                    auth.require(self.headers, auth.OPERATE)
                    if not manager.history.delete(scan_id):
                        return self._problem(404, "Unknown scan")
                    log.info("Scan %s deleted by %s", scan_id, auth.user_name(self.headers) or "?")
                    return self._send_json(200, {"deleted": scan_id})
                if method == "GET":
                    auth.require(self.headers, auth.VIEW)
                    result = manager.rated(scan_id)
                    if result is None:
                        return self._problem(404, "Unknown scan")
                    if len(parts) == 1:
                        return self._send_json(200, result)
                    if parts[1] == "report.pdf":
                        data = report_pdf.build(result, config.app_version())
                        return self._send_bytes(200, data, "application/pdf", "network-scan-{}.pdf".format(scan_id))
                    if parts[1] == "export.json":
                        data = json.dumps(result, indent=1).encode("utf-8")
                        return self._send_bytes(200, data, "application/json; charset=utf-8",
                                                "network-scan-{}.json".format(scan_id))
                    if parts[1] == "export.csv":
                        return self._send_bytes(200, export_csv(result), "text/csv; charset=utf-8",
                                                "network-scan-{}.csv".format(scan_id))
                return self._problem(404, "Unknown API route")

            if route == "/profiles" and method == "GET":    # the run card needs them; the settings are configure-only
                auth.require(self.headers, auth.VIEW)
                return self._send_json(200, store.get_redacted()["profiles"])
            if route == "/settings" and method == "GET":
                auth.require(self.headers, auth.CONFIGURE)
                return self._send_json(200, store.get_redacted())
            if route == "/settings" and method == "PUT":
                auth.require(self.headers, auth.CONFIGURE)
                try:
                    store.update(self._read_json())
                except SettingsError as exc:
                    return self._problem(400, "Invalid settings", errors=exc.errors)
                log.info("Settings changed via web UI")
                return self._send_json(200, store.get_redacted())
            if route == "/settings/meta" and method == "GET":
                auth.require(self.headers, auth.CONFIGURE)
                return self._send_json(200, {"defaults": store.defaults(), "rules": store.rules(),
                                                 "types": store.types()})
            if route == "/settings/reset" and method == "POST":
                auth.require(self.headers, auth.CONFIGURE)
                store.reset_to_defaults(persist=True)
                return self._send_json(200, store.get_redacted())

            if route == "/values" and method == "GET":
                scopes = auth.require(self.headers, auth.VIEW)
                values = {}
                for name, entry in datalayer.registry.snapshot().items():
                    if auth.allows(scopes, entry.pop("viewRole")):
                        write_role = entry.pop("writeRole")
                        entry["canWrite"] = entry["writable"] and auth.allows(scopes, write_role)
                        values[name] = entry
                return self._send_json(200, values)
            if route.startswith("/values/") and method == "PUT":
                auth.require(self.headers, auth.VIEW)
                name = route[len("/values/"):]
                spec = datalayer.registry.spec(name)
                if spec is None:
                    return self._problem(404, "Write rejected", "unknown node " + name)
                auth.require(self.headers, spec.write_role)
                body = self._read_json()
                if not isinstance(body, dict) or "value" not in body:
                    return self._problem(400, "Body must be {\"value\": ...}")
                try:
                    value = datalayer.registry.write(name, body["value"])
                except NodeWriteError as exc:
                    status = 404 if exc.reason == "unknown" else 400
                    return self._problem(status, "Write rejected", str(exc))
                return self._send_json(200, {"name": name, "value": value})

            self._problem(404, "Unknown API route")

    return Handler
