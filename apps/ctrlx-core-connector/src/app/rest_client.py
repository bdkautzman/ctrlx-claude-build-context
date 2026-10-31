"""HTTPS client for the REST API of another ctrlX device (stdlib only).

Used for what the Data Layer can't do: editing the KVD nodes.json in the other device's app data
(Solutions WebDAV, /solutions/webdav/appdata/...). Logs in with the core's connection user
(POST /identity-manager/api/v2/auth/token) and keeps the token until it expires or a call answers 401.
Outgoing connection only - covered by the `network` plug, no port of this device is opened.
"""

import base64
import json
import ssl
import threading
import time
import urllib.error
import urllib.request

TIMEOUT_S = 10.0
TOKEN_MARGIN_S = 60.0


class RestError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status   # HTTP status, 0 = no answer


def _token_expiry(token):
    try:
        payload = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return float(data.get("exp", 0)) or None
    except (IndexError, ValueError, TypeError, AttributeError):
        return None


class CoreRestClient:
    def __init__(self, host, port, user, password, skip_certificate_check, timeout=TIMEOUT_S):
        self.base = "https://{}{}".format(host, "" if int(port) == 443 else ":{}".format(int(port)))
        self._user, self._password = user, password
        self._timeout = timeout
        ctx = ssl.create_default_context()
        if skip_certificate_check:   # same choice as for the Data Layer remote (NoCheckCertificate)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        # never through a proxy: the cores are on the machine network
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                                   urllib.request.HTTPSHandler(context=ctx))
        self._lock = threading.Lock()
        self._token = None
        self._token_exp = 0.0

    def _open(self, method, path, data=None, headers=None):
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=dict(headers or {}))
        try:
            with self._opener.open(req, timeout=self._timeout) as res:
                return res.status, res.read()
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read()
            except Exception:
                body = b""
            return exc.code, body
        except (urllib.error.URLError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            raise RestError(0, "{} not reachable: {}".format(self.base, reason))

    def _login(self):
        body = json.dumps({"name": self._user, "password": self._password}).encode()
        status, raw = self._open("POST", "/identity-manager/api/v2/auth/token", body,
                                 {"Content-Type": "application/json"})
        if status not in (200, 201):
            raise RestError(status, "login as {} rejected (HTTP {})".format(self._user, status))
        try:
            token = json.loads(raw)["access_token"]
        except (ValueError, KeyError, TypeError):
            raise RestError(status, "unexpected login answer")
        self._token = token
        self._token_exp = _token_expiry(token) or time.time() + 600

    def request(self, method, path, data=None, content_type=None):
        """(status, body bytes). Logs in when needed; a 401 gets one new login."""
        for attempt in (1, 2):
            with self._lock:
                if self._token is None or time.time() > self._token_exp - TOKEN_MARGIN_S:
                    self._login()
                token = self._token
            headers = {"Authorization": "Bearer " + token}
            if content_type:
                headers["Content-Type"] = content_type
            status, body = self._open(method, path, data, headers)
            if status == 401 and attempt == 1:
                with self._lock:
                    self._token = None
                continue
            return status, body
        return status, body
