"""Static configuration and environment detection.

Everything that depends on "am I running inside the snap on a ctrlX or locally
in the build environment?" is decided here, in one place.
"""

import os

# ---------------------------------------------------------------------------
# Identity. SNAP_NAME must equal `name:` in snap/snapcraft.yaml, the manifest `id`,
# the proxy URL prefix and the app-data directory name. tools/new_app.py rewrites it.
# ---------------------------------------------------------------------------
SNAP_NAME = "ctrlx-acme-template"
APP_TITLE = "Template App"

# About dialog (help menu -> About, GET /status "about"). tools/new_app.py writes the new app's author, URL and license
# (kit.toml or its options) here, into snap/snapcraft.yaml and LICENSE; tests/test_about.py checks they agree.
APP_AUTHOR = "ACME Automation"
APP_URL = "https://example.com"
APP_LICENSE = "MIT"                       # SPDX identifier; the license text is in LICENSE
APP_AI_NOTE = "This app was created with the help of AI (Claude Code by Anthropic)."

# URL prefix the reverse proxy forwards to us (package-manifest services.proxyMapping[].url)
URL_PREFIX = "/" + SNAP_NAME
API_PREFIX = URL_PREFIX + "/api/v1"

# Root of all Data Layer nodes this app provides
DATALAYER_ROOT = SNAP_NAME

# Scopes declared in the package manifest (scopes-declaration) - three roles, each includes the one before
# (auth.py, docs/ctrlx/web-ui-and-reverse-proxy.md "Roles"):
#   view       see status, values and results
#   operate    + run the app: write runtime values, start/stop/trigger actions (no settings)
#   configure  + settings and everything else
SCOPE_VIEW = SNAP_NAME + ".web.all.r"
SCOPE_OPERATE = SNAP_NAME + ".web.operate.x"
SCOPE_CONFIGURE = SNAP_NAME + ".web.all.rw"
SCOPE_ADMIN = "rexroth-device.all.rwx"  # built-in ctrlX admin scope: always full access

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
IN_SNAP = "SNAP" in os.environ
_HERE = os.path.dirname(os.path.abspath(__file__))              # <project>/src/app
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_HERE))         # <project>

if IN_SNAP:
    SNAP_DIR = os.environ["SNAP"]                  # read-only squashfs
    SNAP_DATA = os.environ["SNAP_DATA"]            # per revision, writable
    SNAP_COMMON = os.environ["SNAP_COMMON"]        # across revisions, writable
    WWW_DIR = os.path.join(SNAP_DIR, "www")
    DEFAULTS_DIR = os.path.join(SNAP_DIR, "defaults")
    SOCKET_DIR = os.path.join(SNAP_DATA, "package-run", SNAP_NAME)
    SOCKET_PATH = os.path.join(SOCKET_DIR, "web.sock")
    SOLUTIONS_DIR = os.path.join(SNAP_COMMON, "solutions")
else:
    # Local development (App Build Environment / your PC): everything relative to the project
    SNAP_DIR = _PROJECT_ROOT
    SNAP_DATA = os.path.join(_PROJECT_ROOT, ".dev")
    SNAP_COMMON = SNAP_DATA
    WWW_DIR = os.path.join(_PROJECT_ROOT, "www")
    DEFAULTS_DIR = os.path.join(_PROJECT_ROOT, "defaults")
    SOCKET_DIR = ""
    SOCKET_PATH = ""
    SOLUTIONS_DIR = os.path.join(SNAP_DATA, "solutions")

# $SNAP_COMMON/solutions/activeConfiguration -> symlink maintained by the Solutions app
ACTIVE_CONFIGURATION_DIR = os.path.join(SOLUTIONS_DIR, "activeConfiguration")
# Our directory inside the active configuration (= configuration.appDirectories[].name)
APPDATA_DIR = os.path.join(ACTIVE_CONFIGURATION_DIR, SNAP_NAME)
SETTINGS_FILE = os.path.join(APPDATA_DIR, "settings.json")
DEFAULT_SETTINGS_FILE = os.path.join(DEFAULTS_DIR, "settings.json")

# Local development web server (only used outside the snap)
DEV_HTTP_HOST = os.environ.get("APP_DEV_HTTP_HOST", "127.0.0.1")
DEV_HTTP_PORT = int(os.environ.get("APP_DEV_HTTP_PORT", "8080"))
# Outside the snap there is no reverse proxy validating tokens. Set APP_DEV_NO_AUTH=1 to let
# the local dev server treat every request as admin. NEVER has any effect inside the snap.
DEV_NO_AUTH = (not IN_SNAP) and os.environ.get("APP_DEV_NO_AUTH") == "1"
# Dev-only UI feedback overlay (app/dev_feedback.py, skill /ui-feedback): run-local.sh sets APP_DEV_FEEDBACK to the
# kit's tools/ui-feedback/overlay.js. Only together with DEV_NO_AUTH, so never in the snap; ui_check.py removes it.
_FEEDBACK_JS = os.environ.get("APP_DEV_FEEDBACK", "")
DEV_FEEDBACK_JS = _FEEDBACK_JS if DEV_NO_AUTH and _FEEDBACK_JS not in ("", "0") else ""
DEV_FEEDBACK_FILE = os.path.join(SNAP_DATA, "feedback.json")     # <project>/.dev/feedback.json

# Data Layer connection. In the snap: "ipc://" (no credentials needed).
# Locally: only if CTRLX_HOST is set, e.g. CTRLX_HOST=10.0.2.2 CTRLX_SSL_PORT=8443 for a
# ctrlX COREvirtual with port forwarding seen from the App Build Environment.
CTRLX_HOST = os.environ.get("CTRLX_HOST", "")
CTRLX_USER = os.environ.get("CTRLX_USER", "boschrexroth")  # dev only  ctrlx-check: ignore
CTRLX_PASSWORD = os.environ.get("CTRLX_PASSWORD", "boschrexroth")  # dev only  ctrlx-check: ignore
CTRLX_SSL_PORT = int(os.environ.get("CTRLX_SSL_PORT", "443"))


def datalayer_connection_string():
    """Return the Data Layer connection string, or "" when the Data Layer is disabled."""
    if IN_SNAP:
        return "ipc://"
    if not CTRLX_HOST:
        return ""
    conn = "tcp://{}:{}@{}".format(CTRLX_USER, CTRLX_PASSWORD, CTRLX_HOST)
    if CTRLX_SSL_PORT != 443:
        conn += "?sslport={}".format(CTRLX_SSL_PORT)
    return conn


def app_version():
    """Snap version inside the snap, 'dev' otherwise."""
    return os.environ.get("SNAP_VERSION", "dev")
