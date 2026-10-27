#!/usr/bin/env bash
# Build the snap for ctrlX CORE hardware (X3/X5/X7: arm64), cross-built on amd64 in the ctrlX
# App Build Environment (multiarch set up by the SDK script install-required-packages.sh).
# Warnings "Unable to determine library dependencies" may appear when cross-building - ignore them.
#   ./build-snap-arm64.sh            incremental (default) - only changed parts are rebuilt
#   ./build-snap-arm64.sh --clean    full clean build: for builds you hand over / release
# Logic and rules: ../../tools/build-snap.sh
exec "$(dirname "$0")/../../tools/build-snap.sh" "$(dirname "$0")" arm64 "$@"
