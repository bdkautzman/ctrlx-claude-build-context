# Changelog

Versions follow semantic versioning and must match `version:` in `snap/snapcraft.yaml`.

## 0.3.0

- **Kit UI** (template 0.5.0, user's request 2026-11-10): the page runs on the kit's `www/kit.js` / `kit.css`
  (API calls, roles, settings drawer, help menu + tutorial, About, light/dark) instead of its own copies; app code stays
  in `app.js` / `style.css`.
- **Three roles** view / operate / configure (new scope `ctrlx-barcode-reader.web.operate.x`, manifest names and
  descriptions for all three). View: results, images, history, camera state (`GET /reader` brings the page's `view`
  data: region, live interval). Operate: **Read**, **Continuous**, **Live**, camera **Reconnect**. Configure: settings,
  the decode **Region**, camera **Discover**. Before: `.r` / `.rw`.
- Tabs **Reader** and **History** (with the number of reads) under the top bar.
- Settings drawer keeps the app's own fields (code formats, camera address with *Discover*, medium); the pixel formats
  have readable names (`enumLabels`).
- The empty image area keeps the camera's aspect ratio before the first image.
- **Settings drawer** (kit): help texts as ⓘ tooltips next to the labels, true/false as switches, *Save* stores and
  closes the drawer.
- **Styling** settings (new, as in the template): `style.accent`, `panel`, `text`, `panelDark`, `textDark`; page
  background, lines and secondary text are derived. **Light / dark** button in the top bar (per browser).
- **About** in the help menu: name, version, author, license (MIT), AI note (`GET /status` `about`; `config.py`
  `APP_AUTHOR`, `APP_URL`, `APP_LICENSE`, `APP_AI_NOTE`; `snapcraft.yaml` `license: MIT`).
- `/status` answers `role`, `canOperate`, `canConfigure`, `style`, `about`; `GET /values` lists the nodes for view
  (`NodeSpec.view_role`: `settings/active` only for configure), `PUT /values/<node>` needs operate
  (`NodeSpec.write_role`).
- Tests: `tests/test_about.py`, the role split in `tests/test_web_and_nodes.py`, UI scenario as reader and operator.
- **UI feedback overlay, dev only** (2026-11-09, from the kit template, user's request): `./run-local.sh` sets
  `APP_DEV_FEEDBACK` to the kit's `tools/ui-feedback/overlay.js`; the dev server (`src/app/dev_feedback.py`, hooks in
  `web_server.py` and `config.py`) adds it to the HTML pages and keeps the notes in `.dev/feedback.json`. Alt+click an
  element, type a note; skill `/ui-feedback` works through the notes. Only with `APP_DEV_NO_AUTH=1` outside the snap;
  `APP_DEV_FEEDBACK=0 ./run-local.sh` turns it off. Tests: `tests/test_dev_feedback.py`. No snap built with it
  (nothing in the snap changes behaviour).

## 0.2.0

Speed fixes and general improvements (2026-11-04). This app stays a simple reader for one camera: `ROADMAP.md`. Settings of 0.1.0 keep working and keep their
values, so an upgraded app gets the new defaults only if they are chosen in the settings.

Measured at 5 ms exposure, gain 100, one Code 128 label at rest (0.1.0 → 0.2.0):

| | X7 | X3 |
|---|---|---|
| Read, default settings | 226 → 122 ms | 669 → 652 ms |
| Continuous, default settings | 4.1 → 7.5/s | 1.1 → 1.4/s |
| Continuous, Code 128 only, no rotate/downscale | 9.8 → 12.4/s | 2.5 → 3.9/s |
| The same with the new camera region | 48/s, 20 ms per read | 18/s, 38-43 ms per read |
| RAM (RSS) while reading | 131-137 → ~102 MB | 126-128 → ~99 MB |

- Camera region (`decode.roi.onCamera`, off by default): the camera sends only the decode region; code positions stay in sensor pixels.
- `camera.packetDelayUs` default -2, automatic: 6 µs on amd64 (X5, X7), 40 µs on arm64 (X2, X3); two images failing close together raise it a step.
- `camera.pixelFormat` default `Mono8` for new installs (a gray picture); Bayer formats stay selectable.
- Display images: halve instead of scaling when within 10 % of the target, so 1280 gives 1224 wide (X3: 153 → 65 ms in color, 106 → 24 ms in gray).
- `continuous.intervalMs` minimum 100 → 10 ms.
- `MALLOC_MMAP_THRESHOLD_` for the service: freed image buffers go back to the system at once. PNG compression level 3 → 1.
- Opening the camera resets a region and binning left by other software and clears the camera's 1 s AcquisitionStart after such changes (`KNOWN-ISSUES.md` 20).
- Fixed: when the stream got a port reserved by ctrlX (~3.5 % of the times the camera opens), no image arrived until 3 failed reads reopened the camera. Present since 0.1.0.
- Camera errors name the application that holds the camera. Discover searches again when the first answer is empty.
- New Data Layer nodes `result/grab-ms`, `result/decode-ms`, `camera/resent-packets`, `camera/missing-packets`; `result/json` holds `grabMs`, `decodeMs`.
- Tested on devices for the first time: image storage on a USB stick, PNG download, the Data Layer handshakes on the X7, running beside other apps, long runs with memory samples (44 minutes X3, 40 minutes X7). Auto exposure turned out unreliable with SingleFrame.

Open: `KNOWN-ISSUES.md` 26 (camera region on the X3: about 1 image in 100 fails). Not tested: camera power cycle, save and load with configurations.

## 0.1.0

First version (2026-11-03), from the kit template 0.1.7.

- GigE Vision camera through Aravis 0.8.30 + PyGObject (Ubuntu 24.04 packages staged into the snap): open by IP
  address or device id, software-trigger acquisition, exposure/gain (+ auto), pixel format, packet size/delay,
  reserved stream ports avoided, reconnect after control loss / 3 failed images, discovery.
- OpenCV 4.14 image processing (Bayer/RGB/Mono 8-bit → gray for decoding, half-size debayer for display images,
  decode region), zxing-cpp 3.1.1 decoding of 19 symbologies.
- Data Layer: `trigger` (bool handshake), `request-id` (id handshake, queue of 4), `continuous`, `busy`,
  `result/*` incl. `result/json`, `camera/*`.
- Web UI: image with code outlines, Read / Live / Continuous / Region (drawn with the mouse) / PNG download,
  result and camera cards, history table with thumbnails (RAM), settings drawer with symbology checkboxes,
  camera discovery and medium selection.
- Optional image storage (JPEG/PNG + results.csv) on a removable medium (`removable-media` plug), size limit.
- Simulated camera (Aravis fake camera + generated code scenes or app-data test images) for the COREvirtual,
  whose QEMU NAT address the camera can't stream to.
- Binary wheels (OpenCV 4.14.0.94, numpy 2.3.5, zxing-cpp 3.1.1) installed for the target architecture with sha256
  pins (part `wheels`). numpy 2.5.4 failed on the COREvirtual (needs x86-64-v2) and took the Data Layer with it.
- First start after the install: settings loading retries while the app data folder is not writable yet.
- FOSS: fossinfo with every component's license text, written source offer for the LGPL/GPL parts.

- Acquisition: one SingleFrame acquisition per read by default (`camera.acquisition`; the STC-MCS500POE ignored
  software triggers, gotchas #100); packet delay in µs, default 40 (`camera.packetDelayUs`, gotchas #101).
- Web server listen backlog 64 and at most 3 thumbnail requests at a time (a full history gave a 502, gotchas #102).
- numpy, OpenCV and zxing-cpp load at start (the first read on a CORE was 3 s slower).

Verified 2026-11-03 (clean builds of both architectures):
- Local: 78 unit tests, `tools/check.sh` OK, `tools/ui_check.py --narrow` OK with the simulated camera.
- Real ctrlX CORE (arm64, 61 MB snap), camera at 192.168.1.99: every 5 MP frame complete (1520 B packets, 40 µs);
  0.5 s exposure in the dim test room: grab ~685 ms, decode ~420 ms (all 19 symbologies), ~1.1 s per read;
  `trigger` and `request-id` handshakes, `continuous` (8 reads in 10 s); 0.1 % CPU idle, 57 % of one core at 1 read/s,
  128 MB RSS steady (above the 75 MB guideline - accepted for this app); ui_check --device core OK with 50 history
  entries. A real Code 128 label (X004WHVORB) on a box ~0.5 m away: 5 of 5 reads decoded it, ~1.15 s per read
  (grab ~685 ms incl. 0.5 s exposure, decode ~450 ms), Data Layer result nodes OK.
- ctrlX COREvirtual (amd64, 87 MB snap), simulated camera: 23 of 23 reads decoded exactly the generated codes, both
  handshakes, continuous, 0.2 % CPU idle, 97 MB RSS; ui_check --device virtual OK. With the real camera: control
  through the NAT works, reads end with the NAT hint after 3 s and the camera is reopened after 3 failures.
- No AppArmor/seccomp denials of the app (besides a read of /proc/<pid>/mounts that every kit app shows).
