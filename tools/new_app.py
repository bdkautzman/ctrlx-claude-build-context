#!/usr/bin/env python3
"""Create a new ctrlX app from templates/python-webapp.

    python3 tools/new_app.py --name ctrlx-acme-conveyor --title "Conveyor Monitor" \
        --author "ACME Automation" --url https://acme.example
    python3 tools/new_app.py --name conveyor --title "Conveyor Monitor"     # with kit.toml

Creates apps/<name>/ with every identifier replaced (snap name, manifest id, proxy URL,
scopes, Data Layer root, app-data directory, widget tag, file names), then runs the checks.

Defaults come from <kit>/kit.toml (git-ignored; template kit.example.toml): `author`, `url`, the optional
`company` and `license`. A --name without the "ctrlx-" prefix becomes ctrlx-<company>-<name> (ctrlx-<name> without
a company).

License: MIT unless --license (or kit.toml license) names another SPDX id (user's request 2026-11-10). It goes into
LICENSE (MIT: the full text with this year and the author), snap/snapcraft.yaml `license:` and src/app/config.py
APP_LICENSE (the About dialog); for another license LICENSE gets a placeholder - put that license's text in.
"""

import argparse
import datetime
import os
import re
import shutil
import stat
import subprocess
import sys

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    tomllib = None

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
TEMPLATE = os.path.join(KIT, "templates", "python-webapp")

T_NAME = "ctrlx-acme-template"
T_TITLE = "Template App"
T_AUTHOR = "ACME Automation"
T_URL = "https://example.com"
TEXT_EXT = {".py", ".json", ".yaml", ".yml", ".toml", ".md", ".sh", ".html", ".js", ".css", ".svg", ".txt", ""}
KIT_TOML = os.path.join(KIT, "kit.toml")
DEFAULT_LICENSE = "MIT"
SPDX_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]*")


def load_kit_defaults(path=KIT_TOML):
    """author / url / company / license from kit.toml ({} without the file)."""
    if not os.path.exists(path):
        return {}
    if tomllib is None:
        sys.exit("reading {} needs Python 3.11+ (tomllib)".format(path))
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return {k: str(data[k]).strip() for k in ("author", "url", "company", "license") if k in data}


def apply_license(dest, license_id, author, year):
    """LICENSE, snap/snapcraft.yaml `license:` and config.APP_LICENSE of a new app (they must agree:
    tests/test_about.py). MIT: the template's MIT text with this year and the author; another SPDX id: a placeholder."""
    path = os.path.join(dest, "LICENSE")
    if license_id == "MIT":
        with open(path, encoding="utf-8") as f:
            text = f.read()
        text = re.sub(r"(?m)^Copyright \(c\) \d{4} .*$", "Copyright (c) {} {}".format(year, author), text, count=1)
    else:
        text = ("{lic}\n\nCopyright (c) {year} {author}\n\nTODO: replace this paragraph with the full text of the {lic} "
                "license (https://spdx.org/licenses/{lic}.html).\n").format(lic=license_id, year=year, author=author)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    for rel, pattern, rep in (("snap/snapcraft.yaml", r"(?m)^license: *\S+", "license: " + license_id),
                              ("src/app/config.py", r'(?m)^APP_LICENSE = "[^"]*"', 'APP_LICENSE = "{}"'.format(license_id))):
        p = os.path.join(dest, rel)
        with open(p, encoding="utf-8", newline="") as f:
            text, n = re.subn(pattern, rep, f.read(), count=1)
        if n != 1:
            raise SystemExit("{}: no license line to set".format(rel))
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)


def snap_name(name, company=""):
    """--name as given (ctrlx-...), or ctrlx-[<company>-]<name> for a short name."""
    name = name.strip()
    if name.startswith("ctrlx-"):
        return name
    return "ctrlx-" + (company + "-" if company else "") + name


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True,
                    help="snap name ctrlx-[<company>-]<app> (lowercase, <= 29 chars), or just <app>: "
                         "the prefix then comes from kit.toml (company)")
    ap.add_argument("--title", required=True, help="display title, 3..40 chars")
    ap.add_argument("--author", help="company / author for the snap description (default: kit.toml author)")
    ap.add_argument("--url", help="company / product URL: snap description, help menu link (default: kit.toml url)")
    ap.add_argument("--license", help="SPDX license id (default: kit.toml license, else MIT)")
    ap.add_argument("--dest", default=os.path.join(KIT, "apps"))
    args = ap.parse_args()

    defaults = load_kit_defaults()
    author = (args.author or defaults.get("author") or "").strip()
    url = (args.url or defaults.get("url") or "").strip()
    if not author or not url:
        sys.exit("--author and --url are needed (or set author / url in kit.toml, template kit.example.toml)")
    company = defaults.get("company", "")
    license_id = (args.license or defaults.get("license") or DEFAULT_LICENSE).strip()
    if not SPDX_ID.fullmatch(license_id):
        sys.exit("--license must be an SPDX id like MIT or Apache-2.0")
    name, title = snap_name(args.name, company), args.title.strip()
    if not re.fullmatch(r"ctrlx-[a-z0-9]+(-[a-z0-9]+)*", name):
        sys.exit("name must look like ctrlx-<app> or ctrlx-<company>-<app> (lowercase letters, digits, single dashes)")
    if company and not name.startswith("ctrlx-{}-".format(company)):
        print("Note: kit.toml has company = {!r}, but {} does not contain it".format(company, name))
    if len(name) > 29:
        sys.exit("name must be <= 29 characters (socket path limit of the reverse proxy)")
    if not 3 <= len(title) <= 40:
        sys.exit("title must be 3..40 characters")
    if '"' in title or "\\" in title:
        sys.exit("title must not contain quotes or backslashes")

    dest = os.path.join(args.dest, name)
    if os.path.exists(dest):
        sys.exit("already exists: " + dest)
    common = shutil.ignore_patterns(".dev", ".ui-check", "__pycache__", "*.pyc", "*.snap")

    def ignore(directory, names):
        skip = set(common(directory, names))
        if os.path.abspath(directory) == os.path.abspath(TEMPLATE):
            # snapcraft build output and local venv of the template (after a template build)
            skip |= {n for n in names if n in ("parts", "stage", "prime", "overlay", ".venv", ".craft")}
        return skip

    shutil.copytree(TEMPLATE, dest, ignore=ignore)

    replacements = [(T_NAME, name), (T_TITLE, title), (T_AUTHOR, author), (T_URL, url)]
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

    apply_license(dest, license_id, author, datetime.date.today().year)
    if license_id != "MIT":
        print("Note: put the full text of the {} license into {}".format(license_id, os.path.join(dest, "LICENSE")))

    with open(os.path.join(dest, "CHANGELOG.md"), "w", encoding="utf-8") as f:
        f.write("# Changelog\n\nVersions follow semantic versioning and must match `version:` in "
                "`snap/snapcraft.yaml`.\n\n## 0.1.0\n\n- Created from the ctrlX Python web-app template.\n")

    print("Created", dest)
    print("Running checks ...")
    rc = subprocess.call([os.path.join(HERE, "check.sh"), dest])
    print("\nNext: edit defaults/settings.json, src/app/settings_rules.py, src/app/datalayer_nodes.py, www/;\n"
          "then ./run-local.sh, ../../tools/check.sh ., ./build-snap-amd64.sh\n"
          "Test users for the roles on each test device: tools/ctrlx.py --device <device> test-users\n"
          "(missing: ask the user, then test-users create)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
