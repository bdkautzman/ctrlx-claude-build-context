"""UI scenario for tools/ui_check.py - Core Connector in headless Chromium.

    python3 ../../tools/ui_check.py .                  # app started locally (Data Layer off)
    python3 ../../tools/ui_check.py . --role reader    # view: without the settings / add / remove controls
    python3 ../../tools/ui_check.py . --role operator  # operate: value writes in the browser, no cores / KVD / settings
    python3 ../../tools/ui_check.py . --device         # the installed app on the default device (read-only steps)

Locally the Data Layer is off: tiles show "offline" and the browser page its "not connected" state (HTTP 503
from the API, allowed below). On a device the browser page of this device is opened, folders are expanded
through the tree, a node is selected through the address field and deselected with a click below the tree -
nothing is written there.

Help menu and tutorial: the GitHub link, every step of the start page's tutorial, its last step's "Show me"
continues the tutorial in the browser of this device, every step there, keyboard (arrows, Escape).
"""

import json
import re

GITHUB_URL = "https://github.com/bdkautzman/ctrlx-claude-build-context/tree/main/apps/ctrlx-core-connector"

WIDGET_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;font:14px system-ui,sans-serif;background:#eef0f2;color:#1f2328">
<div id="tile" style="width:420px;height:300px;margin:16px;background:#fff;border-radius:8px;padding:8px;
box-shadow:0 1px 3px rgba(0,0,0,.15)"><ctrlx-core-connector-widget></ctrlx-core-connector-widget></div>
<script src="/ctrlx-core-connector/widgets.js"></script></body></html>"""


def prepare(ui):
    if ui.local:   # as admin: a reader may not add cores
        ui.api("POST", "/cores", {"name": "line-1", "host": "192.0.2.10", "user": "boschrexroth", "password": "x",
                                  "sslPort": 443, "skipCertificateCheck": True}, as_admin=True)


def run(ui):
    page = ui.page
    first = page.locator(".tile").first
    assert first.get_attribute("data-core") == "_local", "this device's tile comes first"
    count = page.locator("#cores-count").text_content()
    assert count.endswith(" of {} connected)".format(page.locator(".tile").count())), "count incl. this device: " + count
    assert first.locator(".btn", has_text="Remove").is_hidden(), "this device can't be removed"
    assert first.locator(".core-link").get_attribute("target") == "_blank"

    configure = ui.role in ("admin", "writer")   # cores, KVD nodes and settings (user's split 2026-11-10)
    if not configure:
        assert page.locator("#btn-settings").is_hidden() and page.locator("#btn-add").is_hidden()
        assert page.locator("#role").is_visible(), "role pill"
    else:
        settings_drawer(ui)
        add_dialog(ui)
    about(ui)
    page.click("#btn-theme")                               # light / dark (per browser)
    page.locator("html[data-theme=dark]").wait_for(state="attached")
    ui.shot("dark")
    page.click("#btn-theme")
    page.locator("html[data-theme=light]").wait_for(state="attached")

    widget(ui)
    if ui.local:
        ui.allow_http(503)   # no Data Layer locally: the browser page's API calls answer 503
    tutorial(ui)

    # a click on the tile (not on its name link) opens the Data Layer browser
    first = page.locator(".tile").first
    first.locator(".kv").click()
    page.wait_for_url("**/browse.html?core=_local")
    if ui.local:
        page.locator(".row-msg.error").wait_for()
        ui.shot("browse-offline")
        return
    browse_device(ui)


def settings_drawer(ui):
    page = ui.page
    page.click("#btn-settings")
    page.wait_for_selector("#drawer.open #f-general-pollIntervalMs")
    assert page.locator("#drawer .field .help").count() == 0, "help texts belong into the info tooltips"
    assert page.input_value("#f-browser-refreshMs") == "1000" or not ui.local, "the browser's read interval"
    page.wait_for_timeout(400)                             # slide-in animation
    ui.shot("settings")
    page.keyboard.press("Escape")
    page.wait_for_function("!document.getElementById('drawer').classList.contains('open')")
    assert page.locator("#drawer-backdrop").is_hidden()


def add_dialog(ui):
    page = ui.page
    page.click("#btn-add")
    assert page.locator("#a-sslPort").count() == 0, "the port goes into the host field"
    assert page.get_attribute("#a-name", "placeholder") == "Line-8-Core"
    assert page.get_attribute("#a-host", "placeholder") == "192.168.1.1"
    sw = page.locator("#a-skipCertificateCheck")           # true/false: the kit's switch
    assert sw.get_attribute("role") == "switch" and sw.get_attribute("aria-checked") == "true"
    page.fill("#a-name", "Line-2")
    page.fill("#a-host", "192.0.2.11:8443")
    page.focus("#add-dialog .info[aria-label='HTTPS port']")   # its tooltip in the screenshot
    ui.shot("add-dialog")
    page.click("#btn-add-cancel")


def widget(ui):
    """The home screen widget alone in another tab on the app's origin - on a device the ctrlX UI hosts it."""
    page = ui.new_page()
    url = ui.app.base + "/__ui-check-widget.html"          # served by the browser test, same origin as the app
    page.route(url, lambda route: route.fulfill(status=200, content_type="text/html", body=WIDGET_PAGE))
    page.add_init_script("sessionStorage.setItem('idm_token', {})".format(json.dumps(ui.app.token or "")))
    page.goto(url, wait_until="load")                      # token in sessionStorage first, as in the ctrlX UI
    page.wait_for_function(
        "(() => { const w = document.querySelector('ctrlx-core-connector-widget');"
        " return w && w.shadowRoot && w.shadowRoot.querySelectorAll('li').length >= 2; })()")
    path = ui.out + "/{}-widget.png".format(ui.prefix)
    page.locator("#tile").screenshot(path=path)
    ui.shots.append(path)
    msg = page.evaluate("document.querySelector('ctrlx-core-connector-widget').shadowRoot.querySelector('.msg').textContent")
    assert not msg, "widget reports an error: " + msg
    page.close()


def browse_device(ui):
    page = ui.page
    ui.wait_ready()
    for path in ("framework", "framework/metrics"):
        page.locator('.row[data-path="{}"] .caret'.format(path)).click()
        page.wait_for_selector('.row[data-path^="{}/"]'.format(path))   # its children are shown
        ui.wait_ready()
    page.fill("#goto", "framework/metrics/system/cpu-utilisation-percent")
    page.press("#goto", "Enter")
    page.locator("pre.value").wait_for()
    page.wait_for_selector('.row.selected[data-path="framework/metrics/system/cpu-utilisation-percent"]')
    ui.shot("browse")
    if page.locator("#btn-add-node").is_visible():         # KVD runs on this device: its controls (nothing is changed)
        assert page.inner_text("#kvd-pill") == "Editable"
        kvd_nodes = sorted(p for p in ui.api("GET", "/cores/_local/kvd").get("nodes") or [] if "*" not in p)
        if kvd_nodes:                                      # open one through the address field (may be in a folder)
            page.fill("#goto", kvd_nodes[0])
            page.press("#goto", "Enter")
            row = page.locator('.row.selected[data-path="{}"]'.format(kvd_nodes[0]))
            row.wait_for()
            assert row.locator(".tag-kvd svg use").get_attribute("href") == "#i-user", "a person marks user nodes"
            page.locator("#btn-delete-node").wait_for()
            ui.wait_ready()
            ui.shot("browse-kvd-node")
        page.click("#btn-add-node")
        page.fill("#n-name", "ui-check-example")
        page.select_option("#n-type", "arint32")
        ui.shot("add-node")
        page.click("#btn-node-cancel")
    # a click on the empty part of the tree deselects
    page.click("#btn-collapse")
    box = page.locator("#tree").bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] - 8)
    page.wait_for_function("!document.querySelector('#tree .row.selected')")
    assert page.input_value("#goto") == "" and "Select a node" in page.locator("#detail").text_content()
    ui.shot("browse-deselected")


def tutorial(ui):
    """Help menu, the start page's tutorial, handed over to the browser of this device, back to the start page."""
    page = ui.page
    menu = page.locator("#help-menu")
    page.click("#btn-help")
    menu.wait_for()
    link = page.locator("#help-github")
    assert (link.get_attribute("href"), link.get_attribute("target")) == (GITHUB_URL, "_blank")
    ui.shot("help")
    page.keyboard.press("Escape")
    assert menu.is_hidden()
    titles = step_through(ui, shot_at="Status of a core",
                          ring_at={"Web UI": page.locator(".tile-local .core-link")})
    assert titles[0] == "Welcome" and titles[-1] == "Data Layer browser", titles
    assert ("Add core" in titles) == ("Settings" in titles) == (ui.role in ("admin", "writer")), titles
    page.click("#tour-try")                                # "Show me": continues in the browser
    page.wait_for_url("**/browse.html?core=_local")        # ?tour=1 is taken off the address
    tour = page.locator("#tour")
    tour.wait_for()
    assert page.inner_text("#tour-title") == "Data Layer browser"
    assert page.inner_text("#tour-count").startswith("Step 1 of ")
    page.click("#tour-close")
    ui.wait_ready()
    browse_titles = step_through(ui, shot_at="Tree", act_at=None if ui.local else "Details",
                                 ring_at={"Back": page.locator("#btn-back"), "Tree": page.locator("#tree")},
                                 at_bottom=("Tree", "Details"))
    assert "Add node" in browse_titles or ui.role == "reader", "explains Add node on every core"
    assert browse_titles[0] == "Data Layer browser" and browse_titles[-1] == "Help", browse_titles
    page.click("#btn-help")                                # keyboard: arrows step, Escape ends
    page.click("#btn-tutorial")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowLeft")
    assert page.inner_text("#tour-title") == browse_titles[1]
    page.keyboard.press("Escape")
    assert tour.is_hidden() and page.locator("#tour-ring").is_hidden()
    page.go_back()
    page.wait_for_selector(".tile")
    ui.wait_ready()
    tutorial_narrow(ui)


def tutorial_narrow(ui):
    """390 px: the card fills the width at the bottom, the ring still marks the control."""
    page = ui.page
    size = page.viewport_size
    page.set_viewport_size({"width": 390, "height": 844})
    try:
        page.click("#btn-help")
        page.click("#btn-tutorial")
        while page.inner_text("#tour-title") != "Web UI":
            page.click("#tour-next")
        check_ring_over(ui, page.locator(".tile-local .core-link"))
        card = page.locator("#tour").bounding_box()
        assert card["x"] >= 0 and card["x"] + card["width"] <= 390, card
        ui.shot("tutorial-narrow")
        page.click("#tour-close")
    finally:
        page.set_viewport_size(size)


def step_through(ui, shot_at=None, act_at=None, ring_at=None, at_bottom=()):
    """Start the tutorial from the help menu, click through every step ("Done" at the end), return the titles.
    at_bottom: steps whose control the card covers only partly - the card stays at the bottom, over the ring."""
    page = ui.page
    page.click("#btn-help")
    page.click("#btn-tutorial")
    assert page.locator("#help-menu").is_hidden()
    tour = page.locator("#tour")
    tour.wait_for()
    count = int(re.search(r"of (\d+)$", page.inner_text("#tour-count")).group(1))
    titles = []
    for i in range(count):
        assert page.inner_text("#tour-count") == "Step {} of {}".format(i + 1, count)
        titles.append(page.inner_text("#tour-title"))
        if titles[-1] in (ring_at or {}):
            check_ring_over(ui, ring_at[titles[-1]])
        if titles[-1] in at_bottom:
            assert "at-top" not in (tour.get_attribute("class") or ""), titles[-1] + ": the card stays at the bottom"
            z = page.evaluate("[document.getElementById('tour-ring'), document.getElementById('tour')]"
                              ".map(e => Number(getComputedStyle(e).zIndex))")
            assert z[0] < z[1], "the ring is under the card: {}".format(z)
        if titles[-1] == act_at:
            page.click("#tour-try")
            page.wait_for_selector("#tree .row.selected")
            ui.wait_ready()
        if titles[-1] == shot_at:
            ui.shot("tutorial-" + re.sub(r"\W+", "-", shot_at.lower()))
        if i < count - 1:
            page.click("#tour-next")
    if titles[-1] != "Data Layer browser":                  # (the start page's last step: "Show me" follows)
        page.click("#tour-next")                            # "Done"
        assert tour.is_hidden() and page.locator("#tour-ring").is_hidden(), "Done ends the tutorial"
    return titles


def check_ring_over(ui, target):
    ring = ui.page.locator("#tour-ring")
    assert ring.is_visible(), "the step's control is marked"
    assert ring.evaluate("e => getComputedStyle(e).animationIterationCount") == "infinite", "blinks while the step is shown"
    r, t = ring.bounding_box(), target.bounding_box()
    e = 2   # the ring keeps 2 px from the window's edges (a control touching them: the tree)
    assert r["x"] <= t["x"] + e and r["y"] <= t["y"] + e and r["x"] + r["width"] >= t["x"] + t["width"] - e \
        and r["y"] + r["height"] >= t["y"] + t["height"] - e, (r, t)


def about(ui):
    """Help menu -> About: name, version, author, license, AI note; Esc closes it."""
    page = ui.page
    page.click("#btn-help")
    page.click("#btn-about")
    dialog = page.locator("#about-dialog[open]")
    dialog.wait_for()
    text = dialog.inner_text()
    for part in ("Core Connector", "Version", "Author", "MIT", "AI"):
        assert part in text, "About dialog without " + part
    ui.shot("about", dialog)
    page.keyboard.press("Escape")
    dialog.wait_for(state="detached")
