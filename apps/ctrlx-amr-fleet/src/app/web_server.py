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
  GET  /<snap>/api/v1/settings       write  (read-only users get no settings, only /view-config)
  GET  /<snap>/api/v1/settings/meta  write  (defaults + rules for the UI)
  PUT  /<snap>/api/v1/settings       write  (validate, persist to app data, apply)
  POST /<snap>/api/v1/settings/reset write
  GET  /<snap>/api/v1/values         write  (Data Layer nodes of this app, incl. settings/active)
  PUT  /<snap>/api/v1/values/<name>  write  body {"value": ...}
  GET  /<snap>/api/v1/view-config    read   what the map view needs: style colors, units, AMR size, refresh, paths
  GET  /<snap>/api/v1/fleet[?since=<revision>]   read  AMRs; only status if the revision is unchanged
  GET  /<snap>/api/v1/fleet/path?id=<id>          read  driven path of one AMR: points [x, y, age s]
  GET  /<snap>/api/v1/map            read   active map: file, error, cell size, extent, tile levels
  GET  /<snap>/api/v1/map/tiles/<map id>/<level>/<x>/<y>.png[?color=rrggbb]   read  map tile
  POST /<snap>/api/v1/appdata/save   Solutions save command
  POST /<snap>/api/v1/appdata/load   Solutions load command
All /api paths are "restricted" in the manifest -> the proxy validates the token.
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

from app import appdata_commands, auth, config
from app.fleet import PATH_STEP_M
from app.datalayer_nodes import NodeWriteError
from app.settings_store import SettingsError

log = logging.getLogger("web")

MAX_BODY_BYTES = 1024 * 1024
STATIC_TYPES = {".html", ".js", ".css", ".svg", ".png", ".ico", ".json", ".woff2", ".map"}
_START_TIME = time.monotonic()
_TILE_ROUTE = re.compile(r"/map/tiles/([0-9a-f]{12})/(\d{1,2})/(\d{1,6})/(\d{1,6})\.png")
_COLOR = re.compile(r"[0-9a-fA-F]{6}")
DEFAULT_TILE_COLOR = "333d4a"


class _UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def get_request(self):
        request, _ = super().get_request()
        return request, ("local", 0)  # BaseHTTPRequestHandler expects a (host, port) tuple


class _TCPHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True


class WebServer:
    def __init__(self, store, datalayer, fleet, maps):
        self.store = store
        self.datalayer = datalayer
        self.fleet = fleet
        self.maps = maps
        self._server = None
        self._thread = None

    def start(self):
        handler = _make_handler(self.store, self.datalayer, self.fleet, self.maps)
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


def _make_handler(store, datalayer, fleet, maps):
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

        def _send_bytes(self, data, content_type, cache_control):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", cache_control)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
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
            """Query parameters, first value of each."""
            return {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}

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
                    "canWrite": auth.can_write(scopes),
                })

            # settings (and the raw node list with settings/active) only for users who may change them:
            # read-only users don't see the settings at all; the view gets what it needs from /view-config
            if route == "/settings" and method == "GET":
                auth.require(self.headers, write=True)
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
                auth.require(self.headers, write=True)
                return self._send_json(200, {"defaults": store.defaults(), "rules": store.rules(),
                                                 "types": store.types()})
            if route == "/settings/reset" and method == "POST":
                auth.require(self.headers, write=True)
                store.reset_to_defaults(persist=True)
                return self._send_json(200, store.get_redacted())

            if route == "/values" and method == "GET":
                auth.require(self.headers, write=True)
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

            if route == "/view-config" and method == "GET":
                auth.require(self.headers, write=False)
                settings = store.get()
                return self._send_json(200, {
                    "style": settings["style"],
                    "unitsPerMeter": settings["map"]["unitsPerMeter"],
                    "amrLengthM": settings["fleet"]["amrLengthM"],
                    "amrWidthM": settings["fleet"]["amrWidthM"],
                    "staleAfterMs": settings["fleet"]["staleAfterMs"],
                    "refreshIntervalMs": settings["display"]["refreshIntervalMs"],
                    "pathHistoryMinutes": settings["fleet"]["pathHistoryMinutes"],
                    "pathStepM": PATH_STEP_M,
                })
            if route == "/fleet" and method == "GET":
                auth.require(self.headers, write=False)
                stale_after_s = store.get()["fleet"]["staleAfterMs"] / 1000.0
                snap = fleet.snapshot(stale_after_s, include_amrs=False)
                if self._query().get("since") == str(snap["revision"]):
                    snap["unchanged"] = True
                else:
                    snap = fleet.snapshot(stale_after_s)
                occ_map = maps.current()
                snap["mapId"] = occ_map.id if occ_map is not None else None
                return self._send_json(200, snap)
            if route == "/fleet/path" and method == "GET":
                auth.require(self.headers, write=False)
                path = fleet.path(self._query().get("id", ""))
                if path is None:
                    return self._problem(404, "No such AMR")
                return self._send_json(200, path)
            if route == "/map" and method == "GET":
                auth.require(self.headers, write=False)
                info = maps.info()
                info["unitsPerMeter"] = store.get()["map"]["unitsPerMeter"]
                return self._send_json(200, info)
            tile = _TILE_ROUTE.fullmatch(route)
            if tile and method == "GET":
                auth.require(self.headers, write=False)
                color = self._query().get("color", DEFAULT_TILE_COLOR)
                if not _COLOR.fullmatch(color):
                    return self._problem(400, "color must be 6 hex digits (rrggbb)")
                occ_map = maps.current()
                if occ_map is None or occ_map.id != tile.group(1):
                    return self._problem(404, "Map changed or not loaded")
                level, tx, ty = (int(tile.group(i)) for i in (2, 3, 4))
                png = occ_map.tile_png(level, tx, ty, bytes.fromhex(color))
                if png is None:
                    return self._problem(404, "No such tile")
                # the URL contains the map id (content hash) -> the tile never changes
                return self._send_bytes(png, "image/png", "private, max-age=86400")

            self._problem(404, "Unknown API route")

    return Handler
