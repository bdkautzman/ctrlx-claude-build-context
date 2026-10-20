---
name: record-gotcha
description: Record a newly discovered ctrlX/snapcraft problem and its fix in docs/ctrlx/gotchas.md (and propose a checker rule). Use after solving a non-obvious build, deploy or runtime problem.
---

# Record a gotcha

1. Append to the "Found during development" section of `docs/ctrlx/gotchas.md`:
   `- YYYY-MM-DD - **symptom (exact error text)** → cause → fix. Source: <build log / device / link>.`
2. If it moves an item out of "Unverified assumptions", update that list too.
3. If a static check could have caught it: propose a rule for `tools/ctrlx_check.py` with a code name and a test in
   `tools/tests/test_ctrlx_check.py` (ask before editing tools/).
4. If a doc in `docs/ctrlx/` was wrong or incomplete, fix that doc as well (never `upstream-sdk-*`).
