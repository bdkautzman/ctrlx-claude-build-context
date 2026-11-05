"""The reader: one camera thread that captures, decodes and publishes results.

Work, in this order of priority:
  1. read requests (UI button, Data Layer trigger / request-id) - a queue of at most MAX_QUEUE
  2. continuous reading (continuous.intervalMs between the starts of two reads) while switched on
  3. live preview frames (live.intervalMs, not decoded) while a page asked for one in the last LIVE_HOLD_S
A read: grab (software trigger) -> grayscale -> decode region -> zxing-cpp -> Data Layer nodes ->
display image + thumbnail into the RAM history -> optional copy to a removable medium (storage.py).

Data Layer order for a PLC: all result/* values, then result/request-id, then result/sequence, then
trigger = false (if the read came from it), then busy. result/json holds the whole result in one node.

A read while the camera is down tries to open it once; if that fails the read ends with an error result,
so every PLC request gets an answer. The camera is reopened after settings changes that need it,
after "control lost" and after MAX_CAMERA_ERRORS failed reads in a row.
"""

import collections
import datetime
import json
import logging
import threading
import time

from app import camera as camera_mod
from app import imaging
from app.camera import Camera, CameraError
from app.datalayer_nodes import ReaderBusyError
from app.decoder import Decoder

log = logging.getLogger("reader")

MAX_QUEUE = 4
LIVE_HOLD_S = 5.0
RECONNECT_S = (1, 2, 5, 10, 20, 30)
MAX_CAMERA_ERRORS = 3
THUMB_WIDTH = 192
REOPEN_KEYS = ("source", "address", "pixelFormat", "acquisition", "packetSize", "packetDelayUs", "buffers")
EXPOSURE_KEYS = ("exposureUs", "exposureAuto", "gain", "gainAuto")


class Request:
    def __init__(self, source, request_id=0):
        self.source = source
        self.request_id = request_id
        self.created = time.monotonic()
        self.done = threading.Event()
        self.result = None


def _camera_region(s):
    """The region the camera should send (x, y, w, h), or None for the full frame."""
    roi = s["decode"]["roi"]
    return (roi["x"], roi["y"], roi["width"], roi["height"]) if roi["enabled"] and roi["onCamera"] else None


def _iso(epoch):
    return datetime.datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="milliseconds")


class Reader:
    def __init__(self, store, registry=None, storage=None, simulator=None, camera_factory=Camera):
        self.store = store
        self.registry = registry
        self.storage = storage
        self.simulator = simulator
        self.camera_factory = camera_factory
        s = store.get()
        self._settings = s
        self.decoder = Decoder(s["decode"])
        self._cond = threading.Condition()
        self._queue = collections.deque()
        self._running = None              # the Request being processed
        self._calls = []                  # [(fn, holder)] to run in the camera thread
        self._continuous = False
        self._live_until = 0.0
        self._live = None                 # {"seq", "jpeg", "epoch", "width", "height"}
        self._history = collections.deque(maxlen=s["images"]["historySize"])
        self._last_frame = None           # (sequence, Frame) of the newest result, for the PNG download
        self._sequence = 0
        self._camera = None
        self._cam = {"state": "disconnected", "error": None, "info": {}, "stats": {}, "frames": 0, "failures": 0}
        self._reopen = False
        self._exposure_dirty = False
        self._decode_dirty = False
        self._errors_in_row = 0
        self._stop = threading.Event()
        self._thread = None

    # -- lifecycle ----------------------------------------------------------------------------
    def start(self):
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)
        self._thread.start()

    def stop(self, timeout=10.0):
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread:
            self._thread.join(timeout)

    # -- commands (any thread) ----------------------------------------------------------------
    def can_queue(self):
        with self._cond:
            return len(self._queue) < MAX_QUEUE

    def request_read(self, source, request_id=0):
        """Queue a read -> Request, or None for a trigger while another trigger read is pending."""
        with self._cond:
            if source == "trigger" and any(r.source == "trigger" for r in self._pending()):
                return None
            if len(self._queue) >= MAX_QUEUE:
                raise ReaderBusyError("busy: {} reads are waiting".format(len(self._queue)))
            req = Request(source, request_id)
            self._queue.append(req)
            self._cond.notify_all()
        self._set_node("busy", True)
        return req

    def set_continuous(self, enabled):
        with self._cond:
            self._continuous = bool(enabled)
            self._cond.notify_all()
        self._set_node("continuous", bool(enabled))
        log.info("Continuous reading %s", "started" if enabled else "stopped")

    def touch_live(self):
        """A page wants live images: keep the preview running for LIVE_HOLD_S -> newest live image."""
        with self._cond:
            self._live_until = time.monotonic() + LIVE_HOLD_S
            self._cond.notify_all()
            return self._live

    def call(self, fn, timeout=30.0):
        """Run fn(camera_or_None) in the camera thread and return its result (discovery, reconnect)."""
        holder = {"done": threading.Event()}
        with self._cond:
            self._calls.append((fn, holder))
            self._cond.notify_all()
        if not holder["done"].wait(timeout):
            raise TimeoutError("the camera thread did not answer in time")
        if "error" in holder:
            raise holder["error"]
        return holder.get("value")

    def discover(self):
        simulated = self._settings["camera"]["source"] == "simulated"
        return self.call(lambda _cam: camera_mod.discover(simulated))

    def reconnect(self):
        camera_mod.forget_raised_delay()
        with self._cond:
            self._reopen = True
            self._cond.notify_all()

    # -- settings ------------------------------------------------------------------------------
    def on_settings(self, s):
        old = self._settings
        with self._cond:
            self._settings = s
            if any(old["camera"][k] != s["camera"][k] for k in REOPEN_KEYS) or _camera_region(old) != _camera_region(s):
                camera_mod.forget_raised_delay()
                self._reopen = True
            elif any(old["camera"][k] != s["camera"][k] for k in EXPOSURE_KEYS):
                self._exposure_dirty = True
            if old["decode"] != s["decode"]:
                self._decode_dirty = True
            if s["images"]["historySize"] != self._history.maxlen:
                self._history = collections.deque(self._history, maxlen=s["images"]["historySize"])
            self._cond.notify_all()
        if self.storage is not None:
            self.storage.configure(s["storage"])

    # -- queries (any thread) -----------------------------------------------------------------
    def status(self):
        with self._cond:
            last = self._history[-1]["result"] if self._history else None
            out = {
                "camera": {k: (dict(v) if isinstance(v, dict) else v) for k, v in self._cam.items()},
                "source": self._settings["camera"]["source"],
                "busy": bool(self._pending()),
                "queued": len(self._queue),
                "continuous": self._continuous,
                "live": time.monotonic() < self._live_until,
                "sequence": self._sequence,
                "last": last,
            }
        out["storage"] = self.storage.status() if self.storage is not None else None
        return out

    def history(self):
        with self._cond:
            return [e["result"] for e in reversed(self._history)]

    def image(self, sequence, kind):
        """kind display|thumb -> JPEG bytes; full -> PNG of the newest result's frame; None if gone."""
        if kind == "full":
            with self._cond:
                last = self._last_frame
            if last is None or last[0] != sequence:
                return None
            return imaging.png(imaging.to_bgr(last[1]))
        with self._cond:
            for e in self._history:
                if e["result"]["sequence"] == sequence:
                    return e.get(kind)
        return None

    # -- camera thread ------------------------------------------------------------------------
    def _pending(self):
        return list(self._queue) + ([self._running] if self._running else [])

    def _warm_up(self):
        """Load numpy, OpenCV and zxing-cpp now: on a ctrlX CORE that takes ~3 s, the first read shouldn't."""
        try:
            imaging.cv()
            from app.decoder import zxing
            zxing()
        except Exception as exc:  # noqa: BLE001 - reads report it again
            log.error("Image processing libraries not usable: %s", exc)

    def _run(self):
        self._warm_up()
        next_connect = 0.0
        attempt = 0
        next_cont = next_live = 0.0
        while not self._stop.is_set():
            try:
                self._run_calls()
                if self._reopen or (self._camera is not None and self._camera.control_lost.is_set()):
                    self._close_camera("reopening")
                    self._reopen = False
                    next_connect, attempt = 0.0, 0
                now = time.monotonic()
                if self._camera is None and now >= next_connect:
                    if self._open_camera():
                        attempt = 0
                    else:
                        next_connect = time.monotonic() + RECONNECT_S[min(attempt, len(RECONNECT_S) - 1)]
                        attempt += 1
                self._apply_dirty()

                work = None
                with self._cond:
                    s = self._settings
                    now = time.monotonic()
                    if self._queue:
                        work = ("read", self._queue.popleft())
                        self._running = work[1]
                    elif self._camera is not None:
                        if self._continuous and now >= next_cont:
                            work = ("read", Request("continuous"))
                            self._running = work[1]
                            next_cont = now + s["continuous"]["intervalMs"] / 1000.0
                        elif now < self._live_until and now >= next_live and not self._continuous:
                            work = ("live", None)
                            next_live = now + s["live"]["intervalMs"] / 1000.0
                    if work is None:
                        waits = [1.0]
                        if self._camera is None:
                            waits.append(next_connect - now)
                        elif self._continuous:
                            waits.append(next_cont - now)
                        elif now < self._live_until:
                            waits.append(next_live - now)
                        if not self._calls and not self._reopen:
                            self._cond.wait(max(0.01, min(waits)))
                        continue
                if work[0] == "read":
                    self._do_read(work[1])
                else:
                    self._do_live()
            except Exception:  # noqa: BLE001 - the camera thread must survive anything
                log.exception("Camera thread error")
                self._stop.wait(1.0)
        self._close_camera("app stops")

    def _run_calls(self):
        with self._cond:
            calls, self._calls = self._calls, []
        for fn, holder in calls:
            try:
                holder["value"] = fn(self._camera)
            except Exception as exc:  # noqa: BLE001 - handed to the caller
                holder["error"] = exc
            holder["done"].set()

    def _apply_dirty(self):
        s = self._settings
        if self._decode_dirty:
            self._decode_dirty = False
            self.decoder.configure(s["decode"])
        if self._camera is not None:
            self._camera.sim_settings = s["simulation"]
            if self._exposure_dirty:
                self._exposure_dirty = False
                self._camera.apply_exposure(s["camera"])
                self._update_cam(info=dict(self._camera.info))

    def _update_cam(self, **kw):
        with self._cond:
            self._cam.update(kw)
        if self.registry is None:
            return
        for key, node in (("state", "camera/state"), ("error", "camera/error"),
                          ("frames", "camera/frames"), ("failures", "camera/failures")):
            if key in kw:
                self._set_node(node, kw[key] if kw[key] is not None else "")
        if "stats" in kw:
            stats = kw["stats"] or {}
            self._set_node("camera/resent-packets", stats.get("resentPackets", 0))
            self._set_node("camera/missing-packets", stats.get("missingPackets", 0))
        if "info" in kw:
            info = kw["info"] or {}
            self._set_node("camera/model", " ".join(x for x in (info.get("vendor"), info.get("model")) if x))
            self._set_node("camera/address", info.get("address") or "")

    def _open_camera(self):
        s = self._settings
        self._update_cam(state="connecting")
        cam = self.camera_factory(s["camera"], simulator=self.simulator, sim_settings=s["simulation"],
                                  region=_camera_region(s))
        try:
            info = cam.open()
        except CameraError as exc:
            log.warning("Camera not available: %s", exc)
            self._update_cam(state="error", error=str(exc), info={}, stats={})
            return False
        self._camera = cam
        self._errors_in_row = 0
        self._update_cam(state="connected", error=None, info=info, stats=cam.stats())
        return True

    def _close_camera(self, why):
        if self._camera is None:
            return
        log.info("Closing the camera (%s)", why)
        try:
            self._camera.close()
        finally:
            self._camera = None
            self._update_cam(state="disconnected", info={}, stats={})

    def _grab(self):
        s = self._settings
        if self._camera is None and not self._open_camera():
            raise CameraError("camera not connected: {}".format(self._cam.get("error") or "unknown error"))
        try:
            frame = self._camera.grab(s["camera"]["timeoutMs"])
        except CameraError as exc:
            self._errors_in_row += 1
            self._update_cam(failures=self._cam["failures"] + 1, error=str(exc), stats=self._camera.stats(),
                             info=dict(self._camera.info))
            if self._errors_in_row >= MAX_CAMERA_ERRORS:
                self._reopen = True
            raise
        self._errors_in_row = 0
        self._update_cam(frames=self._cam["frames"] + 1, error=None, stats=self._camera.stats())
        return frame

    def _do_live(self):
        s = self._settings
        try:
            frame = self._grab()
        except CameraError as exc:
            log.debug("Live image failed: %s", exc)
            return
        img = imaging.scaled(frame, s["live"]["width"])
        live = {"jpeg": imaging.jpeg(img, s["images"]["jpegQuality"]), "epoch": frame.timestamp,
                "width": frame.width, "height": frame.height, "origin": frame.origin}
        with self._cond:
            live["seq"] = (self._live["seq"] + 1) if self._live else 1
            self._live = live

    def _do_read(self, req):
        s = self._settings
        frame = None
        codes, error = [], None
        decode_ms = 0.0
        region = None
        expected = None
        try:
            frame = self._grab()
            if self._camera is not None and self._camera.simulated:
                expected = [{"format": f, "text": t} for f, t in self._camera.last_expected]
            gray = imaging.to_gray(frame)
            if frame.camera_region:          # the camera sent only the region: decode all of it, in sensor pixels
                region = frame.camera_region
                codes, decode_ms = self.decoder.decode(gray, frame.origin)
            else:
                region = imaging.clamp_roi(s["decode"]["roi"], frame.width, frame.height)
                codes, decode_ms = self.decoder.decode(imaging.crop(gray, region), region[:2] if region else (0, 0))
        except CameraError as exc:
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 - decoder/imaging problems end the read with an error
            log.exception("Read failed")
            error = "processing failed: {}".format(exc)
        epoch = frame.timestamp if frame is not None else time.time()
        with self._cond:
            self._sequence += 1
            seq = self._sequence
        result = {
            "sequence": seq,
            "requestId": req.request_id,
            "source": req.source,
            "ok": bool(codes) and error is None,
            "count": len(codes),
            "codes": codes,
            "durationMs": round((time.monotonic() - req.created) * 1000.0, 1),
            "grabMs": round(frame.grab_ms, 1) if frame is not None else None,
            "decodeMs": round(decode_ms, 1),
            "timestamp": _iso(epoch),
            "epoch": epoch,
            "error": error,
            "width": frame.width if frame is not None else 0,
            "height": frame.height if frame is not None else 0,
            "origin": list(frame.origin) if frame is not None else [0, 0],
            "pixelFormat": frame.pixel_format if frame is not None else "",
            "region": list(region) if region else None,
            "saved": False,
        }
        if expected is not None:
            result["simulation"] = {"expected": expected}
        with self._cond:
            # finished before trigger goes false: a new trigger = true right after must queue a new read
            self._running = None
            busy = bool(self._queue)
        self._publish(result, req, busy)
        entry = {"result": result}
        if frame is not None:
            try:
                display = imaging.scaled(frame, s["images"]["displayWidth"])
                entry["display"] = imaging.jpeg(display, s["images"]["jpegQuality"])
                entry["thumb"] = imaging.jpeg(imaging.shrink(display, THUMB_WIDTH), 70)
            except Exception:  # noqa: BLE001
                log.exception("Display image failed")
            if self.storage is not None:
                result["saved"] = self.storage.submit(frame, result)
        with self._cond:
            self._history.append(entry)
            if frame is not None:
                self._last_frame = (seq, frame)
        req.result = result
        req.done.set()
        log.log(logging.INFO if req.source != "continuous" else logging.DEBUG,
                "Read %d (%s%s): %s in %.0f ms", seq, req.source,
                " #{}".format(req.request_id) if req.request_id else "",
                error or ", ".join("{} {!r}".format(c["format"], c["text"]) for c in codes) or "no code",
                result["durationMs"])

    def _publish(self, result, req, busy):
        if self.registry is None:
            return
        codes = [{"format": c["format"], "text": c["text"], "corners": c["corners"]} for c in result["codes"]]
        first = codes[0] if codes else {"format": "", "text": ""}
        summary = {k: result[k] for k in ("sequence", "requestId", "source", "ok", "count", "durationMs", "grabMs",
                                          "decodeMs", "timestamp", "error")}
        summary["codes"] = codes
        for name, value in (("result/source", result["source"]), ("result/ok", result["ok"]),
                            ("result/count", result["count"]), ("result/text", first["text"]),
                            ("result/format", first["format"]),
                            ("result/codes", json.dumps(codes, ensure_ascii=False, separators=(",", ":"))),
                            ("result/duration-ms", float(result["durationMs"])),
                            ("result/grab-ms", float(result["grabMs"] or 0.0)),
                            ("result/decode-ms", float(result["decodeMs"])),
                            ("result/timestamp", result["timestamp"]), ("result/error", result["error"] or ""),
                            ("result/json", json.dumps(summary, ensure_ascii=False, separators=(",", ":"))),
                            ("result/request-id", result["requestId"]), ("result/sequence", result["sequence"])):
            self._set_node(name, value)
        if req.source == "trigger":
            self._set_node("trigger", False)
        self._set_node("busy", busy)

    def _set_node(self, name, value):
        if self.registry is not None:
            self.registry.set_internal(name, value)
