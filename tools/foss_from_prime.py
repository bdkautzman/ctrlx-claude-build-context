#!/usr/bin/env python3
"""List the Python packages inside a built snap and update fossinfo.json versions.

After `snapcraft pack`, the files are in <app>/prime/ (or unpack a .snap with unsquashfs).

    python3 tools/foss_from_prime.py apps/<app>                    # report only
    python3 tools/foss_from_prime.py apps/<app> --write            # fill versions, add missing components
    python3 tools/foss_from_prime.py apps/<app> --site <dir>       # explicit site-packages dir

It reads */site-packages/*.dist-info/METADATA (name, version, license, home page).
New components get license name/spdx from the metadata but a TODO license TEXT: copy the
real license text (dist-info/LICENSE* or the project page) - check.sh --release fails on TODO.
Debian stage-packages (e.g. libs pulled in by ctrlx-datalayer) are not covered: check
prime/usr/share/doc/*/copyright for those.
"""

import argparse
import glob
import json
import os
import re
import sys
from email.parser import Parser

# venv tooling of the python plugin; the template primes it out (snap/snapcraft.yaml, part app)
SKIP = {"pip", "setuptools", "wheel", "packaging", "pkg-resources", "_distutils_hack"}


def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def read_dists(site, own=()):
    """Python distributions in site-packages, without venv tooling and the app's own package."""
    skip = {normalize(n) for n in SKIP} | {normalize(n) for n in own}
    dists = []
    for meta in glob.glob(os.path.join(site, "*.dist-info", "METADATA")):
        with open(meta, encoding="utf-8", errors="replace") as f:
            msg = Parser().parse(f, headersonly=True)
        name = msg.get("Name", "")
        if not name or normalize(name) in skip:
            continue
        lic = msg.get("License-Expression") or msg.get("License") or ""
        if len(lic) > 80:
            lic = ""
        if not lic:
            for c in msg.get_all("Classifier") or []:
                if c.startswith("License ::"):
                    lic = c.split("::")[-1].strip()
        dist_dir = os.path.dirname(meta)
        lic_files = sorted(glob.glob(os.path.join(dist_dir, "LICEN[CS]E*")) + glob.glob(os.path.join(dist_dir, "licenses", "*")))
        dists.append({"name": name, "version": msg.get("Version", ""), "license": lic,
                      "homepage": msg.get("Home-page") or "", "license_file": lic_files[0] if lic_files else ""})
    return sorted(dists, key=lambda d: d["name"].lower())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("app_dir")
    ap.add_argument("--site", help="site-packages directory (default: <app>/prime/lib/python3*/site-packages)")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    site = args.site or next(iter(glob.glob(os.path.join(args.app_dir, "prime", "lib", "python3*", "site-packages"))), None)
    if not site or not os.path.isdir(site):
        sys.exit("site-packages not found - build the snap first (snapcraft pack keeps prime/) or pass --site")
    with open(os.path.join(args.app_dir, "snap", "snapcraft.yaml"), encoding="utf-8") as f:
        name = next(l.split(":", 1)[1].strip() for l in f if l.startswith("name:"))
    dists = read_dists(site, own=[name])  # pyproject name == snap name (tools/new_app.py)
    print("{:30} {:15} {}".format("package", "version", "license"))
    for d in dists:
        print("{:30} {:15} {}".format(d["name"], d["version"], d["license"] or "?"))

    foss = os.path.join(args.app_dir, "configs", "package-assets", name + ".fossinfo.json")
    with open(foss, encoding="utf-8") as f:
        doc = json.load(f)
    comps = {c["name"].lower(): c for c in doc.get("components", [])}
    changed = False
    for d in dists:
        c = comps.get(d["name"].lower())
        if c is None:
            text = "TODO: paste the license text"
            if d["license_file"]:
                with open(d["license_file"], encoding="utf-8", errors="replace") as f:
                    text = f.read()
            c = {"name": d["name"], "version": d["version"] or "unknown", "homepage": d["homepage"] or "https://pypi.org/project/" + d["name"],
                 "usage": "AsIs", "integrationMechanism": "LinkedDynamically",
                 "licenses": [{"name": d["license"] or "TODO", "text": text}]}
            doc["components"].append(c)
            print("  + add", d["name"])
            changed = True
        elif d["version"] and c.get("version") != d["version"]:
            print("  ~ {} version {} -> {}".format(d["name"], c.get("version"), d["version"]))
            c["version"] = d["version"]
            changed = True
    installed = {d["name"].lower() for d in dists}
    for cname in comps:
        if cname not in installed:
            print("  ? {} is in fossinfo but not in site-packages (remove it if it is not shipped)".format(cname))
    if args.write and changed:
        with open(foss, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2)
            f.write("\n")
        print("updated", foss)
    elif changed:
        print("(dry run - use --write to update {})".format(foss))


if __name__ == "__main__":
    main()
