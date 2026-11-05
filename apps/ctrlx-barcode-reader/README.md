# Barcode Reader (`ctrlx-barcode-reader`)

Reads 1D and 2D barcodes with a GigE Vision camera on ctrlX OS 4.6. Written for the **Omron Sentech
STC-MCS500POE** (5 MP, 2448 × 2048, BayerRG8/Mono8, PoE); it uses only standard GenICam (SFNC) features,
so other GigE Vision cameras should work as well.

* **Acquisition**: [Aravis](https://github.com/AravisProject/aravis) 0.8.30 (Ubuntu 24.04 package) through
  PyGObject. Every read is a fresh exposure: one SingleFrame acquisition per read (the STC-MCS500POE ignored
  software triggers, see "Camera and network"), or a software trigger for cameras where it works.
* **Image processing**: OpenCV 4.14 (debayer/grayscale, decode region, display images).
* **Decoding**: [zxing-cpp](https://github.com/zxing-cpp/zxing-cpp) 3.1.1 - Code 128/39/93, Codabar, ITF, EAN-8/13,
  UPC-A/E, GS1 DataBar, QR, Micro QR, rMQR, Data Matrix, PDF417, Aztec, MaxiCode. (OpenCV's own decoders
  read only QR and EAN/UPC.)
* **Triggers**: Read button in the web UI, Data Layer `trigger` (bool handshake) and `request-id` (id
  handshake), continuous reading (UI and Data Layer), live picture for setting up (UI).
* **Results**: Data Layer nodes, the web UI (image with code outlines, result, history of the last reads with
  thumbnails), optionally images + CSV on a removable medium.

**Known issues, caveats, measurements and what is not tested yet: [KNOWN-ISSUES.md](KNOWN-ISSUES.md)** (working
document - keep it current).

Design decisions (the user's, 2026-11-03): decoder OpenCV + zxing-cpp; one camera, generic GenICam, code laid
out for more; both PLC handshakes; images in RAM, a removable medium optional in the settings; live picture
and continuous reading (no hardware trigger); decode region drawn in the UI; result history table;
a written offer for the LGPL/GPL sources; on the COREvirtual a simulated camera (see "Test setup").

## Web UI

ctrlX menu → **Barcode Reader**.

| Control | What it does |
|---|---|
| **Read** | trigger, capture, decode; the result and the image with numbered code outlines appear |
| **Live** | a downscaled live picture (`live.intervalMs`, `live.width`), not decoded - for focus, exposure, region |
| **Continuous** | reads every `continuous.intervalMs` until switched off (same as Data Layer `continuous`) |
| **Region** | drag a rectangle on the image: only that part is decoded (`decode.roi`); **Whole image** clears it |
| **PNG** | the full-size image of the newest read (lossless) |
| History | the last `images.historySize` reads (RAM) - click a row to show its image and codes |
| Camera card | model, serial, address, image format, exposure/gain, trigger mode, stream port/packets, counters, warnings; **Reconnect** |
| Settings | drawer with all settings; **Discover** lists the GigE Vision cameras on the device's networks |

Users with only `ctrlx-barcode-reader.web.all.r` see results, images and the history but no controls (a
live picture makes the camera take pictures, so it needs `.rw` too).

## Data Layer (`ctrlx-barcode-reader/...`)

| Node | Type | Access | Meaning |
|---|---|---|---|
| `trigger` | bool8 | rw | write **true**: capture + decode. The app writes **false** when the result is published. A second true while one is pending is ignored |
| `request-id` | uint64 | rw | write a **new** id (not 0, not the current value): capture + decode; `result/request-id` gets the same id. Writing the same id again does nothing (cyclic writes are harmless). Rejected with `DL_INVALID_VALUE` while 4 reads wait |
| `continuous` | bool8 | rw | true: read continuously until false |
| `busy` | bool8 | r | a read is waiting or running |
| `result/sequence` | uint64 | r | +1 per published result |
| `result/request-id` | uint64 | r | id of the read (0 for trigger, UI, continuous) |
| `result/source` | string | r | `trigger`, `request`, `ui`, `continuous` |
| `result/ok` | bool8 | r | at least one code and no error |
| `result/count` | uint64 | r | number of codes |
| `result/text`, `result/format` | string | r | first code (reading order: top to bottom, left to right) |
| `result/codes` | string | r | JSON `[{"format","text","corners":[[x,y]×4]}]` in sensor pixels |
| `result/duration-ms` | float64 | r | from the trigger to the result (incl. waiting in the queue) |
| `result/grab-ms`, `result/decode-ms` | float64 | r | of that: getting the complete image; decoding it |
| `result/timestamp` | string | r | time of the image, ISO 8601 in the device's time zone |
| `result/error` | string | r | e.g. `no image within 3000 ms`, `camera not connected: …` - empty when OK |
| `result/json` | string | r | the whole result in **one** node - read this for a consistent set |
| `camera/state` | string | r | `connected`, `connecting`, `disconnected`, `error` |
| `camera/model`, `camera/address`, `camera/error` | string | r | |
| `camera/frames`, `camera/failures` | uint64 | r | images received / failed since the app started |
| `camera/resent-packets`, `camera/missing-packets` | uint64 | r | since the camera was opened: requested again (the device is near its limit); lost for good |
| `status/version`, `status/state`, `status/heartbeat`, `settings/active` | | r | as in every app of this kit |

**Order of the updates** after a read: all `result/*` values, then `result/request-id`, then `result/sequence`,
then `trigger` = false (if the read came from it), then `busy`. A read always ends with a result - also when the
camera is down (`ok` = false, `error` set), so a PLC handshake never hangs.

PLC sequences:

* **Bool**: write `trigger` = true → wait until `trigger` = false (or `result/sequence` changed) → read `result/json`
  (or `result/ok`, `result/text`, …).
* **Id**: write `request-id` = n (n = previous + 1, skip 0) → wait until `result/request-id` = n → read the result.
  Up to 4 requests can wait; each gets its own result in order.

## Settings (app data `ctrlx-barcode-reader/settings.json`)

| Setting | Default | |
|---|---|---|
| `camera.source` | `gige` | `gige` or `simulated` (Aravis' fake camera + generated codes / test images) |
| `camera.address` | `192.168.1.99` | IP address or Aravis device id; empty = first camera found |
| `camera.pixelFormat` | `Mono8` | `""` = keep the camera's; or `BayerRG8` for a color picture, … (8-bit formats only). Mono8 is the fastest |
| `camera.exposureUs`, `camera.exposureAuto` | 20000, `Off` | `Off`/`Once`/`Continuous` |
| `camera.gain`, `camera.gainAuto` | 0, `Off` | camera units (STC-MCS500POE 0-208) |
| `camera.acquisition` | `singleframe` | `singleframe`: AcquisitionStart → one frame → AcquisitionStop per read; `trigger`: running acquisition + software trigger per read |
| `camera.packetSize` | 0 | 0 = negotiate (largest packet the network passes; 1520 B on the test CORE) |
| `camera.packetDelayUs` | -2 | pause between stream packets; -2 = automatic (6 µs on amd64 such as X5/X7, 40 µs on arm64 such as X3, raised after a failed image), -1 = keep the camera's, 0 or more = fixed. Raise it when images arrive incomplete |
| `camera.timeoutMs`, `camera.buffers` | 3000, 2 | wait for the image; frame buffers (one 5 MB frame each) |
| `decode.formats` | all 19 | fewer = faster |
| `decode.tryRotate`, `tryInvert`, `tryDownscale` | true, false, true | zxing-cpp search options |
| `decode.maxCodes` | 10 | |
| `decode.roi` | off | `enabled`, `x`, `y`, `width`, `height` in sensor pixels (drawn in the UI); `onCamera`: the camera sends only the region |
| `live.intervalMs`, `live.width` | 500, 960 | |
| `continuous.intervalMs` | 1000 | between the starts of two reads, 10 ms or more |
| `images.displayWidth`, `jpegQuality`, `historySize` | 1280, 80, 50 | display images in RAM |
| `storage.target` | `ram` | `medium`: also save images to a USB stick / SD card |
| `storage.medium`, `save`, `format`, `jpegQuality`, `maxMb` | `""`, `all`, `jpg`, 90, 1024 | see below |
| `simulation.width`, `height`, `codes` | 1224, 1024, 3 | generated test scenes |

Camera settings that need a new stream (source, address, pixel format, packet size/delay, buffers, a camera region)
reopen the camera; exposure and gain are applied to the running camera. Nothing is written into the camera's user sets.

## Image storage on a removable medium

Mount the medium in ctrlX **Settings → Storage**; it appears under `/media/<name>`. Settings → Image storage:
`Store images` = medium, choose the medium. Each read (or only no-reads, `storage.save` = `noread`) is written to

    /media/<name>/ctrlx-barcode-reader/<YYYY-MM-DD>/<HHMMSS>-<sequence>-<ok|noread>.<jpg|png>
                                                   /results.csv   (time, sequence, source, requestId, ok, count, formats, texts, durationMs, error, file)

by its own thread; the oldest images are deleted above `storage.maxMb`. A medium that is too slow drops images
(counted in the camera card). Never the internal flash (kit rule).

## Getting speed

Measured 2026-11-04 with the STC-MCS500POE at 5 ms exposure and gain 100, one Code 128 label at rest. A read is exposure + transfer + decoding.

| Settings | X7: read, continuous | X3: read, continuous |
|---|---|---|
| Defaults (Mono8, automatic packet delay, all formats) | 122 ms, 7.5/s | 652 ms, 1.4/s |
| + `decode.formats` = only the symbologies in use (here Code 128) | 86 ms, 11/s | 281 ms, 2.9/s |
| + `tryRotate`, `tryDownscale` off | 77 ms, 12/s | 214 ms, 3.9/s |
| + camera region around the label (`decode.roi.onCamera`) | 20 ms, 48/s | 38-43 ms, 18/s |

In order of effect:

1. **Light.** Exposure adds directly to every read: a lamp or a wider aperture costs nothing in processing. Short exposures also reduce motion blur. Light the label from the front and use a fixed exposure:
   auto exposure isn't reliable with one frame per read, and in a dark room it ended with a black picture (`KNOWN-ISSUES.md` 24).
2. **Camera region** for a label that stays in one place: less to transfer and to decode. Draw the region with **Region**, tick
   *Camera sends only the region* in the settings. The picture then shows only the region; **Clear** gets the full frame back. Leave a margin:
   a label moving out of the region is not read. On an X3 about 1 image in 100 then fails with an error (`KNOWN-ISSUES.md` 26), so a PLC should check `result/ok` and read again.
3. **Only the symbologies in use**, and rotation and downscaled search off when labels have a fixed orientation and size. These searches also make
   reading more tolerant, so check the success rate after switching them off.
4. **Mono8** (default) needs no debayering. Choose a Bayer format only for a color picture; it costs the X3 about 40 ms more per read.

The packet delay is automatic per device type. A camera card or `camera/resent-packets` that counts up means the device is near its limit; `camera/missing-packets`
or failed images mean it is over it: raise `camera.packetDelayUs`. The X3 resends some packets at any delay. Keep the label away from the picture's edge:
a code needs its quiet zones.

## Camera and network

* The camera needs an IP address in a network the CORE reaches directly (here 192.168.1.99 on the CORE's
  XF10 network 192.168.1.0/24). Discovery (broadcast) finds cameras only on directly connected networks; others
  work by IP address.
* GigE Vision traffic: GVCP commands to UDP port 3956 of the camera; the replies and the GVSP image stream
  come back to two UDP ports the OS chooses. A stream port reserved by ctrlX OS is not used (the app opens
  another one). `validation/portlist-description.json`.
* ctrlX OS limits socket receive buffers to 208 KB (`net.core.rmem_max`, all devices) - ~2.5 ms of a camera
  sending at 1 Gbit/s - and a strict snap can't use packet sockets. How close a device runs to that limit depends on its CPU, measured with
  the STC-MCS500POE (5 MP): the X7 takes the stream at any packet delay, the X3 needs 40 µs. Automatic mode (`camera.packetDelayUs` = -2)
  starts at 6 µs on amd64 and 40 µs on arm64; two images failing close together raise it a step (6, 12, 24, 40, 60, 100 µs), which the camera card
  shows; **Reconnect**, a camera settings change or an app restart start over.
* **Software trigger of the STC-MCS500POE (firmware 20.50) did not work with Aravis 0.8.30** on the CORE:
  `TriggerSoftware`, writing its register directly and `SoftwareSignalPulse` (TriggerSource SoftwareSignal0) all
  gave no frame, with TriggerSelector FrameStart, TriggerMode On, TriggerSource Software, acquisition running,
  burst count 1 - while free run and SingleFrame stream fine. Hence `camera.acquisition` = `singleframe`.
* No jumbo frames so far: MTU 1500 on the test devices. Aravis negotiates packets of 1520 B (X3) and 1628 B (X7).
* **One controlling application per camera** (GigE Vision control channel + heartbeat): of two devices with this
  app and the same `camera.address`, only one gets the camera; the other's camera error names the holder's address and it keeps trying. The app holds the
  camera while it runs (also without reads), and gives it free when it stops or `camera.source` changes.
* Opening the camera resets the region and binning that other software left, without writing to its user sets.
* **If the camera goes away** (cable, power; two pulls of about 10 s on the X7): the app reconnects by itself and sets the region, exposure and format again, 22-26 s in that test. Reads in between end with
  an error, and one can block for more than 10 s. `camera/state` can still say `connected` for several seconds, so a PLC should check `result/ok` and `camera/error`.
* **Configurations:** the settings are saved and loaded with ctrlX configurations (tested on the X3). Loading one without a folder for this app, saved before the app was installed, resets it to defaults, including
  `camera.source` = `gige`.
* **ctrlX COREvirtual with port forwarding** (QEMU user networking, address 10.0.2.15): commands reach the camera,
  but the camera can't send the image stream back to 10.0.2.15 - every read ends with "no image within … ms
  - this device has the NAT address …". Use `camera.source` = `simulated` there (or a COREvirtual network
  adapter in the camera's network).

## Test setup

* **COREvirtual and local runs**: `camera.source` = `simulated`. Aravis' fake camera delivers the frames
  (trigger, stream and buffers run through Aravis), the content is a generated scene with
  `simulation.codes` random codes (QR, Data Matrix, Code 128, EAN-13, PDF417, Aztec, slightly tilted, noise,
  BayerRG8 mosaic) - or the images in the app data folder `test-images/` (PNG/JPEG/BMP, in name order; put them
  there with "Manage app data"). Each simulated result carries the codes it should find (`simulation.expected`).
* **Real camera**: the ctrlX CORE in the camera's network.

## Local development

OpenCV, numpy, zxing-cpp and Aravis are not installed in the build VM. Put them into `.dev/` (git-ignored, no
root needed), PyGObject is the VM's `python3-gi`:

```bash
python3 -m pip install --no-deps --only-binary=:all: --target .dev/pylib -r wheels/requirements.txt --require-hashes
mkdir -p .dev/debs && (cd .dev/debs && apt-get download libaravis-0.8-0 gir1.2-aravis-0.8)
for f in .dev/debs/*.deb; do dpkg-deb -x "$f" .dev/aravis; done
```

Then `./run-local.sh` (it evaluates `./dev-env.sh`), `../../tools/check.sh .` (tests/__init__.py finds `.dev/`
itself) and `eval "$(./dev-env.sh)"; python3 ../../tools/ui_check.py . --narrow`. Started with the defaults, the
local app opens the camera at 192.168.1.99 (control works from the VM, images don't - NAT); the UI check
switches to the simulated camera.

## Licenses (FOSS)

All components and their license texts: `configs/package-assets/ctrlx-barcode-reader.fossinfo.json` (shown by
ctrlX OS with the app). LGPL-2.1+ and GPL-3.0 (with GCC Runtime Library Exception) components are linked
dynamically and can be replaced; their sources are available through the **written offer**
`configs/package-assets/ctrlx-barcode-reader.foss-offer.txt` (user's choice; contact: https://github.com/bdkautzman).

| Component | Version | License | How it gets into the snap |
|---|---|---|---|
| Aravis (`libaravis-0.8-0`, `gir1.2-aravis-0.8`) | 0.8.30 | LGPL-2.1-or-later | Ubuntu package |
| PyGObject (`python3-gi`) + GObject Introspection, GLib typelibs | 3.48.2 / 1.80.1 / 2.80 | LGPL-2.1-or-later | Ubuntu packages |
| libxml2 + ICU, libusb-1.0 (Aravis dependencies) | Ubuntu 24.04 | MIT, Unicode-3.0, LGPL-2.1-or-later | Ubuntu packages |
| opencv-python-headless | 4.14.0.94 | Apache-2.0 (OpenCV), MIT (packaging) + bundled: FFmpeg (LGPL-2.1+), OpenSSL 1.1.1k, OpenBLAS, libgfortran (GPL-3.0+ WITH GCC-exception-3.1), libquadmath (LGPL-2.1+), libvpx, libaom, dav1d, libavif, libpng, libdrm, libjpeg-turbo, libwebp, libtiff, OpenJPEG, OpenEXR, protobuf, Intel IPP (amd64) / KleidiCV + carotene (arm64), … | PyPI wheel (part `wheels`) |
| numpy | 2.3.5 | BSD-3-Clause + bundled OpenBLAS, LAPACK, libgfortran, libquadmath | PyPI wheel (2.4+ needs x86-64-v2: not on the COREvirtual) |
| zxing-cpp | 3.1.1 | Apache-2.0 (+ nanobind, BSD-3-Clause) | PyPI wheel |
| ctrlx-datalayer, ctrlx-fbs, flatbuffers | 3.5.0 / 2.5.1 / 24.3.25 | MIT, Apache-2.0 | as in every app of this kit |

The OpenCV wheel bundles OpenSSL 1.1.1k (end of life); only FFmpeg's network protocols use it, which this app
never calls (it reads no videos or streams with OpenCV).

## Known limitations (0.2.0)

Details, evidence and open questions: [KNOWN-ISSUES.md](KNOWN-ISSUES.md); plans: [ROADMAP.md](ROADMAP.md). In short: the STC-MCS500POE's software
trigger gives no frame (SingleFrame per read instead), the X3 needs 40 µs packet pacing, the COREvirtual with port
forwarding gets no images, one camera and 8-bit pixel formats only, no hardware trigger, RAM ~100 MB, above the 75 MB guideline (accepted by the user; idle is already ~76 MB with OpenCV and zxing-cpp).

## Files

`src/app/`: `camera.py` (Aravis), `imaging.py` (OpenCV), `decoder.py` (zxing-cpp), `simulator.py`, `reader.py`
(camera thread, queue, modes, history, Data Layer publishing), `storage.py` (medium), `datalayer_nodes.py`,
`web_server.py`, `settings_rules.py`. `wheels/requirements.txt`: the hash-pinned binary wheels.
`www/`: the page. `tests/`: unit tests (fake camera, fake Data Layer) and `ui_scenario.py`.
