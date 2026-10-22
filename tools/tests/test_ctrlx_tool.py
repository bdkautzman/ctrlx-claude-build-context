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
        self.assertEqual(set(ctrlx.device_names(devices)), {"virtual", "core"})
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


if __name__ == "__main__":
    unittest.main()
