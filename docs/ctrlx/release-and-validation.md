# Release checklist and Bosch Rexroth app validation

For **in-house apps** (installed as unsigned snaps with "allow unknown sources" or OEM-signed),
the release checklist is enough. For **ctrlX World partner apps** (signed by Bosch Rexroth) the
validation artifacts below are mandatory (`upstream-sdk-4.6.0/appdevguide.md` §2, §4).

## Release checklist (`tools/check.sh <app> --release` automates the marked items)

- [x] `version` increased (semver), CHANGELOG entry, `src/pyproject.toml` version equal
- [x] `grade: stable`, `confinement: strict`, `base: core24`, restart-delay ≥ 5s
- [x] description with `<!-- author/url -->`
- [x] fossinfo complete, no TODO (run `tools/foss_from_prime.py --write` after the build)
- [x] `validation/*.json` match snapcraft.yaml
- [ ] both snaps built (amd64 + arm64) from a clean tree, inspected with `--snap`
- [ ] smoke test on COREvirtual and on real hardware (`build-deploy-test.md` §4)
- [ ] no debug ports / debug code enabled, no default credentials, no hardcoded paths into other snaps
- [ ] RAM < 75 MB in steady state (Settings → System → resources, or `ps` via SSH), snap < 100 MB
- [ ] tag the commit `v<version>`

## Validation artifacts (partner apps)

Template folder structure: `upstream-sdk-4.6.0/` → `appdevguide.md` §4.1 and `artifacts.zip` in the SDK.

| Folder | File | Source in this kit |
|---|---|---|
| disclosure | `fossinfo.json` (or CycloneDX), `foss-sources.zip` or `foss-offer.x` | `configs/package-assets/<name>.fossinfo.json` |
| buildinfo | `snapcraft.yaml`, `package-manifest.json`, `portlist-description.json`, `unixsocket-description.json`, `slotplug-description.json` | `snap/`, `configs/`, `validation/` |
| documentation | `manual.pdf`, `test-setup-description.pdf`, `release-notes.pdf` | write per app |
| appstates | `standard-scenario1.json` (Postman collection of REST calls) | write per app |
| snaps | `ctrlx-<company>-<app>_<version>_arm64.snap` (mandatory), `_amd64.snap` (recommended) | build |

Plus `architecture-overview.x` for the initial meeting. Mandatory runtime aspects for partner
apps: **ctrlX license check** (out of scope for this kit, which builds unlicensed apps -
see `upstream-sdk-4.6.0/licensing.md` if that changes), app signing by Bosch Rexroth (or OEM signing,
`upstream-sdk-4.6.0/appsigning-oem.md`).

What the validation team checks automatically: `upstream-sdk-4.6.0/appdevguide_basechecks.md`
(most of it is implemented in `tools/ctrlx_check.py`).

## `.app` packages (one file for amd64 + arm64)

Official apps ship as `<snap>_<version>.app`: a tar archive with
`public/snaps/{amd64,arm64}/release/<snap>_<version>.snap` and a `.signature` per snap; the device
installs the snap of its own architecture. Signing needs either Bosch Rexroth (partner validation)
or **OEM App Signing** (contract; your private key + a Bosch-issued OEM certificate that must be
uploaded to the device certificate store "App signature validation certificates"). Packaging and
signing: SDK `scripts/app-signing-tool.sh <oem-cert> <key> <snap-dir> [out-dir]`, check with
`scripts/verify-apps.sh`; details `upstream-sdk-4.6.0/appsigning-oem.md`. Reserved interfaces
additionally need a Bosch-signed app-privileges declaration.
For unofficial apps the same tar layout works **without** signatures on devices that allow unknown
sources (gotchas #48); the device then still reports "System integrity degraded: UNSIGNED_APPS".
One two-architecture `.app` installs on both the COREvirtual (amd64) and a real CORE (arm64), each
picking its own snap (verified 2026-10-22, gotchas #49).

In this kit a `.app` is made **on demand only** (hand-over, "one file for both architectures"; skill
`package-app`) - normal builds and tests stay with the `.snap` files:
```bash
tools/make-app.sh apps/<app>             # <snap>_<ver>_{amd64,arm64}.snap -> apps/<app>/<snap>_<ver>.app
tools/make-app.sh apps/<app> --build     # first build missing/outdated architectures (--clean, ~17 min each)
tools/make-app.sh apps/<app> --rebuild   # first build both architectures (--clean)
tools/make-app.sh apps/<app> --sign <oem-cert> <key>   # OEM-signed: SDK app-signing-tool.sh + verify-apps.sh
tools/ctrlx.py [--device core] install apps/<app>/<snap>_<ver>.app --service   # same file on every device
```
- The version comes from `snap/snapcraft.yaml`; the tool refuses when an architecture is missing, the
  two versions differ, or they don't match snapcraft.yaml, and checks name, version and architecture
  in each snap's `meta/snap.yaml` (a renamed file is caught).
- Unsigned output: uncompressed tar, only the two `.snap` files (no `.signature`), owner root, sorted.
  The tool verifies that each member equals its `.snap` and prints size and sha256.
- `--sign`: SDK at `$CTRLX_SDK` (default `~/ctrlx-automation-sdk`); an `apps/<app>/<snap>.privileges`
  (Bosch-signed app-privileges declaration) is passed on. Keys and certificates never go into the kit.
- `tools/ctrlx.py install` reads the archive and refuses a `.app` without the device's architecture.
- `.app` files are git-ignored like `.snap` files; build each snap with `--clean` before handing it over.
