#!/usr/bin/env bash
# Compliance check for a ctrlX app.  Usage: tools/check.sh <app-dir> [--release] [--pre-build] [--snap file.snap]
# Runs the unit tests of the app (if any) and the static ctrlX checks. Exit code != 0 on problems.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="${1:?usage: check.sh <app-dir> [options]}"; shift
rc=0
if [ -d "$APP/tests" ]; then
  echo "== unit tests ($APP/tests)"
  out="$(cd "$APP" && python3 -m unittest discover -s tests -t . 2>&1)" || rc=1
  if [ $rc -ne 0 ]; then echo "$out"; else echo "$out" | tail -n 3; fi
fi
echo "== ctrlX checks"
python3 "$HERE/ctrlx_check.py" "$APP" "$@" || rc=1
[ $rc -eq 0 ] && echo "== RESULT: OK" || echo "== RESULT: FAILED"
exit $rc
