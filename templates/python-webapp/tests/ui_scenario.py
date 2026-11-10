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

SNAP = "ctrlx-acme-template"
WIDGET_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;font:14px system-ui,sans-serif;background:#eef0f2;color:#1f2328">
<div id="tile" style="width:320px;height:240px;margin:16px;background:#fff;border-radius:8px;padding:8px;
box-shadow:0 1px 3px rgba(0,0,0,.15)"><ctrlx-acme-template-widget></ctrlx-acme-template-widget></div>
<script src="/ctrlx-acme-template/widgets.js"></script></body></html>"""


def run(ui):
    page = ui.page
    configure = ui.role in ("admin", "writer")
    operate = configure or ui.role == "operator"

    # pages: tabs under the top bar; Overview first, the hash names the page
    assert page.locator("#status-card").is_visible() and page.locator("#values-card").is_hidden(), "Overview first"
    assert page.get_attribute("#tabs a[href='#overview']", "aria-current") == "page"
    page.click("#tabs a[href='#values']")
    page.locator("#values-card").wait_for()
    assert page.locator("#status-card").is_hidden() and page.url.endswith("#values"), "Data Layer tab"
    ui.wait_ready()
    ui.shot("values")

    # roles: the settings button only for configure, the role pill only for the others, Edit only for operate
    assert page.locator("#btn-settings").is_visible() == configure, "settings button vs role " + ui.role
    assert page.locator("#role").is_visible() == (not configure), "role pill vs role " + ui.role
    setpoint = page.locator("#values-body tr", has_text="/values/setpoint")
    assert (setpoint.get_by_role("button", name="Edit").count() == 1) == operate, "Edit button vs role " + ui.role
    assert (page.locator("#values-body tr", has_text="/settings/active").count() == 1) == configure, \
        "settings/active is for the configure role only"

    if not configure:   # the API refuses settings too (a GET: fine on a device)
        ui.allow_http(403)
        page.evaluate("Kit.api('GET', '/settings').catch(() => null)")

    widget(ui)
    theme(ui, configure)
    about(ui)
    if not ui.local:
        return  # on a device: no changes

    if operate:  # Data Layer value: Edit -> type -> click "Set"
        setpoint.get_by_role("button", name="Edit").click()
        setpoint.locator("input").fill("12.5")
        setpoint.get_by_role("button", name="Set").click()
        setpoint.locator("td.val", has_text="12.5").wait_for()

        # an array of float32 (PLC ARRAY OF REAL): JSON in, shown as the PLC shows REALs (0.1, not 0.100000001...)
        limits = page.locator("#values-body tr", has_text="/values/limits")
        limits.get_by_role("button", name="Edit").click()
        limits.locator("input").fill("[1.5, 0.1]")
        limits.get_by_role("button", name="Set").click()
        limits.locator("td.val", has_text="[1.5,0.1]").wait_for()

        # the example PLC command (PlcCommand): target -> execute true -> done -> execute false
        def row(name):
            return page.locator("#values-body tr", has_text="/commands/set-setpoint/" + name)

        def set_value(name, value, switch=False):
            row(name).get_by_role("button", name="Edit").click()
            if switch:   # true/false: the kit's switch
                field = row(name).get_by_role("switch")
                if field.get_attribute("aria-checked") != value:
                    field.click()
                assert field.get_attribute("aria-checked") == value
            else:
                row(name).locator("input").fill(value)
            row(name).get_by_role("button", name="Set").click()
            row(name).locator("td.val", has_text=value).wait_for()

        set_value("target", "33")
        set_value("execute", "true", switch=True)
        row("done").locator("td.val", has_text="true").wait_for()           # after ~1 s
        setpoint.locator("td.val", has_text="33").wait_for()
        ui.shot("command-done")
        set_value("execute", "false", switch=True)
        row("done").locator("td.val", has_text="false").wait_for()

    if configure:  # settings drawer: a valid change is saved, an invalid one is rejected with a field error
        page.click("#btn-settings")
        page.locator("#drawer.open #f-example-label").wait_for()
        enabled = page.locator("#f-example-enabled")               # true/false: a switch; its label toggles it too
        assert enabled.get_attribute("role") == "switch" and enabled.get_attribute("aria-checked") == "true"
        enabled.click()
        assert enabled.get_attribute("aria-checked") == "false"
        page.click("label[for=f-example-enabled]")
        assert enabled.get_attribute("aria-checked") == "true"
        page.fill("#f-example-label", "UI check")
        page.click("#btn-save")
        page.locator("#drawer.open").wait_for(state="detached")              # saved -> the drawer closes
        page.click("#btn-settings")
        page.locator("#drawer.open #f-example-label").wait_for()
        assert page.input_value("#f-example-label") == "UI check"
        ui.allow_http(400)
        page.fill("#f-general-heartbeatIntervalMs", "5")
        page.click("#btn-save")
        page.locator("#settings-errors li").first.wait_for()                 # not saved: it stays open
        assert page.locator("#drawer.open").count() == 1
        ui.shot("settings-error")
        page.click("#btn-revert")
        page.click("#btn-close-settings")
        page.locator("#drawer.open").wait_for(state="detached")
        page.locator("#values-body td.val", has_text="UI check").wait_for()   # values/label follows the setting

        # help texts as info tooltips; colors: shown while edited, Revert drops them, saved they hold for the page
        accent = lambda: page.evaluate("getComputedStyle(document.querySelector('.brand-mark')).backgroundColor")  # noqa: E731
        default_accent = accent()
        page.click("#btn-settings")
        page.locator("#drawer.open #f-style-accent").wait_for()
        assert page.locator("#drawer .field .help").count() == 0, "help texts belong into the info tooltips"
        tip = page.locator("#h-f-example-setpoint")
        assert "values/setpoint" in tip.get_attribute("data-tip"), "tooltip of Initial setpoint"
        assert page.get_attribute("#f-example-setpoint", "aria-describedby") == "h-f-example-setpoint"
        tip.hover()
        page.wait_for_function("() => getComputedStyle(document.querySelector('#h-f-example-setpoint'), '::after')"
                               ".opacity === '1'")
        ui.shot("settings-tooltip")
        page.fill("#f-style-accent", "#aa3300")
        page.wait_for_function("() => getComputedStyle(document.querySelector('.brand-mark')).backgroundColor"
                               " === 'rgb(170, 51, 0)'")
        assert page.input_value(".color-input:has(#f-style-accent) .color-picker") == "#aa3300"
        page.click("#btn-revert")
        assert accent() == default_accent, "Revert drops the color preview"
        page.fill("#f-style-accent", "#aa3300")
        page.click("#btn-save")
        page.locator("#drawer.open").wait_for(state="detached")
        assert accent() == "rgb(170, 51, 0)", "saved accent color"

    # tutorial: help menu -> Tutorial, step through to the end (steps depend on the role)
    page.click("#btn-help")
    page.click("#btn-tutorial")
    page.locator("#tour:not([hidden])").wait_for()
    ui.shot("tutorial")
    for _ in range(12):
        if page.locator("#tour").is_hidden():
            break
        page.click("#tour-next")
    assert page.locator("#tour").is_hidden(), "the tutorial did not end"


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
    assert page.locator("#values-card").is_visible(), "the page from the hash (#values) after a reload"
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
                           "w.shadowRoot.querySelectorAll('dd').length >= 3); }")
    path = ui.out + "/{}-widget.png".format(ui.prefix)
    page.locator("#tile").screenshot(path=path)
    ui.shots.append(path)
    msg = page.evaluate("document.querySelector('{}').shadowRoot.querySelector('.msg').textContent".format(tag))
    assert not msg, "widget reports: " + msg
    page.close()
