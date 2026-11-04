"""GigE Vision camera access with Aravis 0.8 (LGPL-2.1+) through PyGObject (gi, LGPL-2.1+).

Only standard GenICam (SFNC) features are used, so other GigE Vision cameras should work like the
Omron Sentech STC-MCS500POE this was written for. All Aravis calls of one camera happen in ONE thread
(the reader's camera thread); Aravis itself runs a stream thread and a heartbeat thread.

Acquisition (camera.acquisition), every read is a fresh exposure:
  singleframe (default): AcquisitionMode=SingleFrame, grab() runs AcquisitionStart -> one frame -> AcquisitionStop.
    Plain GenICam. The STC-MCS500POE (firmware 20.50) ignored every software trigger with Aravis 0.8.30
    (TriggerSoftware, its register, SoftwareSignalPulse - gotchas #100), SingleFrame works.
  trigger: the camera runs continuously in software-trigger mode (TriggerSelector=FrameStart, TriggerMode=On,
    TriggerSource=Software), grab() drops stale frames and sends TriggerSoftware - saves the start/stop
    round trips for cameras whose software trigger works. Without one it falls back to singleframe.

Network (validation/portlist-description.json): GVCP commands go to UDP port 3956 of the camera, the
replies and the GVSP image stream come back to local ports the OS chooses. A stream port reserved by
ctrlX OS or other apps (docs/ctrlx/reserved-interfaces-and-ports.md) is not kept: the stream is opened
again. Packet sockets are switched off (a strict snap may not open them; Aravis falls back to UDP anyway).

camera.source = simulated: Aravis' fake camera ("Fake_1") delivers the frames; their content is replaced
by app.simulator (test codes / test images), see there.

In the snap, PyGObject (python3-gi), the Aravis typelib and their libraries are Ubuntu packages staged
into $SNAP/usr (snap/snapcraft.yaml); load_aravis() puts them on the search paths.
"""

import glob
import logging
import os
import sys
import threading
import time

from app import config, imaging

log = logging.getLogger("camera")

GVCP_PORT = 3956
RESERVED_PORTS = {22, 80, 81, 443, 1338, 1880, 1900, 2069, 2070, 4840, 5353, 5355, 5858, 6000, 7878, 8069,
                  11740, 11741, 1881, 1884, 1885, 2883, 4222, 6123, 7505, 8000, 8080, 8088, 8142, 8840, 8883,
                  9230, 9240, 10123, 16620, 16700, 16701, 16800, 16810, 18500, 47808, 48898, 51218, 56090, 58000}
RESERVED_RANGES = ((49250, 50250),)
STREAM_PORT_TRIES = 8
FAKE_ID = "Fake_1"

_aravis = None
_aravis_lock = threading.Lock()


class CameraError(Exception):
    pass


def port_reserved(port):
    return port in RESERVED_PORTS or any(lo <= port <= hi for lo, hi in RESERVED_RANGES)


def load_aravis():
    """Import Aravis through gi once; raises CameraError with the reason if that is not possible."""
    global _aravis
    with _aravis_lock:
        if _aravis is not None:
            return _aravis
        if config.IN_SNAP:
            dist = os.path.join(config.SNAP_DIR, "usr", "lib", "python3", "dist-packages")
            if dist not in sys.path:
                sys.path.append(dist)
            typelibs = glob.glob(os.path.join(config.SNAP_DIR, "usr", "lib", "*-linux-gnu", "girepository-1.0"))
            if typelibs:
                os.environ["GI_TYPELIB_PATH"] = os.pathsep.join(typelibs)
        try:
            import gi
            gi.require_version("Aravis", "0.8")
            from gi.repository import Aravis
        except Exception as exc:  # ImportError, ValueError (typelib missing), OSError (library)
            raise CameraError("Aravis is not available: {}".format(exc))
        # no USB3 Vision: the snap has no raw-usb plug, libusb would only log AppArmor denials
        try:
            Aravis.disable_interface("USB3Vision")
        except Exception:  # noqa: BLE001 - older Aravis without this interface name
            pass
        _aravis = Aravis
        return Aravis


def library_versions():
    try:
        load_aravis()
        import gi
    except Exception as exc:  # noqa: BLE001 - only for the status page
        return {"aravis": "unavailable: {}".format(exc)}
    # the 0.8 typelib has no version function: take it from the library's file name
    dirs = os.environ.get("LD_LIBRARY_PATH", "").split(os.pathsep) + glob.glob("/usr/lib/*-linux-gnu")
    version = "0.8"
    for d in filter(None, dirs):
        names = glob.glob(os.path.join(d, "libaravis-0.8.so.0.*"))
        if names:
            version = os.path.basename(names[0]).split(".so.", 1)[1]
            break
    return {"aravis": version, "pygobject": gi.__version__}


def discover(simulated=False):
    """Cameras Aravis finds (GigE Vision broadcast on all interfaces; + the fake camera when simulated)."""
    ar = load_aravis()
    if simulated:
        ar.enable_interface("Fake")
    else:
        ar.disable_interface("Fake")
    ar.update_device_list()
    found = []
    for i in range(ar.get_n_devices()):
        found.append({
            "id": ar.get_device_id(i),
            "address": ar.get_device_address(i) or "",
            "protocol": ar.get_device_protocol(i) or "",
            "vendor": ar.get_device_vendor(i) or "",
            "model": ar.get_device_model(i) or "",
            "serial": ar.get_device_serial_nbr(i) or "",
        })
    return found


def _err(exc):
    return getattr(exc, "message", None) or str(exc)


def _ip(addr):
    """Gio InetSocketAddress ("192.168.1.99:3956") or InetAddress -> "192.168.1.99"."""
    return addr.get_address().to_string() if hasattr(addr, "get_address") else addr.to_string()


class Camera:
    """One opened camera. Not thread-safe: use it from one thread only."""

    def __init__(self, cam_settings, simulator=None, sim_settings=None):
        self.settings = dict(cam_settings)
        self.simulated = cam_settings["source"] == "simulated"
        self.simulator = simulator
        self.sim_settings = sim_settings or {}
        self.cam = self.dev = self.stream = None
        self.software_trigger = False
        self.single_frame = True
        self.warnings = []
        self.info = {}
        self.last_expected = []          # simulated: the codes in the last generated scene
        self.control_lost = threading.Event()
        self._signal_id = None
        self._payload = 0

    # -- open / close -------------------------------------------------------------------------
    def open(self):
        ar = load_aravis()
        s = self.settings
        if self.simulated:
            ar.enable_interface("Fake")
            device_id = FAKE_ID
        else:
            ar.disable_interface("Fake")
            device_id = s["address"] or None
        try:
            self.cam = ar.Camera.new(device_id)
        except Exception as exc:
            raise CameraError("cannot open camera {}: {}".format(device_id or "(first found)", _err(exc)))
        self.dev = self.cam.get_device()
        try:
            self._signal_id = self.dev.connect("control-lost", self._on_control_lost)
        except Exception:  # noqa: BLE001 - devices without the signal
            self._signal_id = None
        self.warnings = []
        try:
            self._try("stop a running acquisition", self.cam.stop_acquisition)
            if s["pixelFormat"] and not self.simulated:
                self._try("set pixel format " + s["pixelFormat"], self.cam.set_pixel_format_from_string, s["pixelFormat"])
            if self.cam.is_gv_device():
                self._setup_network()
            self.software_trigger = s["acquisition"] == "trigger" and bool(self.cam.is_software_trigger_supported())
            self.single_frame = not self.software_trigger
            if self.software_trigger:
                self.cam.set_acquisition_mode(ar.AcquisitionMode.CONTINUOUS)
                self.cam.set_trigger("Software")
            else:
                self._try("switch triggers off", self.cam.clear_triggers)
                self.cam.set_acquisition_mode(ar.AcquisitionMode.SINGLE_FRAME)
            self.apply_exposure(s)
            self._payload = self.cam.get_payload()
            self._open_stream(ar)
            if self.software_trigger:
                self.cam.start_acquisition()
        except CameraError:
            self.close()
            raise
        except Exception as exc:
            self.close()
            raise CameraError("camera setup failed: {}".format(_err(exc)))
        self.info = self._read_info()
        for w in self.warnings:
            log.warning("Camera: %s", w)
        log.info("Camera open: %s %s at %s, %s %dx%d, %s, stream port %s", self.info.get("vendor"),
                 self.info.get("model"), self.info.get("address"), self.info.get("pixelFormat"),
                 self.info.get("width", 0), self.info.get("height", 0),
                 "software trigger" if self.software_trigger else "single frame per read", self.info.get("streamPort"))
        return self.info

    def close(self):
        if self.cam is not None:
            try:
                self.cam.stop_acquisition()
            except Exception:  # noqa: BLE001 - camera may be gone
                pass
        if self.dev is not None and self._signal_id is not None:
            try:
                self.dev.disconnect(self._signal_id)
            except Exception:  # noqa: BLE001
                pass
        self.stream = None
        self.dev = None
        self.cam = None       # releases the GVCP control channel (and the heartbeat)
        self._signal_id = None

    def _on_control_lost(self, *_args):
        log.warning("Camera control lost")
        self.control_lost.set()

    def _try(self, what, fn, *args):
        try:
            return fn(*args)
        except Exception as exc:
            self.warnings.append("cannot {}: {}".format(what, _err(exc)))
            return None

    def _setup_network(self):
        s = self.settings
        if s["packetSize"] > 0:
            self._try("set packet size", self.cam.gv_set_packet_size, s["packetSize"])
        else:
            # test packets of growing size; fails behind NAT - then the camera's value stays
            self._try("negotiate the packet size", self.cam.gv_auto_packet_size)
        if s["packetDelayUs"] >= 0:
            # Aravis converts ns to the camera's GevSCPD ticks (GevTimestampTickFrequency)
            self._try("set the packet delay", self.cam.gv_set_packet_delay, int(s["packetDelayUs"] * 1000))

    def _open_stream(self, ar):
        if self.cam.is_gv_device():
            self._try("disable packet sockets", self.dev.set_stream_options, ar.GvStreamOption.PACKET_SOCKET_DISABLED)
        rejected = []          # kept open until a good port is found, so the OS doesn't hand them out again
        for _ in range(STREAM_PORT_TRIES):
            stream = self.cam.create_stream(None, None)
            if stream is None:
                raise CameraError("cannot create the image stream")
            port = stream.get_port() if isinstance(stream, ar.GvStream) else 0
            if not port_reserved(port):
                break
            log.info("Stream port %d is reserved on ctrlX - opening another one", port)
            rejected.append(stream)
        else:
            raise CameraError("no free stream port outside the reserved ports after {} tries".format(STREAM_PORT_TRIES))
        rejected.clear()
        self.stream = stream
        self.stream_port = port
        for _ in range(max(2, int(self.settings["buffers"]))):
            stream.push_buffer(ar.Buffer.new_allocate(self._payload))

    def apply_exposure(self, s):
        """Exposure and gain (also on an open camera, without restarting the stream)."""
        ar = load_aravis()
        auto = {"Off": ar.Auto.OFF, "Once": ar.Auto.ONCE, "Continuous": ar.Auto.CONTINUOUS}
        self.settings.update({k: s[k] for k in ("exposureUs", "exposureAuto", "gain", "gainAuto")})
        if self.cam.is_exposure_auto_available():
            self._try("set auto exposure", self.cam.set_exposure_time_auto, auto[s["exposureAuto"]])
        if s["exposureAuto"] == "Off" and self.cam.is_exposure_time_available():
            self._try("set the exposure time", self.cam.set_exposure_time, float(s["exposureUs"]))
        if self.cam.is_gain_auto_available():
            self._try("set auto gain", self.cam.set_gain_auto, auto[s["gainAuto"]])
        if s["gainAuto"] == "Off" and self.cam.is_gain_available():
            self._try("set the gain", self.cam.set_gain, float(s["gain"]))
        if self.info:
            self.info.update(self._exposure_info())

    # -- images -------------------------------------------------------------------------------
    def grab(self, timeout_ms):
        """Trigger (if possible) and wait for the next complete image -> imaging.Frame."""
        ar = load_aravis()
        if self.stream is None:
            raise CameraError("camera is not open")
        while True:                           # stale frames (free run, late frames of a timeout)
            old = self.stream.try_pop_buffer()
            if old is None:
                break
            self.stream.push_buffer(old)
        t0 = time.monotonic()
        try:
            if self.single_frame:
                self.cam.start_acquisition()
            else:
                self.cam.software_trigger()
        except Exception as exc:
            raise CameraError("{} failed: {}".format("acquisition start" if self.single_frame else "software trigger", _err(exc)))
        try:
            buf = self.stream.timeout_pop_buffer(int(timeout_ms) * 1000)
        finally:
            if self.single_frame:
                self._try("stop the acquisition", self.cam.stop_acquisition)
        grab_ms = (time.monotonic() - t0) * 1000.0
        if buf is None:
            raise CameraError("no image within {} ms{}".format(int(timeout_ms), self._no_image_hint()))
        try:
            status = buf.get_status()
            if status != ar.BufferStatus.SUCCESS:
                raise CameraError("image incomplete: {}".format(status.value_nick))
            frame_id = buf.get_frame_id()
            if self.simulated and self.simulator is not None:
                sim = self.sim_settings
                scene = self.simulator.next_scene(sim["width"], sim["height"], sim["codes"], frame_id)
                self.last_expected = scene.expected
                frame = scene.frame
            else:
                frame = imaging.frame_from_buffer(buf.get_data(), buf.get_image_width(), buf.get_image_height(),
                                                  buf.get_image_pixel_format(), frame_id=frame_id)
            frame.timestamp = time.time()
            frame.grab_ms = grab_ms
            return frame
        except ValueError as exc:
            raise CameraError(str(exc))
        finally:
            self.stream.push_buffer(buf)

    def _no_image_hint(self):
        try:
            iface = _ip(self.dev.get_interface_address()) if self.cam.is_gv_device() else ""
        except Exception:  # noqa: BLE001
            iface = ""
        if iface.startswith("10.0.2."):
            return (" - this device has the NAT address {}: the camera can't send the image stream to it "
                    "(ctrlX COREvirtual with port forwarding?)".format(iface))
        return ""

    # -- info ---------------------------------------------------------------------------------
    def _exposure_info(self):
        out = {}
        for key, fn in (("exposureUs", self.cam.get_exposure_time), ("gain", self.cam.get_gain)):
            try:
                out[key] = round(float(fn()), 3)
            except Exception:  # noqa: BLE001
                out[key] = None
        return out

    def _read_info(self):
        info = {"simulated": self.simulated, "softwareTrigger": self.software_trigger,
                "acquisition": "software trigger" if self.software_trigger else "single frame per read",
                "streamPort": getattr(self, "stream_port", 0), "warnings": list(self.warnings)}
        for key, fn in (("vendor", self.cam.get_vendor_name), ("model", self.cam.get_model_name),
                        ("serial", self.cam.get_device_serial_number), ("pixelFormat", self.cam.get_pixel_format_as_string)):
            try:
                info[key] = fn() or ""
            except Exception:  # noqa: BLE001
                info[key] = ""
        try:
            region = self.cam.get_region()
            info["width"], info["height"] = int(region.width), int(region.height)
        except Exception:  # noqa: BLE001
            pass
        try:
            info["firmware"] = self.dev.get_string_feature_value("DeviceFirmwareVersion")
        except Exception:  # noqa: BLE001
            info["firmware"] = ""
        if self.cam.is_gv_device():
            try:
                info["address"] = _ip(self.dev.get_device_address())
                info["interface"] = _ip(self.dev.get_interface_address())
                info["packetSize"] = self.cam.gv_get_packet_size()
            except Exception:  # noqa: BLE001
                pass
            try:
                info["packetDelayUs"] = round(self.cam.gv_get_packet_delay() / 1000.0, 1)
            except Exception:  # noqa: BLE001
                pass
        else:
            info["address"] = FAKE_ID if self.simulated else ""
        if self.simulated:      # the images are the simulator's, not the fake camera's 512 x 512 Mono8
            info.update(width=self.sim_settings.get("width", 0), height=self.sim_settings.get("height", 0),
                        pixelFormat="BayerRG8 (generated) or test images")
        info.update(self._exposure_info())
        return info

    def stats(self):
        ar = load_aravis()
        out = {}
        if self.stream is None:
            return out
        try:
            completed, failures, underruns = ar.Stream.get_statistics(self.stream)
            out.update(completed=int(completed), failures=int(failures), underruns=int(underruns))
        except Exception:  # noqa: BLE001
            pass
        if isinstance(self.stream, ar.GvStream):
            try:
                resent, missing = ar.GvStream.get_statistics(self.stream)
                out.update(resentPackets=int(resent), missingPackets=int(missing))
            except Exception:  # noqa: BLE001
                pass
        return out
