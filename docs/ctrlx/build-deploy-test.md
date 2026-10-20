# Build, deploy, test

## 0. One-time: App Build Environment (Ubuntu VM from ctrlX WORKS)

Recommended by Bosch Rexroth: ctrlX WORKS → App Build Environment (QEMU VM, user/password
`boschrexroth`, SSH on host port 10022). Run **Claude Code inside this VM** (or WSL/Ubuntu 24.04
with the same setup) so it can run snapcraft itself. Then (SDK README, `upstream-sdk-4.6.0/install-sources-from-github.md`):

```bash
wget https://raw.githubusercontent.com/boschrexroth/ctrlx-automation-sdk/main/scripts/install-required-packages.sh
wget https://raw.githubusercontent.com/boschrexroth/ctrlx-automation-sdk/main/scripts/install-snapcraft.sh
wget https://raw.githubusercontent.com/boschrexroth/ctrlx-automation-sdk/main/scripts/clone-install-sdk.sh
chmod +x *.sh && ./install-required-packages.sh && ./install-snapcraft.sh
./clone-install-sdk.sh          # choose tag 4.6.0 when asked
# the clone script merges the release zip (contains the ctrlx-datalayer .deb) into the clone:
find ~/ctrlx-automation-sdk -name 'ctrlx-datalayer*.deb'
ctrlx-automation-sdk/scripts/install-ctrlx-datalayer.sh <folder containing that .deb>
sudo apt-get install -y python3-yaml python3-jsonschema squashfs-tools   # for tools/check.sh
```

Verify: `apt-cache policy ctrlx-datalayer` shows a candidate; `snap list snapcraft` shows 8.x;
`python3 tools/ctrlx_check.py templates/python-webapp --pre-build` has no `ENV-*` errors.
Put the kit on the VM with git (preferred) or scp; keep LF line endings (see gotchas).

## 1. Inner loop (no snap, seconds)

```bash
cd apps/<name>
python3 -m unittest discover -s tests -t .        # unit tests (fake Data Layer)
./run-local.sh                                     # http://127.0.0.1:8080/<name>/ , auth off
CTRLX_HOST=10.0.2.2 CTRLX_SSL_PORT=8443 ./run-local.sh   # + real Data Layer of the COREvirtual
```
For the last one install the Python deps in a venv (`python3 -m venv .venv && . .venv/bin/activate
&& pip install ctrlx-datalayer'<=3.6' ctrlx-fbs`) – the native lib comes from the .deb.

## 2. Check and build (minutes)

```bash
../../tools/check.sh .              # tests + ctrlX static checks; must be clean
./build-snap-amd64.sh               # COREvirtual  -> <name>_<ver>_amd64.snap
./build-snap-arm64.sh               # CORE X3/X5/X7 -> <name>_<ver>_arm64.snap (cross build)
python3 ../../tools/ctrlx_check.py . --snap <name>_<ver>_amd64.snap   # inspect the built snap
python3 ../../tools/foss_from_prime.py . --write   # sync fossinfo versions with prime/
```
Cross-build warnings "Unable to determine library dependencies" are expected (SDK quick start).

## 3. Deploy to ctrlX COREvirtual

ctrlX WORKS → create/start a COREvirtual (port forwarding), log in once in the browser
(https://localhost:8443 from Windows), then Settings → Apps → allow installation from
unknown sources (or `--allow-unknown` below).

```bash
export CTRLX_HOST=10.0.2.2:8443           # from the App Build Environment VM
../../tools/ctrlx.py install <name>_<ver>_amd64.snap
../../tools/ctrlx.py packages
../../tools/ctrlx.py dl-read <name>/status/heartbeat
../../tools/ctrlx.py appdata <name>/settings.json
../../tools/ctrlx.py logs --search <name>
```
Manual alternative: web UI → Settings → Apps → Install from file.
The COREvirtual's license is the 4-hour engineering license; it shuts down after 4 h.

## 4. Smoke test on the device (every release)

1. App appears in Apps with the right version, state "running"; no errors in Logbook.
2. Sidebar/overview entry opens the UI in a new tab; values update; settings save.
3. Users & Permissions: a user with only `.r` sees read-only UI; without scopes the API returns 403.
4. Settings → Data Layer: `<name>/…` nodes readable; writable ones accept/reject correctly.
5. Manage app data: `<name>/settings.json` exists; save a configuration, change a setting,
   load the configuration → the old value is back (load phases in the log).
6. Reboot the device → app comes back, settings persisted.
7. arm64 snap on real hardware before release (different CPU, slower).

## Debugging

* Logs: Diagnostics → Logbook (filter on the snap) or `tools/ctrlx.py logs`. With SSH
  (needs a user with SSH rights): `sudo snap logs -f <name>`, `snap services <name>`,
  `snap connections <name>`, `sudo snap run --shell <name>.service` then inspect `$SNAP_COMMON/solutions`.
* Python remote debugging with debugpy: `upstream-sdk-4.6.0/remote-debug-python.md` and the SDK
  sample `reference/sdk-4.6.0-samples-python/datalayer.remote.debug` (debug port only on demand!).
