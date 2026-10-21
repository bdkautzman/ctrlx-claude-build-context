# snapcraft.yaml rules for ctrlX OS 4.6

Reference implementation: `templates/python-webapp/snap/snapcraft.yaml`. Enforced by `tools/check.sh`.

## Metadata (App Guide 4.3, basechecks 1.1)

| Key | Rule |
|---|---|
| `name` | starts with `ctrlx-`, lowercase, `[a-z0-9-]`, ≤ 32 chars; partners: `ctrlx-<company>-<app>`. Keep ≤ 29 so `package-run/<name>/web.sock` stays ≤ 50 chars. Unique in the snap universe. |
| `title` | 3–40 chars, shown in the App Store / apps page |
| `version` | **quoted** semver `'1.2.3'` (unquoted `1.0` becomes a YAML float), ≤ 32 chars, must increase every release |
| `summary` | ≤ 78 chars |
| `description` | starts with `<!--\n author: …\n url: …\n-->` followed by ≥ 16 chars of text |
| `base` | `core24` |
| `confinement` | `strict` |
| `grade` | `stable` for releases (`devel` tolerated during development, check warns) |
| `platforms` | `amd64:` and `arm64: {build-on: [amd64, arm64], build-for: [arm64]}` (core24 syntax; `architectures:` is core22) |

Snap file names produced: `<name>_<version>_<arch>.snap`.

## Apps (daemons)

```yaml
apps:
  service:
    command: bin/run.sh          # wrapper dumped from wrapper/run.sh - must be executable, LF endings
    daemon: simple
    restart-condition: always
    passthrough:
      restart-delay: 5s          # >= 5s required (validation), avoids systemd 10 s lock-out
```

## Interfaces used by the template (all must be described in `validation/slotplug-description.json`)

| Name | Kind | Definition | Why |
|---|---|---|---|
| `package-assets` | slot, content | `content: package-assets`, `source.read: [$SNAP/package-assets/${CRAFT_PROJECT_NAME}]` | ctrlX reads manifest, fossinfo, i18n |
| `package-run` | slot, content | `content: package-run`, `source.write: [$SNAP_DATA/package-run/${CRAFT_PROJECT_NAME}]` | reverse proxy reaches the Unix socket |
| `datalayer` | plug, content | `content: datalayer`, `target: $SNAP_DATA/.datalayer` | Data Layer IPC |
| `active-solution` | plug, content | `content: solutions`, `target: $SNAP_COMMON/solutions` | app data (configurations) |
| `network`, `network-bind` | plug | standard | Data Layer lib / socket server (as in SDK samples) |

Optional ones you may need later: `package-certificates` (slot, content, `source.write: [$SNAP_COMMON/package-certificates/${CRAFT_PROJECT_NAME}]`,
needs `certificatestores` in the manifest), `serial-port`, `raw-usb`, `hardware-observe` …
**Every new plug/slot: check `reserved-interfaces-and-ports.md` first, add it to the
validation description, and justify it. Never add a plug just to make an error go away.**

## Parts (Python)

```yaml
parts:
  app:
    plugin: python               # core24: builds a venv into the snap from pyproject.toml
    source: .
    stage-packages: [ctrlx-datalayer]   # native libcomm_datalayer; apt repo from the SDK
    prime: [...]                 # + override-prime: keep only the target arch's libcomm_datalayer.so*
                                 #   (full list in templates/python-webapp/snap/snapcraft.yaml)
  wrapper:  {plugin: dump, source: ./wrapper,  organize: {run.sh: bin/run.sh}}
  configs:  {plugin: dump, source: ./configs,  organize: {'package-assets/*': package-assets/${CRAFT_PROJECT_NAME}/}}
  www:      {plugin: dump, source: ./www,      organize: {'*': www/}}
  defaults: {plugin: dump, source: ./defaults, organize: {'*': defaults/}}
lint:
  ignore:
    - library: [usr/lib/aarch64-linux-gnu/libcomm_datalayer.so.*, usr/lib/x86_64-linux-gnu/libcomm_datalayer.so.*]
```

* Keep the template's `prime:` filter: without it the `ctrlx-datalayer` .deb adds ~100 MB
  (both architectures' libs, a static lib, headers, tools) - gotchas #32. If you add a
  stage-package, check `du -h --max-depth=3 prime/usr` after the build.
* Every Python sub-package must be listed in `pyproject.toml` `[tool.setuptools] packages`
  (check: `PY-PACKAGES`), otherwise it is silently missing in the snap.
* Pure-Python wheels only, if possible. Packages with C extensions must have manylinux
  wheels for **aarch64 and cp312** or the arm64 build fails/needs native build deps.
* Use `${CRAFT_PROJECT_NAME}` (core24); `${SNAPCRAFT_PROJECT_NAME}` is deprecated.
* `wrapper/run.sh` is a Python script with `#!/usr/bin/env python3` (SDK 4.6 pattern).
