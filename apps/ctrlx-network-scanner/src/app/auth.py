"""Authorization for requests that arrive through the ctrlX reverse proxy.

Security model (see docs/ctrlx/web-ui-and-reverse-proxy.md):
  * The web server listens ONLY on a Unix socket, so every request comes through the
    ctrlX reverse proxy.
  * All API paths (<prefix>/api...) are listed under "restricted" in the package manifest.
    For those paths the reverse proxy validates the bearer token (signature, expiry)
    before forwarding. That is why we may decode the JWT payload WITHOUT verifying the
    signature here: we only read the scopes of an already-validated token.
  * Static files (HTML/JS/CSS) are NOT restricted and must never contain data.

If you ever add a path outside <prefix>/api that returns data, add it to "restricted".
"""

import base64
import json
import time

from app import config


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


def scopes_from_headers(headers):
    """Return the set of scopes of the caller. Raises AuthError(401) without a usable token."""
    if config.DEV_NO_AUTH:
        return {config.SCOPE_ADMIN}
    auth = headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        raise AuthError(401, "Missing bearer token")
    payload = decode_jwt_payload(auth[7:].strip())
    if payload is None:
        raise AuthError(401, "Malformed bearer token")
    exp = payload.get("exp")
    if isinstance(exp, (int, float)) and exp < time.time():
        raise AuthError(401, "Token expired - reopen the app from the ctrlX menu")
    return scopes_from_payload(payload)


def can_read(scopes):
    return bool(scopes & {config.SCOPE_ADMIN, config.SCOPE_WRITE, config.SCOPE_READ})


def can_write(scopes):
    return bool(scopes & {config.SCOPE_ADMIN, config.SCOPE_WRITE})


def require(headers, write):
    """Return scopes or raise AuthError(401/403)."""
    scopes = scopes_from_headers(headers)
    allowed = can_write(scopes) if write else can_read(scopes)
    if not allowed:
        raise AuthError(403, "Missing permission " + (config.SCOPE_WRITE if write else config.SCOPE_READ))
    return scopes


def user_name(headers):
    """Login name of the caller (JWT claim "name", ctrlX tokens carry it) for logs and scan records."""
    if config.DEV_NO_AUTH:
        return "dev"
    auth = headers.get("Authorization", "")
    payload = decode_jwt_payload(auth[7:].strip()) if auth.lower().startswith("bearer ") else None
    if not payload:
        return ""
    for key in ("name", "preferred_username", "sub"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value[:64]
    return ""
