#!/usr/bin/env bash
# Build the snap of a ctrlX app - called by <app>/build-snap-amd64.sh and build-snap-arm64.sh.
#
#   tools/build-snap.sh <app-dir> <amd64|arm64> [--clean]
#
# Incremental by default: snapcraft re-runs only the parts whose sources changed (a full build
# takes ~20 min in an App Build Environment without hardware acceleration, gotchas #47).
# A full clean build runs when
#   * --clean is given: for builds you hand over or release, after updating the SDK or the
#     ctrlx-datalayer .deb (snapcraft does not notice a newer .deb), or when a build behaves oddly
#   * the target architecture differs from the last build in this folder (marker parts/.build-for):
#     the build state of the other architecture must not be reused
set -euo pipefail

usage() { echo "usage: $0 <app-dir> <amd64|arm64> [--clean]" >&2; exit 2; }
[ $# -ge 2 ] || usage
APP_DIR=$1
ARCH=$2
shift 2
case "$ARCH" in amd64|arm64) ;; *) usage ;; esac
CLEAN=0
for arg in "$@"; do
  case "$arg" in
    --clean) CLEAN=1 ;;
    *) usage ;;
  esac
done

TOOLS=$(cd "$(dirname "$0")" && pwd)
cd "$APP_DIR"
"$TOOLS/check.sh" . --pre-build

MARKER=parts/.build-for
LAST=$(cat "$MARKER" 2>/dev/null || true)
if [ "$CLEAN" = 1 ]; then
  echo "== full build (--clean)"
elif [ "$LAST" != "$ARCH" ]; then
  echo "== full build: last build here was '${LAST:-none}', now $ARCH"
  CLEAN=1
else
  echo "== incremental build ($ARCH) - use --clean for builds you hand over"
fi
[ "$CLEAN" = 1 ] && snapcraft clean

snapcraft pack --build-for="$ARCH" --verbosity=brief
mkdir -p parts && echo "$ARCH" > "$MARKER"
ls -l ./*_"$ARCH".snap
echo "== done in $((SECONDS / 60)) min $((SECONDS % 60)) s"
