"""Self-test of tools/make-app.sh (no device, no snapcraft):  python3 -m unittest discover -s tools/tests

Snaps are tiny squashfs images with a meta/snap.yaml when mksquashfs is installed (squashfs-tools),
else plain bytes (make-app.sh then skips the content check)."""

import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import ctrlx  # noqa: E402

MAKE_APP = os.path.join(HERE, "..", "make-app.sh")
SNAP = "ctrlx-acme-demo"
SQUASHFS = bool(shutil.which("mksquashfs") and shutil.which("unsquashfs"))
SDK = os.environ.get("CTRLX_SDK", os.path.expanduser("~/ctrlx-automation-sdk"))
SIGN_TOOL = os.path.join(SDK, "scripts", "app-signing-tool.sh")


def make_snap(path, version, arch, name=SNAP):
    if not SQUASHFS:
        with open(path, "wb") as f:
            f.write("{} {} {}\n".format(name, version, arch).encode() + os.urandom(64))
        return
    with tempfile.TemporaryDirectory() as root:
        os.makedirs(os.path.join(root, "meta"))
        with open(os.path.join(root, "meta", "snap.yaml"), "w", encoding="utf-8") as f:
            f.write("name: {}\nversion: '{}'\narchitectures:\n- {}\nbase: core24\n".format(name, version, arch))
        with open(os.path.join(root, "payload"), "wb") as f:
            f.write(os.urandom(256))
        subprocess.run(["mksquashfs", root, path, "-quiet", "-noappend", "-all-root"], check=True,
                       stdout=subprocess.DEVNULL)


class MakeAppTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = os.path.join(self.tmp.name, SNAP)
        os.makedirs(os.path.join(self.app, "snap"))
        self.set_version("0.1.2")

    def tearDown(self):
        self.tmp.cleanup()

    def set_version(self, version):
        with open(os.path.join(self.app, "snap", "snapcraft.yaml"), "w", encoding="utf-8") as f:
            f.write("name: {}\ntitle: Demo\nversion: '{}'  # semver\nbase: core24\n".format(SNAP, version))

    def snap(self, version, arch):
        path = os.path.join(self.app, "{}_{}_{}.snap".format(SNAP, version, arch))
        make_snap(path, version, arch)
        return path

    def fake_build_script(self, arch, version="0.1.2"):
        """build-snap-<arch>.sh that logs its arguments and 'builds' the snap by copying a prepared one."""
        built = os.path.join(self.app, ".built-{}.snap".format(arch))
        make_snap(built, version, arch)
        script = os.path.join(self.app, "build-snap-{}.sh".format(arch))
        with open(script, "w", encoding="utf-8") as f:
            f.write('#!/usr/bin/env bash\ncd "$(dirname "$0")"\necho "{0} $*" >> build.log\n'
                    'cp .built-{0}.snap {1}_{2}_{0}.snap\n'.format(arch, SNAP, version))
        os.chmod(script, 0o755)

    def run_tool(self, *args, env=None):
        return subprocess.run(["bash", MAKE_APP, self.app] + list(args), capture_output=True, text=True,
                              env=dict(os.environ, **(env or {})))

    def app_file(self, version="0.1.2"):
        return os.path.join(self.app, "{}_{}.app".format(SNAP, version))

    def build_log(self):
        path = os.path.join(self.app, "build.log")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return f.read().splitlines()

    def test_layout(self):
        snaps = {a: self.snap("0.1.2", a) for a in ("amd64", "arm64")}
        self.snap("0.1.1", "arm64")  # older builds in the folder are ignored
        r = self.run_tool()
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(self.app_file(), "rb") as f:
            self.assertNotEqual(f.read(2), b"\x1f\x8b")  # not gzip
        with tarfile.open(self.app_file(), "r:") as tar:  # uncompressed tar
            files = {m.name: m for m in tar.getmembers() if m.isfile()}
            self.assertEqual(set(files), {"public/snaps/{}/release/{}_0.1.2.snap".format(a, SNAP) for a in snaps})
            for arch, src in snaps.items():
                with open(src, "rb") as f:
                    data = tar.extractfile("public/snaps/{}/release/{}_0.1.2.snap".format(arch, SNAP)).read()
                    self.assertEqual(data, f.read())
        self.assertEqual(ctrlx.app_archs(self.app_file()), ["amd64", "arm64"])
        self.assertEqual(self.build_log(), [])
        self.assertFalse([n for n in os.listdir(self.app) if n.endswith(".part")])

    def test_version_mismatch(self):
        self.snap("0.1.2", "amd64")
        self.snap("0.1.1", "arm64")
        r = self.run_tool()
        self.assertEqual(r.returncode, 1)
        self.assertIn("versions differ: amd64 0.1.2, arm64 0.1.1", r.stderr)
        self.assertIn("./build-snap-arm64.sh --clean", r.stderr)
        self.assertFalse(os.path.exists(self.app_file()))

    def test_version_not_in_snapcraft_yaml(self):
        self.snap("0.1.1", "amd64")
        self.snap("0.1.1", "arm64")
        r = self.run_tool()
        self.assertEqual(r.returncode, 1)
        self.assertIn("the snaps are 0.1.1, but snap/snapcraft.yaml says 0.1.2", r.stderr)
        self.assertFalse(os.path.exists(self.app_file()) or os.path.exists(self.app_file("0.1.1")))

    def test_missing_architecture(self):
        self.snap("0.1.2", "amd64")
        r = self.run_tool()
        self.assertEqual(r.returncode, 1)
        self.assertIn("missing architecture: arm64", r.stderr)
        self.assertFalse(os.path.exists(self.app_file()))

    @unittest.skipUnless(SQUASHFS, "needs squashfs-tools")
    def test_renamed_snap_is_refused(self):
        self.snap("0.1.2", "amd64")
        shutil.copy(self.snap("0.1.2", "amd64"), os.path.join(self.app, "{}_0.1.2_arm64.snap".format(SNAP)))
        r = self.run_tool()
        self.assertEqual(r.returncode, 1)
        self.assertIn("built for 'amd64', not arm64", r.stderr)
        self.assertFalse(os.path.exists(self.app_file()))

    def test_build_builds_only_missing_or_outdated(self):
        self.snap("0.1.2", "amd64")
        self.snap("0.1.1", "arm64")
        self.fake_build_script("amd64")
        self.fake_build_script("arm64")
        r = self.run_tool("--build")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.build_log(), ["arm64 --clean"])
        self.assertEqual(ctrlx.app_archs(self.app_file()), ["amd64", "arm64"])

    def test_rebuild_builds_both(self):
        self.snap("0.1.2", "amd64")
        self.snap("0.1.2", "arm64")
        self.fake_build_script("amd64")
        self.fake_build_script("arm64")
        r = self.run_tool("--rebuild")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.build_log(), ["amd64 --clean", "arm64 --clean"])

    def test_usage(self):
        self.assertEqual(self.run_tool("--sign", "only-cert").returncode, 2)
        self.assertEqual(self.run_tool("--nope").returncode, 2)

    @unittest.skipUnless(os.path.exists(SIGN_TOOL) and shutil.which("openssl"), "needs the SDK and openssl")
    def test_sign_hands_off_to_sdk(self):
        self.snap("0.1.2", "amd64")
        self.snap("0.1.2", "arm64")
        key, cert = os.path.join(self.tmp.name, "oem.key"), os.path.join(self.tmp.name, "oem.crt")
        subprocess.run(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", key], check=True)
        subprocess.run(["openssl", "req", "-new", "-x509", "-key", key, "-out", cert, "-days", "1",
                        "-subj", "/O=AcmeTest/CN=com.ctrlx-automation.app.acmetest"], check=True)
        r = self.run_tool("--sign", cert, key, env={"CTRLX_SDK": SDK})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Signature verified", r.stdout)
        with tarfile.open(self.app_file(), "r:") as tar:
            files = {m.name for m in tar.getmembers() if m.isfile()}
        expect = {"public/snaps/{}/release/{}_0.1.2.{}".format(a, SNAP, ext)
                  for a in ("amd64", "arm64") for ext in ("snap", "signature")}
        self.assertEqual(files, expect)

    def test_sign_with_missing_key(self):
        self.snap("0.1.2", "amd64")
        self.snap("0.1.2", "arm64")
        r = self.run_tool("--sign", os.path.join(self.tmp.name, "no.crt"), os.path.join(self.tmp.name, "no.key"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("not found", r.stderr)
        self.assertFalse(os.path.exists(self.app_file()))


if __name__ == "__main__":
    unittest.main()
