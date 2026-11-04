#!/usr/bin/env bash
# Prints the environment for running the app or its tests outside the snap:  eval "$(./dev-env.sh)"
# src/ + .dev/pylib (pinned wheels) on PYTHONPATH, .dev/aravis (Ubuntu's Aravis .debs, extracted) for
# the library and the typelib. How to fill .dev/: README.md "Local development".
cd "$(dirname "$0")"
triplet="$(gcc -dumpmachine 2>/dev/null || echo x86_64-linux-gnu)"
py="$PWD/src"
[ -d "$PWD/.dev/pylib" ] && py="$py:$PWD/.dev/pylib"
echo "export PYTHONPATH='$py'\${PYTHONPATH:+:\$PYTHONPATH}"
lib="$PWD/.dev/aravis/usr/lib/$triplet"
if [ -d "$lib" ]; then
  echo "export LD_LIBRARY_PATH='$lib'\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
  echo "export GI_TYPELIB_PATH='$lib/girepository-1.0'\${GI_TYPELIB_PATH:+:\$GI_TYPELIB_PATH}"
fi
