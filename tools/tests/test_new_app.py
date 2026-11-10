"""tools/new_app.py: the license of a new app (MIT by default) lands in LICENSE, snapcraft.yaml and config.py."""

import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import new_app  # noqa: E402


class ApplyLicenseTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dest = tmp.name
        for rel in ("LICENSE", "snap/snapcraft.yaml", "src/app/config.py"):
            os.makedirs(os.path.dirname(os.path.join(self.dest, rel)), exist_ok=True)
            shutil.copy(os.path.join(new_app.TEMPLATE, rel), os.path.join(self.dest, rel))

    def read(self, rel):
        with open(os.path.join(self.dest, rel), encoding="utf-8") as f:
            return f.read()

    def test_default_is_mit(self):
        self.assertEqual(new_app.DEFAULT_LICENSE, "MIT")
        new_app.apply_license(self.dest, "MIT", "Jane Doe", 2031)
        text = self.read("LICENSE")
        self.assertTrue(text.startswith("MIT License\n\nCopyright (c) 2031 Jane Doe\n"))
        self.assertIn("Permission is hereby granted", text)
        self.assertRegex(self.read("snap/snapcraft.yaml"), r"(?m)^license: MIT\b")
        self.assertIn('APP_LICENSE = "MIT"', self.read("src/app/config.py"))

    def test_other_license(self):
        new_app.apply_license(self.dest, "Apache-2.0", "Jane Doe", 2031)
        self.assertTrue(self.read("LICENSE").startswith("Apache-2.0\n\nCopyright (c) 2031 Jane Doe\n"))
        self.assertRegex(self.read("snap/snapcraft.yaml"), r"(?m)^license: Apache-2\.0 ")
        self.assertIn('APP_LICENSE = "Apache-2.0"', self.read("src/app/config.py"))

    def test_spdx_ids(self):
        for ok in ("MIT", "Apache-2.0", "GPL-3.0-or-later", "LicenseRef-Proprietary"):
            self.assertTrue(new_app.SPDX_ID.fullmatch(ok), ok)
        for bad in ("", "MIT License", "../x", "-MIT"):
            self.assertFalse(new_app.SPDX_ID.fullmatch(bad), bad)


class TemplateTest(unittest.TestCase):
    def test_template_license_is_mit(self):
        with open(os.path.join(new_app.TEMPLATE, "LICENSE"), encoding="utf-8") as f:
            text = f.read()
        self.assertTrue(text.startswith("MIT License"))
        self.assertIn(new_app.T_AUTHOR, text)          # new_app replaces it with the app's author
        with open(os.path.join(new_app.TEMPLATE, "snap", "snapcraft.yaml"), encoding="utf-8") as f:
            self.assertTrue(re.search(r"(?m)^license: MIT\b", f.read()))


if __name__ == "__main__":
    unittest.main()
