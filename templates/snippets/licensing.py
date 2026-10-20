"""ctrlX license check via the license manager Unix socket - OPTIONAL snippet (not wired in).

Copy to app/licensing.py when an app must enforce a ctrlX license (mandatory for Bosch-signed
partner apps, App Guide 7.1). Spec: docs/ctrlx/upstream-sdk-4.6.0/licensing.md,
reference/rest-api/licensemanager.*.openapi.json.  UNVERIFIED against a device - test once and
record findings in docs/ctrlx/gotchas.md.

Requires in snapcraft.yaml (+ validation/slotplug-description.json):
    plugs:
      licensing-service:
        interface: content
        content: licensing-service
        target: $SNAP_DATA/licensing-service
and the license in the manifest:
    "licenses": [{"name": "SWL-XC...", "title": "...", "description": "...", "required": true}]

Usage:
    lic = LicenseClient(["SWL-XCR-MYAPP-NNNN"])   # your license name(s)
    ok = lic.acquire()        # at start; True also for the COREvirtual 4h engineering license
    ...                       # call lic.refresh() e.g. every hour; show a warning in the UI if False
    lic.release()             # on shutdown
"""

import http.client
import json
import logging
import os
import socket

log = logging.getLogger("licensing")

ENGINEERING_LICENSE = "SWL_XCR_ENGINEERING_4H"   # ctrlX COREvirtual
API = "/license-manager/api/v1"


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout=10):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._path)
        self.sock = sock


class LicenseClient:
    def __init__(self, license_names, accept_engineering=True):
        self.names = list(license_names) + ([ENGINEERING_LICENSE] if accept_engineering else [])
        self.socket_path = os.path.join(os.environ.get("SNAP_DATA", "/tmp"), "licensing-service",
                                        "licensing-service.sock")
        snap = os.environ.get("SNAP_NAME", "app")
        self.id_file = "/tmp/{}.license-id".format(snap)   # /tmp survives app restarts until reboot
        self.active = None   # dict from the acquire response, or None

    def _call(self, method, path, body=None):
        conn = _UnixHTTPConnection(self.socket_path)
        try:
            conn.request(method, API + path, body=json.dumps(body) if body is not None else None,
                         headers={"Content-Type": "application/json", "Accept": "application/json"})
            res = conn.getresponse()
            data = res.read()
            return res.status, (json.loads(data) if data else None)
        finally:
            conn.close()

    def available(self):
        return os.path.exists(self.socket_path)

    def acquire(self):
        if not self.available():
            log.error("licensing-service plug not connected (%s missing)", self.socket_path)
            return False
        self._release_stale()
        for name in self.names:
            try:
                status, body = self._call("POST", "/license", {"name": name, "version": "1.0"})
            except OSError as exc:
                log.error("License manager not reachable: %s", exc)
                return False
            if status in (200, 201) and isinstance(body, dict) and body.get("id"):
                self.active = body
                with open(self.id_file, "w") as f:
                    f.write(body["id"])
                log.info("License %s acquired (permanent=%s)", name, body.get("isPermanent"))
                return True
        self.active = None
        log.warning("No license available: %s", ", ".join(self.names))
        return False

    def refresh(self):
        """Release + re-acquire (recommended periodic check, e.g. hourly)."""
        self.release()
        return self.acquire()

    def release(self):
        if self.active and self.active.get("id"):
            try:
                self._call("DELETE", "/license/" + self.active["id"])
            except OSError:
                pass
        self.active = None
        try:
            os.unlink(self.id_file)
        except OSError:
            pass

    def _release_stale(self):
        """Release a license id left over from a crashed previous run."""
        try:
            with open(self.id_file) as f:
                stale = f.read().strip()
        except OSError:
            return
        if stale:
            try:
                self._call("DELETE", "/license/" + stale)
            except OSError:
                pass
