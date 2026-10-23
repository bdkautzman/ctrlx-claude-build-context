"""UI smoke scenario for tools/ui_check.py (headless Chromium); helpers: templates/python-webapp/tests/ui_scenario.py.

    python3 ../../tools/ui_check.py . --narrow          # admin: simulation, details, lock, styling, widget
    python3 ../../tools/ui_check.py . --role reader     # read-only user: no settings (button, drawer, API)
    python3 ../../tools/ui_check.py . --device          # installed app: screenshots only, changes nothing

Screenshots: start, details (expanded tile), follow (lock + heading-up), styling (unsaved preview of
other colors), widget (the home screen widget on its own page, same origin as on the device).
"""

import json
import re
import urllib.error

WIDGET_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;font:14px system-ui,sans-serif;background:#eef0f2;color:#1f2328">
<div id="tile" style="width:420px;height:320px;margin:16px;background:#fff;border-radius:8px;padding:8px;
box-shadow:0 1px 3px rgba(0,0,0,.15)"><ctrlx-amr-fleet-widget></ctrlx-amr-fleet-widget></div>
<script src="/ctrlx-amr-fleet/widgets.js"></script></body></html>"""


def prepare(ui):
    if ui.local:   # as admin: a reader may not change settings
        ui.api("PUT", "/settings", {"simulation": {"enabled": True, "amrCount": 5}}, as_admin=True)


def run(ui):
    page = ui.page
    if ui.local:
        page.wait_for_function(r"/^\(\d+\)$/.test(document.getElementById('amr-count').textContent)",
                               timeout=30000)
    if ui.role == "reader":
        check_reader(ui)
    else:
        assert page.locator("#btn-settings").is_visible(), "a user who may write sees the settings button"
    if page.locator("#amr-list li").count():
        details_and_lock(ui)
    if ui.role != "reader" and ui.local:
        styling(ui)
    widget(ui)


def check_reader(ui):
    page = ui.page
    assert not page.locator("#btn-settings").is_visible(), "read-only: no settings button"
    page.evaluate("location.hash = '#settings'")
    page.reload(wait_until="load")   # the token stays in sessionStorage
    ui.wait_ready()
    assert page.locator("#drawer").is_hidden(), "read-only: #settings must not open the drawer"
    for path in ("/settings", "/settings/meta", "/values"):
        try:
            ui.api("GET", path)
        except urllib.error.HTTPError as exc:
            assert exc.code == 403, (path, exc.code)
        else:
            raise AssertionError(path + " is readable for a read-only user")


def details_and_lock(ui):
    page = ui.page
    tile = page.locator("#amr-list li").first
    tile.locator(".amr-head").click()                      # expands; must not move or lock the map
    tile.locator(".amr-details dt", has_text="Position").wait_for()
    assert page.locator("#followbar").is_hidden()
    ui.shot("details")
    tile.locator(".amr-head").click()
    assert tile.locator(".amr-details").is_hidden(), "a second click collapses the tile"
    tile.get_by_role("button", name=re.compile(r"^Center on ")).click()
    tile.get_by_role("button", name=re.compile(r"^Follow ")).click()
    page.wait_for_selector("#followbar:not([hidden])")
    page.click("#btn-zoom-in")
    ui.shot("follow")
    box = page.locator("#map").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 150, y + 40, steps=6)
    page.mouse.up()
    page.wait_for_selector("#followbar", state="hidden")
    page.keyboard.press("f")


def styling(ui):
    page = ui.page
    accent = "getComputedStyle(document.documentElement).getPropertyValue('--accent').trim()"
    default = page.evaluate(accent)
    page.click("#btn-settings")
    page.wait_for_selector("#f-style-accent")
    page.fill("#f-style-accent", "#d4570a")                # hex field: preview at once
    assert page.evaluate(accent) == "#d4570a", page.evaluate(accent)
    page.fill("#f-style-mapWalls", "#7a2e9e")
    page.fill("#f-style-panel", "#22272e")                 # dark panels: text stays readable?
    page.fill("#f-style-text", "#e6edf3")
    page.locator("#f-style-amrColors .btn-add").click()
    assert page.locator("#f-style-amrColors .color-item").count() == 11
    ui.shot("styling")                                     # unsaved preview, map visible left of the drawer
    page.click("#btn-revert")
    assert page.evaluate(accent) == default, "Revert restores the saved colors"
    page.fill("#f-style-accent", "#d4570a")
    page.keyboard.press("Escape")
    assert page.evaluate(accent) == default, "closing the settings drops an unsaved preview"


def widget(ui):
    """The widget alone in another tab on the app's origin - on a device the ctrlX UI hosts it."""
    page = ui.new_page()
    url = ui.app.base + "/__ui-check-widget.html"          # served by the browser test, same origin as the app
    page.route(url, lambda route: route.fulfill(status=200, content_type="text/html", body=WIDGET_PAGE))
    page.add_init_script("sessionStorage.setItem('idm_token', {})".format(json.dumps(ui.app.token or "")))
    page.goto(url, wait_until="load")                      # token in sessionStorage first, as in the ctrlX UI
    w = page.locator("ctrlx-amr-fleet-widget")
    w.locator(".status").first.wait_for()
    if ui.local:
        page.wait_for_function(
            "document.querySelector('ctrlx-amr-fleet-widget').shadowRoot.querySelectorAll('li').length >= 5")
    path = ui.out + "/{}-widget.png".format(ui.prefix)
    page.locator("#tile").screenshot(path=path)
    ui.shots.append(path)
    status = w.locator(".status").get_attribute("class")
    assert "error" not in status, "widget reports an error: " + json.dumps(page.evaluate(
        "document.querySelector('ctrlx-amr-fleet-widget').shadowRoot.querySelector('.msg').textContent"))
    page.close()
