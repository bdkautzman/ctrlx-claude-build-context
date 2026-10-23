"""UI smoke scenario for tools/ui_check.py - this app's page in headless Chromium. Adapt it to your UI.

    python3 ../../tools/ui_check.py .                  # app started locally (Data Layer off)
    python3 ../../tools/ui_check.py . --device         # the installed app on the default device

tools/ui_check.py calls prepare(ui) before the page opens, then opens it, saves the screenshot "start"
and calls run(ui). Any JavaScript error, console error or HTTP error >= 400 fails the run.
  ui.page          Playwright page, sync API (https://playwright.dev/python/docs/api/class-page)
  ui.shot(name)    wait until the page is ready, save <app>/.ui-check/<local|device>-<name>.png
  ui.wait_ready()  after an action: let the page react, wait for <body data-ui-ready="true">
  ui.api(method, path, body[, as_admin=True])   call this app's API (/api/v1 + path) as the page's user;
                   as_admin (local only) e.g. to set up a test that runs with --role reader
  ui.shot(name, locator)       screenshot of one element instead of the page
  ui.role          "admin" (default, auth off), "writer" or "reader" (tools/ui_check.py --role)
  ui.allow_http(400)           before provoking an expected HTTP error
  ui.local         False with --device: don't change anything on a device unless the user asked
Click the real buttons (gotchas #35: "Set" wrote the old value back, Enter worked).
Not run by tools/check.sh (takes ~1 min in the build VM): run it after UI changes and look at the screenshots.
"""


def run(ui):
    page = ui.page
    if not ui.local:
        return  # on a device: the start screenshot only

    # Data Layer value: Edit -> type -> click "Set"
    row = page.locator("#values-body tr", has_text="/values/setpoint")
    row.get_by_role("button", name="Edit").click()
    row.locator("input").fill("12.5")
    row.get_by_role("button", name="Set").click()
    row.locator("td.val", has_text="12.5").wait_for()

    # settings form: a valid change is saved, an invalid one is rejected with a field error
    page.fill("#f-example-label", "UI check")
    page.click("#btn-save")
    page.locator("#save-state", has_text="Saved").wait_for()
    ui.allow_http(400)
    page.fill("#f-general-heartbeatIntervalMs", "5")
    page.click("#btn-save")
    page.locator("#settings-errors li").first.wait_for()
    ui.shot("settings-error")
    page.click("#btn-revert")
