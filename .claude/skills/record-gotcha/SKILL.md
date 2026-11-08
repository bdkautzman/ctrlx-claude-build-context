---
name: record-gotcha
description: Record a newly discovered ctrlX/snapcraft problem and its fix in docs/ctrlx/gotchas.md (and propose a checker rule). Use after solving a non-obvious build, deploy or runtime problem.
---

# Record a gotcha

1. Append to the end of `docs/ctrlx/gotchas.md` (section "Found during development"), with the next number:
   `N. **symptom (exact error text)** (device, versions, YYYY-MM-DD) → cause → fix. Source: <build log / device / link>.`
   Check first that it isn't there yet (grep the error text, then the topic index at the top).
   Add `#N <hint of 2-6 words>` to its topic in "Index by topic" at the top (more than one topic if it fits;
   a new topic only when none fits). `python3 -m unittest discover -s tools/tests` checks the index.
2. If it moves an item out of "Unverified assumptions", update that list too.
3. If a static check could have caught it: propose a rule for `tools/ctrlx_check.py` with a code name and a test in
   `tools/tests/test_ctrlx_check.py` (ask before editing tools/).
4. If a doc in `docs/ctrlx/` was wrong or incomplete, fix that doc as well (never `upstream-sdk-*`).
5. If it concerns a specific app: add it (with evidence, what was tried, status) to that app's `KNOWN-ISSUES.md`
   and reference the gotcha number there. App-specific findings that teach nothing generic go only there.
