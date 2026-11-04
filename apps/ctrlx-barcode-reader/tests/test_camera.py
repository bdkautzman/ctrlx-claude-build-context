"""camera.py: reserved ports; with Aravis available, the real Camera class against Aravis' fake camera."""

import unittest

from app.camera import RESERVED_PORTS, port_reserved
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


@unittest.skipUnless(HAVE_ARAVIS, "Aravis (gi) not available (README: Local development)")
class FakeAravisCameraTest(unittest.TestCase):
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
