---
name: package-app
description: Package the amd64 and arm64 snaps of a ctrlX app into ONE .app file with tools/make-app.sh, check it and report the file. Use when the user asks for a packaged app, an .app file, one file for both architectures, or a release / hand-over package. Not part of normal builds.
argument-hint: "apps/<name> [--build] [--sign <oem-cert> <key>]"
---

# Package an app (.app)

On demand only - normal builds stay `./build-snap-amd64.sh` / `./build-snap-arm64.sh` and produce `.snap` files.
Background: `docs/ctrlx/release-and-validation.md` → "`.app` packages", `docs/ctrlx/gotchas.md` #48/#49.

1. `tools/make-app.sh <app>` packs `<snap>_<version>_amd64.snap` + `_arm64.snap` (version from `snap/snapcraft.yaml`)
   into `<app>/<snap>_<version>.app` (uncompressed tar, `public/snaps/<arch>/release/<snap>_<version>.snap`).
   It refuses when an architecture is missing, the versions differ or don't match snapcraft.yaml, or a snap's
   `meta/snap.yaml` disagrees with its file name. Then tell the user what is missing or outdated; if they want
   it built, run `tools/make-app.sh <app> --build` **in the background** (full `--clean` build of each missing
   architecture, ~17 min each in the emulated build VM, gotchas #47). Sources changed since a snap of this
   version was built? Bump the version (snapcraft.yaml + src/pyproject.toml + CHANGELOG), or `--rebuild` both.
2. A hand-over package needs clean release checks: `tools/check.sh <app> --release --snap <app>/<snap>_<ver>_arm64.snap`,
   same for amd64 → `RESULT: OK` (before or after packaging, the snaps are the same files).
3. Check the result: the tool verifies that the archive holds exactly the two snaps (+ `.signature`s when
   signed) and that each equals its `.snap`; confirm with `tar -tvf <app>/<snap>_<ver>.app`.
4. Signing only when the user has OEM App Signing (Bosch-issued certificate + private key):
   `--sign <oem-cert> <key>` hands off to the SDK's `app-signing-tool.sh` and verifies with `verify-apps.sh`.
   Never copy keys or certificates into the kit. Unsigned `.app` files install only where unknown sources are allowed.
5. Install only if the user agreed - the same file on every device: `tools/ctrlx.py [--device core] install
   <app>/<snap>_<ver>.app --service` (the tool checks that the archive contains the device's architecture;
   ask before the first `--service`, never pass `--allow-unknown` unless the user said so). Verify the
   architecture: `tools/ctrlx.py --device <name> ssh 'uname -m; snap info <snap> | grep installed'`, then the
   smoke test of the build-and-deploy skill.
6. Report: path of the `.app`, size, sha256 (printed by the tool), version, architectures, signed or unsigned,
   check results, which devices it was installed on. `.app` files are git-ignored like `.snap` files.
