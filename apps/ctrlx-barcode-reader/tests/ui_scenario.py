"""UI scenario for tools/ui_check.py - Barcode Reader in headless Chromium.

    eval "$(./dev-env.sh)"; python3 ../../tools/ui_check.py . --narrow   # local: simulated camera (needs .dev/)
    python3 ../../tools/ui_check.py . --role reader                       # without the camera/settings controls
    python3 ../../tools/ui_check.py . --device [NAME]                     # the installed app (read-only steps)

Locally the app starts with the defaults (a GigE camera at 192.168.1.99 that the build VM can't stream
from): prepare() switches it to the simulated camera (Aravis' fake camera + generated codes) and makes one
read, so the start screenshot shows an image with code outlines.
Steps (local admin): Read, the history (pin an older read, back to the newest), draw a decode region with
the mouse and read inside it, clear it, the live picture, continuous reading on/off, the settings drawer
(symbology checkboxes, Discover lists the fake camera, an invalid value is rejected).
On a device nothing is triggered (a read changes the Data Layer result nodes a PLC may watch).
"""


def prepare(ui):
    if not ui.local:
        return
    s = ui.api("GET", "/settings", as_admin=True)
    s["camera"]["source"] = "simulated"
    s["continuous"]["intervalMs"] = 300
    s["live"]["intervalMs"] = 200
    ui.api("PUT", "/settings", s, as_admin=True)
    first = ui.api("POST", "/read", {"wait": True}, as_admin=True)
    assert first["ok"], first


def seq(page):
    return int(page.locator("#r-seq").text_content().lstrip("#") or 0)


def run(ui):
    page = ui.page
    page.locator("#cam-pill", has_text="Camera:").wait_for()
    if not ui.local:
        assert page.locator("#camera-kv dt").count() >= 1
        return
    page.locator("#r-seq", has_text="#1").wait_for()
    page.locator("#img:not([hidden])").wait_for()

    if ui.role == "reader":
        for b in ("#btn-read", "#btn-live", "#btn-continuous", "#btn-region", "#btn-settings", "#btn-reconnect"):
            assert page.locator(b).is_hidden(), b + " must be hidden for a reader"
        ui.shot("reader")
        return

    # read from the UI: codes in the result card, outlines on the image
    page.click("#btn-read")
    page.locator("#r-seq", has_text="#2").wait_for()
    page.locator("#result-badge.badge-ok").wait_for()
    n = page.locator("#codes li").count()
    assert n >= 1 and page.locator("#overlay polygon").count() == n, "one outline per code"
    page.locator("#history-body tr").nth(1).wait_for()
    page.locator("#history-body img.thumb[src]").nth(1).wait_for()
    ui.shot("read")
    ui.shot("history", page.locator(".history-card"))

    # history: pin the older read, then back to the newest
    page.locator("#history-body tr").nth(1).click()
    page.locator("#viewing-note:not([hidden])").wait_for()
    assert page.locator("#r-seq").text_content() == "#1"
    assert page.locator("#btn-download").is_disabled(), "PNG only for the newest read"
    ui.shot("history-pinned")
    page.click("#btn-newest")
    page.locator("#r-seq", has_text="#2").wait_for()

    # decode region: drag on the image, the next read decodes only inside it
    page.click("#btn-region")
    box = page.locator("#overlay").bounding_box()
    page.mouse.move(box["x"] + box["width"] * 0.05, box["y"] + box["height"] * 0.05)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * 0.6, box["y"] + box["height"] * 0.6, steps=8)
    page.mouse.up()
    page.locator("#btn-region-clear:not([hidden])").wait_for()
    assert page.locator("#overlay rect.roi").count() == 1
    page.click("#btn-read")
    page.locator("#r-seq", has_text="#3").wait_for()
    page.locator("#img-info", has_text="region").wait_for()
    ui.shot("region")
    page.click("#btn-region-clear")
    page.locator("#btn-region-clear").wait_for(state="hidden")

    # live picture (not decoded), then stop
    page.click("#btn-live")
    page.locator("#img-info", has_text="Live").wait_for()
    page.locator("#img-info", has_text="frame 2").wait_for()
    assert page.locator("#overlay polygon").count() == 0, "live frames are not decoded"
    ui.shot("live")
    page.click("#btn-live")
    page.locator("#btn-live[aria-pressed=false]").wait_for()

    # continuous reading: results keep coming until it is switched off
    before = seq(page)
    page.click("#btn-continuous")
    page.locator("#btn-continuous[aria-pressed=true]").wait_for()
    page.wait_for_function("n => Number(document.getElementById('r-seq').textContent.slice(1)) >= n + 2",
                           arg=before, timeout=60000)
    assert page.locator("#r-source").text_content() == "Continuous"
    page.click("#btn-continuous")
    page.locator("#btn-continuous[aria-pressed=false]").wait_for()

    # settings drawer: symbologies, discovery, an invalid value
    page.click("#btn-settings")
    page.locator("#drawer.open").wait_for()
    page.locator("#f-decode-formats input").first.wait_for()
    assert page.locator("#f-decode-formats input").count() == 19
    assert page.locator("#f-decode-formats input:checked").count() == 19
    page.locator("#drawer button", has_text="Discover").click()
    page.locator(".found li", has_text="Fake").wait_for()
    ui.shot("settings")
    ui.allow_http(400)
    page.fill("#f-live-intervalMs", "5")
    page.click("#btn-save")
    page.locator("#settings-errors li").first.wait_for()
    assert page.locator("#settings-errors li", has_text="live.intervalMs").count() == 1
    page.click("#btn-revert")
    page.click("#btn-close-settings")
    page.locator("#drawer.open").wait_for(state="detached")
