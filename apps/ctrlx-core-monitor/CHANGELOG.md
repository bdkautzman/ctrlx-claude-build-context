# Changelog

Versions follow semantic versioning and must match `version:` in `snap/snapcraft.yaml`.

## 0.1.2

- Template 0.1.4 layout: Python package in `src/`, one `files` part, incremental builds
  and `python-packages: []` (same snap content and behaviour). Builds: UI change 4.6 min
  instead of 12.8, Python change 13.3 instead of ~20, full 16.7 instead of ~20.

## 0.1.1

- ctrlX OS version also for ctrlX OS 3.6 devices (no `system/info/ctrlxOsVersion` there: taken
  from the Device Admin app, node `snap_rexroth-deviceadmin`); the UI shows only "ctrlX OS x.y".
- Add dialog: "Skip certificate check" label next to its checkbox; hints as (i) tooltips
  (core name, password storage, connections).

## 0.1.0

- Created from the ctrlX Python web-app template (0.1.2).
- Monitoring of other ctrlX COREs via Data Layer remote connections (`devices/remotes/cm-<name>`):
  host name, IP, ctrlX OS / Device Admin version, architecture, uptime, CPU and memory.
- Connections stored in `settings.json` (app data); web UI with "+ Add core" dialog and remove.
- Passwords never leave the app (masked in API responses and Data Layer nodes).
