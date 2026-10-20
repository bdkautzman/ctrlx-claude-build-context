# Sources (snapshot taken 2026-10-15)

| Source | Version / commit | Used for |
|---|---|---|
| github.com/boschrexroth/ctrlx-automation-sdk | tag **4.6.0** (`6522821`, 2026-04-07) | `docs/ctrlx/upstream-sdk-4.6.0/` (verbatim), `reference/sdk-4.6.0-samples-python/`, Python API source extracted from `doc/api/python` → `reference/ctrlxdatalayer-api-3.6.0/` |
| github.com/boschrexroth/json-schema | `1a6aefc` (2025-12-12, "release 4.4") | `tools/schemas/` (package-manifest 1.6/1.7, fossinfo, validation descriptions, configuration, setup), `reference/schemas/` examples |
| github.com/boschrexroth/rest-api-description | `4774d1d` (2026-04-16) | `reference/rest-api/` (package manager 1.15.0, identity manager 2.10.0, Data Layer 2.1.5, logbook 2.4.0, solutions 1.12.2, license manager 1.10.0) |
| github.com/boschrexroth/ctrlx-actions | `5cb1539` (2022-12-19) | evaluated, **not used** (outdated for core24) |
| github.com/S-Gilk/datalayer.ab | `80b58d6` (2024-07-15) | `reference/example-apps/datalayer.ab` (CIP/Allen-Bradley provider, core20) |
| github.com/bdkautzman/ctrlx-cam-builder | `f79f4ff` (2026-02-23) | `reference/example-apps/ctrlx-cam-builder` (web UI + node, core22; vendored JS libs removed) |
| github.com/alex4acre/sqlite-ctrlx | `6966527` (2024-07-30) | `reference/example-apps/sqlite-ctrlx` (SQLite provider, core20) |
| boschrexroth.github.io/ctrlx-automation-sdk/latest | online docs = the SDK repo `doc/` folder | – |
| developer.community.boschrexroth.com (forum) | GLIBC/arm64 thread | gotcha 16 |

Binaries (`*.snap`, `*.whl`, `*.deb`), images and vendored JS libraries were left out of the copies.

## Refreshing for a new ctrlX OS / SDK version

1. `git clone https://github.com/boschrexroth/ctrlx-automation-sdk && git checkout <tag>`;
   re-copy the files listed in `docs/ctrlx/upstream-sdk-*/` and the Python samples; diff them.
2. Re-extract the Python API: the Doxygen pages `doc/api/python/*_8py_source.html`.
3. Update `tools/schemas/` from the json-schema repo, `MANIFEST_SCHEMA`/`EXPECTED_BASE` in
   `tools/ctrlx_check.py`, and `tools/data/reserved-interfaces.json` from
   `appdevguide_reserved-interfaces.md`.
4. Update `platform-baseline.md`, then run `python3 -m unittest discover -s tools/tests` and
   `tools/check.sh templates/python-webapp`.
