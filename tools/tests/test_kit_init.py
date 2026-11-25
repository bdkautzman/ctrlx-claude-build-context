"""Self-test of tools/kit_init.py with a fake machine (no root, network or device needed):
python3 -m unittest discover -s tools/tests"""

import os
import shutil
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import kit_init  # noqa: E402

KIT = kit_init.KIT


class FakeEnv(kit_init.Env):
    """Answers from tables instead of the machine. commands: 'argv prefix' -> (rc, output)."""

    def __init__(self, kit, home, commands=None, files=None, environ=None, which=(), modules=(), urls=None,
                 tcp=None, dpkg=None, device=None):
        super().__init__(kit=kit, environ=environ if environ is not None else {"PATH": "/nonexistent"}, home=home)
        self.commands, self.files, self._which, self.modules = commands or {}, files or {}, set(which), set(modules)
        self.urls, self._tcp, self.dpkg, self._device = urls or {}, tcp or {}, dpkg, device
        self.ran = []

    def run(self, argv, timeout=30):
        self.ran.append(argv)
        for n in range(len(argv), 0, -1):
            key = " ".join(argv[:n])
            if key in self.commands:
                return self.commands[key]
        return 127, ""

    def which(self, cmd):
        return "/usr/bin/" + cmd if cmd in self._which else None

    def read(self, path):
        return self.files.get(path)

    def has_module(self, name):
        return name in self.modules

    def module_version(self, dist):
        return "1.0"

    def url(self, url, timeout=15):
        return self.urls.get(url, (True, "HTTP 200"))

    def tcp(self, host, port, timeout=5):
        return self._tcp.get((host, int(port)), (True, ""))

    def dpkg_installed(self, packages):
        return None if self.dpkg is None else {p for p in packages if p in self.dpkg}

    def device(self, target):
        return self._device

    def disk_free(self, path):
        return 50e9

    def ram_total(self):
        return 4e9


class FakeDevice:
    def __init__(self, users=(), fail=None):
        self.users, self.fail, self.logged_out = list(users), fail, False

    def token(self, timeout=60):
        if self.fail:
            raise SystemExit(self.fail)
        return "token"

    def request(self, method, path, body=None, **kw):
        return 200, {}, [{"name": n, "id": str(i)} for i, n in enumerate(self.users)]

    def logout(self):
        self.logged_out = True


def statuses(results):
    return [(r["status"], r["name"]) for r in results]


class TempKit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.kit = os.path.join(self.tmp, "kit")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(self.kit, "tools"))
        os.makedirs(self.home)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel, data, mode=0o644):
        path = os.path.join(self.kit, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode())
        os.chmod(path, mode)
        return path

    def env(self, **kw):
        return FakeEnv(self.kit, self.home, **kw)


class ParserTest(unittest.TestCase):
    def test_apt_policy(self):
        self.assertEqual(kit_init.parse_apt_policy("x:\n  Installed: 3.5.2\n  Candidate: 3.5.2\n"), ("3.5.2", "3.5.2"))
        self.assertEqual(kit_init.parse_apt_policy("x:\n  Installed: (none)\n  Candidate: 3.5.2\n"), ("", "3.5.2"))
        self.assertEqual(kit_init.parse_apt_policy("x:\n  Installed: (none)\n  Candidate: (none)\n"), ("", ""))
        self.assertEqual(kit_init.parse_apt_policy(""), ("", ""))

    def test_snapcraft_version(self):
        self.assertEqual(kit_init.parse_snapcraft_version("snapcraft 8.14.5\n"), (8, "8.14.5"))
        self.assertEqual(kit_init.parse_snapcraft_version("snapcraft, version 7.5.2"), (7, "7.5.2"))
        self.assertEqual(kit_init.parse_snapcraft_version("nonsense"), (None, ""))

    def test_environment_file(self):
        env = kit_init.parse_environment_file('PATH="/usr/bin"\n# c\nSNAPCRAFT_BUILD_ENVIRONMENT=host\nX="1"\n')
        self.assertEqual((env["SNAPCRAFT_BUILD_ENVIRONMENT"], env["X"], env["PATH"]), ("host", "1", "/usr/bin"))

    def test_pins_come_from_setup_ui_test(self):
        with open(os.path.join(KIT, "tools", "setup-ui-test.sh")) as f:
            pins = kit_init.parse_setup_ui(f.read())
        self.assertRegex(pins["PLAYWRIGHT_VERSION"], r"^\d+\.\d+\.\d+$")
        self.assertRegex(pins["PDFIUM_VERSION"], r"^\d+\.\d+\.\d+$")
        self.assertIn("fonts-liberation", pins["APT_PACKAGES"])
        self.assertIn("libgbm1", pins["APT_PACKAGES"])

    def test_apt_source_uris(self):
        one_line = ("deb [arch=amd64] http://archive.ubuntu.com/ubuntu noble main\n"
                    "# deb http://commented.example/ubuntu noble main\n"
                    "deb-src http://archive.ubuntu.com/ubuntu/ noble main\n"
                    "deb [trusted=yes] file:/home/u/ctrlx-automation-sdk/deb ./\n")
        deb822 = ("## URIs: A URL to the repository\nTypes: deb\n"
                  "URIs: http://archive.ubuntu.com/ubuntu http://security.ubuntu.com/ubuntu\n")
        self.assertEqual(kit_init.apt_source_uris([one_line, None, deb822]),
                         ["http://archive.ubuntu.com/ubuntu", "http://security.ubuntu.com/ubuntu"])

    def test_must_be_executable(self):
        self.assertTrue(kit_init.must_be_executable("apps/a/run-local.sh", b""))
        self.assertTrue(kit_init.must_be_executable("apps/a/wrapper/run.sh", b""))
        self.assertTrue(kit_init.must_be_executable("tools/ctrlx.py", b"#!"))
        self.assertTrue(kit_init.must_be_executable(".claude/hooks/post_edit_check.py", b"#!"))
        self.assertFalse(kit_init.must_be_executable("tools/tests/test_x.py", b"#!"))
        self.assertFalse(kit_init.must_be_executable("tools/lib.py", b"im"))
        self.assertFalse(kit_init.must_be_executable("apps/a/src/app/main.py", b"#!"))

    def test_every_tool_with_a_shebang_is_executable_in_the_kit(self):
        results = list(kit_init.kit_files(kit_init.Env()))
        self.assertEqual(statuses(results), [("OK", "executable bits"), ("OK", "line endings")],
                         [r["detail"] for r in results])


class KitFilesTest(TempKit):
    def test_lost_bits_and_crlf(self):
        self.write("tools/check.sh", "#!/bin/bash\n", 0o644)
        self.write("tools/ctrlx.py", "#!/usr/bin/env python3\n", 0o755)
        self.write("tools/fix-permissions.sh", "#!/bin/bash\r\necho\r\n", 0o755)
        self.write("reference/x/build.sh", "#!/bin/bash\r\n", 0o644)     # upstream: not the kit's files
        self.write("apps/a/parts/x/y.sh", "#!/bin/bash\r\n", 0o644)      # build output
        results = list(kit_init.kit_files(self.env()))
        self.assertEqual(statuses(results), [("MISSING", "executable bits"), ("MISSING", "line endings")])
        self.assertIn("tools/check.sh", results[0]["detail"])
        self.assertIn("1 of 3 scripts", results[0]["detail"])
        self.assertIn("tools/fix-permissions.sh", results[1]["detail"])
        self.assertTrue(results[1]["fix"].startswith("sed -i"), results[1]["fix"])

    def test_clean_kit(self):
        self.write("tools/check.sh", "#!/bin/bash\n", 0o755)
        self.write("docs/a.md", "# a\n")
        self.assertEqual(statuses(kit_init.kit_files(self.env())), [("OK", "executable bits"), ("OK", "line endings")])


class BuildTest(TempKit):
    def test_sdk_environment(self):
        env = self.env(environ={"SNAPCRAFT_BUILD_INFO": "1"},
                       files={"/etc/environment": 'SNAPCRAFT_BUILD_ENVIRONMENT=host\nSNAPCRAFT_BUILD_INFO="1"\n'})
        self.assertEqual(statuses(kit_init.sdk_environment(env)),
                         [("WARN", "SNAPCRAFT_BUILD_ENVIRONMENT"),               # saved, not in this shell yet
                          ("MISSING", "SNAPCRAFT_ENABLE_EXPERIMENTAL_TARGET_ARCH"),
                          ("OK", "SNAPCRAFT_BUILD_INFO")])
        env = self.env(environ={}, files={"/etc/environment": "SNAPCRAFT_BUILD_ENVIRONMENT=host\n"
                                          "SNAPCRAFT_ENABLE_EXPERIMENTAL_TARGET_ARCH=1\n"})
        self.assertEqual([r["status"] for r in kit_init.sdk_environment(env)], ["WARN", "WARN", "OPTIONAL"])

    def test_snapcraft_version(self):
        listing = "Name       Version  Rev    Tracking     Publisher   Notes\nsnapcraft  {}  1  {}  canonical  classic\n"
        env = self.env(which={"snapcraft"}, commands={"snap list snapcraft": (0, listing.format("8.14.5", "8.x/stable"))})
        self.assertEqual([(r["status"], r["detail"]) for r in kit_init.snapcraft(env)], [("OK", "8.14.5 (8.x/stable)")])
        env = self.env(which={"snapcraft"}, commands={"snap list snapcraft": (0, listing.format("7.5.2", "7.x/stable"))})
        r = list(kit_init.snapcraft(env))[0]
        self.assertEqual(r["status"], "MISSING")
        self.assertIn("--channel=8.x/stable", r["fix"])
        self.assertEqual(statuses(kit_init.snapcraft(self.env())), [("MISSING", "snapcraft")])

    def test_datalayer_deb_missing_points_to_the_sdk_deb(self):
        deb = os.path.join(self.home, "ctrlx-automation-sdk", "deb", "ctrlx-datalayer-3.5.2.deb")
        os.makedirs(os.path.dirname(deb))
        open(deb, "w").close()
        env = self.env(which={"apt-cache"}, commands={"apt-cache policy": (0, "ctrlx-datalayer:\n  Installed: (none)\n"
                                                                              "  Candidate: (none)\n")})
        r = list(kit_init.datalayer_deb(env))[0]
        self.assertEqual(r["status"], "MISSING")
        self.assertEqual(r["fix"], "~/ctrlx-automation-sdk/scripts/install-ctrlx-datalayer.sh ~/ctrlx-automation-sdk/deb")

    def test_build_packages(self):
        r = list(kit_init.build_packages(self.env(dpkg={"findutils"})))[0]
        self.assertEqual((r["status"], r["apt"]), ("MISSING", ["python3-dev", "python3-venv"]))
        self.assertEqual(statuses(kit_init.build_packages(self.env(dpkg=set(kit_init.BUILD_PACKAGES)))),
                         [("OK", "build packages")])

    def test_cross_builds(self):
        env = self.env(commands={"dpkg --print-foreign-architectures": (0, "\n")})
        env.cache["arch"] = "amd64"
        self.assertEqual(statuses(kit_init.cross_builds(env)), [("MISSING", "arm64 packages")])
        env = self.env(commands={"dpkg --print-foreign-architectures": (0, "arm64\n"),
                                 "apt-cache policy libc6:arm64": (0, "libc6:arm64:\n  Candidate: 2.39\n")})
        env.cache["arch"] = "amd64"
        self.assertEqual(statuses(kit_init.cross_builds(env)), [("OK", "arm64 packages")])


class NetworkTest(TempKit):
    def test_unreachable_mirror_is_missing(self):
        env = self.env(files={"/etc/apt/sources.list": "deb http://mirror.example/ubuntu noble main\n"},
                       urls={"http://mirror.example/ubuntu/": (False, "timed out")})
        found = {r["name"]: r["status"] for r in kit_init.network(env)}
        self.assertEqual((found["PyPI"], found["apt mirror.example"]), ("OK", "MISSING"))


class DevicesTest(TempKit):
    DEVICES = ('default = "core"\n[core]\nhost = "192.168.1.7"\nhttps_port = 443\nssh_port = 22\narch = "arm64"\n'
               'api_user = "u"\napi_password = "p"\nssh_user = "s"\nssh_password = "sp"\n')

    def run_devices(self, env):
        config = list(kit_init.devices_toml(env))
        return config, list(kit_init.devices(env))

    def test_no_devices_file_is_optional(self):
        config, devices = self.run_devices(self.env())
        self.assertEqual(statuses(config), [("OPTIONAL", "devices.toml")])
        self.assertEqual(statuses(devices), [("INFO", "devices")])

    def test_broken_devices_file_is_missing(self):
        self.write("devices.toml", "[core\n")
        config, _ = self.run_devices(self.env())
        self.assertEqual(statuses(config), [("MISSING", "devices.toml")])

    def test_reachable_device_with_missing_test_users(self):
        self.write("devices.toml", self.DEVICES)
        dev = FakeDevice(users=["boschrexroth", "kit-view"])
        config, devices = self.run_devices(self.env(device=dev))
        self.assertEqual(statuses(config), [("OK", "devices.toml")])
        self.assertEqual(statuses(devices), [("OK", "core"), ("OPTIONAL", "core test users"), ("OK", "core SSH"),
                                             ("OPTIONAL", "sshpass")])
        self.assertIn("kit-operate, kit-configure", devices[1]["detail"])
        self.assertIn("ask first", devices[1]["fix"])
        self.assertTrue(dev.logged_out)

    def test_failed_login_logs_out_and_stops(self):
        self.write("devices.toml", self.DEVICES)
        dev = FakeDevice(fail="HTTP 401 POST /identity-manager/api/v2/auth/token\nbody")
        _, devices = self.run_devices(self.env(device=dev, which={"sshpass"}))
        self.assertEqual(statuses(devices), [("OPTIONAL", "core")])
        self.assertIn("HTTP 401", devices[0]["detail"])
        self.assertTrue(dev.logged_out)

    def test_unreachable_device(self):
        self.write("devices.toml", self.DEVICES)
        _, devices = self.run_devices(self.env(tcp={("192.168.1.7", 443): (False, "timed out")}, which={"sshpass"}))
        self.assertEqual(statuses(devices), [("OPTIONAL", "core")])


class ConfigTest(TempKit):
    def test_kit_toml(self):
        self.write("kit.example.toml", 'author = "ACME"\nurl = "https://acme.example"\n')
        self.assertEqual(statuses(kit_init.kit_toml(self.env())), [("OPTIONAL", "kit.toml")])
        self.write("kit.toml", 'author = "ACME"\nurl = "https://me.example"\n')
        r = list(kit_init.kit_toml(self.env()))[0]
        self.assertEqual(r["status"], "WARN")
        self.assertIn("author", r["detail"])
        self.write("kit.toml", 'author = "Me"\nurl = "https://me.example"\n')
        self.assertEqual(statuses(kit_init.kit_toml(self.env())), [("OK", "kit.toml")])


class RunTest(TempKit):
    def test_nothing_installed_never_crashes(self):
        results = kit_init.run_checks(self.env())
        crashed = [r for r in results if r["detail"].startswith("check failed")]
        self.assertFalse(crashed, crashed)
        self.assertFalse(kit_init.summary(results)["ready"])

    def test_a_broken_check_does_not_hide_the_others(self):
        def broken(env):
            raise RuntimeError("boom")
            yield
        saved = list(kit_init.CHECKS)
        try:
            kit_init.CHECKS[:] = [("kit", broken), ("kit", lambda env: iter([kit_init.ok("next")]))]
            results = kit_init.run_checks(self.env())
        finally:
            kit_init.CHECKS[:] = saved
        self.assertEqual(statuses(results), [("WARN", "broken"), ("OK", "next")])

    def test_summary_collects_apt_packages(self):
        results = [kit_init.missing("a", "", apt=["x", "y"]), kit_init.missing("b", "", apt=["y", "z"]),
                   kit_init.optional("c", "", apt=["w"]), kit_init.ok("d")]
        s = kit_init.summary(results)
        self.assertEqual((s["apt"]["MISSING"], s["apt"]["OPTIONAL"], s["ready"]), (["x", "y", "z"], ["w"], False))
        self.assertTrue(kit_init.summary([kit_init.optional("c", ""), kit_init.warn("w", "")])["ready"])


if __name__ == "__main__":
    unittest.main()
