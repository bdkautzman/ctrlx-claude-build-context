"""HTTP server behind the ctrlX reverse proxy (stdlib only, no web framework).

In the snap it listens on the Unix socket
    $SNAP_DATA/package-run/<snap>/web.sock
which the reverse proxy reaches via the package-manifest proxyMapping "binding".
Outside the snap (development) it listens on http://127.0.0.1:8080 with the same paths.

The proxy forwards the FULL path, so every route starts with config.URL_PREFIX.

Routes (role = the lowest role that may call it, auth.py: view < operate < configure)
  GET  /<snap>/                      index.html (static, unrestricted, no data!)
  GET  /<snap>/<file>                static assets from www/ (kit.js/kit.css: shared UI parts, widgets.js)
  GET  /<snap>/api/v1/status         view       (incl. the caller's role)
  GET  /<snap>/api/v1/values         view       (Data Layer nodes of this app the caller may see)
  PUT  /<snap>/api/v1/values/<name>  NodeSpec.write_role (default operate)  body {"value": ...}
  GET  /<snap>/api/v1/settings       configure
  GET  /<snap>/api/v1/settings/meta  configure  (defaults + rules for the settings form)
  PUT  /<snap>/api/v1/settings       configure  (validate, persist to app data, apply)
  POST /<snap>/api/v1/settings/reset configure
  POST /<snap>/api/v1/appdata/save   configure  Solutions save command
  POST /<snap>/api/v1/appdata/load   configure  Solutions load command
All /api paths are "restricted" in the manifest -> the proxy validates the token.
Dev only (run-local.sh, never in the snap): /<snap>/__dev/feedback[.js] for the UI feedback overlay, dev_feedback.py.

Helpers for your own routes (Handler): _query() (query parameters), _send_bytes() (binary data and file
downloads with a file name), _receive_file() (an upload streamed into the app data, never held in memory),
_read_json() / _send_json() / _problem(). Raise UploadError(status, text) to reject an upload.
"""

import http.server
import json
import logging
import mimetypes
import os
import socketserver
import threading
import time
from urllib.parse import parse_qs, quote, unquote, urlsplit

from app import appdata_commands, auth, config, dev_feedback
from app.datalayer_nodes import NodeWriteError
from app.settings_store import SettingsError

log = logging.getLogger("web")

MAX_BODY_BYTES = 1024 * 1024        # JSON bodies; uploads (_receive_file) have their own limit
CHUNK_BYTES = 256 * 1024
DRAIN_MAX_BYTES = 64 * 1024 * 1024  # read at most this much of a rejected upload, else close the connection
STATIC_TYPES = {".html", ".js", ".css", ".svg", ".png", ".ico", ".json", ".woff2", ".map"}
_START_TIME = time.monotonic()


class UploadError(Exception):
    """An upload that is rejected (status 400/409/413/...); the rest of the body is read before the answer."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class RequestBody:
    """A request body: read(n) returns at most the bytes still left of Content-Length."""

    def __init__(self, rfile, length):
        self._rfile, self.left = rfile, max(0, length)

    def read(self, n):
        data = self._rfile.read(min(n, self.left)) if self.left else b""
        self.left = self.left - len(data) if data else 0     # nothing more: the client is gone
        return data

    def drain(self, limit=DRAIN_MAX_BYTES):
        """Read the rest so the client gets our answer (http.server would otherwise close the socket and the
        client sees a reset, gotchas #91). False when more than `limit` is left: close the connection instead."""
        if self.left > limit:
            return False
        while self.left and self.read(CHUNK_BYTES):
            pass
        return True


def receive_file(body, path, max_bytes, check=None):
    """Stream a request body into <path>.tmp, then rename it to `path`; returns the size.

    Put uploads into the app data folder (config.APPDATA_DIR): the manifest's appPrivateFiles keeps the
    *.tmp file out of saved configurations. check(tmp_path) may look at the complete file first and raise
    UploadError to reject it. Any failure removes the .tmp file; the caller drains the body."""
    if body.left > max_bytes:
        raise UploadError(413, "larger than {} bytes".format(max_bytes))
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    try:
        size = 0
        with open(tmp, "wb") as f:
            while body.left:
                chunk = body.read(CHUNK_BYTES)
                if not chunk:
                    raise UploadError(400, "upload incomplete (connection closed)")
                f.write(chunk)
                size += len(chunk)
            f.flush()
            os.fsync(f.fileno())
        if check is not None:
            check(tmp)
        os.replace(tmp, path)
        return size
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def content_disposition(filename):
    """Content-Disposition for a download: ASCII name plus the UTF-8 name (RFC 6266)."""
    ascii_name = "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else "_" for c in filename)
    value = 'attachment; filename="{}"'.format(ascii_name)
    if ascii_name != filename:
        value += "; filename*=UTF-8''" + quote(filename, safe="")
    return value


class _UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    # listen() backlog: socketserver's default 5 made the reverse proxy answer 502 when a page opened ~50
    # image requests at once (gotchas #102)
    request_queue_size = 64

    def get_request(self):
        request, _ = super().get_request()
        return request, ("local", 0)  # BaseHTTPRequestHandler expects a (host, port) tuple


class _TCPHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64


class WebServer:
    def __init__(self, store, datalayer):
        self.store = store
        self.datalayer = datalayer
        self._server = None
        self._thread = None

    def start(self):
        handler = _make_handler(self.store, self.datalayer)
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


def _make_handler(store, datalayer):
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

        def _send_bytes(self, status, data, ctype, filename=None, cache_control="no-store"):
            """Binary data (images, PDF, CSV, ...). filename: a download ("Save as" name, Kit.download() in
            the page uses it). Content-addressed data may be cached: cache_control="private, max-age=3600"."""
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", cache_control)
            self.send_header("X-Content-Type-Options", "nosniff")
            if filename:
                self.send_header("Content-Disposition", content_disposition(filename))
            self.end_headers()
            self.wfile.write(data)

        def _query(self):
            """Query parameters of the request, first value of each: {"from": "17...", ...}."""
            return {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}

        def _receive_file(self, path, max_bytes, check=None):
            """Stream the request body into `path` (see receive_file); returns the size. On UploadError or
            OSError the rest of the body is read first, so the client gets the answer (gotchas #91)."""
            if self.headers.get("Content-Length") is None:
                raise UploadError(411, "Content-Length required")
            body = RequestBody(self.rfile, int(self.headers.get("Content-Length") or 0))
            try:
                return receive_file(body, path, max_bytes, check)
            except (UploadError, OSError) as exc:
                if not body.drain():
                    self.close_connection = True
                if isinstance(exc, UploadError):
                    raise
                log.error("Upload to %s failed: %s", path, exc)       # e.g. app data full
                raise UploadError(500, "not stored: {}".format(exc.strerror or exc))

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
            except UploadError as exc:
                self._problem(exc.status, "Upload rejected", str(exc))
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
                return self._send_bytes(200, data, "text/javascript; charset=utf-8")
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
                })

            # settings: only for users who may change them (view/operate users don't see them at all; when the
            # page needs a setting to show its data, give it to them through a view route of its own)
            if route == "/settings" and method == "GET":
                auth.require(self.headers, auth.CONFIGURE)
                return self._send_json(200, store.get_redacted())
            if route == "/settings" and method == "PUT":
                auth.require(self.headers, auth.CONFIGURE)
                try:
                    store.update(self._read_json())
                except SettingsError as exc:
                    return self._problem(400, "Invalid settings", errors=exc.errors)
                log.info("Settings changed via web UI by %s", auth.user_name(self.headers) or "?")
                return self._send_json(200, store.get_redacted())
            if route == "/settings/meta" and method == "GET":
                auth.require(self.headers, auth.CONFIGURE)
                return self._send_json(200, {"defaults": store.defaults(), "rules": store.rules(),
                                                 "types": store.types()})
            if route == "/settings/reset" and method == "POST":
                auth.require(self.headers, auth.CONFIGURE)
                store.reset_to_defaults(persist=True)
                log.info("Settings reset to defaults via web UI by %s", auth.user_name(self.headers) or "?")
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
