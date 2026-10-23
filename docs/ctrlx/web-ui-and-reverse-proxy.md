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

## Home screen widgets (`uiExtensions.dashboard`) - verified on ctrlX OS 4.6 (COREvirtual, 2026-10-23), see `apps/ctrlx-amr-fleet`

* A widget is a **Web Component** in a JS file the app serves as a static file (e.g. `/<snap>/widgets.js`,
  unrestricted like other static files - it holds no data). The manifest entry names `src`, `tag`
  (custom element name - prefix it with the snap name), `title`, `description`, `image` (catalog picture,
  served by the app), `initialSize`/`minSize`/`maxSize` (`{"cols", "rows"}` of the home grid; the Bosch
  widgets use 3x3 ... 6x3) and `permissions` (scope ids; users without one don't see it in the catalog).
  Optional `settingsTag` (a second component for the widget's settings), `categories`, `i18n`.
  Upstream: `upstream-sdk-4.6.0/package-assets.md` "uiExtensions"; installed ones: GET
  `/package-manager/api/v1/manifests` → `uiExtensions`.
* It runs **inside the ctrlX UI page** (same origin as `/<snap>/api`, no CORS). The ctrlX UI keeps the
  user's bearer token in **`sessionStorage["idm_token"]`** (plus `"loggedIn": "true"`); its own widgets
  (motion, OPC UA, Data Layer watchlist) read it there and send `Authorization: Bearer <token>`. A widget
  does the same for the app's restricted API. The ctrlX UI also accepts `/?token=<jwt>` and stores it there.
* **After the install the widget is already on the home screen** (ctrlX OS 4.6 creates new widgets
  initially for the users who may see them) - set `"noInitialCreation": true` in the manifest entry if
  users should add it themselves from the catalog. Users remove it in the home screen's edit mode.
* Use Shadow DOM (`attachShadow`) so the ctrlX UI's CSS and yours don't mix; inherit the font; poll
  slowly (≥ 2 s) and only while `connectedCallback` .. `disconnectedCallback`; guard
  `customElements.define` with `customElements.get` (the file may be loaded twice). Rebuild DOM only
  when something visible changed: the home screen is already a heavy page, and churning elements
  every poll also breaks hover/selection (and Playwright's waits).
* Opening the full app from a widget: `window.open("/<snap>/?token=" + idm_token, "_blank")` - the same
  as the menu link's `${bearertoken}`.
* Test without the ctrlX UI: serve a small HTML page with the tag on the app's origin and put the token
  into `sessionStorage` first - `apps/ctrlx-amr-fleet/tests/ui_scenario.py` `widget()` (Playwright
  `page.route` + `add_init_script`).

