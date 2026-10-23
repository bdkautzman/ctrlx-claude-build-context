#!/usr/bin/env bash
# Compliance check for a ctrlX app.  Usage: tools/check.sh <app-dir> [--release] [--pre-build] [--snap file.snap] [--ui]
# Runs the unit tests of the app (if any) and the static ctrlX checks. Exit code != 0 on problems.
# --ui: afterwards also tools/ui_check.py (headless Chromium, ~1 min; one-time setup: tools/setup-ui-test.sh)
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="${1:?usage: check.sh <app-dir> [options]}"; shift
UI=0
ARGS=()
for arg in "$@"; do
  if [ "$arg" = "--ui" ]; then UI=1; else ARGS+=("$arg"); fi
done
rc=0
if [ -d "$APP/tests" ]; then
  echo "== unit tests ($APP/tests)"
  out="$(cd "$APP" && python3 -m unittest discover -s tests -t . 2>&1)" || rc=1
  if [ $rc -ne 0 ]; then echo "$out"; else echo "$out" | tail -n 3; fi
fi
echo "== ctrlX checks"
python3 "$HERE/ctrlx_check.py" "$APP" "${ARGS[@]}" || rc=1
if [ $UI = 1 ]; then
  echo "== UI check (headless Chromium)"
  python3 "$HERE/ui_check.py" "$APP" || rc=1
fi
[ $rc -eq 0 ] && echo "== RESULT: OK" || echo "== RESULT: FAILED"
exit $rc
