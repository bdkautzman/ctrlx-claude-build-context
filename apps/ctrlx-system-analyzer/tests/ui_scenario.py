"""UI scenario for tools/ui_check.py (headless Chromium); helpers: templates/python-webapp/tests/ui_scenario.py.

    python3 ../../tools/ui_check.py . --narrow          # admin: every page, an app + process, marker, medium
    python3 ../../tools/ui_check.py . --role reader     # view: no marker, no settings
    python3 ../../tools/ui_check.py . --role operator   # operate: markers, no settings
    python3 ../../tools/ui_check.py . --device          # installed app: screenshots of every tab, changes nothing

Locally the app analyzes the build VM itself (/proc of the VM). prepare() samples every second so the
charts have some points, and creates a folder in the local stand-in for /media as a "USB stick".
Screenshots: start, chart-dialog, apps-detail, process, leaks, events-marker, settings, settings-error, dark, about, tutorial
(local admin); <role>-events; device: overview, apps, leaks, events, about.
"""

import os
import re
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
    """A page (tab under the top bar, kit.js Kit.setupTabs): the hash names it."""
    ui.page.click('#tabs a[href="#{}"]'.format(name))
    ui.page.locator('[data-page="{}"]:not([hidden])'.format(name)).wait_for()
    ui.wait_ready()


def run(ui):
    page = ui.page
    if not ui.local:
        return device_tour(ui)
    if ui.role in ("reader", "operator"):          # markers = operate, settings = configure
        _tab(ui, "events")
        assert page.locator("#marker-form").is_visible() == (ui.role == "operator"), "marker form vs role"
        assert page.locator("#btn-settings").is_hidden(), "settings are for the configure role"
        ui.shot("events")
        tutorial(ui)
        return

    # overview: charts drawn by uPlot; legend markers are solid circles
    assert page.locator("#ch-sys-cpu canvas").count() == 1, "CPU chart"
    assert page.locator("#ch-top-mem .u-legend .u-series").count() >= 2, "top apps chart has series"
    marker = page.eval_on_selector("#ch-sys-mem .u-legend .u-series:nth-child(2) .u-marker",
                                   "m => [getComputedStyle(m).borderRadius, getComputedStyle(m).backgroundColor]")
    assert marker[0] == "50%" and marker[1] not in ("rgba(0, 0, 0, 0)", "transparent"), "legend marker " + str(marker)
    chart_hover_keeps_height(ui)
    chart_dialog(ui)

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

    # apps: the sorted column has an arrow and a bold title; a click cycles high -> low, low -> high, unsorted
    _tab(ui, "apps")
    assert _sort_state(ui, "apps-table", "rssMb") == ("down", True), "apps sorted by RSS high to low"
    sort_cycle(ui, "apps-table", "cpu", 4)

    # leaks: the table exists (too little data locally for a verdict); its columns sort the same way
    _tab(ui, "leaks")
    page.locator("#leak-table tbody tr").first.wait_for()
    sort_cycle(ui, "leak-table", "currentMb", 3)
    ui.shot("leaks")

    # events: add a marker with the real button
    _tab(ui, "events")
    page.fill("#marker-text", "UI check marker")
    page.click("#btn-marker")
    page.locator("#events-table tbody tr", has_text="UI check marker").wait_for()
    ui.shot("events-marker")

    # settings drawer: choose the local "USB stick" (refresh icon), save (the drawer closes); an invalid value
    # is rejected with a field error; true/false = a switch, help texts = info tooltips
    page.click("#btn-settings")
    page.locator("#drawer.open #f-recording-medium").wait_for()
    page.click("#btn-media-refresh")                   # the refresh icon looks for media again
    page.locator("#f-recording-medium option[value='{}']".format(LOCAL_MEDIUM)).wait_for(state="attached")
    page.select_option("#f-recording-medium", LOCAL_MEDIUM)
    assert page.get_attribute("#f-recording-threads", "role") == "switch"
    assert page.locator("#drawer .field .help").count() == 0 and page.locator("#drawer .info").count() >= 1
    ui.shot("settings")
    page.click("#btn-save")
    page.locator("#drawer.open").wait_for(state="detached")
    assert ui.api("GET", "/settings")["recording"]["medium"] == LOCAL_MEDIUM
    _tab(ui, "overview")
    page.locator("#rec-status dd", has_text=LOCAL_MEDIUM).wait_for()
    page.click("#btn-settings")
    page.locator("#drawer.open #f-collector-intervalSeconds").wait_for()
    ui.allow_http(400)
    page.fill("#f-collector-intervalSeconds", "0")
    page.click("#btn-save")
    page.locator("#settings-errors li").first.wait_for()
    ui.shot("settings-error")
    page.click("#btn-revert")
    page.click("#btn-close-settings")
    page.locator("#drawer.open").wait_for(state="detached")

    # light / dark: the charts are rebuilt in the theme's colors
    page.click("#btn-theme")
    page.locator("html[data-theme=dark]").wait_for(state="attached")
    page.locator("#ch-sys-cpu canvas").wait_for()
    ui.wait_ready()
    ui.shot("dark")
    page.click("#btn-theme")
    page.locator("html[data-theme=light]").wait_for(state="attached")
    about(ui)
    tutorial(ui)


def _overview_card_heights(ui):
    return ui.page.eval_on_selector_all("#tab-overview .card:has(.chart)",
                                        "cards => cards.map(c => Math.round(c.getBoundingClientRect().height))")


def chart_hover_keeps_height(ui):
    """The cursor over a chart shows its time and values in every legend; no card grows (user's note 2026-11-11).
    The live data can change a legend too (the busiest apps change), so a difference has to repeat 3 times."""
    page = ui.page
    box = page.locator("#ch-sys-mem .u-over").bounding_box()
    seen = []
    for _ in range(3):
        page.mouse.move(2, 2)
        page.wait_for_timeout(200)
        before = _overview_card_heights(ui)
        page.mouse.move(box["x"] + box["width"] * 0.6, box["y"] + box["height"] / 2)
        page.wait_for_timeout(300)
        shown = page.locator("#ch-sys-mem .u-legend .u-series:first-child .u-value").inner_text()
        assert shown not in ("", "–"), "the legend shows no time under the cursor: " + repr(shown)
        after = _overview_card_heights(ui)
        if before == after:
            break
        seen.append((before, after))
    else:
        raise AssertionError("card heights changed with the cursor: {}".format(seen))
    page.mouse.move(2, 2)


def chart_dialog(ui):
    """A click on a chart opens it large in a dialog; it follows the data; Esc closes it."""
    page = ui.page
    small = page.locator("#ch-sys-mem .u-over").bounding_box()
    page.mouse.click(small["x"] + small["width"] / 2, small["y"] + small["height"] / 2)
    dialog = page.locator("#chart-dialog[open]")
    dialog.wait_for()
    assert "System memory" in page.inner_text("#chart-dialog-title")
    big = page.locator("#chart-dialog-plot .u-over").bounding_box()
    assert big["width"] > small["width"] * 1.3 and big["height"] > small["height"] * 1.3, "the dialog chart is not larger"
    assert page.locator("#chart-dialog-plot .u-legend .u-series").count() >= 3, "legend in the dialog"
    page.wait_for_timeout(1500)                          # a refresh while it is open keeps it
    assert page.locator("#chart-dialog-plot canvas").count() == 1
    ui.shot("chart-dialog", dialog)
    page.keyboard.press("Escape")
    dialog.wait_for(state="detached")
    assert page.locator("#chart-dialog-plot canvas").count() == 0


def _sort_state(ui, table, key):
    return tuple(ui.page.eval_on_selector('#{} th[data-sort="{}"]'.format(table, key),
                                          "th => [th.dataset.dir || '', getComputedStyle(th).fontWeight === '700']"))


def _column(ui, table, index):
    texts = ui.page.eval_on_selector_all("#{} tbody tr".format(table),
                                         "rows => rows.map(r => (r.cells[{}] || {{}}).innerText || '')".format(index))
    return [float(m.group()) for m in (re.search(r"-?\d+(\.\d+)?", t) for t in texts) if m]


def sort_cycle(ui, table, key, index):
    """Click a column title three times: high -> low (arrow down), low -> high (arrow up), unsorted (no arrow)."""
    page = ui.page
    th = page.locator('#{} th[data-sort="{}"]'.format(table, key))
    th.click()
    assert _sort_state(ui, table, key) == ("down", True), "first click: high to low"
    assert page.eval_on_selector('#{} th[data-sort="{}"]'.format(table, key),
                                 "th => getComputedStyle(th, '::after').content").find("↓") >= 0
    values = _column(ui, table, index)
    assert values == sorted(values, reverse=True), "{} not high to low: {}".format(key, values)
    th.click()
    assert _sort_state(ui, table, key) == ("up", True), "second click: low to high"
    values = _column(ui, table, index)
    assert values == sorted(values), "{} not low to high: {}".format(key, values)
    th.click()
    assert _sort_state(ui, table, key)[0] == "" and page.locator("#{} th.sorted".format(table)).count() == 0, "unsorted"


def device_tour(ui):
    """Installed app: look at every page, change nothing."""
    ui.shot("overview")
    about(ui)
    for name in ("apps", "leaks", "events"):
        _tab(ui, name)
        if name == "apps":
            row = ui.page.locator("#apps-table tbody tr", has_text="rexroth-automationcore")
            if row.count():
                row.click()
                ui.wait_ready()
                ui.page.locator("#ch-app-mem canvas, #ch-app-mem .empty").first.wait_for()
        ui.shot(name)


def about(ui):
    """Help menu -> About: name, version, author, license, AI note; Esc closes it."""
    page = ui.page
    page.click("#btn-help")
    page.click("#btn-about")
    dialog = page.locator("#about-dialog[open]")
    dialog.wait_for()
    text = dialog.inner_text()
    for part in ("System Analyzer", "Version", "Author", "MIT", "AI"):
        assert part in text, "About dialog without " + part
    ui.shot("about", dialog)
    page.keyboard.press("Escape")
    dialog.wait_for(state="detached")


def tutorial(ui):
    page = ui.page
    page.click("#btn-help")
    page.click("#btn-tutorial")
    page.locator("#tour:not([hidden])").wait_for()
    ui.shot("tutorial")
    for _ in range(20):
        if page.locator("#tour").is_hidden():
            break
        page.click("#tour-next")
    assert page.locator("#tour").is_hidden(), "the tutorial did not end"
