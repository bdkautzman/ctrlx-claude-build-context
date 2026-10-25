# Platform baseline (pinned facts)

| Item | Value | Source |
|---|---|---|
| Target OS | ctrlX OS **4.6** (ctrlX CORE X3/X5/X7 = arm64, ctrlX COREvirtual = amd64) | user setup |
| Ubuntu Core base | **core24** (Python 3.12 inside the snap) | SDK 4.6.0 samples all use `base: core24` |
| SDK | ctrlX AUTOMATION SDK **4.6.0** (git tag `4.6.0`) | github.com/boschrexroth/ctrlx-automation-sdk |
| snapcraft | **8.x** (`snap install snapcraft --channel=8.x/stable --classic`) | SDK `scripts/install-snapcraft.sh` |
| Build env vars | `SNAPCRAFT_BUILD_ENVIRONMENT=host`, `SNAPCRAFT_ENABLE_EXPERIMENTAL_TARGET_ARCH=1`, `SNAPCRAFT_BUILD_INFO=1` (set in /etc/environment by the SDK script) | same |
| Python Data Layer package | `ctrlx-datalayer<=3.6` + `ctrlx-fbs` (PyPI); native lib via apt package `ctrlx-datalayer` (.deb from the SDK release zip) | SDK samples `pyproject.toml` |
| Data Layer API docs | 3.6.0 → `reference/ctrlxdatalayer-api-3.6.0/` | SDK `doc/api/python` |
| UI test browser (optional) | Playwright **1.64.1** (PyPI) with its chrome-headless-shell 156.0.8078.4, venv `~/.cache/ctrlx-kit/uivenv`; Ubuntu 24.04 libs + `fonts-liberation` via apt | `tools/setup-ui-test.sh` |
| PDF check (optional) | pypdfium2 **5.14.0** (PyPI, bundles PDFium) in the same venv, for `tools/pdf_check.py`; dev only, never in a snap | `tools/setup-ui-test.sh` |
| package-manifest schema | v1.7 (released with ctrlX OS 4.4; v1.6 is what SDK 4.6 samples reference — 1.7 only adds `licenses[].supportsTrial`, widget `noInitialCreation`) | `tools/schemas/` |
| Old bases | core20 = ctrlX OS 1.x, core22 = ctrlX OS 2.x/3.x. Do not use them for 4.x apps: the base snap may be missing on the device (a forum case: a core20 snap failed to install on a 2.x core with "base core20" error) and the SDK 4.6 libraries are built for core24 | SDK history, example repos, forum |

## Paths inside the snap at runtime

| Variable | Typical value | Use |
|---|---|---|
| `$SNAP` | `/snap/<name>/current` (read-only) | code, `www/`, `defaults/`, package-assets |
| `$SNAP_DATA` | `/var/snap/<name>/current` (per revision) | `package-run/<name>/web.sock`, `.datalayer` |
| `$SNAP_COMMON` | `/var/snap/<name>/common` (all revisions) | `solutions/` mount (app data) |
| app data dir | `$SNAP_COMMON/solutions/activeConfiguration/<name>/` | `settings.json` |
| socket | `$SNAP_DATA/package-run/<name>/web.sock` | reverse proxy → web server |
| `/tmp` | private per snap | scratch (RAM) |

## Network facts

* ctrlX COREvirtual with **port forwarding**: from the App Build Environment VM the device is
  `10.0.2.2`, HTTPS port `8443` (host port forwarded to 443); from the Windows host it is
  `localhost:8443`. With a network adapter: `192.168.1.1`. Real CORE: its IP, port 443.
  (SDK `doc/remote-debug-python.md`, `ctrlx_datalayer_helper.py` of the samples)
* Data Layer in the snap: connection string **`ipc://`** — no credentials. Outside the snap:
  `tcp://user:password@host?sslport=8443` (ports 2069/2070 are obsolete).
* Default credentials of a new device/COREvirtual: `boschrexroth` / `boschrexroth` — never in snap code.

## Resource guidelines (App Guide 6.2)

RAM < 75 MB per app, snap < 100 MB, **no swap** on ctrlX CORE, **no cyclic writes to internal
flash** (logs, diagnostics, high-rate values), must not disturb real-time tasks.
