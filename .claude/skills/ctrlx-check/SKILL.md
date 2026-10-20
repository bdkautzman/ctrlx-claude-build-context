---
name: ctrlx-check
description: Run the ctrlX compliance checks and unit tests for an app and fix every finding. Use after changes, before builds, or when the user asks to check/validate an app.
argument-hint: "[apps/<name>] [--release]"
allowed-tools: Bash(tools/check.sh *) Bash(python3 tools/ctrlx_check.py *)
---

# Check an app

1. Run `tools/check.sh $ARGUMENTS` (default: the app you are working on; add `--pre-build` before a snap build,
   `--release` before handing over a release).
2. For each ERROR: find the code (e.g. `PROXY-BINDING`) in `docs/ctrlx/gotchas.md` and the docs referenced in
   the message, fix the root cause (never silence the checker, never edit `tools/` to make a finding go away).
3. WARNINGs: fix them or explain in your answer why they are acceptable. `ctrlx-check: ignore` comments only
   for dev-only code paths that never run in the snap.
4. Re-run until `RESULT: OK`. If the checker is wrong (false positive), tell the user and propose a fix to
   `tools/ctrlx_check.py` + a test in `tools/tests/` - don't change it unasked.
