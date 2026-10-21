# ctrlX app development reference (curated)

Pinned to **ctrlX OS 4.6 · SDK 4.6.0 · base core24 · snapcraft 8.x · package-manifest schema 1.7**.
Everything here was distilled from the files in `upstream-sdk-4.6.0/` (verbatim SDK 4.6.0 docs)
and the other sources listed in `sources.md`. When this summary and an upstream file
disagree, the upstream file wins, and the disagreement belongs in `gotchas.md`.

| Read this | when you… |
|---|---|
| `platform-baseline.md` | need any version number, path, or environment fact |
| `snapcraft-rules.md` | touch `snap/snapcraft.yaml` (plugs, slots, parts, naming, versions) |
| `package-manifest.md` | touch `configs/package-assets/*.package-manifest.json` |
| `web-ui-and-reverse-proxy.md` | change the web server, routes, menus, auth or scopes |
| `datalayer-python.md` | add/change Data Layer nodes or read other nodes |
| `app-data-settings.md` | add settings or change persistence / save-load behaviour |
| `reserved-interfaces-and-ports.md` | want to add any plug, slot, or open a port |
| `build-deploy-test.md` | build, install on COREvirtual, read logs, debug |
| `release-and-validation.md` | prepare a release or the Bosch validation artifacts |
| `gotchas.md` | **before** debugging anything: known problems and fixes |
| `sources.md` | need provenance (repos, commits, URLs) |

Upstream SDK docs worth opening directly (in `upstream-sdk-4.6.0/`):
`appdevguide.md` (all rules), `appdevguide_basechecks.md` (what validation checks),
`package-assets.md` (full manifest reference), `persistdata.md` (app data protocol),
`persist-device-settings.md` (backup/restore via the Setup app).

Exact Python Data Layer API: `../../reference/ctrlxdatalayer-api-3.6.0/*.py`.
REST APIs of the device: `../../reference/rest-api/*.openapi.json`.
