"""UI scenario for tools/ui_check.py (headless Chromium); helpers: templates/python-webapp/tests/ui_scenario.py.

    python3 ../../tools/ui_check.py . --narrow          # admin: scans of 127.0.0.1, lists, profiles, downloads
    python3 ../../tools/ui_check.py . --role reader     # read-only user: no scan start, no edits (API 403)
    python3 ../../tools/ui_check.py . --device          # installed app: screenshots of every tab, changes nothing

Locally the scans only target 127.0.0.1 (the build VM itself): a profile "local" with the app's own dev
port, which the blacklist forbids - so a scan has an alarm to show.
Screenshots: start, overview-scanned, results-detail, results-forbidden, history, profiles, profile-error,
lists, settings (local admin); reader-* ; device: overview, results, history, profiles, lists, settings.
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
    if ui.role == "reader":
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
    page.click('.tab[data-tab="results"]')
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
    page.click('.tab[data-tab="history"]')
    page.locator("#hist-chart svg").wait_for()
    ui.shot("history")

    # profiles: an invalid target is rejected with a field error, then reverted
    page.click('.tab[data-tab="profiles"]')
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
    page.click('.tab[data-tab="lists"]')
    page.locator("#list-blacklist input").first.wait_for()
    ui.shot("lists")

    # settings + Data Layer node table
    page.click('.tab[data-tab="settings"]')
    page.locator("#values-body td", has_text="scan/start").wait_for()
    ui.shot("settings")


def reader(ui):
    page = ui.page
    assert page.locator("#readonly-note").is_visible(), "read-only note"
    assert page.locator("#btn-start").is_disabled(), "a reader cannot start scans"
    ui.allow_http(403)
    try:
        ui.api("POST", "/scans", {"profile": "local"})
        raise AssertionError("POST /scans must be refused for a reader")
    except Exception as exc:   # urllib HTTPError 403
        assert "403" in str(exc), exc
    ui.shot("reader-overview")
    page.click('.tab[data-tab="results"]')
    page.locator('#res-hosts tr[data-ip="127.0.0.1"]').click()
    page.locator("#res-hosts td.detail").wait_for()
    assert page.locator("#res-hosts td.detail button").count() == 0, "no list buttons for a reader"
    ui.shot("reader-results")
    page.click('.tab[data-tab="lists"]')
    assert page.locator("#btn-lists-save").is_disabled()


def device_tour(ui):
    """Installed app: look at every tab, change nothing."""
    page = ui.page
    for tab in ("overview", "results", "history", "profiles", "lists", "settings"):
        page.click('.tab[data-tab="{}"]'.format(tab))
        ui.shot(tab)
