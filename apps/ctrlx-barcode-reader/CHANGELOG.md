# Changelog

Versions follow semantic versioning and must match `version:` in `snap/snapcraft.yaml`.

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
  entries. Not yet: decoding of a real code (none fully in view).
- ctrlX COREvirtual (amd64, 87 MB snap), simulated camera: 23 of 23 reads decoded exactly the generated codes, both
  handshakes, continuous, 0.2 % CPU idle, 97 MB RSS; ui_check --device virtual OK. With the real camera: control
  through the NAT works, reads end with the NAT hint after 3 s and the camera is reopened after 3 failures.
- No AppArmor/seccomp denials of the app (besides a read of /proc/<pid>/mounts that every kit app shows).
