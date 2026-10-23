#!/usr/bin/env bash
# One-time setup for tools/ui_check.py: headless Chromium driven by Playwright, version-pinned.
#
#   tools/setup-ui-test.sh          install / repair (user level, no root), then verify
#   tools/setup-ui-test.sh --check  only verify (exit 0 = ready)
#
# Installs, outside the kit and outside every snap:
#   ~/.cache/ctrlx-kit/uivenv   Python venv with playwright==$PLAYWRIGHT_VERSION   (CTRLX_UI_VENV overrides)
#   ~/.cache/ms-playwright/     Playwright's matching chrome-headless-shell (~280 MB, downloaded once)
# The App Build Environment (Ubuntu 24.04 server-like VM) lacks a few shared libraries and has no fonts:
# the script prints ONE sudo apt-get line for them instead of running it (system change = the user's call).
# Pinned on purpose: a Playwright version always uses the same browser build. Bump PLAYWRIGHT_VERSION
# deliberately (like the SDK), re-run this script, re-run tools/ui_check.py on the template.
set -euo pipefail

PLAYWRIGHT_VERSION=1.64.1
VENV="${CTRLX_UI_VENV:-$HOME/.cache/ctrlx-kit/uivenv}"
# Ubuntu 24.04 packages of the libraries chrome-headless-shell links against that a server install lacks
# (found with ldd, 2026-10-23), plus fonts - without fonts every text renders as boxes.
APT_PACKAGES="libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libasound2t64 libatk1.0-0t64 \
libatk-bridge2.0-0t64 libatspi2.0-0t64 libgbm1 fonts-liberation"

CHECK=0
case "${1:-}" in
  --check) CHECK=1 ;;
  "") ;;
  *) echo "usage: $0 [--check]" >&2; exit 2 ;;
esac
fail() { echo "NOT READY: $*" >&2; exit 1; }

# 1. venv + pinned Playwright
if [ ! -x "$VENV/bin/python" ]; then
  [ "$CHECK" = 1 ] && fail "no venv at $VENV - run tools/setup-ui-test.sh"
  echo "== creating $VENV"
  mkdir -p "$(dirname "$VENV")"
  python3 -m venv "$VENV"
fi
have=$("$VENV/bin/python" -c "import importlib.metadata as m; print(m.version('playwright'))" 2>/dev/null || true)
if [ "$have" != "$PLAYWRIGHT_VERSION" ]; then
  [ "$CHECK" = 1 ] && fail "playwright ${have:-missing} in $VENV, want $PLAYWRIGHT_VERSION - run tools/setup-ui-test.sh"
  echo "== installing playwright==$PLAYWRIGHT_VERSION (pip uses http(s)_proxy from the environment)"
  "$VENV/bin/pip" install -q "playwright==$PLAYWRIGHT_VERSION"
fi

# 2. the browser build that belongs to this Playwright version (headless shell only, no full Chrome)
location=$("$VENV/bin/playwright" install --dry-run --only-shell chromium 2>/dev/null \
  | awk '/Install location:/ && /chromium_headless_shell/ {print $3; exit}')
[ -n "$location" ] || fail "cannot determine the browser location (playwright install --dry-run)"
browser=$(find "$location" -name chrome-headless-shell -type f 2>/dev/null | head -1)
if [ -z "$browser" ]; then
  [ "$CHECK" = 1 ] && fail "browser not downloaded - run tools/setup-ui-test.sh"
  echo "== downloading the headless browser (~110 MB) to $location"
  "$VENV/bin/playwright" install --only-shell chromium
  browser=$(find "$location" -name chrome-headless-shell -type f | head -1)
fi

# 3. system libraries and fonts (needs root: print, don't run)
missing=$(ldd "$browser" | awk '/not found/ {print $1}' | sort -u | tr '\n' ' ')
fonts=$(find /usr/share/fonts -name '*.ttf' 2>/dev/null | head -1)
if [ -n "$missing" ] || [ -z "$fonts" ]; then
  [ -n "$missing" ] && echo "Missing libraries: $missing"
  [ -z "$fonts" ] && echo "No fonts installed (text would render as boxes)."
  echo "Run once (Ubuntu 24.04, ~25 packages, ~42 MB), then this script again:"
  echo "  sudo apt-get install -y --no-install-recommends $APT_PACKAGES"
  echo "(other distributions: $VENV/bin/playwright install-deps --dry-run chromium)"
  exit 1
fi

# 4. it really starts
echo "== starting the browser once (takes ~15 s in an emulated build VM)"
"$VENV/bin/python" - <<'EOF'
from playwright.sync_api import sync_playwright
with sync_playwright() as pw:
    browser = pw.chromium.launch(args=["--no-proxy-server"])
    page = browser.new_page()
    page.set_content("<p id=t>ok</p>")
    assert page.inner_text("#t") == "ok"
    print("browser", browser.version)
    browser.close()
EOF
echo "READY: python3 tools/ui_check.py apps/<app> [--device NAME]"
