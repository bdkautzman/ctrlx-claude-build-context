"""Self-test of tools/ctrlx_check.py: the template must pass, broken copies must fail.

    python3 -m unittest discover -s tools/tests
"""

import os
import shutil
import stat
import sys
import tempfile
import unittest

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT = os.path.dirname(TOOLS)
TEMPLATE = os.path.join(KIT, "templates", "python-webapp")
sys.path.insert(0, TOOLS)
import ctrlx_check  # noqa: E402

NAME = "ctrlx-acme-template"


def codes(app_dir, *extra):
    rep, ctx = ctrlx_check.Report(), {}
    if ctrlx_check.check_snapcraft(app_dir, rep, ctx, "--release" in extra) is not None:
        ctrlx_check.check_sources(app_dir, rep, ctx)
        ctrlx_check.check_manifest(app_dir, rep, ctx, ctrlx_check.DEFAULT_SCHEMAS)
        ctrlx_check.check_fossinfo(app_dir, rep, ctx, ctrlx_check.DEFAULT_SCHEMAS, "--release" in extra)
        ctrlx_check.check_validation_files(app_dir, rep, ctx, ctrlx_check.DEFAULT_SCHEMAS, "--release" in extra)
    return {i["code"] for i in rep.errors}


class CheckerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.app = os.path.join(self.tmp, "app")
        shutil.copytree(TEMPLATE, self.app, ignore=shutil.ignore_patterns(".dev", "__pycache__"))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def edit(self, rel_path, old, new):
        p = os.path.join(self.app, rel_path)
        with open(p, encoding="utf-8") as f:
            s = f.read()
        self.assertIn(old, s, "test setup: text not found in " + rel_path)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(s.replace(old, new))

    def test_template_passes(self):
        self.assertEqual(codes(self.app), set())

    def test_wrong_base(self):
        self.edit("snap/snapcraft.yaml", "base: core24", "base: core22")
        self.assertIn("SNAP-BASE", codes(self.app))

    def test_reserved_plug(self):
        self.edit("snap/snapcraft.yaml", "      - active-solution\n    slots:", "      - active-solution\n      - docker\n    slots:")
        found = codes(self.app)
        self.assertIn("IFACE-RESERVED", found)
        self.assertIn("VAL-SLOTPLUG", found)  # also not described in validation/

    def test_numeric_version(self):
        self.edit("snap/snapcraft.yaml", "version: 0.1.0", "version: 1.0")
        self.assertIn("SNAP-VERSION", codes(self.app))

    def test_bad_binding_syntax(self):
        self.edit("configs/package-assets/%s.package-manifest.json" % NAME, "{$SNAP_DATA}", "${SNAP_DATA}")
        self.assertIn("PROXY-BINDING", codes(self.app))

    def test_menu_without_items(self):
        self.edit("configs/package-assets/%s.package-manifest.json" % NAME,
                  '"target": "_blank",\n        "permissions": [\n          "ctrlx-acme-template.web.all.rw"\n        ],\n        "items": []',
                  '"target": "_blank",\n        "permissions": [\n          "ctrlx-acme-template.web.all.rw"\n        ]')
        self.assertIn("MANIFEST-SCHEMA", codes(self.app))

    def test_crlf_wrapper(self):
        self.edit("wrapper/run.sh", "\n", "\r\n")
        self.assertIn("SRC-CRLF", codes(self.app))

    def test_wrapper_not_executable(self):
        p = os.path.join(self.app, "wrapper", "run.sh")
        os.chmod(p, os.stat(p).st_mode & ~(stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
        self.assertIn("SRC-EXEC-BIT", codes(self.app))

    def test_unlisted_python_package(self):
        os.makedirs(os.path.join(self.app, "app", "extra"))
        open(os.path.join(self.app, "app", "extra", "__init__.py"), "w").close()
        self.assertIn("PY-PACKAGES", codes(self.app))

    def test_missing_datalayer_plug(self):
        self.edit("snap/snapcraft.yaml", "      - datalayer\n", "")
        self.edit("snap/snapcraft.yaml", "  datalayer:\n    interface: content\n    content: datalayer\n    target: $SNAP_DATA/.datalayer\n", "")
        self.assertIn("IFACE-DATALAYER", codes(self.app))

    def test_manifest_id_mismatch(self):
        self.edit("configs/package-assets/%s.package-manifest.json" % NAME, '"id": "%s"' % NAME, '"id": "other"')
        self.assertIn("MANIFEST-ID", codes(self.app))

    def test_release_mode_fossinfo_todo(self):
        self.assertIn("FOSS-TODO", codes(self.app, "--release"))

    def test_hardcoded_solutions_path(self):
        self.edit("app/config.py", 'SOLUTIONS_DIR = os.path.join(SNAP_COMMON, "solutions")',
                  'SOLUTIONS_DIR = "/var/snap/rexroth-solutions/common/solutions"')
        self.assertIn("SRC-SOLUTIONS-PATH", codes(self.app))


if __name__ == "__main__":
    unittest.main()
