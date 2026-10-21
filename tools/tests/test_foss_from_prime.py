"""Self-test of tools/foss_from_prime.py:  python3 -m unittest discover -s tools/tests"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import foss_from_prime  # noqa: E402


def make_dist(site, name, version):
    d = os.path.join(site, "{}-{}.dist-info".format(name.replace("-", "_"), version))
    os.makedirs(d)
    with open(os.path.join(d, "METADATA"), "w", encoding="utf-8") as f:
        f.write("Metadata-Version: 2.1\nName: {}\nVersion: {}\nLicense: MIT\n\n".format(name, version))


class FossFromPrimeTest(unittest.TestCase):
    def test_skips_venv_tooling_and_own_package(self):
        with tempfile.TemporaryDirectory() as site:
            for name in ("pip", "setuptools", "wheel", "packaging", "ctrlx-datalayer", "ctrlx_acme_template"):
                make_dist(site, name, "1.0")
            names = [d["name"] for d in foss_from_prime.read_dists(site, own=["ctrlx-acme-template"])]
            self.assertEqual(names, ["ctrlx-datalayer"])


if __name__ == "__main__":
    unittest.main()
