#!/usr/bin/env bash
# Run the app WITHOUT building a snap - the fastest development loop.
#
#   ./run-local.sh                      web UI + settings only (Data Layer disabled)
#   (eval "$(../../tools/ctrlx.py env)"; ./run-local.sh)
#                                       + Data Layer provider against the default device of
#                                         devices.toml (or set CTRLX_HOST/CTRLX_SSL_PORT/...): a COREvirtual
#                                         (port forwarding, seen from the App Build Environment;
#                                          needs ctrlx-datalayer installed in the venv + the
#                                          ctrlx-datalayer .deb on this machine)
#
# A controller with the host "fake-plc" is the demo PLC of src/app/dev_fake_plc.py (local runs only).
# Open http://127.0.0.1:8080/<snap-name>/  (auth disabled locally via APP_DEV_NO_AUTH=1)
# App data is emulated in ./.dev/solutions/activeConfiguration/<snap-name>/settings.json
#
# UI feedback overlay (dev only, the kit's tools/ui-feedback/overlay.js): Alt+click an element of the page, type a
# note -> ./.dev/feedback.json; the skill /ui-feedback works through the notes. APP_DEV_FEEDBACK=0 ./run-local.sh: off.
# Reaching the page from outside the VM (port forwarding): APP_DEV_HTTP_HOST=0.0.0.0 ./run-local.sh
set -euo pipefail
cd "$(dirname "$0")"
export APP_DEV_NO_AUTH=1
overlay="$(cd ../.. && pwd)/tools/ui-feedback/overlay.js"
if [ "${APP_DEV_FEEDBACK:-}" != "0" ] && [ -f "$overlay" ]; then
  export APP_DEV_FEEDBACK="$overlay"
else
  unset APP_DEV_FEEDBACK
fi
# the package is in src/; pyads for local runs in .dev/pylib (README.md "Develop": pip install ... --target .dev/pylib)
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
[ -d "$PWD/.dev/pylib" ] && export PYTHONPATH="$PYTHONPATH:$PWD/.dev/pylib"
exec python3 -m app.main
