"""UI scenario for tools/ui_check.py (headless Chromium); helpers: templates/python-webapp/tests/ui_scenario.py.

    python3 ../../tools/ui_check.py . --narrow          # admin: every tab, an app + process, marker, medium
    python3 ../../tools/ui_check.py . --role reader     # read-only user: no marker, no settings changes
    python3 ../../tools/ui_check.py . --device          # installed app: screenshots of every tab, changes nothing

Locally the app analyzes the build VM itself (/proc of the VM). prepare() samples every second so the
charts have some points, and creates a folder in the local stand-in for /media as a "USB stick".
Screenshots: start, apps-detail, process, leaks, events-marker, settings, settings-error (local admin);
reader-events; device: overview, apps, leaks, events, settings.
"""

import os
import time

LOCAL_MEDIUM = "USB-TEST"


def prepare(ui):
    if not ui.local:
        return
    os.makedirs(os.path.join(ui.app.tmp, ".dev", "media", LOCAL_MEDIUM), exist_ok=True)
    settings = ui.api("GET", "/settings", as_admin=True)
    settings["collector"]["intervalSeconds"] = 1
    ui.api("PUT", "/settings", settings, as_admin=True)
    time.sleep(12)                      # a few samples for the charts


def _tab(ui, name):
    ui.page.click('.tab[data-tab="{}"]'.format(name))
    ui.wait_ready()


def run(ui):
    page = ui.page
    if not ui.local:
        return device_tour(ui)
    if ui.role == "reader":
        _tab(ui, "events")
        assert page.locator("#btn-marker").is_disabled(), "readers can't add markers"
        ui.shot("reader-events")
        return

    # overview: charts drawn by uPlot
    assert page.locator("#ch-sys-cpu canvas").count() == 1, "CPU chart"
    assert page.locator("#ch-top-mem .u-legend .u-series").count() >= 2, "top apps chart has series"

    # apps: open this app itself (it is always there), then its process and threads
    _tab(ui, "apps")
    row = page.locator("#apps-table tbody tr", has_text="this app")
    if not row.count():
        row = page.locator("#apps-table tbody tr").first
    row.click()
    ui.wait_ready()
    page.locator("#ch-app-mem canvas").wait_for()
    page.locator("#proc-table tbody tr.clickable").first.wait_for()
    ui.shot("apps-detail")
    page.locator("#proc-table tbody tr.clickable").first.click()
    page.locator("#proc-detail:not([hidden]) #ch-proc-cpu").wait_for()
    ui.wait_ready()
    ui.shot("process")

    # leaks: the table exists (too little data locally for a verdict)
    _tab(ui, "leaks")
    page.locator("#leak-table tbody tr").first.wait_for()
    ui.shot("leaks")

    # events: add a marker with the real button
    _tab(ui, "events")
    page.fill("#marker-text", "UI check marker")
    page.click("#btn-marker")
    page.locator("#events-table tbody tr", has_text="UI check marker").wait_for()
    ui.shot("events-marker")

    # settings: choose the local "USB stick", an invalid value is rejected with a field error
    _tab(ui, "settings")
    page.click("#btn-rec-refresh")
    page.locator("#rec-medium option[value='{}']".format(LOCAL_MEDIUM)).wait_for(state="attached")
    page.select_option("#rec-medium", LOCAL_MEDIUM)
    page.click("#btn-rec-save")
    page.locator("#rec-state", has_text="Recording to " + LOCAL_MEDIUM).wait_for()
    assert ui.api("GET", "/settings")["recording"]["medium"] == LOCAL_MEDIUM
    ui.shot("settings")
    ui.allow_http(400)
    page.fill("#f-collector-intervalSeconds", "0")
    page.click("#btn-save")
    page.locator("#settings-errors li").first.wait_for()
    ui.shot("settings-error")
    page.click("#btn-revert")


def device_tour(ui):
    """Installed app: look at every tab, change nothing."""
    ui.shot("overview")
    for name in ("apps", "leaks", "events", "settings"):
        _tab(ui, name)
        if name == "apps":
            row = ui.page.locator("#apps-table tbody tr", has_text="rexroth-automationcore")
            if row.count():
                row.click()
                ui.wait_ready()
                ui.page.locator("#ch-app-mem canvas, #ch-app-mem .empty").first.wait_for()
        ui.shot(name)
