# Changelog

Versions follow semantic versioning and must match `version:` in `snap/snapcraft.yaml`.
Every release needs a higher version than the previous one (ctrlX validation rule).

## 0.1.2

- Web UI: clicking "Set" after editing a Data Layer value wrote the old value back (the Edit
  button kept its first click handler); a refresh that was running when an edit started could
  discard the edit field.
- Removed the Settings-page menu entry: ctrlX OS 4.6 shows `_blank` settings tiles without a link
  and without a `category` under "core.settings.categories.undefined".

## 0.1.1

- Snap ~100 MB smaller: prime only the target architecture's `libcomm_datalayer.so*`; drop the
  static lib, headers and build tools of the ctrlx-datalayer .deb, and the venv tooling
  (pip, setuptools, wheel, packaging). fossinfo versions filled from the build.
- Solutions load: the polling pause started in the "query" phase expires after 60 s (Solutions
  sends no "abort" when another app refuses the load in "query").

## 0.1.0

- Initial version from the ctrlX Python web-app template.
