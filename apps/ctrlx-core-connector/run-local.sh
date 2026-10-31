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
# Open http://127.0.0.1:8080/<snap-name>/  (auth disabled locally via APP_DEV_NO_AUTH=1)
# App data is emulated in ./.dev/solutions/activeConfiguration/<snap-name>/settings.json
set -euo pipefail
cd "$(dirname "$0")"
export APP_DEV_NO_AUTH=1
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"   # the package is in src/
exec python3 -m app.main
