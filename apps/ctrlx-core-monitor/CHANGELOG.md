# Changelog

Versions follow semantic versioning and must match `version:` in `snap/snapcraft.yaml`.

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
