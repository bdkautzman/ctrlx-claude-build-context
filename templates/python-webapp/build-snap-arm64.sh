#!/usr/bin/env bash
# Build the snap for ctrlX CORE hardware (X3/X5/X7: arm64).
# Cross-build on amd64 works in the ctrlX App Build Environment (multiarch set up by the SDK
# script install-required-packages.sh). Warnings "Unable to determine library dependencies"
# may appear when cross-building and can be ignored (none in the 0.1.2 build).
set -euo pipefail
cd "$(dirname "$0")"
../../tools/check.sh . --pre-build
snapcraft clean
snapcraft pack --build-for=arm64 --verbosity=brief
ls -l ./*_arm64.snap
