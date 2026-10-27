"""HTTP server behind the ctrlX reverse proxy (stdlib only, no web framework).

In the snap it listens on the Unix socket
    $SNAP_DATA/package-run/<snap>/web.sock
which the reverse proxy reaches via the package-manifest proxyMapping "binding".
Outside the snap (development) it listens on http://127.0.0.1:8080 with the same paths.

The proxy forwards the FULL path, so every route starts with config.URL_PREFIX.

Routes (times: epoch seconds; from/to default: the last hour)
  GET  /<snap>/                      index.html (static, unrestricted, no data!)
  GET  /<snap>/<file>                static assets from www/
  GET  /<snap>/api/v1/status         read
  GET  /<snap>/api/v1/settings       read
  GET  /<snap>/api/v1/settings/meta  read   (defaults + rules for the UI)
  PUT  /<snap>/api/v1/settings       write  (validate, persist to app data, apply)
  POST /<snap>/api/v1/settings/reset write
  GET  /<snap>/api/v1/overview       read   system + every app now (no process lists)
  GET  /<snap>/api/v1/group?key=     read   one app with its processes and threads now
  GET  /<snap>/api/v1/series?ids=a,b&from=&to=&points=   read   time series (min/avg/max)
  GET  /<snap>/api/v1/analysis?from=&to=&level=apps|processes&metric=&app=   read   leak table
  GET  /<snap>/api/v1/events?from=&to=  read;  POST {"text": ...} write: add a marker
  GET  /<snap>/api/v1/media          read   mounted external media + recording state
  GET  /<snap>/api/v1/export?from=&to=&level=system|apps|processes|threads&format=csv|json   read
  GET  /<snap>/api/v1/values         read   (Data Layer nodes of this app)
  PUT  /<snap>/api/v1/values/<name>  write  body {"value": ...}
  POST /<snap>/api/v1/appdata/save   Solutions save command
  POST /<snap>/api/v1/appdata/load   Solutions load command
All /api paths are "restricted" in the manifest -> the proxy validates the token.
"""

import http.server
import json
import logging
import mimetypes
import os
import socketserver
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit

from app import appdata_commands, auth, config, medium
from app.datalayer_nodes import NodeWriteError
from app.settings_store import SettingsError

log = logging.getLogger("web")

MAX_BODY_BYTES = 1024 * 1024
MAX_RANGE_S = 400 * 86400
MAX_IDS = 200
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
    def __init__(self, store, datalayer, services):
        """services: object with collector, history, queries and recorder (app.main.Services)."""
        self.store = store
        self.datalayer = datalayer
        self.services = services
        self._server = None
        self._thread = None

    def start(self):
        handler = _make_handler(self.store, self.datalayer, self.services)
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


def _range(query):
    """from/to/points of a query string -> (t0, t1); ValueError for nonsense."""
    now = time.time()
    try:
        t1 = float(query.get("to", [now])[0])
        t0 = float(query.get("from", [t1 - 3600])[0])
    except ValueError:
        raise ValueError("from/to must be epoch seconds")
    if not t0 < t1 or t1 - t0 > MAX_RANGE_S:
        raise ValueError("from must be before to, at most 400 days apart")
    return t0, t1


def _int_param(query, name, default, lo, hi):
    try:
        return min(max(int(query.get(name, [default])[0]), lo), hi)
    except ValueError:
        raise ValueError(name + " must be an integer")


def _overview(snap):
    if snap is None:
        return None
    apps = [{k: v for k, v in g.items() if k != "processList"} for g in snap["groups"].values()]
    return {"time": snap["time"], "interval": snap["interval"], "system": snap["system"],
            "counts": snap["counts"], "apps": apps}


def _make_handler(store, datalayer, services):
    collector = services.collector
    queries = services.queries
    recorder = services.recorder

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

        def _query(self):
            return parse_qs(urlsplit(self.path).query)

        def _send_bytes(self, status, data, ctype, filename=None):
            self.send_response(status)
            self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text/", "application/json")) else ""))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            if filename:
                self.send_header("Content-Disposition", 'attachment; filename="{}"'.format(filename))
            self.end_headers()
            self.wfile.write(data)

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
                elif path.startswith(config.URL_PREFIX + "/") and method == "GET":
                    self._static(path[len(config.URL_PREFIX) + 1:])
                else:
                    self._problem(404, "Not found")
            except auth.AuthError as exc:
                self._problem(exc.status, exc.title)
            except (BrokenPipeError, ConnectionResetError):
                log.debug("client went away: %s %s", method, path)
            except ValueError as exc:     # also QueryError
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

        # -- API ----------------------------------------------------------
        def _api(self, method, route):
            if route == "/appdata/save" and method == "POST":
                auth.require(self.headers, write=True)
                status, body = appdata_commands.handle_save(self._read_body(), store)
                return self._send_json(status, body)
            if route == "/appdata/load" and method == "POST":
                auth.require(self.headers, write=True)
                status, body = appdata_commands.handle_load(self._read_body(), store)
                return self._send_json(status, body)

            if route == "/status" and method == "GET":
                scopes = auth.require(self.headers, write=False)
                return self._send_json(200, {
                    "app": config.SNAP_NAME,
                    "title": config.APP_TITLE,
                    "version": config.app_version(),
                    "uptimeSeconds": int(time.monotonic() - _START_TIME),
                    "settings": store.status(),
                    "datalayer": datalayer.status(),
                    "collector": collector.status(),
                    "history": services.history.usage(),
                    "recording": recorder.status(),
                    "canWrite": auth.can_write(scopes),
                })

            query = self._query()
            if route == "/overview" and method == "GET":
                auth.require(self.headers, write=False)
                return self._send_json(200, _overview(collector.snapshot()) or {"apps": [], "system": None})
            if route == "/group" and method == "GET":
                auth.require(self.headers, write=False)
                snap = collector.snapshot()
                group = snap and snap["groups"].get(query.get("key", [""])[0])
                if not group:
                    return self._problem(404, "Unknown app", "no process of this app is running now")
                return self._send_json(200, dict(group, time=snap["time"]))
            if route == "/series" and method == "GET":
                auth.require(self.headers, write=False)
                ids = [i for i in query.get("ids", [""])[0].split(",") if i][:MAX_IDS]
                if not ids:
                    return self._problem(400, "Bad request", "ids is empty")
                t0, t1 = _range(query)
                data = queries.series(ids, t0, t1, _int_param(query, "points", 800, 10, 5000))
                data["series"] = {sid: {"min": c[0], "avg": c[1], "max": c[2]} for sid, c in data["series"].items()}
                return self._send_json(200, data)
            if route == "/analysis" and method == "GET":
                auth.require(self.headers, write=False)
                t0, t1 = _range(query)
                return self._send_json(200, queries.analysis(
                    t0, t1, query.get("level", ["apps"])[0], query.get("metric", [None])[0] or None,
                    query.get("app", [None])[0] or None))
            if route == "/events" and method == "GET":
                auth.require(self.headers, write=False)
                t0, t1 = _range(query)
                return self._send_json(200, {"events": queries.events(t0, t1)})
            if route == "/events" and method == "POST":
                auth.require(self.headers, write=True)
                body = self._read_json()
                if not isinstance(body, dict) or not isinstance(body.get("text"), str):
                    return self._problem(400, "Body must be {\"text\": ...}")
                return self._send_json(200, queries.add_marker(body["text"]))
            if route == "/media" and method == "GET":
                auth.require(self.headers, write=False)
                media, error = medium.list_media(recorder.root, recorder.require_mount)
                return self._send_json(200, {"media": media, "error": error, "recording": recorder.status()})
            if route == "/export" and method == "GET":
                auth.require(self.headers, write=False)
                t0, t1 = _range(query)
                level = query.get("level", ["apps"])[0]
                fmt = query.get("format", ["csv"])[0]
                data, ctype = queries.export(t0, t1, level, fmt)
                stamp = time.strftime("%Y%m%d-%H%M", time.localtime(t0))
                return self._send_bytes(200, data, ctype, "system-analyzer_{}_{}.{}".format(level, stamp, fmt))

            if route == "/settings" and method == "GET":
                auth.require(self.headers, write=False)
                return self._send_json(200, store.get_redacted())
            if route == "/settings" and method == "PUT":
                auth.require(self.headers, write=True)
                try:
                    store.update(self._read_json())
                except SettingsError as exc:
                    return self._problem(400, "Invalid settings", errors=exc.errors)
                log.info("Settings changed via web UI")
                return self._send_json(200, store.get_redacted())
            if route == "/settings/meta" and method == "GET":
                auth.require(self.headers, write=False)
                return self._send_json(200, {"defaults": store.defaults(), "rules": store.rules(),
                                                 "types": store.types()})
            if route == "/settings/reset" and method == "POST":
                auth.require(self.headers, write=True)
                store.reset_to_defaults(persist=True)
                return self._send_json(200, store.get_redacted())

            if route == "/values" and method == "GET":
                auth.require(self.headers, write=False)
                return self._send_json(200, datalayer.registry.snapshot())
            if route.startswith("/values/") and method == "PUT":
                auth.require(self.headers, write=True)
                name = route[len("/values/"):]
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
