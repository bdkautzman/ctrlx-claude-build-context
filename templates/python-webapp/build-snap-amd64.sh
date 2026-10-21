#!/usr/bin/env bash
# Build the snap for ctrlX COREvirtual / x86 ctrlX OS devices.
# Run inside the ctrlX App Build Environment (SDK 4.6.0 installed, ctrlx-datalayer apt repo set up).
set -euo pipefail
cd "$(dirname "$0")"
../../tools/check.sh . --pre-build
snapcraft clean
snapcraft pack --build-for=amd64 --verbosity=brief
ls -l ./*_amd64.snap
