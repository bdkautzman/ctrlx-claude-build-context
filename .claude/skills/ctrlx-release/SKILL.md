---
name: ctrlx-release
description: Prepare a release of a ctrlX app (version bump, release checks, both architectures, fossinfo, changelog, validation files). Use when the user wants to release or hand over an app version.
disable-model-invocation: true
argument-hint: "apps/<name> <new-version>"
---

# Release

Follow `docs/ctrlx/release-and-validation.md`.

1. Set the version ($ARGUMENTS) in `snap/snapcraft.yaml` (quoted semver, higher than the last) and `pyproject.toml`;
   CHANGELOG entry with user-visible changes; `grade: stable`.
2. Clean build of amd64 and arm64; `tools/foss_from_prime.py <app> --write`, fill any TODO license texts.
3. `tools/check.sh <app> --release --snap <arm64 snap>` → OK; same for the amd64 snap.
4. Validation files in `validation/` match the snap (the checker verifies slots/plugs).
5. Report: versions, snap file names + sizes, check results, what was tested on which device, open items
   (manual tests from build-deploy-test.md §4 that only the user can do).
