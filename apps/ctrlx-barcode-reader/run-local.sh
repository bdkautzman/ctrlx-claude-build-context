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
#
# OpenCV, numpy, zxing-cpp and Aravis are not on the build VM: README.md "Local development" puts them
# into .dev/pylib and .dev/aravis (git-ignored); PyGObject is the VM's python3-gi. Without them the web UI
# runs, reads end with "Aravis is not available".
set -euo pipefail
cd "$(dirname "$0")"
eval "$(./dev-env.sh)"
export APP_DEV_NO_AUTH=1
exec python3 -m app.main
