# Example apps - what to take, what to avoid

All three predate ctrlX OS 4.x (core20/core22 bases). Run
`python3 tools/ctrlx_check.py reference/example-apps/<app>` to see every deviation.

## datalayer.ab (CIP Connect, S-Gilk) - core20
* Take: providing many PLC tags (Allen-Bradley via pylogix/pycomm3) as Data Layer nodes,
  bulk/priority read concept, config.json in app data with a menu link to the ctrlX app-data
  file view (`/solutions/configurations/appdata/content?path=CIPData%2F`), reload on mtime change,
  `connect-plug-active-solution` hook that seeds a default config, bundled wheels (`python-packages: ./whl/...`).
* Avoid: hardcoded `/var/snap/rexroth-solutions/...` paths, log files in app data, manifest
  without `id`/`items`, appDirectory name with spaces/upper case, snap name without `ctrlx-`.

## ctrlx-cam-builder (bdkautzman) - core22
* Take: web UI with a chart editor (Chart.js + drag plugin, Observable Plot - vendored libs
  were removed from this copy) writing a JSON string node; provider + client in one app.
* Avoid: token/scope checks commented out with `restricted: []`, manifest named after the SDK
  sample, scopes copied from the sample, `architectures:` (core22 syntax), no `.web` in proxy name.

## sqlite-ctrlx (alex4acre) - core20
* Take: string nodes as a command interface (SQL in, result out), config.json + database in app
  data, log level from config.
* Avoid: database + log files in app data on internal flash if written frequently (put large
  or frequently changing data on external storage), version `2.0.05` (not semver),
  manifest file named `package-manifest.json`.
