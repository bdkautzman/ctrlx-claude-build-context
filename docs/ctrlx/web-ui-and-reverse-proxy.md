# Web UI behind the ctrlX reverse proxy

Implementation: `templates/python-webapp/src/app/web_server.py`, `src/app/auth.py`, `www/`.

## Request path

```
Browser ──HTTPS──► ctrlX reverse proxy (port 443) ──HTTP over Unix socket──► app
                   • TLS                              $SNAP_DATA/package-run/<name>/web.sock
                   • token check for "restricted" paths
                   • forwards the FULL path: /<name>/api/v1/... (no prefix stripping)
```

* Bind **only** to the Unix socket in the snap (App Guide 6.1.2/8.1). No TCP ports → no
  `portlist` entries, no firewall/port conflicts. Remove a stale socket file before binding,
  `chmod 0660`, delete on shutdown.
* Every route starts with `/<name>`. `/<name>` should redirect to `/<name>/` so relative
  asset URLs (`style.css`, `app.js`) resolve below the prefix. Use **relative URLs** in HTML.
* Python stdlib `http.server` + `socketserver.UnixStreamServer` with `ThreadingMixIn`
  (no framework → no extra FOSS, small snap). `get_request()` must return a fake
  `("local", 0)` client address (BaseHTTPRequestHandler expects a tuple).
* Outside the snap the same server runs on `127.0.0.1:8080` (`./run-local.sh`).

## Authentication & authorization

1. Menu link `"/<name>/?token=${bearertoken}"` → ctrlX inserts the user's JWT.
2. `www/app.js` moves the token from the URL to `sessionStorage` (removes it from history) and
   sends `Authorization: Bearer <token>` with every API call.
3. Manifest `restricted: ["/<name>/api"]` → the proxy validates signature/expiry of the token
   for those paths and rejects invalid ones.
4. The app reads the `scope` claim (list) from the JWT payload **without** signature
   verification - safe only because of step 3 and the Unix-socket-only binding.
5. Read endpoints need `<name>.web.all.r` or `.rw` or `rexroth-device.all.rwx`; write endpoints
   need `.rw` or admin. Return 401 (no/invalid token) / 403 (missing scope) as Problem JSON.
6. Static files are unrestricted (the page must load without a header) → **never put data or
   secrets into static files**; anything data-bearing goes under `/api`.

Token lifetime is limited; on 401 the UI tells the user to reopen the app from the ctrlX menu.

Alternative: restricted path object with `"headersForwarding": ["X-Auth-Scopes", ...]` lets
the proxy pass decoded claims as headers (format not verified in this kit - see gotchas).

## UI guidelines

* No CDNs/external fonts: devices are often offline. Vendor small libs into `www/` and list
  them in `fossinfo.json`.
* Keep it light (ctrlX CORE X3 is a small ARM CPU). Poll ≥ 1–2 s or use the ctrlX Data Layer
  REST/SSE APIs from the browser if you need live data at higher rates.
  Live views (`apps/ctrlx-amr-fleet`, 250 ms): poll with a revision (`GET /fleet?since=<rev>`) and
  answer only a few status bytes when nothing changed; interpolate in the browser between updates;
  slow down to ≥ 2 s while `document.hidden`. (CPU load on a real CORE: not measured yet.)
* **Images/binary data from the API**: `<img src="api/...">` can't send the `Authorization` header
  (→ 401 on restricted paths). Use `fetch()` with the header → `res.blob()` → `createImageBitmap()`
  (canvas) or `URL.createObjectURL()` (`<img>`). Content-addressed URLs (hash in the path) can be
  cached: `Cache-Control: private, max-age=...`.
* Large pictures (maps, layouts): serve tiles per zoom level and load only the visible ones -
  `apps/ctrlx-amr-fleet/src/app/occupancy_map.py` (1-bit palette PNG with stdlib `zlib`) and the
  tile cache in its `www/app.js`.
* Show permission state: disable inputs when the user lacks the write scope (`/status` returns `canWrite`).
* `<body data-ui-ready="false">` in `index.html`; `app.js` sets `"true"` once the data is shown (and
  `"false"` while it loads more, e.g. map tiles). `tools/ui_check.py` waits for it before screenshots -
  Playwright's "network idle" never happens on a page that polls.
* Errors from the API are RFC 7807 Problem JSON: `{"type","title","status","detail", "errors"?}`.

## Adding an endpoint (checklist)

1. Route in `web_server.py` `_api()` with `auth.require(headers, write=...)`.
2. Path below `/api/v1` (already restricted). Validate input; return Problem JSON on errors.
3. Unit test in `tests/test_web_and_nodes.py` (incl. 401/403 cases).
4. If it changes settings → go through `SettingsStore.update()` (persists + notifies).
