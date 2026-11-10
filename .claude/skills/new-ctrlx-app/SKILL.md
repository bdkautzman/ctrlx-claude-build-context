---
name: new-ctrlx-app
description: Create a new ctrlX OS app (Python snap with web UI, Data Layer nodes and app-data settings) from the kit template, then shape it to the requested function. Use when the user wants a new ctrlX app/snap.
argument-hint: "[what the app should do]"
---

# Create a new ctrlX app

Goal from the user: $ARGUMENTS

1. **Clarify before creating** (ask in one message, propose defaults):
   - snap name `ctrlx-[<company>-]<app>` (lowercase, ≤ 29 chars; company prefix, author and URL come from `kit.toml` -
     ask for them only when it is missing, template `kit.example.toml`), title (3–40 chars)
   - what the web UI shows/does, which Data Layer nodes (address, type, read-only/writable, unit),
     which settings (name, type, default, limits), external systems/protocols (→ may need plugs or ports!)
   - **roles**: which actions are *operate* (run the app: trigger, start/stop, write runtime values) and which are
     *configure* (change its setup); everything else is *view*. Propose the split; ask where it is not obvious
     (CLAUDE.md "Roles")
   - home screen widget: what it shows (the template's `www/widgets.js` `ROWS`), or none
   - license: MIT unless the user asks for another (then `new_app.py --license <SPDX id>` and put that license's text
     into the app's `LICENSE`); don't ask when the user says nothing about it
   - **RAM and snap size**: estimate them (libraries, buffers, images; device limits: `reference/ctrlx-core-hardware/README.md`). Likely above 75 MB RAM (App Guide 6.2,
     CLAUDE.md "RAM") or 100 MB snap: tell the user with the estimate and ask - accept for this app, or reduce
     (smaller buffers/images, fewer libraries)
   - **custom tools the request would need** (CLAUDE.md "Custom tools: ask first"). Go through the request and
     list everything neither the template, the kit tools nor the standard library covers, for example:

     | Need | Typical custom tool |
     |---|---|
     | an output format (PDF, Excel, images, charts) | your own writer, metrics/font data, a viewer to check the result |
     | a protocol or OS interface (fieldbus, netlink, binary formats) | your own parser/client |
     | checking something the kit can't check yet | a new `tools/` command, an app `scripts/` tool, a test harness |
     | measuring on a device | a measuring script - for CPU/RAM use the System Analyzer instead (`build-deploy-test.md` §5) |
     | any of the above | a new dev dependency (also outside the snap) |

     Per item, ask with the options and a recommendation: existing open-source library (license, size, C extension
     → aarch64/cp312 wheel?), a small custom version (rough size in lines), or reduce the feature / check it another
     way. Rough cost matters to the user: every extra tool means more code to write, test and keep in the
     conversation (`docs/ctrlx/decisions.md`, "Custom tools only after asking"). If the user is not there to
     answer, take the option with the least custom code and say so in the report.
2. Create it: `python3 tools/new_app.py --name <app> --title "..."` (`--author`/`--url` without `kit.toml`) →
   `apps/<name>/`. It must end with `RESULT: OK`.
   **Test users** (user's request 2026-11-08): for each test device this app will be tested on (CLAUDE.md "Test
   devices"), run `tools/ctrlx.py --device <device> test-users`. If any is MISSING, ask the user (AskUserQuestion)
   whether to create the test users kit-view / kit-operate / kit-configure on that device for testing; on yes:
   `tools/ctrlx.py --device <device> test-users create` (they stay there for later apps). On no: the role tests
   run locally only - put that into `KNOWN-ISSUES.md`. Device unreachable: say so and check again before the first
   device test.
3. Read `docs/ctrlx/README.md`, then implement in this order, running `tools/check.sh apps/<name>` after each step:
   1. `defaults/settings.json` + `src/app/settings_rules.py` (see skill `add-app-setting`)
   2. `src/app/datalayer_nodes.py::build_node_specs` (see skill `add-datalayer-node`); replace the example nodes
      (JSON documents: `json=True`; web roles: `view_role` / `write_role`)
   3. business logic in a new module under `src/app/` (add it to `src/pyproject.toml` packages if it is a sub-package),
      wired from `src/app/main.py`; settings changes via `store.add_listener`
   4. `www/` UI: the app's code in `app.js` / `style.css` / `index.html`; `kit.js` / `kit.css` stay as they are (API
      calls, roles, settings drawer, help menu + tutorial steps via `Kit.setupHelp`, `Kit.download` / `Kit.upload`);
      web routes with `auth.require(self.headers, auth.<ROLE>)` and the helpers of `web_server.py` (`_send_bytes`,
      `_receive_file`, `_query`); relative URLs; no CDN. Adapt or remove the widget (`www/widgets.js`, `widget.svg`,
      manifest `uiExtensions`)
   5. manifest: titles, icons, menu texts, scope names/descriptions (say what view / operate / configure mean in
      this app)
   6. `README.md` of the app, `CHANGELOG.md`, `KNOWN-ISSUES.md` (working document of problems, caveats, untested
      parts and diagnostics - CLAUDE.md "Definition of done" 6; link it from the README)
4. New Python dependencies: prefer the standard library. Each dependency → `src/pyproject.toml` + `fossinfo.json`;
   C-extension packages need aarch64/cp312 wheels. Ask before adding.
5. New plugs/slots/ports: **stop and ask** (docs/ctrlx/reserved-interfaces-and-ports.md).
   A custom tool that turns out to be needed only during the work (it was not on the list from step 1):
   **stop and ask** too, before writing it - with the same options and a recommendation.
6. Finish: unit tests for the new logic, `tools/check.sh apps/<name>` clean, `./run-local.sh` smoke test
   (curl the API with APP_DEV_NO_AUTH=1), then the UI (7) and the user's UI check (8); build if snapcraft is available
   only after that. Report what was verified and what wasn't, and put the unverified parts and every problem found into
   `KNOWN-ISSUES.md`.
7. UI: adapt `tests/ui_scenario.py` to the new page (click the real buttons, name the screenshots, the tutorial),
   keep the `data-ui-ready` marker working (`Kit.ready()` in `app.js`), run `python3 tools/ui_check.py apps/<name> --narrow`
   and with `--role reader` and `--role operator`, and look at the screenshots. If `tools/setup-ui-test.sh --check`
   fails, tell the user the UI was not seen. Installed on a device: the same with `--device <device>` (as admin) and
   `--device <device> --role reader|operator|writer` (as the test users).
8. **The user checks the UI before the first snap build** (CLAUDE.md "Definition of done" 5, `build-deploy-test.md` §1):
   `APP_DEV_HTTP_HOST=0.0.0.0 ./run-local.sh` in the background, the URL and what the page shows to the user; they leave
   notes on the page (Alt+click) or say what to change, `/ui-feedback` works through them, repeat until they say the UI
   is OK. Then build and deploy (skill `build-and-deploy`).
