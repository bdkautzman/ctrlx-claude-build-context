"""Self-test of tools/ctrlx.py device selection (no device needed):  python3 -m unittest discover -s tools/tests"""

import contextlib
import io
import os
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import ctrlx  # noqa: E402

DEVICES = {
    "default": "virtual",
    "virtual": {"host": "10.0.2.2", "https_port": 8443, "ssh_port": 8022, "arch": "amd64",
                "api_user": "api-v", "api_password": "pw-v", "ssh_user": "ssh-v", "ssh_password": "spw-v"},
    "core": {"host": "192.168.1.7", "https_port": 443, "ssh_port": 22, "arch": "arm64",
             "api_user": "api-c", "api_password": "pw-c", "ssh_user": "ssh-c", "ssh_password": "spw-c"},
}


class ResolveTargetTest(unittest.TestCase):
    def test_default_device(self):
        t = ctrlx.resolve_target(DEVICES, {})
        self.assertEqual((t["name"], t["api_host"], t["ssh_host"], t["ssh_port"]), ("virtual", "10.0.2.2:8443", "10.0.2.2", 8022))
        self.assertEqual((t["api_user"], t["ssh_user"], t["arch"]), ("api-v", "ssh-v", "amd64"))

    def test_port_443_is_omitted(self):
        t = ctrlx.resolve_target(DEVICES, {}, device="core")
        self.assertEqual((t["api_host"], t["ssh_port"], t["ssh_password"]), ("192.168.1.7", 22, "spw-c"))

    def test_env_overrides_default_device(self):
        t = ctrlx.resolve_target(DEVICES, {"CTRLX_HOST": "10.1.1.1", "CTRLX_SSL_PORT": "9443", "CTRLX_USER": "u"})
        self.assertEqual((t["api_host"], t["ssh_host"], t["api_user"], t["api_password"]), ("10.1.1.1:9443", "10.1.1.1", "u", "pw-v"))

    def test_explicit_device_beats_env(self):
        t = ctrlx.resolve_target(DEVICES, {"CTRLX_HOST": "10.1.1.1", "CTRLX_USER": "u"}, device="core")
        self.assertEqual((t["api_host"], t["api_user"]), ("192.168.1.7", "api-c"))

    def test_ctrlx_device_variable(self):
        self.assertEqual(ctrlx.resolve_target(DEVICES, {"CTRLX_DEVICE": "core"})["name"], "core")

    def test_options_beat_everything(self):
        t = ctrlx.resolve_target(DEVICES, {"CTRLX_USER": "u"}, device="core", host="h:1", user="x", password="y")
        self.assertEqual((t["api_host"], t["api_user"], t["api_password"]), ("h:1", "x", "y"))

    def test_without_devices_file(self):
        t = ctrlx.resolve_target({}, {})
        self.assertEqual((t["api_host"], t["ssh_port"], t["api_user"]), ("10.0.2.2:8443", 8022, "boschrexroth"))

    def test_unknown_device(self):
        with self.assertRaises(SystemExit):
            ctrlx.resolve_target(DEVICES, {}, device="nope")

    def test_load_devices_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "devices.toml")
            self.assertEqual(ctrlx.load_devices(path), {})
            with open(path, "w", encoding="utf-8") as f:
                f.write('default = "a"\n[a]\nhost = "1.2.3.4"\n')
            self.assertEqual(ctrlx.resolve_target(ctrlx.load_devices(path), {})["api_host"], "1.2.3.4")

    def test_example_file_is_valid(self):
        devices = ctrlx.load_devices(os.path.join(ctrlx.KIT, "devices.example.toml"))
        self.assertEqual(set(ctrlx.device_names(devices)), {"virtual", "x3"})
        self.assertIn(devices["default"], devices)

    def test_snap_arch(self):
        self.assertEqual(ctrlx.snap_arch("x/ctrlx-acme-app_1.2.3_arm64.snap"), "arm64")
        self.assertEqual(ctrlx.snap_arch("ctrlx-acme-app_1.2.3_amd64.snap"), "amd64")
        self.assertEqual(ctrlx.snap_arch("odd.snap"), "")


def write_app(path, archs, mode="w"):
    """.app as written by tools/make-app.sh: tar with public/snaps/<arch>/release/<snap>_<ver>.snap"""
    with tempfile.TemporaryDirectory() as tmp:
        for arch in archs:
            d = os.path.join(tmp, "public", "snaps", arch, "release")
            os.makedirs(d)
            with open(os.path.join(d, "ctrlx-acme-app_1.2.3.snap"), "wb") as f:
                f.write(b"snap")
        with tarfile.open(path, mode) as tar:
            tar.add(os.path.join(tmp, "public"), "public")


class PackageArchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = os.path.join(self.tmp.name, "ctrlx-acme-app_1.2.3.app")

    def target(self, name):
        return ctrlx.resolve_target(DEVICES, {}, device=name)

    def check_arch(self, package, device):
        with contextlib.redirect_stdout(io.StringIO()):
            ctrlx.check_arch(package, self.target(device))

    def test_app_archs(self):
        write_app(self.app, ["arm64", "amd64"])
        self.assertEqual(ctrlx.app_archs(self.app), ["amd64", "arm64"])

    def test_app_with_both_archs_fits_every_device(self):
        write_app(self.app, ["amd64", "arm64"])
        self.check_arch(self.app, "virtual")
        self.check_arch(self.app, "core")

    def test_app_without_device_arch_is_refused(self):
        write_app(self.app, ["amd64"])
        self.check_arch(self.app, "virtual")
        with self.assertRaises(SystemExit) as ctx:
            self.check_arch(self.app, "core")
        self.assertIn("contains only amd64, but device 'core' is arm64", str(ctx.exception))

    def test_not_an_app(self):
        with open(self.app, "wb") as f:
            f.write(b"no tar")
        with self.assertRaises(SystemExit):
            ctrlx.app_archs(self.app)
        write_app(self.app, ["amd64"], mode="w:gz")  # compressed: the device expects a plain tar
        with self.assertRaises(SystemExit):
            ctrlx.app_archs(self.app)
        with tarfile.open(self.app, "w") as tar:  # tar without snaps
            tar.add(__file__, "readme.txt")
        with self.assertRaises(SystemExit) as ctx:
            ctrlx.app_archs(self.app)
        self.assertIn("not a .app package", str(ctx.exception))

    def test_snap_arch_check(self):
        snap = os.path.join(self.tmp.name, "ctrlx-acme-app_1.2.3_arm64.snap")
        self.check_arch(snap, "core")
        with self.assertRaises(SystemExit) as ctx:
            self.check_arch(snap, "virtual")
        self.assertIn("build with ./build-snap-amd64.sh", str(ctx.exception))


class DeviceRequestTest(unittest.TestCase):
    def test_dropped_connection_becomes_systemexit(self):
        # seen during an app update: the proxy reloads and closes the connection without a response
        import http.client
        dev = ctrlx.Device("127.0.0.1:1", "u", "p", False)
        dev._token = "t"

        class Opener:
            def open(self, req, timeout):
                raise http.client.RemoteDisconnected("Remote end closed connection without response")
        dev.opener = Opener()
        with self.assertRaises(SystemExit) as ctx:
            dev.request("GET", "/package-manager/api/v1/tasks/x")
        self.assertIn("Connection to", str(ctx.exception))


    def test_logout_ends_the_session(self):
        """gotchas #110: every call logs out again, else the device runs out of sessions for the user."""
        dev = ctrlx.Device("127.0.0.1:1", "u", "p", False)
        seen = []

        class Opener:
            def open(self, req, timeout):
                from urllib.parse import urlsplit
                seen.append((req.get_method(), urlsplit(req.full_url).path, req.headers.get("Authorization")))
                raise OSError("nothing listens")
        dev.opener = Opener()
        dev.logout()                      # never logged in: nothing to end
        self.assertEqual(seen, [])
        dev._token = "t"
        dev.logout()                      # errors are ignored (the session times out on the device)
        self.assertEqual(seen, [("DELETE", "/identity-manager/api/v2/auth/token", "Bearer t")])
        self.assertIsNone(dev._token)


class FakeIdm:
    """The identity manager calls of tools/ctrlx.py test-users, in memory."""

    def __init__(self, users=(), app_scopes=None):
        self.users = {name: {"id": str(1002 + i), "scopes": []} for i, name in enumerate(users)}
        self.app_scopes = app_scopes or {}
        self.passwords, self.calls = {}, []

    def request(self, method, path, body=None, **kw):
        self.calls.append((method, path))
        p = path[len(ctrlx.IDM):]
        by_id = {u["id"]: n for n, u in self.users.items()}
        parts = p.strip("/").split("/")
        if (method, p) == ("GET", "/users"):
            return 200, {}, [{"name": n, "id": u["id"]} for n, u in self.users.items()]
        if (method, p) == ("POST", "/users"):
            uid = str(1100 + len(self.users))
            self.users[body["name"]] = {"id": uid, "scopes": []}
            return 201, {}, {"name": body["name"], "id": uid}
        if (method, p) == ("GET", "/scopes"):
            return 200, {}, {"apps": [{"identifier": snap + ".web", "scopes": [
                {"identifier": snap + sfx, "name": sfx, "description": "d"} for sfx in sfxs]}
                for snap, sfxs in self.app_scopes.items()]}
        name = by_id[parts[1]]
        if parts[2:] == ["scopes"] and method == "GET":
            return 200, {}, list(self.users[name]["scopes"])
        if parts[2:] == ["scopes"] and method == "PUT":
            self.users[name]["scopes"] = list(body)
            return 204, {}, ""
        if parts[2:] == ["credentials"]:
            self.passwords[name] = body["newPassword"]
            return 204, {}, ""
        if parts[2:] == ["pwdchgatnextlogin"]:
            self.users[name]["forced"] = body["forced"]
            return 204, {}, ""
        if method == "DELETE" and len(parts) == 2:
            del self.users[name]
            return 204, {}, ""
        raise AssertionError((method, path))


class TestUsersTest(unittest.TestCase):
    """tools/ctrlx.py test-users: one user per role, the app's scope of that role (user's request 2026-11-08)."""
    ROLES = (".web.all.r", ".web.operate.x", ".web.all.rw")

    def test_grant_adds_only_the_role_scope_and_keeps_others(self):
        idm = FakeIdm(["kit-view", "kit-operate", "kit-configure"], {"ctrlx-a": self.ROLES})
        idm.users["kit-view"]["scopes"] = [{"identifier": "other.web.all.r", "name": "o", "description": "d"}]
        with contextlib.redirect_stdout(io.StringIO()):
            changed = ctrlx.grant_test_users(idm, "ctrlx-a")
        self.assertEqual(changed, ["kit-view", "kit-operate", "kit-configure"])
        self.assertEqual([g["identifier"] for g in idm.users["kit-view"]["scopes"]],
                         ["other.web.all.r", "ctrlx-a.web.all.r"])
        self.assertEqual([g["identifier"] for g in idm.users["kit-operate"]["scopes"]], ["ctrlx-a.web.operate.x"])
        self.assertEqual([g["identifier"] for g in idm.users["kit-configure"]["scopes"]], ["ctrlx-a.web.all.rw"])
        self.assertEqual(ctrlx.grant_test_users(idm, "ctrlx-a", quiet=True), [])        # nothing twice
        self.assertEqual(ctrlx.test_user("operator"), ("kit-operate", ".web.operate.x"))

    def test_grant_needs_the_app_and_the_users(self):
        with self.assertRaises(SystemExit) as ctx:
            ctrlx.grant_test_users(FakeIdm(["kit-view"], {}), "ctrlx-a")
        self.assertIn("installed", str(ctx.exception))
        with self.assertRaises(SystemExit) as ctx:
            ctrlx.grant_test_users(FakeIdm([], {"ctrlx-a": self.ROLES}), "ctrlx-a", roles=["reader"])
        self.assertIn("ask the user", str(ctx.exception))

    def test_create_stores_one_password_and_checks_the_logins(self):
        idm = FakeIdm(["kit-view"])
        logins = []

        class FakeDevice:
            def __init__(self, host, user, password, verify):
                logins.append((user, password))

            def token(self):
                return "t"

            def logout(self):
                logins.append("logout")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "devices.toml")
            with open(path, "w") as f:
                f.write('[core]\nhost = "192.168.1.7"\n')
            devices = ctrlx.load_devices(path)
            args = type("Args", (), {"action": "create", "snap": None, "devices_file": path, "devices": devices,
                                     "target": ctrlx.resolve_target(devices, {}, "core")})()
            orig, ctrlx.Device = ctrlx.Device, FakeDevice
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    ctrlx.cmd_test_users(idm, args)
            finally:
                ctrlx.Device = orig
            stored = ctrlx.load_devices(path)["core"]["test_users"]["password"]
        self.assertEqual(sorted(idm.users), ["kit-configure", "kit-operate", "kit-view"])
        self.assertEqual(idm.passwords, {"kit-operate": stored, "kit-configure": stored})   # kit-view existed
        self.assertFalse(idm.users["kit-operate"]["forced"])                # no password change at the first login
        self.assertEqual(logins, [("kit-operate", stored), "logout", ("kit-configure", stored), "logout"])
        self.assertRegex(stored, r"[A-Z]")
        self.assertRegex(stored, r"[0-9]")


if __name__ == "__main__":
    unittest.main()
