"""camera.py: reserved ports; with Aravis available, the real Camera class against Aravis' fake camera."""

import socket
import struct
import threading
import unittest
import weakref
from unittest import mock

from app import camera as camera_mod
from app.camera import (AUTO_DELAY_STEPS_US, RESERVED_PORTS, Camera, CameraError, align_region, auto_packet_delay_us,
                        control_holder, next_delay_us, port_reserved)
from tests.helpers import HAVE_IMAGING, aravis_available, load_defaults

HAVE_ARAVIS = HAVE_IMAGING and aravis_available()


class PortTest(unittest.TestCase):
    def test_reserved(self):
        self.assertTrue(port_reserved(49250))
        self.assertTrue(port_reserved(50000))
        self.assertTrue(port_reserved(50250))
        self.assertTrue(port_reserved(58000))
        self.assertFalse(port_reserved(50251))
        self.assertFalse(port_reserved(42065))
        self.assertIn(2069, RESERVED_PORTS)

    def test_reserved_list_matches_kit_data(self):
        import json
        import os
        path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "tools", "data", "reserved-interfaces.json")
        if not os.path.exists(path):
            self.skipTest("not inside the kit")
        with open(path, encoding="utf-8") as f:
            ports = json.load(f)["ports"]
        for group in ports.values():
            for p in group:
                if isinstance(p, list):
                    self.assertTrue(port_reserved(p[0]) and port_reserved(p[1]), p)
                else:
                    self.assertTrue(port_reserved(p), p)


class StubCam:
    """The part of Aravis.Camera the region and packet delay code uses, without Aravis."""

    def __init__(self, region=(0, 0, 2448, 2048), binning=(1, 1), busy=0):
        self.region = region
        self.binning = binning
        self.busy = busy
        self.delay_ns = None
        self.writes = []

    def is_binning_available(self):
        return True

    def get_binning(self):
        return self.binning

    def set_binning(self, x, y):
        self.writes.append(("binning", x, y))
        self.binning = (x, y)

    def get_region(self):
        return self.region

    def set_region(self, x, y, w, h):
        self.writes.append(("region", x, y, w, h))
        self.region = (x, y, w or self.region[2], h or self.region[3])

    def get_width_bounds(self):
        return (16, 0)                  # the STC-MCS500POE really answers a maximum of 0 here

    def get_height_bounds(self):
        return (2, 0)

    def get_x_offset_increment(self):
        return 8

    def get_y_offset_increment(self):
        return 2

    def get_width_increment(self):
        return 8

    def get_height_increment(self):
        return 2

    def gv_set_packet_size(self, size):
        pass

    def gv_set_packet_delay(self, ns):
        if self.busy:
            self.busy -= 1
            raise RuntimeError("write_memory error (busy)")
        self.delay_ns = ns


class StubDevice:
    FEATURES = {"WidthMax": 2448, "HeightMax": 2048}

    def get_integer_feature_value(self, name):
        return self.FEATURES[name]


def stub_camera(stub, region=None, delay=-2.0):
    s = dict(load_defaults()["camera"], packetDelayUs=delay, packetSize=1444)
    cam = Camera(s, region=region)
    cam.cam = stub
    cam.dev = StubDevice()
    return cam


class RegionTest(unittest.TestCase):
    def test_align(self):
        w, h, inc = (16, 2448), (2, 2048), (8, 2, 8, 2)
        self.assertEqual(align_region((704, 852, 1200, 380), w, h, inc), (704, 852, 1200, 380))
        x, y, width, height = align_region((701, 853, 1203, 375), w, h, inc)
        self.assertEqual((x, y), (696, 852))
        self.assertEqual((width % 8, height % 2), (0, 0))
        self.assertGreaterEqual(x + width, 701 + 1203)                 # contains the asked region
        self.assertGreaterEqual(y + height, 853 + 375)
        x, y, width, height = align_region((2440, 2040, 100, 100), w, h, inc)     # at the edge of the sensor
        self.assertEqual((x + width, y + height), (2448, 2048))
        self.assertEqual(align_region((5, 5, 1, 1), w, h, inc)[2:], (16, 2))      # the camera's minimum
        self.assertEqual(align_region((3, 3, 20, 20), (1, 100), (1, 100), (1, 1, 1, 1))[:2], (2, 2))  # even: Bayer order

    def test_full_frame_left_by_other_software(self):
        stub = StubCam(region=(100, 50, 400, 300), binning=(2, 2))
        cam = stub_camera(stub)
        cam._setup_region()
        self.assertEqual(stub.binning, (1, 1))
        self.assertEqual(stub.region, (0, 0, 2448, 2048))
        self.assertIsNone(cam.camera_region)

    def test_no_writes_when_already_full_frame(self):
        stub = StubCam()
        stub_camera(stub)._setup_region()
        self.assertEqual(stub.writes, [])         # a region write would slow the next starts down

    def test_requested_region(self):
        stub = StubCam()
        cam = stub_camera(stub, region=(704, 852, 1200, 380))
        cam._setup_region()
        self.assertEqual(cam.camera_region, (704, 852, 1200, 380))

    def test_region_failure_falls_back_to_full_frame(self):
        stub = StubCam()
        stub.get_x_offset_increment = mock.Mock(side_effect=RuntimeError("no such feature"))
        cam = stub_camera(stub, region=(704, 852, 1200, 380))
        cam._setup_region()
        self.assertIsNone(cam.camera_region)
        self.assertIn("cannot set the camera region", cam.warnings[0])


class PacketDelayTest(unittest.TestCase):
    def setUp(self):
        camera_mod._raised_delay_us.clear()
        self.addCleanup(camera_mod._raised_delay_us.clear)

    def test_per_device(self):
        self.assertEqual(auto_packet_delay_us("x86_64"), 6.0)
        self.assertEqual(auto_packet_delay_us("aarch64"), 40.0)

    def test_steps(self):
        self.assertEqual(next_delay_us(6.0), 12.0)
        self.assertEqual(next_delay_us(40.0), 60.0)
        self.assertEqual(next_delay_us(AUTO_DELAY_STEPS_US[-1]), AUTO_DELAY_STEPS_US[-1])

    def test_automatic_and_fixed(self):
        with mock.patch("app.camera.auto_packet_delay_us", return_value=6.0):
            stub = StubCam()
            cam = stub_camera(stub)
            cam._setup_network()
            self.assertEqual(stub.delay_ns, 6000)
            self.assertTrue(cam.auto_delay)
            stub = StubCam()
            cam = stub_camera(stub, delay=25.0)
            cam._setup_network()
            self.assertEqual(stub.delay_ns, 25000)
            self.assertFalse(cam.auto_delay)
            stub = StubCam()
            stub_camera(stub, delay=-1.0)._setup_network()
            self.assertIsNone(stub.delay_ns)                     # keep the camera's value

    def test_failed_image_raises_it_also_after_a_reopen(self):
        with mock.patch("app.camera.auto_packet_delay_us", return_value=6.0):
            stub = StubCam(busy=1)                               # "busy" once: tried again
            cam = stub_camera(stub)
            cam._setup_network()
            stub.busy = 1
            with mock.patch("app.camera.time.sleep"):
                cam._raise_packet_delay()
            self.assertEqual(stub.delay_ns, 12000)
            self.assertEqual(cam.info["packetDelayUs"], 12.0)
            stub = StubCam()
            cam = stub_camera(stub)                              # reopened: stays raised
            cam._setup_network()
            self.assertEqual(stub.delay_ns, 12000)

    def test_one_failed_image_changes_nothing(self):
        with mock.patch("app.camera.auto_packet_delay_us", return_value=40.0):
            stub = StubCam()
            cam = stub_camera(stub)
            cam._setup_network()
            cam._image_failed()
            self.assertEqual(stub.delay_ns, 40000)               # one alone happens at any delay on an X3
            cam._images_since_failure = camera_mod.AUTO_DELAY_WINDOW
            cam._image_failed()
            self.assertEqual(stub.delay_ns, 40000)               # the next one came much later: still one
            cam._images_since_failure = camera_mod.AUTO_DELAY_WINDOW - 1
            cam._image_failed()
            self.assertEqual(stub.delay_ns, 60000)               # two close together: a step up
            cam._image_failed()
            self.assertEqual(stub.delay_ns, 60000)               # and counting starts over

    def test_forgotten_on_request(self):
        with mock.patch("app.camera.auto_packet_delay_us", return_value=6.0):
            stub = StubCam()
            cam = stub_camera(stub)
            cam._setup_network()
            cam._raise_packet_delay()
            camera_mod.forget_raised_delay()
            stub = StubCam()
            stub_camera(stub)._setup_network()
            self.assertEqual(stub.delay_ns, 6000)

    def test_fixed_delay_is_not_raised(self):
        stub = StubCam()
        cam = stub_camera(stub, delay=25.0)
        cam._setup_network()
        cam._raise_packet_delay()
        self.assertEqual(stub.delay_ns, 25000)


class StreamPortTest(unittest.TestCase):
    def test_reserved_port_old_stream_is_gone_before_the_next(self):
        """Dropping a stream clears the camera's stream port: it must not happen after the next stream set it."""
        live = []

        class FakeStream:
            def __init__(self, port):
                self.port = port

            def get_port(self):
                return self.port

            def push_buffer(self, _buf):
                pass

        class FakeAravis:
            GvStream = FakeStream
            GvStreamOption = mock.Mock()
            Buffer = mock.Mock()

        ports = [49250, 50000, 42000]

        def create_stream(_a, _b):
            self.assertTrue(all(ref() is None for ref in live), "an older stream is still alive")
            stream = FakeStream(ports[len(live)])
            live.append(weakref.ref(stream))
            return stream

        stub = StubCam()
        stub.create_stream = create_stream
        stub.is_gv_device = lambda: True
        cam = stub_camera(stub)
        cam.dev.set_stream_options = lambda _o: None
        cam._payload = 1000
        cam._open_stream(FakeAravis)
        self.assertEqual(cam.stream_port, 42000)
        self.assertEqual(len(live), 3)

    def test_no_port_left(self):
        class FakeStream:
            def get_port(self):
                return 49250

        class FakeAravis:
            GvStream = FakeStream
            GvStreamOption = mock.Mock()

        stub = StubCam()
        stub.create_stream = lambda _a, _b: FakeStream()
        stub.is_gv_device = lambda: True
        cam = stub_camera(stub)
        cam.dev.set_stream_options = lambda _o: None
        with self.assertRaises(CameraError):
            cam._open_stream(FakeAravis)


class ControlHolderTest(unittest.TestCase):
    def camera_answering(self, *reply):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        self.addCleanup(sock.close)

        def serve():
            data, peer = sock.recvfrom(64)
            self.assertEqual(data[:4], bytes((0x42, 0x01, 0x00, 0x80)))     # READREG with an acknowledge
            status, privilege, holder = reply
            sock.sendto(struct.pack(">HHHH", status, 0x0081, 8, 1) + struct.pack(">I", privilege) + socket.inet_aton(holder), peer)
        threading.Thread(target=serve, daemon=True).start()
        return sock.getsockname()[1]

    def test_held(self):
        self.assertEqual(control_holder("127.0.0.1", self.camera_answering(0, 2, "192.168.1.7")), "192.168.1.7")

    def test_free(self):
        self.assertIsNone(control_holder("127.0.0.1", self.camera_answering(0, 0, "0.0.0.0")))

    def test_error_status(self):
        self.assertIsNone(control_holder("127.0.0.1", self.camera_answering(0x8001, 2, "192.168.1.7")))

    def test_no_answer(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        self.addCleanup(sock.close)
        self.assertIsNone(control_holder("127.0.0.1", sock.getsockname()[1], timeout=0.05))

    def test_camera_error_names_the_holder(self):
        class NoControlDevice:
            def is_controller(self):
                return False

            def connect(self, *_a):
                return 1

            def disconnect(self, *_a):
                pass

        class HeldCamera:
            def get_device(self):
                return NoControlDevice()

            def is_gv_device(self):
                return True

            def stop_acquisition(self):
                pass

        class FakeAravis:
            class Camera:
                new = staticmethod(lambda _id: HeldCamera())

            @staticmethod
            def disable_interface(_name):
                pass

        s = dict(load_defaults()["camera"], address="192.168.1.99")
        with mock.patch("app.camera.load_aravis", return_value=FakeAravis), \
                mock.patch("app.camera.control_holder", return_value="192.168.1.7"):
            with self.assertRaises(CameraError) as ctx:
                Camera(s).open()
        self.assertIn("controlled by the application at 192.168.1.7", str(ctx.exception))


class DiscoverTest(unittest.TestCase):
    def test_empty_answer_is_tried_again(self):
        class FakeAravis:
            searches = 0

            @staticmethod
            def disable_interface(_name):
                pass

            @classmethod
            def update_device_list(cls):
                cls.searches += 1

            @classmethod
            def get_n_devices(cls):
                return 0 if cls.searches == 1 else 1

        for name in ("get_device_id", "get_device_address", "get_device_protocol", "get_device_vendor",
                     "get_device_model", "get_device_serial_nbr"):
            setattr(FakeAravis, name, staticmethod(lambda _i, n=name: n))
        with mock.patch("app.camera.load_aravis", return_value=FakeAravis):
            found = camera_mod.discover()
        self.assertEqual(FakeAravis.searches, 2)
        self.assertEqual(found[0]["model"], "get_device_model")


@unittest.skipUnless(HAVE_ARAVIS, "Aravis (gi) not available (README: Local development)")
class FakeAravisCameraTest(unittest.TestCase):
    def test_region_of_the_simulated_camera(self):
        from app.simulator import Simulator
        s = load_defaults()
        s["camera"]["source"] = "simulated"
        cam = Camera(s["camera"], simulator=Simulator("/nonexistent", seed=1),
                     sim_settings={"width": 800, "height": 600, "codes": 1}, region=(101, 51, 300, 200))
        cam.open()
        try:
            frame = cam.grab(2000)
            self.assertEqual(frame.origin, (100, 50))                 # even: the Bayer order stays
            self.assertEqual(frame.pixel_format, "BayerRG8")
            self.assertGreaterEqual(frame.width, 300)
            self.assertGreaterEqual(frame.height, 200)
        finally:
            cam.close()

    def test_open_grab_close(self):
        from app.camera import Camera, discover
        from app.simulator import Simulator
        s = load_defaults()
        s["camera"]["source"] = "simulated"
        cam = Camera(s["camera"], simulator=Simulator("/nonexistent", seed=1),
                     sim_settings={"width": 800, "height": 600, "codes": 1})
        info = cam.open()
        try:
            self.assertTrue(info["simulated"])
            self.assertFalse(info["softwareTrigger"])            # default: one SingleFrame acquisition per read
            self.assertEqual(info["acquisition"], "single frame per read")
            self.assertEqual(info["address"], "Fake_1")
            for _ in range(2):
                frame = cam.grab(2000)
                self.assertEqual((frame.width, frame.height, frame.pixel_format), (800, 600, "BayerRG8"))
                self.assertEqual(len(cam.last_expected), 1)
            self.assertGreaterEqual(cam.stats().get("completed", 0), 2)
            cam.apply_exposure(dict(s["camera"], exposureUs=1000.0))
        finally:
            cam.close()
        trig = Camera(dict(s["camera"], acquisition="trigger"), simulator=Simulator("/nonexistent", seed=2),
                      sim_settings={"width": 800, "height": 600, "codes": 1})
        info = trig.open()
        try:
            self.assertTrue(info["softwareTrigger"])
            self.assertEqual(trig.grab(2000).width, 800)
        finally:
            trig.close()
        self.assertIn("Fake_1", [d["id"] for d in discover(simulated=True)])
