"""UI smoke scenario for tools/ui_check.py - this app's page in headless Chromium. Adapt it to your UI.

    python3 ../../tools/ui_check.py .                  # app started locally (Data Layer off), as admin
    python3 ../../tools/ui_check.py . --role reader    # as a user with only the view permission (also: operator, writer)
    python3 ../../tools/ui_check.py . --device         # the installed app on the default device
    python3 ../../tools/ui_check.py . --device x3 --role reader   # ... as the device's test user kit-view

tools/ui_check.py calls prepare(ui) before the page opens, then opens it, saves the screenshot "start"
and calls run(ui). Any JavaScript error, console error or HTTP error >= 400 fails the run.
  ui.page          Playwright page, sync API (https://playwright.dev/python/docs/api/class-page)
  ui.shot(name)    wait until the page is ready, save <app>/.ui-check/<local|device>-<name>.png
  ui.wait_ready()  after an action: let the page react, wait for <body data-ui-ready="true">
  ui.api(method, path, body[, as_admin=True])   call this app's API (/api/v1 + path) as the page's user;
                   as_admin (local only) e.g. to set up a test that runs with --role reader
  ui.shot(name, locator)       screenshot of one element instead of the page
  ui.new_page()                another tab (e.g. the home screen widget on its own)
  ui.role          "admin" (default; locally auth off), "writer" (configure), "operator" (operate) or "reader" (view);
                   on a device the test user kit-configure / kit-operate / kit-view (tools/ctrlx.py test-users)
  ui.allow_http(400)           before provoking an expected HTTP error
  ui.local         False with --device: don't change anything on a device unless the user asked
Click the real buttons (gotchas #35: "Set" wrote the old value back, Enter worked).
Not run by tools/check.sh (takes ~1 min in the build VM): run it after UI changes and look at the screenshots.
"""

import json

SNAP = "ctrlx-ads-connector"
WIDGET_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;font:14px system-ui,sans-serif;background:#eef0f2;color:#1f2328">
<div id="tile" style="width:320px;height:240px;margin:16px;background:#fff;border-radius:8px;padding:8px;
box-shadow:0 1px 3px rgba(0,0,0,.15)"><ctrlx-ads-connector-widget></ctrlx-ads-connector-widget></div>
<script src="/ctrlx-ads-connector/widgets.js"></script></body></html>"""


FAKE = "Line-1-CX"          # the fake TwinCAT PLC of app/dev_fake_plc.py (local runs only: host "fake-plc")
GONE = "Press-2"            # a controller that isn't there (TEST-NET address): offline with its error


def prepare(ui):
    """Locally: two controllers and a few Data Layer nodes (as admin, so a reader sees them too). On a device:
    nothing is changed - the scenario looks at the controllers that are configured there."""
    if not ui.local:
        return
    ui.api("POST", "/controllers", {"name": FAKE, "host": "fake-plc", "pollIntervalMs": 100}, as_admin=True)
    ui.api("POST", "/controllers", {"name": GONE, "host": "192.0.2.10", "amsNetId": "192.0.2.10.1.1"}, as_admin=True)
    import time
    deadline = time.time() + 20
    while time.time() < deadline:
        c = next(x for x in ui.api("GET", "/controllers", as_admin=True) if x["name"] == FAKE)
        if c["state"] == "connected" and c["plcStateName"] == "RUN":
            break
        time.sleep(0.2)
    for path, writable in (("GVL_Demo.stAxis", True), ("MAIN.nCounter", False), ("GVL_Demo.aInt", True),
                           ("GVL_Demo.fReal", True)):
        ui.api("POST", "/nodes", {"controller": FAKE, "path": path, "writable": writable}, as_admin=True)


def run(ui):
    page = ui.page
    configure = ui.role in ("admin", "writer")
    operate = configure or ui.role == "operator"

    # start page: one tile per controller, Controllers tab first
    page.locator(".tile").first.wait_for()
    assert page.get_attribute("#tabs a[href='#controllers']", "aria-current") == "page"
    if ui.local:
        page.locator(".tile-" + "connected", has_text=FAKE).wait_for()
        page.locator(".tile-offline, .tile-error", has_text=GONE).wait_for(timeout=15000)
        fake = page.locator(".tile", has_text=FAKE)
        for text in ("CX-FAKE", "RUN", "Demo PLC", "127.0.0.1.1.1"):
            assert text in fake.inner_text(), "tile without " + text
        page.locator(".tile", has_text=GONE).locator("p.error").wait_for()      # why it is offline
    ui.wait_ready()
    ui.shot("controllers")

    # roles: settings and the controller buttons only for configure, the role pill for the others
    assert page.locator("#btn-settings").is_visible() == configure, "settings button vs role " + ui.role
    assert page.locator("#btn-add").is_visible() == configure, "Add controller vs role " + ui.role
    assert page.locator("#role").is_visible() == (not configure), "role pill vs role " + ui.role
    assert page.locator(".tile .tile-actions").first.is_visible() == configure, "tile buttons vs role " + ui.role

    if configure and ui.local:
        # add dialog: "Find" reads the Net ID from the controller (UDP server info), errors name the field
        page.click("#btn-add")
        page.locator("#controller-dialog[open]").wait_for()
        page.fill("#c-name", "bad name")
        page.fill("#c-host", "fake-plc")
        page.click("#btn-find")
        page.locator("#c-found.ok").wait_for()
        assert page.input_value("#c-amsNetId") == "5.1.2.3.1.1"
        ui.allow_http(400)
        page.click("#btn-controller-ok")
        page.locator("#controller-errors li", has_text="name: invalid format").wait_for()
        ui.shot("add-controller", page.locator("#controller-dialog"))
        page.click("#btn-controller-cancel")
        # route dialog: only opened here, never sent (it would change a controller)
        page.locator(".tile", has_text=FAKE).get_by_role("button", name="Add route").click()
        page.locator("#route-dialog[open]").wait_for()
        assert page.inner_text("#r-netid") == "127.0.0.1.1.1"
        ui.shot("add-route", page.locator("#route-dialog"))
        page.click("#btn-route-cancel")

    # Data Layer nodes tab: live values, filter
    page.click("#tabs a[href='#nodes']")
    page.locator("#nodes-card").wait_for()
    if ui.local:
        page.locator("#nodes-body tr", has_text="GVL_Demo/stAxis/sName").locator("td.val", has_text="X").wait_for()
        page.locator("#nodes-body tr", has_text="GVL_Demo/fReal").locator("td.val", has_text="0.1").wait_for()
        page.fill("#nodes-filter", "stAxis")
        assert page.locator("#nodes-body tr").count() == 5
        page.fill("#nodes-filter", "")
        assert (page.locator("#nodes-body button[aria-label^='Remove']").count() > 0) == configure
        # sortable columns: A -> Z, Z -> A, back to the order they were added
        added = page.locator("#nodes-body td.addr").all_inner_texts()
        head = page.locator("#nodes-table th[data-sort=address]")
        head.click()
        texts = page.locator("#nodes-body td.addr").all_inner_texts()
        expected = page.evaluate("(a) => a.slice().sort((x, y) => x.localeCompare(y, undefined, "
                                 "{numeric: true, sensitivity: 'base'}))", added)    # the browser's text order
        assert texts == expected, "A -> Z: " + str(texts)
        assert head.get_attribute("aria-sort") == "ascending"
        head.click()
        assert page.locator("#nodes-body td.addr").all_inner_texts() == texts[::-1], "Z -> A"
        assert head.get_attribute("aria-sort") == "descending"
        head.click()
        assert page.locator("#nodes-body td.addr").all_inner_texts() == added, "unsorted again"
        page.locator("#nodes-table th[data-sort=value]").click()        # numbers high -> low first
        assert page.get_attribute("#nodes-table th[data-sort=value]", "data-dir") == "down"
    ui.wait_ready()
    ui.shot("nodes")

    widget(ui)
    theme(ui, configure)
    about(ui)

    if configure:   # settings drawer: the app's settings, an invalid value is refused
        page.click("#btn-settings")
        page.locator("#drawer.open #f-ads-timeoutMs").wait_for()
        assert page.locator("#drawer .field .help").count() == 0, "help texts belong into the info tooltips"
        page.wait_for_timeout(300)
        ui.shot("settings")
        if ui.local:
            ui.allow_http(400)
            page.fill("#f-ads-localNetId", "1.2.3")
            page.click("#btn-save")
            page.locator("#settings-errors li").first.wait_for()
            page.click("#btn-revert")
        page.click("#btn-close-settings")
        page.locator("#drawer.open").wait_for(state="detached")

    # tutorial on the start page: step through to the end (steps depend on the role; the whole walk only as admin:
    # every click takes seconds in the emulated build VM)
    page.click("#tabs a[href='#controllers']")
    page.click("#btn-help")
    page.click("#btn-tutorial")
    page.locator("#tour:not([hidden])").wait_for()
    ui.shot("tutorial")
    for _ in range(25 if ui.role == "admin" else 2):
        if page.locator("#tour").is_hidden():
            break
        if page.locator("#tour-next").is_hidden():
            break
        page.click("#tour-next")
    page.keyboard.press("Escape")

    # PLC browser: the tile opens it
    name = FAKE if ui.local else page.locator(".tile").first.get_attribute("data-controller")
    if "tile-connected" not in (page.locator(".tile", has_text=name).get_attribute("class") or ""):
        ui.allow_http(503)   # a controller that isn't connected (no route yet): the browser shows why, API 503
    page.locator(".tile", has_text=name).locator(".browse-link").click()
    page.wait_for_url("**/browse.html?controller=*")
    page.locator("#tree .row").first.wait_for()
    ui.wait_ready()
    if not ui.local:
        ui.shot("browse")
        return
    browse(ui, operate, configure)


def browse(ui, operate, configure):
    page = ui.page
    row = lambda path: page.locator('#tree .row[data-path="{}"]'.format(path))  # noqa: E731
    row("GVL_Demo").locator(".caret").click()
    row("GVL_Demo.stAxis").wait_for()
    row("GVL_Demo.fReal").locator(".val", has_text="0.1").wait_for()
    assert row("GVL_Demo.nConst").locator(".tag-ro").count() == 1
    assert row("GVL_Demo.fReal").locator(".tag-dl").count() == 1, "DL mark of a node"
    row("GVL_Demo.stAxis").locator(".caret").click()
    row("GVL_Demo.stAxis.sName").wait_for()
    row("GVL_Demo.stAxis").click()
    page.locator("#detail .detail-name", has_text="stAxis").wait_for()
    page.locator("#detail pre.value", has_text='"sName"').wait_for()
    # its 5 members are nodes (prepare): the struct's Data Layer section lists them, no "Add"
    page.locator("#detail .dl-actions dt", has_text="Addresses (5)").wait_for()
    assert page.locator("#detail .addr-field").count() == 5
    assert page.locator("#btn-detail-add").count() == 0, "no Add for a struct whose members are nodes"
    ui.wait_ready()
    ui.shot("browse-struct")

    # go to a path typed in another case: the PLC's spelling is selected, arrays open on the way
    page.fill("#goto", "gvl_demo.aaxis[2].sname")
    page.press("#goto", "Enter")
    row("GVL_Demo.aAxis[2].sName").wait_for()
    page.locator("#detail .detail-name", has_text="sName").wait_for()
    page.locator("#detail pre.value", has_text="Axis 2").wait_for()

    # write (operate): text and an enum
    page.fill("#goto", "GVL_Demo.eState")
    page.press("#goto", "Enter")
    page.locator("#detail .detail-name", has_text="eState").wait_for()
    assert (page.locator("#detail .write-form").count() == 1) == operate, "write form vs role " + ui.role
    if operate:
        page.select_option("#w-value", "2")
        page.click("#btn-write")
        page.locator("#w-state", has_text="Written").wait_for()
        page.locator("#detail pre.value", has_text="Error (2)").wait_for()
        page.fill("#goto", "GVL_Demo.nUsint")
        page.press("#goto", "Enter")
        page.locator("#detail .detail-name", has_text="nUsint").wait_for()
        ui.allow_http(400)
        page.fill("#w-value", "300")
        page.click("#btn-write")
        page.locator("#w-state.error", has_text="out of range").wait_for()
        ui.shot("browse-write-error")

    # Data Layer (configure): not a node yet -> only "Add" (no Data Layer section); a struct -> one node per member
    assert page.locator("#detail .dl-actions").count() == 0, "no Data Layer section before the variable is a node"
    assert (page.locator("#btn-detail-add").count() == 1) == configure, "Add to Data Layer vs role " + ui.role
    assert page.locator("#btn-add-node, #btn-copy").count() == 0, "removed buttons (user's notes 2026-11-25)"
    if configure:
        page.fill("#goto", "MAIN.fbCounter")
        page.press("#goto", "Enter")
        page.locator("#detail .detail-name", has_text="fbCounter").wait_for()
        page.click("#btn-detail-add")
        page.locator("#node-dialog[open]").wait_for()
        page.locator("#n-plan li", has_text="MAIN/fbCounter/tCycle").wait_for()
        page.fill("#n-address", "counter")
        page.locator("#n-plan li", has_text="counter/tCycle").wait_for()
        page.locator("#node-dialog .switch").click()
        ui.shot("add-node", page.locator("#node-dialog"))
        page.click("#btn-node-ok")
        page.locator("#node-dialog[open]").wait_for(state="detached")
        page.fill("#goto", "MAIN.fbCounter.nCount")
        page.press("#goto", "Enter")
        page.locator('#detail .addr-field[value$="connections/{}/counter/nCount"]'.format(FAKE)).wait_for()
        page.locator("#detail .btn-copy-addr").wait_for()
        page.locator("#btn-detail-remove").wait_for()
        assert page.locator("#btn-detail-add").count() == 0, "no Add for a variable that is a node"
        ui.wait_ready()
        ui.shot("browse-node")

    # the browser's tutorial
    page.click("#btn-help")
    page.click("#btn-tutorial")
    page.locator("#tour:not([hidden])").wait_for()
    for _ in range(15 if ui.role == "admin" else 2):
        if page.locator("#tour").is_hidden() or page.locator("#tour-next").is_hidden():
            break
        page.click("#tour-next")
    page.keyboard.press("Escape")


def theme(ui, configure):
    """Light / dark: the top bar button for every role; the browser keeps the choice (also on a device: nothing
    changes there)."""
    page = ui.page
    bg = lambda: page.evaluate("getComputedStyle(document.querySelector('.card')).backgroundColor")  # noqa: E731
    light = bg()
    page.click("#btn-theme")
    page.locator("html[data-theme=dark]").wait_for(state="attached")
    assert page.get_attribute("#btn-theme", "aria-pressed") == "true"
    assert bg() != light, "dark panels"
    ui.shot("dark")
    if configure:
        page.click("#btn-settings")
        page.locator("#drawer.open #f-style-accent").wait_for()
        page.wait_for_timeout(300)          # the drawer slides in
        ui.shot("settings-dark")
        page.click("#btn-close-settings")
        page.locator("#drawer.open").wait_for(state="detached")
    page.reload()
    ui.wait_ready()
    assert page.get_attribute("html", "data-theme") == "dark", "the browser keeps the theme"
    assert page.locator("#nodes-card").is_visible(), "the page from the hash (#nodes) after a reload"
    page.click("#btn-theme")
    page.locator("html[data-theme=light]").wait_for(state="attached")
    assert bg() == light


def about(ui):
    """Help menu -> About: name, version, author, license, AI note (GET /status "about"); Esc closes it."""
    page = ui.page
    page.click("#btn-help")
    page.click("#btn-about")
    dialog = page.locator("#about-dialog[open]")
    dialog.wait_for()
    text = dialog.inner_text()
    for part in (page.inner_text("#app-title"), "Version", "Author", "License", "AI"):
        assert part in text, "About dialog without " + part
    ui.shot("about", dialog)
    page.keyboard.press("Escape")
    dialog.wait_for(state="detached")


def widget(ui):
    """The home screen widget alone in another tab on the app's origin - on a device the ctrlX UI hosts it."""
    page = ui.new_page()
    url = ui.app.base + "/__ui-check-widget.html"          # served by the browser test, same origin as the app
    page.route(url, lambda route: route.fulfill(status=200, content_type="text/html", body=WIDGET_PAGE))
    token = ui.app.token or ""
    page.add_init_script("sessionStorage.setItem('idm_token', {})".format(json.dumps(token)))
    page.goto(url, wait_until="load")                      # token in sessionStorage first, as in the ctrlX UI
    tag = SNAP + "-widget"
    page.wait_for_function("() => { const w = document.querySelector('" + tag + "'); return !!(w && w.shadowRoot && "
                           "w.shadowRoot.querySelectorAll('dd').length >= 1); }")
    path = ui.out + "/{}-widget.png".format(ui.prefix)
    page.locator("#tile").screenshot(path=path)
    ui.shots.append(path)
    msg = page.evaluate("document.querySelector('{}').shadowRoot.querySelector('.msg').textContent".format(tag))
    assert not msg, "widget reports: " + msg
    page.close()
