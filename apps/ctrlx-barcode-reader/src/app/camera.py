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

Packet delay (camera.packetDelayUs): the camera's pause between stream packets. ctrlX OS limits socket buffers and
a strict snap can't use packet sockets, so a device too slow to empty the socket loses packets. -2 = automatic:
6 us on amd64 (X5, X7), 40 us on arm64 (X2, X3), measured with 5 MP (KNOWN-ISSUES.md 22). Two images that fail with
lost packets close together raise it a step (one alone happens on an X3 at any delay); it stays raised when the camera
is reopened after errors, and starts over with Reconnect, a camera settings change or an app restart.

Camera region (decode.roi.onCamera): the camera sends only that part of the sensor. Frames carry their position, code
corners stay in sensor pixels. Opening also resets the region and binning other software may have left, and switches
AcquisitionMode to Continuous and back: after a region or binning write the STC-MCS500POE otherwise needs 1 s for each
AcquisitionStart in SingleFrame mode (KNOWN-ISSUES.md 20).

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
import math
import os
import platform
import re
import socket
import struct
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
PACKET_DELAY_AUTO = -2.0
AUTO_DELAY_AMD64_US = 6.0
AUTO_DELAY_OTHER_US = 40.0
AUTO_DELAY_STEPS_US = (6.0, 12.0, 24.0, 40.0, 60.0, 100.0)
AUTO_DELAY_WINDOW = 20         # a step up takes a second failed image within this many images
_IP_ADDRESS = re.compile(r"\d+\.\d+\.\d+\.\d+")

_raised_delay_us = {}          # camera address -> automatic packet delay raised after failed images
_aravis = None
_aravis_lock = threading.Lock()


class CameraError(Exception):
    pass


def port_reserved(port):
    return port in RESERVED_PORTS or any(lo <= port <= hi for lo, hi in RESERVED_RANGES)


def auto_packet_delay_us(machine=None):
    """Start value of the automatic packet delay on this device."""
    return AUTO_DELAY_AMD64_US if (machine or platform.machine()) in ("x86_64", "amd64", "AMD64") else AUTO_DELAY_OTHER_US


def next_delay_us(current):
    """The next step of the automatic packet delay; the same value at the end of the steps."""
    return next((step for step in AUTO_DELAY_STEPS_US if step > current), current)


def forget_raised_delay():
    """The automatic packet delay starts over at its start value."""
    _raised_delay_us.clear()


def align_region(region, width_bounds, height_bounds, increments):
    """Region (x, y, w, h) -> the smallest region containing it that the camera accepts. increments: x offset, y offset,
    width, height. Offsets stay even, so a Bayer image keeps its color order."""
    def axis(start, size, bounds, start_step, size_step):
        low, high = int(bounds[0]), int(bounds[1])
        start_step = math.lcm(max(1, int(start_step)), 2)
        size_step = max(1, int(size_step))
        start = min(max(0, int(start)), high - 1)
        first = start // start_step * start_step
        end = min(start + int(size), high)
        length = max(low, math.ceil((end - first) / size_step) * size_step)
        if first + length > high:
            first = max(0, (high - length) // start_step * start_step)
        return first, min(length, high - first)

    x, y, w, h = region
    x0, width = axis(x, w, width_bounds, increments[0], increments[2])
    y0, height = axis(y, h, height_bounds, increments[1], increments[3])
    return x0, y0, width, height


def control_holder(address, port=GVCP_PORT, timeout=0.5):
    """IP address of the application controlling a GigE Vision camera, or None (nobody, or no answer). GVCP READREG of
    the control channel privilege and the primary application's address needs no control of the camera."""
    registers = (0x0A00, 0x0A14)
    body = b"".join(struct.pack(">I", r) for r in registers)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.sendto(struct.pack(">BBHHH", 0x42, 0x01, 0x0080, len(body), 1) + body, (address, port))
            answer = sock.recv(64)
    except OSError:
        return None
    if len(answer) < 16 or answer[:2] != b"\x00\x00":
        return None
    privilege, holder = struct.unpack(">2I", answer[8:16])
    return socket.inet_ntoa(struct.pack(">I", holder)) if privilege & 3 and holder else None


def _holder_hint(address):
    holder = control_holder(address) if address and _IP_ADDRESS.fullmatch(address) else None
    if not holder:
        return ""
    return (" - controlled by the application at {} (a GigE Vision camera takes one controlling application: stop "
            "that one or set its camera source to simulated)".format(holder))


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
    found = []
    for _ in range(2):         # the first search came back empty once with a camera in the network (KNOWN-ISSUES.md 6)
        ar.update_device_list()
        for i in range(ar.get_n_devices()):
            found.append({
                "id": ar.get_device_id(i),
                "address": ar.get_device_address(i) or "",
                "protocol": ar.get_device_protocol(i) or "",
                "vendor": ar.get_device_vendor(i) or "",
                "model": ar.get_device_model(i) or "",
                "serial": ar.get_device_serial_nbr(i) or "",
            })
        if found:
            break
    return found


def _err(exc):
    return getattr(exc, "message", None) or str(exc)


def _ip(addr):
    """Gio InetSocketAddress ("192.168.1.99:3956") or InetAddress -> "192.168.1.99"."""
    return addr.get_address().to_string() if hasattr(addr, "get_address") else addr.to_string()


class Camera:
    """One opened camera. Not thread-safe: use it from one thread only."""

    def __init__(self, cam_settings, simulator=None, sim_settings=None, region=None):
        self.settings = dict(cam_settings)
        self.requested_region = tuple(region) if region else None
        self.camera_region = None
        self.auto_delay = False
        self.packet_delay_us = None
        self._images_since_failure = None
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
            raise CameraError("cannot open camera {}: {}{}".format(device_id or "(first found)", _err(exc), _holder_hint(device_id)))
        self.dev = self.cam.get_device()
        try:
            self._signal_id = self.dev.connect("control-lost", self._on_control_lost)
        except Exception:  # noqa: BLE001 - devices without the signal
            self._signal_id = None
        self.warnings = []
        try:
            if self.cam.is_gv_device() and not self.dev.is_controller():
                raise CameraError("no control of camera {}{}".format(device_id or "(first found)", _holder_hint(device_id)))
            self._try("stop a running acquisition", self.cam.stop_acquisition)
            if s["pixelFormat"] and not self.simulated:
                self._try("set pixel format " + s["pixelFormat"], self.cam.set_pixel_format_from_string, s["pixelFormat"])
            if self.cam.is_gv_device():
                self._setup_network()
            if not self.simulated:
                self._setup_region()
            self.software_trigger = s["acquisition"] == "trigger" and bool(self.cam.is_software_trigger_supported())
            self.single_frame = not self.software_trigger
            if self.software_trigger:
                self.cam.set_acquisition_mode(ar.AcquisitionMode.CONTINUOUS)
                self.cam.set_trigger("Software")
            else:
                self._try("switch triggers off", self.cam.clear_triggers)
                self._try("reset the acquisition mode", self.cam.set_acquisition_mode, ar.AcquisitionMode.CONTINUOUS)
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
        self.auto_delay = s["packetDelayUs"] == PACKET_DELAY_AUTO
        delay = _raised_delay_us.get(s["address"], auto_packet_delay_us()) if self.auto_delay else s["packetDelayUs"]
        if delay >= 0:
            self.packet_delay_us = delay
            # Aravis converts ns to the camera's GevSCPD ticks (GevTimestampTickFrequency)
            self._try("set the packet delay", self.cam.gv_set_packet_delay, int(delay * 1000))

    def _image_failed(self):
        """An image lost packets: the second one close together raises the automatic packet delay."""
        if self._images_since_failure is not None and self._images_since_failure < AUTO_DELAY_WINDOW:
            self._raise_packet_delay()
            self._images_since_failure = None
        else:
            self._images_since_failure = 0

    def _raise_packet_delay(self):
        """Automatic packet delay: the next step."""
        raised = next_delay_us(self.packet_delay_us) if self.auto_delay else None
        if raised is None or raised == self.packet_delay_us:
            return
        for attempt in range(5):         # the camera can answer "busy" right after an acquisition
            try:
                self.cam.gv_set_packet_delay(int(raised * 1000))
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 4:
                    log.warning("Cannot raise the packet delay: %s", _err(exc))
                    return
                time.sleep(0.1)
        log.warning("An image lost packets: packet delay raised from %g to %g us", self.packet_delay_us, raised)
        self.packet_delay_us = raised
        _raised_delay_us[self.settings["address"]] = raised
        self.info["packetDelayUs"] = raised

    def _setup_region(self):
        """No binning, and the full frame or the requested region."""
        try:
            if self.cam.is_binning_available() and tuple(self.cam.get_binning()) != (1, 1):
                self.cam.set_binning(1, 1)
            x, y, w, h = self.cam.get_region()
            if x or y:                   # an offset lowers the maximum width and height
                self.cam.set_region(0, 0, 0, 0)
            # WidthMax and HeightMax: get_width_bounds() answered a maximum of 0 with the STC-MCS500POE
            wmax = int(self.dev.get_integer_feature_value("WidthMax"))
            hmax = int(self.dev.get_integer_feature_value("HeightMax"))
            if self.requested_region:
                wanted = align_region(self.requested_region, (self.cam.get_width_bounds()[0], wmax),
                                      (self.cam.get_height_bounds()[0], hmax),
                                      (self.cam.get_x_offset_increment(), self.cam.get_y_offset_increment(),
                                       self.cam.get_width_increment(), self.cam.get_height_increment()))
            else:
                wanted = (0, 0, wmax, hmax)
            if tuple(self.cam.get_region()) != wanted:
                self.cam.set_region(*wanted)
            if self.requested_region:
                self.camera_region = tuple(int(v) for v in self.cam.get_region())
        except Exception as exc:  # noqa: BLE001 - the reader then crops the full frame itself
            self.camera_region = None
            self.warnings.append("cannot set the camera region: {}".format(_err(exc)))

    def _open_stream(self, ar):
        if self.cam.is_gv_device():
            self._try("disable packet sockets", self.dev.set_stream_options, ar.GvStreamOption.PACKET_SOCKET_DISABLED)
        stream = None
        for _ in range(STREAM_PORT_TRIES):
            # the old stream must be gone first: dropping one clears the camera's stream port, even if a newer stream
            # has set it since, and then no image arrives (KNOWN-ISSUES.md 25)
            stream = None
            stream = self.cam.create_stream(None, None)
            if stream is None:
                raise CameraError("cannot create the image stream")
            port = stream.get_port() if isinstance(stream, ar.GvStream) else 0
            if not port_reserved(port):
                break
            log.info("Stream port %d is reserved on ctrlX - opening another one", port)
        else:
            raise CameraError("no free stream port outside the reserved ports after {} tries".format(STREAM_PORT_TRIES))
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
        lost_before = self._lost_packets()
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
            if self._lost_packets() != lost_before:
                self._image_failed()
            raise CameraError("no image within {} ms{}".format(int(timeout_ms), self._no_image_hint()))
        try:
            status = buf.get_status()
            if status != ar.BufferStatus.SUCCESS:
                self._image_failed()
                raise CameraError("image incomplete: {}".format(status.value_nick))
            if self._images_since_failure is not None:
                self._images_since_failure += 1
            frame_id = buf.get_frame_id()
            if self.simulated and self.simulator is not None:
                sim = self.sim_settings
                scene = self.simulator.next_scene(sim["width"], sim["height"], sim["codes"], frame_id)
                self.last_expected = scene.expected
                frame = self._simulated_region(scene.frame)
            else:
                frame = imaging.frame_from_buffer(buf.get_data(), buf.get_image_width(), buf.get_image_height(),
                                                  buf.get_image_pixel_format(), frame_id=frame_id,
                                                  camera_region=self.camera_region)
            frame.timestamp = time.time()
            frame.grab_ms = grab_ms
            return frame
        except ValueError as exc:
            raise CameraError(str(exc))
        finally:
            self.stream.push_buffer(buf)

    def _simulated_region(self, frame):
        """The generated frame -> what a camera with this region would send."""
        if not self.requested_region:
            return frame
        region = align_region(self.requested_region, (16, frame.width), (16, frame.height), (2, 2, 2, 2))
        self.camera_region = region
        x, y, w, h = region
        return imaging.Frame(frame.data[y:y + h, x:x + w], frame.pixel_format, frame_id=frame.frame_id, camera_region=region)

    def _lost_packets(self):
        stats = self.stats()
        return stats.get("resentPackets", 0) + stats.get("missingPackets", 0)

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
                info["packetDelayAuto"] = self.auto_delay
            except Exception:  # noqa: BLE001
                pass
        else:
            info["address"] = FAKE_ID if self.simulated else ""
        if self.simulated:      # the images are the simulator's, not the fake camera's 512 x 512 Mono8
            info.update(width=self.sim_settings.get("width", 0), height=self.sim_settings.get("height", 0),
                        pixelFormat="BayerRG8 (generated) or test images")
        if self.camera_region:
            info["cameraRegion"] = list(self.camera_region)
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
