"""UI smoke scenario for tools/ui_check.py (headless Chromium); helpers: templates/python-webapp/tests/ui_scenario.py.

    python3 ../../tools/ui_check.py .               # local: switches on the simulation (4 AMRs) first
    python3 ../../tools/ui_check.py . --device      # installed app: screenshots only, changes nothing

Screenshots: start (map + AMRs), follow (locked onto the first AMR, heading-up, zoomed in), settings.
Also checks: dragging the map ends the lock, F fits the map, Escape closes the settings drawer.
"""

import re


def prepare(ui):
    if ui.local:
        ui.api("PUT", "/settings", {"simulation": {"enabled": True, "amrCount": 4}})


def run(ui):
    page = ui.page
    if ui.local:
        page.wait_for_function(r"/^\(\d+\)$/.test(document.getElementById('amr-count').textContent)",
                               timeout=30000)
    if page.locator("#amr-list li").count():
        page.locator("#amr-list li").first.get_by_role("button", name=re.compile(r"^Follow ")).click()
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
    page.click("#btn-settings")
    page.wait_for_selector("#settings-fields fieldset")
    ui.shot("settings")
    page.keyboard.press("Escape")
    page.wait_for_selector("#drawer", state="hidden")
