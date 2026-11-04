#!/usr/bin/env bash
# Build the snap for ctrlX COREvirtual / x86 ctrlX OS devices.
#   ./build-snap-amd64.sh            incremental (default) - only changed parts are rebuilt
#   ./build-snap-amd64.sh --clean    full clean build: for builds you hand over / release
# Logic and rules: ../../tools/build-snap.sh
exec "$(dirname "$0")/../../tools/build-snap.sh" "$(dirname "$0")" amd64 "$@"
