#!/usr/bin/env python3
"""Create a new ctrlX app from templates/python-webapp.

    python3 tools/new_app.py --name ctrlx-acme-conveyor --title "Conveyor Monitor" \
        --author "ACME Automation" --url https://acme.example

Creates apps/<name>/ with every identifier replaced (snap name, manifest id, proxy URL,
scopes, Data Layer root, app-data directory, file names), then runs the checks.
"""

import argparse
import os
import re
import shutil
import stat
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
TEMPLATE = os.path.join(KIT, "templates", "python-webapp")

T_NAME = "ctrlx-acme-template"
T_TITLE = "Template App"
T_AUTHOR = "ACME Automation"
T_URL = "https://example.com"
TEXT_EXT = {".py", ".json", ".yaml", ".yml", ".toml", ".md", ".sh", ".html", ".js", ".css", ".txt", ""}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True, help="snap name: ctrlx-<company>-<app>, lowercase, <= 29 chars")
    ap.add_argument("--title", required=True, help="display title, 3..40 chars")
    ap.add_argument("--author", required=True, help="company / author for the snap description")
    ap.add_argument("--url", required=True, help="company / product URL for the snap description")
    ap.add_argument("--dest", default=os.path.join(KIT, "apps"))
    args = ap.parse_args()

    name, title = args.name.strip(), args.title.strip()
    if not re.fullmatch(r"ctrlx-[a-z0-9]+(-[a-z0-9]+)+", name):
        sys.exit("name must look like ctrlx-<company>-<app> (lowercase letters, digits, single dashes)")
    if len(name) > 29:
        sys.exit("name must be <= 29 characters (socket path limit of the reverse proxy)")
    if not 3 <= len(title) <= 40:
        sys.exit("title must be 3..40 characters")
    if '"' in title or "\\" in title:
        sys.exit("title must not contain quotes or backslashes")

    dest = os.path.join(args.dest, name)
    if os.path.exists(dest):
        sys.exit("already exists: " + dest)
    common = shutil.ignore_patterns(".dev", "__pycache__", "*.pyc", "*.snap")

    def ignore(directory, names):
        skip = set(common(directory, names))
        if os.path.abspath(directory) == os.path.abspath(TEMPLATE):
            # snapcraft build output and local venv of the template (after a template build)
            skip |= {n for n in names if n in ("parts", "stage", "prime", "overlay", ".venv")}
        return skip

    shutil.copytree(TEMPLATE, dest, ignore=ignore)

    replacements = [(T_NAME, name), (T_TITLE, title), (T_AUTHOR, args.author), (T_URL, args.url)]
    for root, _, files in os.walk(dest):
        for fn in files:
            path = os.path.join(root, fn)
            if os.path.splitext(fn)[1] not in TEXT_EXT:
                continue
            with open(path, "r", encoding="utf-8", newline="") as f:
                text = f.read()
            new = text
            for old, rep in replacements:
                new = new.replace(old, rep)
            if new != text:
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(new)
            if T_NAME in fn:
                os.rename(path, os.path.join(root, fn.replace(T_NAME, name)))

    for rel in ("wrapper/run.sh", "build-snap-amd64.sh", "build-snap-arm64.sh", "run-local.sh"):
        p = os.path.join(dest, rel)
        if os.path.exists(p):
            os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # a new app starts at 0.1.0, whatever version the template itself is at
    for rel, pattern, rep in (("snap/snapcraft.yaml", r"(?m)^version: .*$", "version: 0.1.0"),
                              ("src/pyproject.toml", r'(?m)^version = ".*"$', 'version = "0.1.0"')):
        p = os.path.join(dest, rel)
        with open(p, "r", encoding="utf-8", newline="") as f:
            text = f.read()
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(re.sub(pattern, rep, text, count=1))

    with open(os.path.join(dest, "CHANGELOG.md"), "w", encoding="utf-8") as f:
        f.write("# Changelog\n\nVersions follow semantic versioning and must match `version:` in "
                "`snap/snapcraft.yaml`.\n\n## 0.1.0\n\n- Created from the ctrlX Python web-app template.\n")

    print("Created", dest)
    print("Running checks ...")
    rc = subprocess.call([os.path.join(HERE, "check.sh"), dest])
    print("\nNext: edit defaults/settings.json, src/app/settings_rules.py, src/app/datalayer_nodes.py, www/;\n"
          "then ./run-local.sh, ../../tools/check.sh ., ./build-snap-amd64.sh")
    return rc


if __name__ == "__main__":
    sys.exit(main())
