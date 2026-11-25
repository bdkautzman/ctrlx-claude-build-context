"""Authorization for requests that arrive through the ctrlX reverse proxy.

Security model (see docs/ctrlx/web-ui-and-reverse-proxy.md):
  * The web server listens ONLY on a Unix socket, so every request comes through the
    ctrlX reverse proxy.
  * All API paths (<prefix>/api...) are listed under "restricted" in the package manifest.
    For those paths the reverse proxy validates the bearer token (signature, expiry)
    before forwarding. That is why we may decode the JWT payload WITHOUT verifying the
    signature here: we only read the scopes of an already-validated token.
  * Static files (HTML/JS/CSS) are NOT restricted and must never contain data.

Roles (each includes the ones before it; the scopes are declared in the package manifest):
  view       <snap>.web.all.r       status, values, results
  operate    <snap>.web.operate.x   + write runtime values, start/stop/trigger actions
  configure  <snap>.web.all.rw      + settings (and the ctrlX admin scope rexroth-device.all.rwx)
Every API route calls require(headers, <role>). Writes through the Data Layer itself are not
affected: there the ctrlX user's Data Layer permissions apply.

If you ever add a path outside <prefix>/api that returns data, add it to "restricted".
"""

import base64
import json
import time

from app import config

VIEW, OPERATE, CONFIGURE = "view", "operate", "configure"
ROLES = (VIEW, OPERATE, CONFIGURE)   # lowest first
ROLE_SCOPES = {VIEW: config.SCOPE_VIEW, OPERATE: config.SCOPE_OPERATE, CONFIGURE: config.SCOPE_CONFIGURE}


class AuthError(Exception):
    def __init__(self, status, title):
        super().__init__(title)
        self.status = status
        self.title = title


def decode_jwt_payload(token):
    """Return the JWT payload as dict (no signature check), or None if malformed."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        return data if isinstance(data, dict) else None
    except (ValueError, UnicodeError):
        return None


def scopes_from_payload(payload):
    scope = payload.get("scope", [])
    if isinstance(scope, str):
        return set(scope.split())
    if isinstance(scope, list):
        return {str(s) for s in scope}
    return set()


def _payload(headers):
    """The JWT payload of the request. Raises AuthError(401) without a usable token."""
    auth = headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        raise AuthError(401, "Missing bearer token")
    payload = decode_jwt_payload(auth[7:].strip())
    if payload is None:
        raise AuthError(401, "Malformed bearer token")
    exp = payload.get("exp")
    if isinstance(exp, (int, float)) and exp < time.time():
        raise AuthError(401, "Token expired - reopen the app from the ctrlX menu")
    return payload


def scopes_from_headers(headers):
    """Return the set of scopes of the caller. Raises AuthError(401) without a usable token."""
    if config.DEV_NO_AUTH:
        return {config.SCOPE_ADMIN}
    return scopes_from_payload(_payload(headers))


def user_name(headers):
    """Login name of the caller (ctrlX tokens carry the claim "name") for log lines - "" if unknown."""
    if config.DEV_NO_AUTH:
        return "dev"
    try:
        payload = _payload(headers)
    except AuthError:
        return ""
    for key in ("name", "preferred_username", "sub"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value[:64]
    return ""


def role_of(scopes):
    """The highest role the scopes give (VIEW / OPERATE / CONFIGURE), or None for no access."""
    if scopes & {config.SCOPE_ADMIN, config.SCOPE_CONFIGURE}:
        return CONFIGURE
    if config.SCOPE_OPERATE in scopes:
        return OPERATE
    if config.SCOPE_VIEW in scopes:
        return VIEW
    return None


def allows(scopes, role):
    """True when the scopes give `role` or a higher one."""
    have = role_of(scopes)
    return have is not None and ROLES.index(have) >= ROLES.index(role)


def require(headers, role=VIEW):
    """Return the caller's scopes or raise AuthError(401 no/invalid token, 403 role missing)."""
    if role not in ROLES:   # a bug in the route, not a bad request: 500 + log
        raise RuntimeError("unknown role " + repr(role))
    scopes = scopes_from_headers(headers)
    if not allows(scopes, role):
        raise AuthError(403, "Missing permission: {} ({})".format(role, ROLE_SCOPES[role]))
    return scopes
