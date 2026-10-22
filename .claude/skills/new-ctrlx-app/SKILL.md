---
name: new-ctrlx-app
description: Create a new ctrlX OS app (Python snap with web UI, Data Layer nodes and app-data settings) from the kit template, then shape it to the requested function. Use when the user wants a new ctrlX app/snap.
argument-hint: "[what the app should do]"
---

# Create a new ctrlX app

Goal from the user: $ARGUMENTS

1. **Clarify before creating** (ask in one message, propose defaults):
   - snap name `ctrlx-<company>-<app>` (lowercase, ≤ 29 chars), title (3–40 chars), author/company, URL
   - what the web UI shows/does, which Data Layer nodes (address, type, read-only/writable, unit),
     which settings (name, type, default, limits), external systems/protocols (→ may need plugs or ports!)
2. Create it: `python3 tools/new_app.py --name ... --title "..." --author "..." --url ...` → `apps/<name>/`.
   It must end with `RESULT: OK`.
3. Read `docs/ctrlx/README.md`, then implement in this order, running `tools/check.sh apps/<name>` after each step:
   1. `defaults/settings.json` + `src/app/settings_rules.py` (see skill `add-app-setting`)
   2. `src/app/datalayer_nodes.py::build_node_specs` (see skill `add-datalayer-node`); replace the example nodes
   3. business logic in a new module under `src/app/` (add it to `src/pyproject.toml` packages if it is a sub-package),
      wired from `src/app/main.py`; settings changes via `store.add_listener`
   4. `www/` UI (keep the token handling in `app.js`; relative URLs; no CDN)
   5. manifest: titles, icons, menu texts, scope names/descriptions
   6. `README.md` of the app, `CHANGELOG.md`
4. New Python dependencies: prefer the standard library. Each dependency → `src/pyproject.toml` + `fossinfo.json`;
   C-extension packages need aarch64/cp312 wheels. Ask before adding.
5. New plugs/slots/ports: **stop and ask** (docs/ctrlx/reserved-interfaces-and-ports.md).
6. Finish: unit tests for the new logic, `tools/check.sh apps/<name>` clean, `./run-local.sh` smoke test
   (curl the API with APP_DEV_NO_AUTH=1), build if snapcraft is available. Report what was verified and what wasn't.
