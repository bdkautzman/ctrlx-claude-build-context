---
name: ui-feedback
description: Work through the UI feedback notes the user left on an app's page with the dev-only overlay (Alt+click an element on the run-local.sh page; stored in <app>/.dev/feedback.json) - fix each open note in the app's web UI, mark it handled, re-run ui_check.py. Use for /ui-feedback, "my notes", "the feedback I left on the page", or when the user asks to fix what they marked in the UI.
argument-hint: "[apps/<name> | templates/python-webapp] [note ids]"
---

# Work through UI feedback notes

`./run-local.sh` puts the overlay `tools/ui-feedback/overlay.js` on the app's page (dev only, never in the snap; server
side `src/app/dev_feedback.py`). The user Alt+clicks an element, types a note, and it lands in `<app>/.dev/feedback.json`
(`{"version": 1, "items": [...]}`). One item:

| field | meaning |
|---|---|
| `id`, `status` | `open` = work for you; you set `done`, `question` or `wontfix` |
| `note` | what the user wants changed, in their words |
| `selector` | short CSS selector that found exactly this element when it was picked; ` >>> ` = into a shadow root (widget) |
| `path` | full structure from the nearest element with an id, e.g. `#values-body > tr:nth-of-type(6) > td:nth-of-type(3)` (rows the app renders) |
| `text`, `html` | the element's text and its opening tag when picked |
| `rect`, `viewport` | element position/size and browser window in CSS px; `viewport.width` < 700 = phone/HMI width |
| `page`, `colorScheme`, `timestamp` | page path + hash, light/dark, the user's local time |

## Steps

1. **Find the file**: `<argument>/.dev/feedback.json`; without an argument, the `.dev/feedback.json` under `apps/*/` or
   `templates/python-webapp/` with open notes (more than one app: ask which). Read it whole. Work = `status: "open"`
   (or only the ids given).
2. **Understand every note before editing**:
   - Find the element in the code from the ids/classes in `selector`, `path`, `html`: `www/index.html` (static parts),
     `www/app.js` (rendered parts: tables, lists, cards), `www/style.css`, the widget in `www/widgets.js`. The topbar
     buttons, settings drawer, help menu and tutorial are the shared parts `www/kit.js` / `kit.css`.
   - See it as the user did: `python3 tools/ui_check.py <app>` (`--narrow` when the note's viewport was narrow) and
     look at the screenshots; for a closer look, a throwaway Playwright script in the scratchpad (UI venv
     `~/.cache/ctrlx-kit/uivenv`).
   - A note that is unclear or has two reasonable readings: don't guess. Collect them and ask (AskUserQuestion, one
     question per note, up to 4 at once) before changing anything for them.
3. **Fix within the kit rules** (CLAUDE.md): app code in `app.js` / `style.css`. `kit.js` / `kit.css` stay as they come
   from the template: a note about a shared part of an *app* → ask whether to change the template or override it in
   the app's `style.css`; in the *template* change it there and say that existing apps keep their copies. A control that
   moves keeps its role (`data-role`, CLAUDE.md "Roles"). Relative URLs, no CDNs. Settings form: skill `add-app-setting`.
   A note that turns out to be a bug (not a design wish) also goes into the app's `KNOWN-ISSUES.md`.
4. **Mark each handled note** in the JSON (keep its fields, never delete notes - the user deletes them in the overlay):
   `"status": "done"` (or `"wontfix"` / `"question"` with the reason or the open question),
   `"resolution": "<one line: what changed, in which file>"`, `"resolvedAt": "<date -Iseconds>"`. The overlay reads the
   file every 5 s, so handled notes lose their pin on the open page.
5. **Check**: `python3 tools/ui_check.py <app>` (+ `--narrow` / `--role ...` when a note was about that) → `UI CHECK: OK`,
   and look at the screenshot of every fixed spot. Keep `tests/ui_scenario.py` in step with the UI (definition of done
   5), then `tools/check.sh <app>`.
6. **Report** one line per note (id: what changed, or the question / why not), and tell the user to reload the page:
   static files are served fresh; a change under `src/` needs `./run-local.sh` restarted. Don't commit unless asked.
   Ask whether the UI is OK now or they have more notes: repeat until they say it is OK - the snap build waits for that
   (CLAUDE.md "Definition of done" 5).

The dev server: `cd <app> && ./run-local.sh` (from outside the VM through a port forward:
`APP_DEV_HTTP_HOST=0.0.0.0 ./run-local.sh`); `APP_DEV_FEEDBACK=0 ./run-local.sh` turns the overlay off.
`tools/ui_check.py` never shows the overlay.
