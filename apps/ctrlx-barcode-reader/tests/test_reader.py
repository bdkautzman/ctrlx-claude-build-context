"""reader.py with a pure-Python camera: PLC handshakes, queue limit, errors, modes, history, settings."""

import json
import threading
import time
import unittest

from app import config
from app.datalayer_nodes import NodeRegistry, NodeWriteError, build_node_specs
from tests.helpers import HAVE_IMAGING, FakeCamera, make_store, patch

if HAVE_IMAGING:
    import numpy as np

    from app import imaging
    from app.decoder import zxing
    from app.reader import MAX_CAMERA_ERRORS, MAX_QUEUE, Reader
    from app.simulator import Simulator


def wait_for(pred, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


@unittest.skipUnless(HAVE_IMAGING, "OpenCV/numpy/zxing-cpp not available (README: Local development)")
class ReaderTest(unittest.TestCase):
    def setUp(self, changes=None):
        FakeCamera.reset()
        self.store = make_store(changes or {"camera": {"source": "simulated"},
                                            "simulation": {"width": 640, "height": 480, "codes": 2},
                                            "continuous": {"intervalMs": 100}, "live": {"intervalMs": 100}})
        self.reader = Reader(self.store, simulator=Simulator("/nonexistent", seed=3), camera_factory=FakeCamera)
        self.reg = NodeRegistry(config.DATALAYER_ROOT, build_node_specs(self.store.get(), "t", self.reader))
        self.reader.registry = self.reg
        self.store.add_listener(self.reader.on_settings)
        self.reader.start()
        self.assertTrue(wait_for(lambda: self.reader.status()["camera"]["state"] == "connected"))

    def tearDown(self):
        if FakeCamera.gate is not None:
            FakeCamera.gate.set()
        self.reader.stop()

    def seq(self):
        return self.reg.get("result/sequence")

    def test_trigger_handshake(self):
        self.reg.write("trigger", True)
        self.assertTrue(wait_for(lambda: self.seq() == 1))
        self.assertTrue(wait_for(lambda: self.reg.get("trigger") is False))
        self.assertFalse(self.reg.get("busy"))
        self.assertEqual(self.reg.get("result/source"), "trigger")
        summary = json.loads(self.reg.get("result/json"))
        self.assertEqual(summary["sequence"], 1)
        self.assertEqual(json.loads(self.reg.get("result/codes")), summary["codes"])
        self.assertEqual(self.reg.get("result/text"), summary["codes"][0]["text"])
        self.assertTrue(wait_for(lambda: self.reader.status()["last"] is not None))
        last = self.reader.status()["last"]
        self.assertGreaterEqual(len(last["simulation"]["expected"]), 1)
        self.assertEqual(self.reg.get("result/ok"), True)
        self.assertEqual(self.reg.get("result/count"), len(last["simulation"]["expected"]))
        self.assertEqual(sorted((c["format"], c["text"]) for c in last["codes"]),
                         sorted((e["format"], e["text"]) for e in last["simulation"]["expected"]))

    def test_second_true_while_pending_is_ignored(self):
        FakeCamera.gate = threading.Event()
        self.reg.write("trigger", True)
        self.reg.write("trigger", True)
        self.assertTrue(self.reg.get("busy"))
        FakeCamera.gate.set()
        self.assertTrue(wait_for(lambda: self.reg.get("trigger") is False))
        time.sleep(0.3)
        self.assertEqual(self.seq(), 1)

    def test_request_id_handshake(self):
        self.reg.write("request-id", 5)
        self.assertTrue(wait_for(lambda: self.reg.get("result/request-id") == 5))
        self.assertEqual(self.seq(), 1)
        self.assertEqual(self.reg.get("result/source"), "request")
        self.reg.write("request-id", 5)          # same id again: cyclic write, no new read
        self.reg.write("request-id", 0)          # reset
        time.sleep(0.3)
        self.assertEqual(self.seq(), 1)
        self.reg.write("request-id", 6)
        self.assertTrue(wait_for(lambda: self.reg.get("result/request-id") == 6))
        self.assertEqual(self.seq(), 2)

    def test_queue_limit(self):
        FakeCamera.gate = threading.Event()
        first = self.reader.request_read("ui")
        self.assertTrue(wait_for(lambda: self.reader.status()["queued"] == 0))   # running
        for i in range(MAX_QUEUE):
            self.reg.write("request-id", 100 + i)
        with self.assertRaises(NodeWriteError) as ctx:
            self.reg.write("request-id", 999)
        self.assertEqual(ctx.exception.reason, "invalid")
        with self.assertRaises(NodeWriteError):
            self.reg.write("trigger", True)
        self.assertEqual(self.reg.get("request-id"), 100 + MAX_QUEUE - 1)     # rejected value not stored
        FakeCamera.gate.set()
        self.assertTrue(first.done.wait(10))
        self.assertTrue(wait_for(lambda: self.seq() == 1 + MAX_QUEUE))
        self.assertTrue(wait_for(lambda: self.reg.get("busy") is False))
        self.assertEqual(self.reg.get("result/request-id"), 100 + MAX_QUEUE - 1)

    def test_camera_down_answers_with_error(self):
        FakeCamera.fail_open = "no camera at 192.0.2.5"
        self.reader.reconnect()
        self.assertTrue(wait_for(lambda: self.reader.status()["camera"]["state"] == "error"))
        self.reg.write("trigger", True)
        self.assertTrue(wait_for(lambda: self.seq() == 1))
        self.assertFalse(self.reg.get("result/ok"))
        self.assertIn("camera not connected: no camera", self.reg.get("result/error"))
        self.assertTrue(wait_for(lambda: self.reg.get("trigger") is False))
        self.assertEqual(self.reg.get("camera/state"), "error")
        FakeCamera.fail_open = None
        req = self.reader.request_read("ui")       # a read opens the camera right away
        self.assertTrue(req.done.wait(10))
        self.assertTrue(req.result["ok"])

    def test_grab_errors_reopen_camera(self):
        opened = FakeCamera.opened
        FakeCamera.grab_errors = ["no image within 3000 ms"] * MAX_CAMERA_ERRORS
        for _ in range(MAX_CAMERA_ERRORS):
            req = self.reader.request_read("ui")
            self.assertTrue(req.done.wait(10))
            self.assertEqual(req.result["error"], "no image within 3000 ms")
        self.assertTrue(wait_for(lambda: FakeCamera.opened == opened + 1))
        self.assertEqual(self.reg.get("camera/failures"), MAX_CAMERA_ERRORS)
        req = self.reader.request_read("ui")
        self.assertTrue(req.done.wait(10))
        self.assertIsNone(req.result["error"])

    def test_continuous(self):
        self.reg.write("continuous", True)
        self.assertTrue(wait_for(lambda: self.seq() >= 3))
        self.assertEqual(self.reg.get("result/source"), "continuous")
        self.reader.set_continuous(False)
        self.assertFalse(self.reg.get("continuous"))
        time.sleep(0.5)
        n = self.seq()
        time.sleep(0.5)
        self.assertEqual(self.seq(), n)

    def test_live_preview_is_not_decoded(self):
        self.assertIsNone(self.reader.touch_live())
        self.assertTrue(wait_for(lambda: (self.reader.touch_live() or {}).get("seq", 0) >= 2))
        live = self.reader.touch_live()
        self.assertTrue(live["jpeg"].startswith(b"\xff\xd8"))
        self.assertEqual((live["width"], live["height"]), (640, 480))
        self.assertTrue(self.reader.status()["live"])
        self.assertEqual(self.seq(), 0)

    def test_history_and_images(self):
        patch(self.store, {"images": {"historySize": 3}})
        for _ in range(5):
            self.assertTrue(self.reader.request_read("ui").done.wait(10))
        hist = self.reader.history()
        self.assertEqual([r["sequence"] for r in hist], [5, 4, 3])
        self.assertTrue(self.reader.image(4, "display").startswith(b"\xff\xd8"))
        self.assertTrue(self.reader.image(4, "thumb").startswith(b"\xff\xd8"))
        self.assertIsNone(self.reader.image(1, "display"))       # dropped from the history
        self.assertIsNone(self.reader.image(4, "full"))          # full size only for the newest
        self.assertTrue(self.reader.image(5, "full").startswith(b"\x89PNG"))

    def test_settings_changes(self):
        cam = FakeCamera.instances[-1]
        patch(self.store, {"camera": {"exposureUs": 5000.0}})
        self.assertTrue(wait_for(lambda: cam.exposure_applied == 1))
        self.assertFalse(cam.closed)
        patch(self.store, {"camera": {"address": "192.0.2.7"}})
        self.assertTrue(wait_for(lambda: cam.closed and len(FakeCamera.instances) == 2))
        self.assertTrue(wait_for(lambda: self.reader.status()["camera"]["state"] == "connected"))
        self.assertEqual(FakeCamera.instances[-1].settings["address"], "192.0.2.7")

    def test_decode_region(self):
        zx = zxing()
        a = np.asarray(zx.create_barcode("LEFT", zx.BarcodeFormat.QRCode).to_image(scale=4, add_quiet_zones=True))
        b = np.asarray(zx.create_barcode("RIGHT", zx.BarcodeFormat.QRCode).to_image(scale=4, add_quiet_zones=True))
        img = np.full((300, 700), 255, np.uint8)
        img[20:20 + a.shape[0], 20:20 + a.shape[1]] = a
        img[20:20 + b.shape[0], 400:400 + b.shape[1]] = b
        FakeCamera.frame = imaging.Frame(img, "Mono8")
        req = self.reader.request_read("ui")
        self.assertTrue(req.done.wait(10))
        self.assertEqual([c["text"] for c in req.result["codes"]], ["LEFT", "RIGHT"])
        patch(self.store, {"decode": {"roi": {"enabled": True, "x": 350, "y": 0, "width": 350, "height": 300}}})
        req = self.reader.request_read("ui")
        self.assertTrue(req.done.wait(10))
        self.assertEqual([c["text"] for c in req.result["codes"]], ["RIGHT"])
        self.assertEqual(req.result["region"], [350, 0, 350, 300])
        self.assertGreaterEqual(min(p[0] for p in req.result["codes"][0]["corners"]), 400)   # full-image pixels


if __name__ == "__main__":
    unittest.main()
