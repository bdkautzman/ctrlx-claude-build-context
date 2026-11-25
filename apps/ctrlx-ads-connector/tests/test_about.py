"""About dialog data (config.APP_*): the same author, URL and license as snap/snapcraft.yaml and LICENSE.

tools/new_app.py writes all of them for a new app (kit.toml / --author --url --license); change them together.
"""

import os
import re
import unittest

from app import config

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


class AboutTest(unittest.TestCase):
    def test_license_the_same_everywhere(self):
        snap = re.search(r"(?m)^license: *(\S+)", read("snap/snapcraft.yaml"))
        self.assertIsNotNone(snap, "snap/snapcraft.yaml has no license:")
        self.assertEqual(snap.group(1), config.APP_LICENSE)
        text = read("LICENSE")
        if config.APP_LICENSE == "MIT":
            self.assertTrue(text.startswith("MIT License"), "LICENSE is not the MIT text")
        self.assertRegex(text, r"Copyright \(c\) \d{4} " + re.escape(config.APP_AUTHOR))

    def test_author_and_url_as_in_snapcraft(self):
        snap = read("snap/snapcraft.yaml")
        self.assertIn("author: " + config.APP_AUTHOR + "\n", snap)
        self.assertIn("url: " + config.APP_URL + "\n", snap)

    def test_ai_note(self):
        self.assertIn("AI", config.APP_AI_NOTE)


if __name__ == "__main__":
    unittest.main()
