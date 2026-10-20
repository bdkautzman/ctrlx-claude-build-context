#!/usr/bin/env python3
"""PostToolUse hook: after editing a ctrlX integration file of an app, run the fast static
check for that app and feed ERRORS back to Claude (exit code 2 = stderr is shown to Claude).

Only these files trigger it (unit tests are NOT run here - too slow for every edit):
  snap/snapcraft.yaml, configs/package-assets/*.json, validation/*.json, pyproject.toml,
  defaults/settings.json, wrapper/*
Any problem inside the hook itself is ignored (exit 0) so it never blocks work.
"""
import json
import os
import re
import subprocess
import sys

TRIGGER = re.compile(r"(snap/snapcraft\.yaml|configs/package-assets/.*\.json|validation/.*\.json|"
                     r"pyproject\.toml|defaults/settings\.json|wrapper/.*)$")


def main():
    try:
        event = json.load(sys.stdin)
    except Exception:
        return 0
    tool_input = event.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("path") or ""
    if not path:
        return 0
    project = os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or os.getcwd()
    rel = os.path.relpath(os.path.abspath(path), project).replace(os.sep, "/")
    m = re.match(r"^((?:apps/[^/]+)|templates/python-webapp)/(.+)$", rel)
    if not m or not TRIGGER.search(m.group(2)):
        return 0
    app_dir = os.path.join(project, m.group(1))
    checker = os.path.join(project, "tools", "ctrlx_check.py")
    try:
        res = subprocess.run([sys.executable, checker, app_dir, "-q"], capture_output=True, text=True, timeout=60)
    except Exception:
        return 0
    if res.returncode == 1:
        sys.stderr.write("ctrlX check found errors after editing {}:\n{}\nFix them (see docs/ctrlx/ and "
                         "docs/ctrlx/gotchas.md).\n".format(rel, res.stdout.strip()))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
