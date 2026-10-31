"""UI scenario for tools/ui_check.py - Core Connector in headless Chromium.

    python3 ../../tools/ui_check.py .                  # app started locally (Data Layer off)
    python3 ../../tools/ui_check.py . --role reader    # without the settings / add / remove controls
    python3 ../../tools/ui_check.py . --device         # the installed app on the default device (read-only steps)

Locally the Data Layer is off: tiles show "offline" and the browser page its "not connected" state (HTTP 503
from the API, allowed below). On a device the browser page of this device is opened, folders are expanded
through the tree, a node is selected through the address field and deselected with a click below the tree -
nothing is written there.
"""

import json

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

    if ui.role == "reader":
        assert page.locator("#btn-settings").is_hidden() and page.locator("#btn-add").is_hidden()
    else:
        settings_drawer(ui)
        add_dialog(ui)

    widget(ui)

    # a click on the tile (not on its name link) opens the Data Layer browser
    first.locator(".kv").click()
    page.wait_for_url("**/browse.html?core=_local")
    if ui.local:
        ui.allow_http(503)   # no Data Layer locally
        page.locator(".row-msg.error").wait_for()
        ui.shot("browse-offline")
        return
    browse_device(ui)


def settings_drawer(ui):
    page = ui.page
    page.click("#btn-settings")
    page.wait_for_selector("#drawer.open #f-general-pollIntervalMs")
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
        kvd_row = page.locator(".row:has(.tag-kvd)").first
        if kvd_row.count():
            kvd_row.click()
            page.locator("#btn-delete-node").wait_for()
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
