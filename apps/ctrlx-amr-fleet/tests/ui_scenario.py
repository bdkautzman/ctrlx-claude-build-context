"""UI smoke scenario for tools/ui_check.py (headless Chromium); helpers: templates/python-webapp/tests/ui_scenario.py.

    python3 ../../tools/ui_check.py . --narrow          # admin: simulation, details, lock, styling, widget
    python3 ../../tools/ui_check.py . --role reader     # read-only user: no settings (button, drawer, API)
    python3 ../../tools/ui_check.py . --device          # installed app: screenshots only, changes nothing

Screenshots: start, details (expanded tile), follow (lock + heading-up), focus-small (zoomed far out right
after the crosshair: small AMRs keep their shape, the enlarged outline pulses), path (driven path of the first
AMR), help (menu open), tutorial / tutorial-narrow (the "Focus" step after "Show me", 1400 / 390 px wide),
styling (unsaved preview of other colors), widget (the home screen widget on its own page, same origin
as on the device).
"""

import json
import re
import time
import urllib.error
from urllib.parse import quote

GITHUB_URL = "https://github.com/bdkautzman/ctrlx-claude-build-context/tree/main/apps/ctrlx-amr-fleet"

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
    header_heights(ui)
    if ui.local:
        page.wait_for_function(r"/^\(\d+\)$/.test(document.getElementById('amr-count').textContent)",
                               timeout=30000)
    if ui.role == "reader":
        check_reader(ui)
    else:
        assert page.locator("#btn-settings").is_visible(), "a user who may write sees the settings button"
    if page.locator("#amr-list li").count():
        details_and_lock(ui)
        path(ui)
    help_and_tutorial(ui)
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
    box = page.locator("#map").bounding_box()              # the locked AMR is in the middle of the map
    page.mouse.dblclick(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.wait_for_selector("#followbar", state="hidden")   # double-click on the locked AMR unlocks
    tile.get_by_role("button", name=re.compile(r"^Follow ")).click()
    page.wait_for_selector("#followbar:not([hidden])")
    box = page.locator("#map").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 150, y + 40, steps=6)
    page.mouse.up()
    page.wait_for_selector("#followbar", state="hidden")
    page.keyboard.press("f")
    for _ in range(5):                                     # far out: AMRs at their minimum size
        page.click("#btn-zoom-out")
    tile.get_by_role("button", name=re.compile(r"^Center on ")).click()
    shot_path = ui.out + "/{}-focus-small.png".format(ui.prefix)
    page.screenshot(path=shot_path)                        # at once: the outline pulses for 2 s only
    ui.shots.append(shot_path)
    page.keyboard.press("f")


def header_heights(ui):
    """Pills and buttons of the top bar have one height (the help button had another)."""
    boxes = ui.page.evaluate("""() => [...document.querySelectorAll('.topbar-right .pill, .topbar-right .btn')]
        .filter(e => e.getClientRects().length).map(e => [e.id, e.getBoundingClientRect().height, e.getBoundingClientRect().top])""")
    assert len({round(h, 1) for _, h, _ in boxes}) == 1 and len({round(t, 1) for _, _, t in boxes}) == 1, boxes


def path(ui):
    """Path button of the first AMR: the app's path for it (GET /fleet/path) is drawn on the map."""
    page = ui.page
    tile = page.locator("#amr-list li").first
    button = tile.get_by_role("button", name=re.compile(r"^Path of "))
    if button.is_hidden():                                 # path history 0 in the settings (device)
        return
    ident = tile.get_attribute("data-id")
    if button.get_attribute("aria-pressed") != "true":
        button.click()
    assert button.get_attribute("aria-pressed") == "true"
    assert "on" in button.get_attribute("class")
    if ui.local:                                           # the simulation drives: the path grows
        deadline = time.monotonic() + 40
        while len(ui.api("GET", "/fleet/path?id=" + quote(ident))["points"]) < 3:
            assert time.monotonic() < deadline, "no path recorded for " + ident
            time.sleep(1)
    tile.get_by_role("button", name=re.compile(r"^Center on ")).click()
    ui.shot("path")


def help_and_tutorial(ui):
    page = ui.page
    menu = page.locator("#help-menu")
    page.click("#btn-help")
    menu.wait_for()
    link = page.locator("#help-github")
    assert (link.get_attribute("href"), link.get_attribute("target")) == (GITHUB_URL, "_blank")
    ui.shot("help")
    page.keyboard.press("Escape")
    assert menu.is_hidden()
    page.click("#btn-help")
    page.click("#btn-tutorial")
    assert menu.is_hidden()
    tour = page.locator("#tour")
    tour.wait_for()
    count = int(re.search(r"of (\d+)$", page.inner_text("#tour-count")).group(1))
    titles, has_amrs = [], page.locator("#amr-list li").count() > 0
    for i in range(count):
        assert page.inner_text("#tour-count") == "Step {} of {}".format(i + 1, count)
        titles.append(page.inner_text("#tour-title"))
        if titles[-1] == "Focus" and has_amrs:
            check_ring_over(ui, page.locator("#amr-list li").first.get_by_role("button", name=re.compile(r"^Center on ")))
            page.click("#tour-try")
            ui.shot("tutorial")
        page.click("#tour-next")                           # the last one is "Done"
    assert tour.is_hidden() and page.locator("#tour-ring").is_hidden(), "Done ends the tutorial"
    assert ("Settings" in titles) == (ui.role != "reader"), titles
    assert titles[0] == "Welcome" and titles[-1] == "Help", titles
    page.click("#btn-help")                                # keyboard: arrows step, Escape ends
    page.click("#btn-tutorial")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowLeft")
    assert page.inner_text("#tour-title") == titles[1]
    page.keyboard.press("Escape")
    assert tour.is_hidden()
    if has_amrs:
        tutorial_narrow(ui)


def tutorial_narrow(ui):
    """390 px: the list floats over the map - the tutorial opens it for the AMR steps, "Show me" closes it."""
    page = ui.page
    size = page.viewport_size
    page.set_viewport_size({"width": 390, "height": 844})
    try:
        page.click("#btn-help")
        page.click("#btn-tutorial")
        while page.inner_text("#tour-title") != "Focus":
            page.click("#tour-next")
        assert page.locator("#panel").is_visible(), "the AMR steps show the list"
        check_ring_over(ui, page.locator("#amr-list li").first.get_by_role("button", name=re.compile(r"^Center on ")))
        page.click("#tour-try")
        assert page.locator("#panel").is_hidden(), "Show me closes the floating list: the result is on the map"
        ui.shot("tutorial-narrow")
        page.click("#tour-close")
    finally:
        page.set_viewport_size(size)


def check_ring_over(ui, target):
    ring = ui.page.locator("#tour-ring")
    assert ring.is_visible(), "the step's control is marked"
    assert ring.evaluate("e => getComputedStyle(e).animationIterationCount") == "infinite", "pulses while the step is shown"
    r, t = ring.bounding_box(), target.bounding_box()
    assert r["x"] <= t["x"] and r["y"] <= t["y"] and r["x"] + r["width"] >= t["x"] + t["width"] \
        and r["y"] + r["height"] >= t["y"] + t["height"], (r, t)


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
