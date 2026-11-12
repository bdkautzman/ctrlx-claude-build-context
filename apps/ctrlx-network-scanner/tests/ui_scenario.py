"""UI scenario for tools/ui_check.py (headless Chromium); helpers: templates/python-webapp/tests/ui_scenario.py.

    python3 ../../tools/ui_check.py . --narrow          # admin: scans of 127.0.0.1, lists, profiles, downloads
    python3 ../../tools/ui_check.py . --role reader     # view: no scan start, no edits (API 403)
    python3 ../../tools/ui_check.py . --role operator   # operate: start scans, no profiles / lists / settings
    python3 ../../tools/ui_check.py . --device          # installed app: screenshots of every tab, changes nothing

Locally the scans only target 127.0.0.1 (the build VM itself): a profile "local" with the app's own dev
port, which the blacklist forbids - so a scan has an alarm to show.
Screenshots: start, overview-scanned, results-detail, results-forbidden, history, profiles, profile-error,
lists, datalayer, settings, dark, about, tutorial (local admin); <role>-* ; device: overview, results, history,
datalayer, about.
"""

import os
import time


def _local_profile(port):
    return {"id": "local", "name": "Local test", "targets": "127.0.0.1", "exclude": "",
            "tcpPorts": "22, 80, 443, {}".format(port), "udpPorts": "53, 123",
            "discoverNeighbors": False, "discoverTcp": True, "discoverMulticast": False, "assumeAlive": False,
            "serviceDetection": True, "identifyAutomation": False, "resolveNames": False,
            "timeoutMs": 500, "retries": 0, "concurrency": 8, "ratePerSecond": 100, "scheduleMode": "off",
            "scheduleIntervalMinutes": 60, "scheduleTimes": "02:00", "scheduleDays": ""}


def _wait_idle(ui, timeout_s=90):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not ui.api("GET", "/scans/current", as_admin=ui.local and ui.role != "admin")["running"]:
            return
        time.sleep(1)
    raise AssertionError("scan did not finish within {} s".format(timeout_s))


def prepare(ui):
    if not ui.local:
        return
    port = ui.app.port
    settings = ui.api("GET", "/settings", as_admin=True)
    settings["profiles"].append(_local_profile(port))
    settings["lists"]["blacklist"] = [{"hosts": "", "ports": "tcp/{}".format(port), "note": "dev server port"}]
    ui.api("PUT", "/settings", settings, as_admin=True)
    ui.api("POST", "/scans", {"profile": "local"}, as_admin=True)   # a first scan for history/changes
    _wait_idle(ui)


def run(ui):
    if not ui.local:
        return device_tour(ui)
    if ui.role in ("reader", "operator"):
        return reader(ui)
    page = ui.page

    # run a scan from the overview with the real buttons
    page.select_option("#run-profile", "local")
    before = page.locator("#last-when").text_content()
    page.click("#btn-start")
    # finished = the "Last scan" card shows the new scan (not just "start button visible": that is true
    # before the first status poll after the click, too)
    page.wait_for_function("(b) => document.getElementById('last-when').textContent !== b && "
                           "!document.getElementById('btn-start').hidden", arg=before, timeout=90000)
    ui.shot("overview-scanned")

    # results: open the host, mark a port expected, filter forbidden
    page_tab(ui, "results")
    page.locator('#res-hosts tr[data-ip="127.0.0.1"]').wait_for()
    page.click('#res-hosts tr[data-ip="127.0.0.1"]')
    detail = page.locator("#res-hosts td.detail")
    detail.wait_for()
    assert detail.locator(".badge.forbidden").count() >= 1, "the dev port must be rated forbidden"
    ui.shot("results-detail")
    row22 = detail.locator("table.ports tr", has_text="tcp/22")
    if row22.count():
        row22.get_by_role("button", name="Expected").click()
        page.locator("#res-hosts td.detail table.ports tr", has_text="tcp/22").locator(".badge.ok").wait_for()
        settings = ui.api("GET", "/settings")
        assert any("tcp/22" in e["ports"] for e in settings["lists"]["whitelist"]), "whitelist entry added"
    page.click('.seg-btn[data-filter="forbidden"]')
    ui.shot("results-forbidden")
    page.click('.seg-btn[data-filter="all"]')

    # downloads through fetch() + Authorization (PDF, CSV)
    for button, ext, magic in (("#btn-res-pdf", ".pdf", b"%PDF-"), ("#btn-res-csv", ".csv", b"\xef\xbb\xbfscan,")):
        with page.expect_download(timeout=60000) as info:
            page.click(button)
        dl = info.value
        assert dl.suggested_filename.endswith(ext), dl.suggested_filename
        target = os.path.join(ui.out, "local-download" + ext)
        dl.save_as(target)
        with open(target, "rb") as f:
            head = f.read(16)
        assert head.startswith(magic), (ext, head)

    # history (two scans: chart + table)
    page_tab(ui, "history")
    page.locator("#hist-chart svg").wait_for()
    ui.shot("history")

    # profiles: an invalid target is rejected with a field error, then reverted
    page_tab(ui, "profiles")
    page.locator("#prof-list li", has_text="Local test").click()
    page.wait_for_function("document.getElementById('prof-estimate').textContent.includes('address')", timeout=30000)
    ui.shot("profiles")
    ui.allow_http(400)
    page.fill("#p-targets", "300.1.1.1")
    page.click("#btn-prof-save")
    page.locator("#prof-errors li").first.wait_for()
    assert "invalid" in (page.locator("#p-targets").locator("xpath=..").get_attribute("class") or "")
    ui.shot("profile-error")
    page.click("#btn-prof-revert")

    # lists
    page_tab(ui, "lists")
    page.locator("#list-blacklist input").first.wait_for()
    ui.shot("lists")

    # Data Layer node table (its own page now)
    page_tab(ui, "datalayer")
    page.locator("#values-body td", has_text="scan/start").wait_for()
    ui.shot("datalayer")

    # settings drawer: generated form (no profiles / lists in it), Save closes it
    page.click("#btn-settings")
    page.locator("#drawer.open #f-scan-maxAddresses").wait_for()
    assert page.locator("#drawer [data-path^=profiles], #drawer [data-path^=lists]").count() == 0
    assert page.locator("#drawer .info").count() >= 1 and page.locator("#drawer .field .help").count() == 0
    ui.shot("settings")
    page.fill("#f-history-maxScans", "40")
    page.click("#btn-save")
    page.locator("#drawer.open").wait_for(state="detached")
    assert ui.api("GET", "/settings")["history"]["maxScans"] == 40

    # light / dark, About, the tutorial
    page.click("#btn-theme")
    page.locator("html[data-theme=dark]").wait_for(state="attached")
    page_tab(ui, "overview")
    ui.shot("dark")
    page.click("#btn-theme")
    page.locator("html[data-theme=light]").wait_for(state="attached")
    about(ui)
    tutorial(ui)


def reader(ui):
    """view / operate (user's split 2026-11-10): operators start scans; profiles, lists and settings are configure."""
    page = ui.page
    operate = ui.role == "operator"
    assert page.locator("#btn-start").is_visible() == operate, "Start scan vs role " + ui.role
    assert page.locator("#run-note").is_visible() == (not operate), "the note for viewers"
    assert page.locator("#run-profile option").count() >= 1, "profiles from GET /profiles"
    for hidden in ('#tabs a[href="#profiles"]', '#tabs a[href="#lists"]', "#btn-settings"):
        assert page.locator(hidden).is_hidden(), hidden + " is for the configure role"
    if not operate:
        ui.allow_http(403)
        try:
            ui.api("POST", "/scans", {"profile": "local"})
            raise AssertionError("POST /scans must be refused for a viewer")
        except Exception as exc:   # urllib HTTPError 403
            assert "403" in str(exc), exc
    ui.shot("overview")
    page_tab(ui, "results")
    page.locator('#res-hosts tr[data-ip="127.0.0.1"]').click()
    page.locator("#res-hosts td.detail").wait_for()
    assert page.locator("#res-hosts td.detail button").count() == 0, "no list buttons below configure"
    ui.shot("results")
    page.goto(page.url.split("#")[0] + "#lists")          # a hidden page by its address: back to the first one
    ui.wait_ready()
    assert page.locator('[data-page="lists"]').is_hidden()
    tutorial(ui)


def device_tour(ui):
    """Installed app: look at every page, change nothing."""
    about(ui)
    for tab in ("overview", "results", "history", "datalayer"):
        page_tab(ui, tab)
        ui.shot(tab)


def page_tab(ui, name):
    """A page (tab under the top bar, kit.js Kit.setupTabs): the hash names it."""
    ui.page.click('#tabs a[href="#{}"]'.format(name))
    ui.page.locator('[data-page="{}"]:not([hidden])'.format(name)).wait_for()


def about(ui):
    """Help menu -> About: name, version, author, license, AI note; Esc closes it."""
    page = ui.page
    page.click("#btn-help")
    page.click("#btn-about")
    dialog = page.locator("#about-dialog[open]")
    dialog.wait_for()
    text = dialog.inner_text()
    for part in ("Network Scanner", "Version", "Author", "MIT", "AI"):
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
