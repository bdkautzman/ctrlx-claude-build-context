# Release checklist and Bosch Rexroth app validation

For **in-house apps** (installed as unsigned snaps with "allow unknown sources" or OEM-signed),
the release checklist is enough. For **ctrlX World partner apps** (signed by Bosch Rexroth) the
validation artifacts below are mandatory (`upstream-sdk-4.6.0/appdevguide.md` §2, §4).

## Release checklist (`tools/check.sh <app> --release` automates the marked items)

- [x] `version` increased (semver), CHANGELOG entry, `pyproject.toml` version equal
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
apps: **ctrlX license check** (`licensing.md`), app signing by Bosch Rexroth (or OEM signing,
`upstream-sdk-4.6.0/appsigning-oem.md`).

What the validation team checks automatically: `upstream-sdk-4.6.0/appdevguide_basechecks.md`
(most of it is implemented in `tools/ctrlx_check.py`).
