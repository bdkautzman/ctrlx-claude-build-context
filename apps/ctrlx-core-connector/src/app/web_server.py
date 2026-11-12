"""HTTP server behind the ctrlX reverse proxy (stdlib only, no web framework).

In the snap it listens on the Unix socket
    $SNAP_DATA/package-run/<snap>/web.sock
which the reverse proxy reaches via the package-manifest proxyMapping "binding".
Outside the snap (development) it listens on http://127.0.0.1:8080 with the same paths.

The proxy forwards the FULL path, so every route starts with config.URL_PREFIX.

Routes
  GET  /<snap>/                      index.html (static, unrestricted, no data!)
  GET  /<snap>/<file>                static assets from www/
  GET  /<snap>/api/v1/status         read
  GET  /<snap>/api/v1/settings       read
  GET  /<snap>/api/v1/settings/meta  read   (defaults + rules for the UI)
  PUT  /<snap>/api/v1/settings       write  (validate, persist to app data, apply)
  POST /<snap>/api/v1/settings/reset write
  GET  /<snap>/api/v1/values         read   (Data Layer nodes of this app)
  GET  /<snap>/api/v1/cores          read   (this device + configured cores, live status, no passwords)
  POST /<snap>/api/v1/cores          write  body {name, host[:port], user, password, skipCertificateCheck}
  DELETE /<snap>/api/v1/cores/<name> write
  Data Layer browser, <id> = core name or "_local" (this device), paths relative to the core's root:
  GET  /<snap>/api/v1/cores/<id>/children?path=P   read   child nodes with metadata and simple values
  GET  /<snap>/api/v1/cores/<id>/node?path=P       read   metadata + value (also objects)
  GET  /<snap>/api/v1/cores/<id>/value?path=P      read   value only (live refresh)
  POST /<snap>/api/v1/cores/<id>/values            read   body {"paths": [...]} values of tree rows
  PUT  /<snap>/api/v1/cores/<id>/node?path=P       write  body {"value": ...}
  GET  /<snap>/api/v1/cores/<id>/kvd               read   KVD running? addresses defined in its nodes.json
  POST /<snap>/api/v1/cores/<id>/kvd/nodes         write  body {address, nodeClass, type, value, unit, description, readOnly}
  DELETE /<snap>/api/v1/cores/<id>/kvd/nodes?address=A   write
  PUT  /<snap>/api/v1/values/<name>  write  body {"value": ...}
  POST /<snap>/api/v1/appdata/save   Solutions save command
  POST /<snap>/api/v1/appdata/load   Solutions load command
All /api paths are "restricted" in the manifest -> the proxy validates the token.
Dev only (run-local.sh, never in the snap): /<snap>/__dev/feedback[.js] for the UI feedback overlay, dev_feedback.py.
"""

import http.server
import json
import logging
import mimetypes
import os
import re
import socketserver
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit

from app import appdata_commands, auth, config, dev_feedback
from app.datalayer_nodes import NodeWriteError
from app.kvd import KvdError
from app.monitor import DataLayerError, _explain, remote_base
from app.settings_store import SettingsError

log = logging.getLogger("web")

MAX_BODY_BYTES = 1024 * 1024
STATIC_TYPES = {".html", ".js", ".css", ".svg", ".png", ".ico", ".json", ".woff2", ".map"}
_START_TIME = time.monotonic()
# Data Layer result -> HTTP status of the browser API (never 401: the UI reads that as "token expired")
DL_HTTP_STATUS = {"INVALID_ADDRESS": 404, "PERMISSION_DENIED": 403, "SEC_UNAUTHORIZED": 403,
                  "TYPE_MISMATCH": 400, "INVALID_VALUE": 400, "LIMIT_MIN": 400, "LIMIT_MAX": 400,
                  "SIZE_MISMATCH": 400, "INVALID_FLOATINGPOINT": 400, "UNSUPPORTED": 400,
                  "CLIENT_NOT_CONNECTED": 503}


class _UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def get_request(self):
        request, _ = super().get_request()
        return request, ("local", 0)  # BaseHTTPRequestHandler expects a (host, port) tuple


class _TCPHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True


class WebServer:
    def __init__(self, store, datalayer, monitor, browser, kvd):
        self.store = store
        self.datalayer = datalayer
        self.monitor = monitor
        self.browser = browser
        self.kvd = kvd
        self._server = None
        self._thread = None

    def start(self):
        handler = _make_handler(self.store, self.datalayer, self.monitor, self.browser, self.kvd)
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


def _make_handler(store, datalayer, monitor, browser, kvd):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = config.SNAP_NAME
        sys_version = ""

        # -- plumbing -----------------------------------------------------
        def log_message(self, fmt, *args):  # route http.server logs to DEBUG
            log.debug("%s - " + fmt, self.command, *args)

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

        def _query(self, name, default=""):
            values = parse_qs(urlsplit(self.path).query, keep_blank_values=True).get(name)
            return values[0] if values else default

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
            except DataLayerError as exc:
                detail = str(exc) if str(exc) != exc.result else _explain(exc.result)
                self._problem(DL_HTTP_STATUS.get(exc.result, 502), "Data Layer: " + exc.result,
                              detail if detail != exc.result else None)
            except KvdError as exc:
                self._problem(exc.status, exc.title, errors=exc.errors)
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
                    "browserRefreshMs": store.get()["browser"]["refreshMs"],   # browse.html (settings: configure)
                    "style": store.get().get("style"),      # page colors for every role (kit.js Kit.applyStyle)
                    "about": {"author": config.APP_AUTHOR, "url": config.APP_URL,       # help menu -> About
                              "license": config.APP_LICENSE, "ai": config.APP_AI_NOTE},
                })

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

            if route == "/cores" and method == "GET":
                auth.require(self.headers, auth.VIEW)
                return self._send_json(200, [monitor.local_status()] + monitor.snapshot())
            if route == "/cores" and method == "POST":
                auth.require(self.headers, auth.CONFIGURE)
                status, body = add_core(store, self._read_json())
                return self._send_json(status, body)
            parts = route.strip("/").split("/")
            if len(parts) == 2 and parts[0] == "cores" and method == "DELETE":
                auth.require(self.headers, auth.CONFIGURE)
                if parts[1] == config.LOCAL_ID:
                    return self._problem(400, "This device can't be removed")
                status, body = delete_core(store, parts[1])
                return self._send_json(status, body)
            if len(parts) >= 3 and parts[0] == "cores":
                return self._core_api(method, parts[1], "/".join(parts[2:]))

            self._problem(404, "Unknown API route")

        def _core_api(self, method, core_id, rest):
            """Data Layer browser and KVD editor of one core."""
            # roles (user's split 2026-11-10): writing a value on a core = operate, KVD node add/delete = configure
            if method == "PUT":
                needed = auth.OPERATE
            elif method == "DELETE" or (method == "POST" and rest != "values"):
                needed = auth.CONFIGURE
            else:
                needed = auth.VIEW
            auth.require(self.headers, needed)
            if core_id == config.LOCAL_ID:
                base, core = "", None
            else:
                core = monitor.core(core_id)
                if core is None:
                    return self._problem(404, "No core named " + core_id)
                base = remote_base(core_id)
            path = self._query("path")
            if rest == "children" and method == "GET":
                return self._send_json(200, browser.children(base, path))
            if rest == "node" and method == "GET":
                return self._send_json(200, browser.node(base, path))
            if rest == "value" and method == "GET":
                return self._send_json(200, browser.value(base, path))
            if rest == "values" and method == "POST":
                body = self._read_json()
                paths = body.get("paths") if isinstance(body, dict) else None
                if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
                    return self._problem(400, 'Body must be {"paths": [...]}')
                return self._send_json(200, browser.values(base, paths))
            if rest == "node" and method == "PUT":
                result = browser.write(base, path, self._read_json())
                log.info("Data Layer write via web UI: %s %s", core_id, path)
                return self._send_json(200, result)
            if rest == "kvd" and method == "GET":
                return self._send_json(200, kvd.status(base, core))
            if rest == "kvd/nodes" and method == "POST":
                return self._send_json(201, kvd.add(base, core, self._read_json(), core_id))
            if rest == "kvd/nodes" and method == "DELETE":
                return self._send_json(200, kvd.remove(base, core, self._query("address"), core_id))
            self._problem(404, "Unknown API route")

    return Handler


def _problem_body(status, title, errors=None):
    body = {"type": "about:blank", "title": title, "status": status}
    if errors:
        body["errors"] = errors
    return body


HOST_PORT_RE = re.compile(r"(?i:https?://)?([^/:]*)(?::(\d{1,5}))?/?")


def split_host_port(entry):
    """The dialog's "IP address / host name" may carry the HTTPS port ("192.168.1.10:8443", also a pasted
    "https://host:port/"): split it into host + sslPort. Without a port, sslPort stays as sent (default 443)."""
    host = entry.get("host")
    match = HOST_PORT_RE.fullmatch(host.strip()) if isinstance(host, str) else None
    if not match:
        return entry   # left to the settings rules ("host: invalid format")
    entry = dict(entry, host=match.group(1))
    if match.group(2):
        entry["sslPort"] = int(match.group(2))
    return entry


def add_core(store, entry):
    """Append a core connection to settings["cores"] (validated + persisted by the store).
    Returns (status, body). Errors name the dialog fields ("host: invalid format")."""
    if not isinstance(entry, dict):
        return 400, _problem_body(400, "Body must be a JSON object")
    entry = split_host_port(entry)
    settings = store.get()
    name = entry.get("name")
    if isinstance(name, str) and any(str(c.get("name", "")).lower() == name.lower() for c in settings["cores"]):
        return 409, _problem_body(409, "A core with this name exists", ["name: already used"])
    settings["cores"].append(entry)
    index = len(settings["cores"]) - 1
    try:
        store.update(settings)
    except SettingsError as exc:
        prefix = "cores[{}].".format(index)
        errors = [e[len(prefix):] if e.startswith(prefix) else e for e in exc.errors]
        # the port is part of the host field in the dialog
        errors = ["host: port must be 1-65535" if e.startswith("sslPort:") else e for e in errors]
        return 400, _problem_body(400, "Invalid connection", errors)
    log.info("Core %s added via web UI", entry.get("name"))
    added = [c for c in store.get_redacted()["cores"] if c["name"] == entry["name"]]
    return 201, added[0]


def delete_core(store, name):
    settings = store.get()
    remaining = [c for c in settings["cores"] if c.get("name") != name]
    if len(remaining) == len(settings["cores"]):
        return 404, _problem_body(404, "No core named " + name)
    settings["cores"] = remaining
    try:
        store.update(settings)
    except SettingsError as exc:
        return 400, _problem_body(400, "Invalid settings", exc.errors)
    log.info("Core %s removed via web UI", name)
    return 200, {"deleted": name}
