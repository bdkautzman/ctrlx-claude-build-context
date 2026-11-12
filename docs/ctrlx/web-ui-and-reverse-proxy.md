# Web UI behind the ctrlX reverse proxy

Implementation: `templates/python-webapp/src/app/web_server.py`, `src/app/auth.py`, `www/` (`kit.js` + `kit.css`:
shared UI parts, `app.js` + `style.css` + `index.html`: the app's page, `widgets.js` + `widget.svg`: home screen widget).

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
* Outside the snap the same server runs on `127.0.0.1:8080` (`./run-local.sh`). There, and only there, it can add the
  dev-only UI feedback overlay to its pages (`/<name>/__dev/feedback[.js]`, `src/app/dev_feedback.py`;
  `build-deploy-test.md` §1 "UI feedback from the page").

## Authentication & authorization

1. Menu link `"/<name>/?token=${bearertoken}"` → ctrlX inserts the user's JWT.
2. `www/kit.js` (apps before template 0.2.0: `www/app.js`) moves the token from the URL to `sessionStorage` (removes it from history) and
   sends `Authorization: Bearer <token>` with every API call.
3. Manifest `restricted: ["/<name>/api"]` → the proxy validates signature/expiry of the token
   for those paths and rejects invalid ones.
4. The app reads the `scope` claim (list) from the JWT payload **without** signature
   verification - safe only because of step 3 and the Unix-socket-only binding.
5. Every API route names the lowest role that may call it: `auth.require(self.headers, auth.VIEW | auth.OPERATE |
   auth.CONFIGURE)` ("Roles" below). Return 401 (no/invalid token) / 403 (role missing) as Problem JSON.
6. Static files are unrestricted (the page must load without a header) → **never put data or
   secrets into static files**; anything data-bearing goes under `/api`.

Token lifetime is limited; on 401 the UI tells the user to reopen the app from the ctrlX menu.

## Roles: view / operate / configure (template 0.2.0, user's request 2026-11-07)

| Role | Scope | May | Template routes |
|---|---|---|---|
| view | `<name>.web.all.r` | see status, values, results | `GET /status`, `GET /values` |
| operate | `<name>.web.operate.x` | + write runtime values, start/stop/trigger/abort, acknowledge | `PUT /values/<name>` (`NodeSpec.write_role`) |
| configure | `<name>.web.all.rw`, `rexroth-device.all.rwx` | + settings, uploads that change the configuration, app data commands | `/settings*`, `/appdata/*` |

* Each role includes the ones before it (`auth.allows(scopes, role)`, `auth.role_of(scopes)`). The ctrlX admin always
  configures. Same three levels as ctrlX OS's own services (`scheduler.view/operate/manage`).
* Decide per route: does it only show something (view), run the app without changing how it is set up (operate),
  or change the setup (configure)? Examples from the apps: trigger a barcode read, start/abort a scan, write a marker,
  switch continuous reading = operate; scan profiles, camera address, connections to other cores, map uploads = configure.
* Settings are configure-only, reading included (amr-fleet and core-connector both hid them from read-only users,
  gotchas #55). When the page needs a setting to show its data (a unit, a color), give it to everyone through a view
  route of its own with just those values (amr-fleet: `GET /view-config`).
* Data Layer nodes: `NodeSpec(view_role=..., write_role=...)` for the web API (`GET /values` lists only what the role
  may see and says `canWrite` per node; `settings/active` is configure-only). **Writes through the Data Layer itself
  (PLC, REST `/automation/api/v2/nodes`) are not affected** - there the ctrlX user's Data Layer permissions apply.
* `GET /status` answers `role`, `canOperate`, `canConfigure` and `user` (login name; also in the log line of a
  settings change). In the page: `Kit.can("operate")`, and `data-role="operate"|"configure"` on elements only those
  roles see (kit.js hides them for the others); a pill in the top bar says "View only" / "Operator".
* Test locally: `python3 tools/ui_check.py <app> --role reader|operator|writer` (view / operate / configure);
  unit tests: `tests/test_web_and_nodes.py` `test_roles_on_routes`. On a device: a user with only one of the scopes
  (Settings → Users & Permissions).

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
  (canvas) or `URL.createObjectURL()` (`<img>`). Server: `self._send_bytes(200, data, "image/png")`;
  content-addressed URLs (hash in the path) can be cached: `cache_control="private, max-age=3600"`.
* **File downloads (PDF/CSV/JSON reports) from the API**: an `<a href="api/...">` can't send the header
  either. Page: `Kit.download(path, fallbackName)` (fetch with the token → blob → a temporary `<a download>`);
  server: `self._send_bytes(200, data, "text/csv", filename="report.csv")` sets `Content-Disposition` (also for
  non-ASCII names). `tests/ui_scenario.py` checks a download with Playwright's `page.expect_download()`.
* **File uploads** (maps, recipes, images): page `Kit.upload("PUT", path, file, onProgress)` (XMLHttpRequest: progress);
  server `size = self._receive_file(path, max_bytes, check=None)` streams the body into `<path>.tmp` in the app data
  (manifest `appPrivateFiles` keeps `*.tmp` out of configurations), fsyncs and renames it - never in memory. Raise
  `UploadError(status, text)` (also from `check(tmp_path)`) to reject; the rest of the body is read before the answer,
  otherwise the client sees a reset instead of it (gotchas #91). The reverse proxy passes big uploads (8.7 MB tested).
* **Many requests at once**: `Kit.api` runs at most 4 at a time and queues the rest - the reverse proxy answered 502
  when a page fired ~50 at once (gotchas #102; the server's listen backlog is 64 since template 0.1.8).
* **PDF documents**: no PDF library is needed - `apps/ctrlx-network-scanner/src/app/pdf_writer.py` (stdlib,
  standard fonts, tables via `report_pdf.py`) is self-contained and reusable. Look at what it produces
  with `python3 tools/pdf_check.py file.pdf` (pages → PNG + text), like UI screenshots.
* Large pictures (maps, layouts): serve tiles per zoom level and load only the visible ones -
  `apps/ctrlx-amr-fleet/src/app/occupancy_map.py` (1-bit palette PNG with stdlib `zlib`) and the
  tile cache in its `www/app.js`.
* Show permission state: hide what the user's role can't do (`data-role="operate"|"configure"`, `Kit.can(...)`;
  `GET /values` says `canWrite` per node) instead of showing controls that answer 403.
* `<body data-ui-ready="false">` in `index.html`; `app.js` calls `Kit.ready()` once the data is shown (and
  `Kit.ready(false)` while it loads more, e.g. map tiles). `tools/ui_check.py` waits for it before screenshots -
  Playwright's "network idle" never happens on a page that polls.
* Errors from the API are RFC 7807 Problem JSON: `{"type","title","status","detail", "errors"?}`.

## Adding an endpoint (checklist)

1. Route in `web_server.py` `_api()` with `auth.require(self.headers, auth.VIEW|OPERATE|CONFIGURE)` ("Roles").
2. Path below `/api/v1` (already restricted). Validate input; return Problem JSON on errors (`self._problem`).
   Helpers: `self._query()`, `self._read_json()`, `self._send_json()`, `self._send_bytes()`, `self._receive_file()`.
3. Unit test in `tests/test_web_and_nodes.py` (incl. 401/403 per role).
4. If it changes settings → go through `SettingsStore.update()` (persists + notifies).

## Shared UI parts: `www/kit.js` + `www/kit.css` (template 0.2.0)

What every app of the kit built the same way lives there instead of in each `app.js` (the user's request
2026-11-07: features several apps need are built once, in the kit): token handling and `Kit.api` (with the
request limit), `Kit.download` / `Kit.upload`, `Kit.refreshStatus` (title, version, Data Layer pill, role pill,
`data-role` elements), the **settings drawer** (`Kit.setupSettings`: form generated from `GET /settings/meta`,
slides in from the right, configure role only), the **help menu + tutorial** (`Kit.setupHelp(steps)`: a card that
steps through the page's controls and rings each one, "Show me" actions, ← → keys; from ctrlx-amr-fleet and
ctrlx-core-connector), `Kit.ready`, `Kit.poll` (skips while the tab is hidden), icons (`Kit.icon`, `<use href="#i-...">`).
The list of functions is at the top of `kit.js`.

Since 2026-11-09 (user's request):
* **Info tooltips**: a settings rule's `help` is an "i" icon next to the field label (hover / keyboard focus / tap),
  not text under the field. Anywhere else: `<span class="info" tabindex="0" data-tip="...">` after a title.
* **Styling**: settings section `style` (Accent, Panels, Text + Panels / Text for dark mode, rule `format: "color"` =
  color picker + hex field). `GET /status` answers `style` for every role and `Kit.refreshStatus` applies it
  (`Kit.applyStyle`). `kit.css` derives all other colors from these with `color-mix()`: app styles use the variables
  (`--card`, `--text`, `--muted`, `--line`, `--hover`, `--field`, `--accent`, `--ok/--warn/--err`), never fixed
  colors, or they break in dark mode.
* **True/false = a switch** (`Kit.switchInput`, user's choice): settings form and value editors; label above it like
  every other field. Don't build checkboxes or true/false drop-downs for a single on/off.
* **Pages = tabs under the top bar** (`Kit.setupTabs`, user's choice): `<nav class="tabs" id="tabs">` with
  `<a href="#id">`, one `<div class="page" data-page="id">` per page; the hash names the page; `data-role` on a tab
  limits it to a role; tutorial steps with `page: "id"` switch to it. A one-page app removes the nav and the wrappers.
* **About** (help menu, 2026-11-10): `#btn-about` opens a `<dialog>` with name, version, author, license and the AI
  note - from `GET /status` "about" (`config.APP_AUTHOR` / `APP_URL` / `APP_LICENSE` / `APP_AI_NOTE`).
* **Light / dark**: `#btn-theme` in the top bar (every role), the choice per browser (localStorage), first visit from
  the OS; `<html data-theme="light|dark">`. Canvas drawings and charts read the colors with
  `getComputedStyle(document.documentElement).getPropertyValue("--text")` and redraw when `data-theme` changes.

* An app keeps its own code in `app.js` / `style.css` and leaves `kit.js` / `kit.css` as they came from the
  template, so a newer version can be copied over. Something every app needs → change it in the template.
* `index.html` holds the markup kit.js works with (top bar buttons, help menu, drawer); its comments name the ids.
* An app needs its own control in the drawer (a list editor, a file upload, a "Discover" button): `Kit.setupSettings`
  `field(path, rule, def, id)` returns it for one setting, `afterBuild` / `onLoaded` / `beforeSave` / `onRevert` add
  and save the parts that are not one setting (AMR Fleet's AMR list, Barcode Reader's camera address). Long entries:
  rule `wide: true`. Own colors (a map, charts): listen to `kit:style` / `kit:theme`.
* Since 2026-11-11 all five apps run on `kit.js` / `kit.css` with the three roles (AMR Fleet 0.6.0, Barcode Reader
  0.3.0, Core Connector 0.3.0, Network Scanner 0.2.0, System Analyzer 0.2.0); a newer kit.js is copied into each
  `www/`. Core Connector's `www/common.js` only maps its old names to `Kit`; AMR Fleet keeps its own help menu
  entries and map tutorial.

## Home screen widgets (`uiExtensions.dashboard`) - verified on ctrlX OS 4.6 (COREvirtual, 2026-10-23), see `apps/ctrlx-amr-fleet`

The template has one since 0.2.0: `www/widgets.js` (the app's state and the nodes listed in `ROWS`, an "Open" button)
and `www/widget.svg` (its picture in the widget list); manifest `uiExtensions.dashboard` with all three role scopes
and `"noInitialCreation": true` (users add it from the list - a new app doesn't put itself on every home screen;
remove the flag when it should). Adapt `ROWS` and the title, or remove the three pieces when the app has no use for one.

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

