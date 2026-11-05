# Roadmap - ctrlx-barcode-reader

## Scope (tentative, user, 2026-11-04)

This app stays a simple barcode reader for one camera. Several cameras, more image processing and other major
features go to a separate new app built on the same design, see "Later".

## 0.2.0: done (2026-11-04)

Speed fixes and general improvements, built clean for amd64 and arm64 and installed on the X7 and X3. Results: `CHANGELOG.md`, `KNOWN-ISSUES.md` 24. Decisions by the user: camera region included, off by default; Mono8 default for new installs;
~100 MB as this app's RAM figure. A worker thread for display images was tried and dropped: an exact-half resize gave more.
Tested afterwards (2026-11-05): save and load on the X3, camera cable pulls on the X7 (`KNOWN-ISSUES.md` 28, 29).

## Next, small

* Help text of `camera.exposureAuto`: warn or remove it, auto exposure isn't reliable with SingleFrame (`KNOWN-ISSUES.md` 24).
* Camera region on the X3: find out why about 1 image in 100 fails (26).
* Camera gone: set `camera/state` before closing the camera instead of about 8 s later, and a shorter error text than the GenICam register chain (29).
* Jumbo frames on the X3: fewer packets, the cost that matters there (needs a device setting and a switch that passes them). The software trigger analysis (1).

## Later: a separate app (tentative, user, 2026-11-04)

Same underlying design, with more features, several cameras and higher RAM limits, directed at the X5 and X7 only.

Points to settle when it starts:

* Name, and creating it with `tools/new_app.py`, then bringing in `camera`, `imaging`, `storage`, `simulator`.
* Only an amd64 snap would enforce "not on X2/X3", but a COREvirtual could still install it. `docs/ctrlx/release-and-validation.md` lists
  the arm64 snap as mandatory: this matters only if the app ever goes through Bosch validation.
* A settings and Data Layer layout for several cameras from the start.
* From the measurements: receiving costs ~5 % of a core per camera on the X7, one port carries ~4 cameras at 40 µs, and
  exposing cameras together needs the hardware trigger (not tested). An X5 has to be measured: same ports, slower CPU, half the RAM.
* Free run with a rate limit and newest-frame handling (`KNOWN-ISSUES.md` 21), the software trigger question, an AI module on X5/X7.
* The RAM exception needs its own OK (kit rule).
