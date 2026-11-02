"""UI smoke scenario for tools/ui_check.py (headless Chromium); helpers: templates/python-webapp/tests/ui_scenario.py.

    python3 ../../tools/ui_check.py . --narrow          # admin: simulation, details, lock, styling, widget
    python3 ../../tools/ui_check.py . --role reader     # read-only user: no settings (button, drawer, API)
    python3 ../../tools/ui_check.py . --device          # installed app: screenshots only, changes nothing

Screenshots: start, details (expanded tile), follow (lock + heading-up), focus-small (zoomed far out right
after the crosshair: small AMRs keep their shape, the enlarged outline pulses), path (driven path of the first
AMR), help (menu open), tutorial / tutorial-narrow (the "Focus" step after "Show me", 1400 / 390 px wide),
styling (unsaved preview of other colors), inputs (input mode + AMR list after an upload, before Save), map-file
(map drop-down after an upload, before Save), widget (the home screen widget on its own page, same origin
as on the device).
"""

import json
import os
import re
import tempfile
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
        inputs(ui)
        map_file(ui)
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


def inputs(ui):
    """Settings: input mode select (labels) and the AMR list - add, remove, upload a file, Save; restored after."""
    page = ui.page
    original_list = ui.api("GET", "/amr-list", as_admin=True)["ids"]
    original = ui.api("GET", "/settings", as_admin=True)     # all of it: a partial PUT resets the rest
    try:
        with page.expect_response(lambda r: r.url.endswith("/api/v1/amr-list")):   # the editor stays from styling():
            page.click("#btn-settings")                                             # wait until it is reloaded
        page.wait_for_selector("#amr-ids .amr-id")
        labels = page.locator("#f-fleet-input option").all_inner_texts()
        assert labels[0].startswith("One JSON node for the whole fleet") and len(labels) == 3, labels
        assert page.locator("#amr-ids .amr-id").count() == len(original_list)
        page.fill("#amr-id-new", "AMR 5")                      # invalid: a space
        page.click("#btn-amr-add")
        assert page.locator("#amr-list-errors").is_visible()
        page.fill("#amr-id-new", "AMR-05")
        page.keyboard.press("Enter")                           # Enter adds, does not submit the form
        assert page.locator("#amr-ids .amr-id").count() == len(original_list) + 1
        assert "not saved" in page.inner_text("#amr-list-count")
        page.locator('#amr-ids .amr-id[data-id="AMR-02"] button').click()
        with tempfile.TemporaryDirectory() as tmp:
            good = os.path.join(tmp, "fleet.csv")
            with open(good, "w") as f:
                f.write("# test fleet\nT-1,one\nT-2;two\nT 3\n")
            page.set_input_files("#amr-list-file", good)
            page.wait_for_selector("#amr-list-errors:not([hidden])")   # "T 3" is not valid: nothing loaded
            assert "line 4" in page.inner_text("#amr-list-errors")
            with open(good, "w") as f:
                f.write("# test fleet\nT-1,one\nT-2;two\nT-3\n")
            page.set_input_files("#amr-list-file", good)
            page.wait_for_function("document.querySelectorAll('#amr-ids .amr-id').length === 3")
        page.select_option("#f-fleet-input", "amr-fields")
        ui.shot("inputs")
        page.click("#btn-save")
        page.wait_for_function("/^Saved/.test(document.getElementById('save-state').textContent)")
        assert ui.api("GET", "/amr-list", as_admin=True)["ids"] == ["T-1", "T-2", "T-3"]
        assert ui.api("GET", "/settings", as_admin=True)["fleet"]["input"] == "amr-fields"
        assert "not saved" not in page.inner_text("#amr-list-count")
        page.keyboard.press("Escape")
    finally:
        ui.api("PUT", "/amr-list", {"ids": original_list}, as_admin=True)
        ui.api("PUT", "/settings", original, as_admin=True)


def map_file(ui):
    """Settings: map drop-down (bundled + app data maps), upload a map (name cleaned, replace asks), Save loads it.
    Local only: the upload stays in the app data (the local app runs from a temp copy)."""
    page = ui.page
    original = ui.api("GET", "/settings", as_admin=True)
    try:
        with page.expect_response(lambda r: r.url.endswith("/api/v1/maps")):
            page.click("#btn-settings")
        options = page.locator("#f-map-file option").all_inner_texts()
        assert any(o.startswith("HMI_Map.txt – bundled") for o in options), options
        assert page.input_value("#f-map-file") == original["map"]["file"]
        with tempfile.TemporaryDirectory() as tmp:
            notes = os.path.join(tmp, "notes.md")
            with open(notes, "w") as f:
                f.write("0\t0\n")
            page.set_input_files("#map-upload-file", notes)              # checked in the browser: not a .txt
            page.wait_for_selector("#map-upload-errors:not([hidden])")
            hall = os.path.join(tmp, "UI test hall.txt")                # spaces -> UI_test_hall.txt
            with open(hall, "w") as f:                                  # a 4 x 3 m room, 20 mm cells, door gap
                f.writelines("{}\t{}\n".format(x, y) for x in range(0, 4001, 20) for y in (0, 3000) if not 1000 < x < 2000 or y)
                f.writelines("{}\t{}\n".format(x, y) for y in range(20, 3000, 20) for x in (0, 4000))
            page.set_input_files("#map-upload-file", hall)
            page.wait_for_function("/uploaded/.test(document.getElementById('map-upload-note').textContent)")
            assert page.input_value("#f-map-file") == "UI_test_hall.txt"
            assert "UI test hall.txt uploaded as UI_test_hall.txt" in page.inner_text("#map-upload-note")
            page.once("dialog", lambda d: d.accept() if "Replace" in d.message else d.dismiss())
            page.evaluate("document.getElementById('map-upload-note').textContent = ''")
            page.set_input_files("#map-upload-file", hall)              # again: replace after the question
            page.wait_for_function("/uploaded/.test(document.getElementById('map-upload-note').textContent)")
        assert page.locator("#map-upload-errors").is_hidden()
        page.evaluate("document.getElementById('f-map-file').closest('fieldset').scrollIntoView()")
        ui.shot("map-file")
        page.click("#btn-save")
        page.wait_for_function("/^Saved/.test(document.getElementById('save-state').textContent)")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            info = ui.api("GET", "/map", as_admin=True)
            if info["file"] == "UI_test_hall.txt" and info["loaded"]:
                break
            time.sleep(0.5)
        assert info["file"] == "UI_test_hall.txt" and info["loaded"] and not info["error"], info
        assert abs(info["right"] - info["left"] - 4020) < 1, info              # 4 m + one 20 mm cell
        page.keyboard.press("Escape")
    finally:
        ui.api("PUT", "/settings", original, as_admin=True)


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
