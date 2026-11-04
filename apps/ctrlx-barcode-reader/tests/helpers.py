"""Shared test helpers: a settings store in a temp folder and a pure-Python camera."""

import importlib
import json
import os
import tempfile
import threading

from app import config
from app.camera import CameraError
from app.settings_rules import RULES, validate_settings
from app.settings_store import SettingsStore

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULTS_FILE = os.path.join(HERE, "..", "defaults", "settings.json")


def have(*modules):
    """True if all modules import (OpenCV/numpy/zxing-cpp/gi come from .dev/ - tests/__init__.py)."""
    try:
        for m in modules:
            importlib.import_module(m)
        return True
    except Exception:  # noqa: BLE001 - ImportError or a broken native library
        return False


HAVE_IMAGING = have("numpy", "cv2", "zxingcpp")


def aravis_available():
    try:
        from app.camera import load_aravis
        load_aravis()
        return True
    except Exception:  # noqa: BLE001
        return False


def load_defaults():
    with open(DEFAULTS_FILE, encoding="utf-8") as f:
        return json.load(f)


def make_store(changes=None):
    """SettingsStore with app data in a temp folder; changes are applied over the defaults."""
    tmp = tempfile.TemporaryDirectory()
    active = os.path.join(tmp.name, "activeConfiguration")
    os.makedirs(active)
    store = SettingsStore(os.path.join(active, config.SNAP_NAME, "settings.json"), DEFAULTS_FILE, active,
                          RULES, validator=validate_settings)
    store.load_from_disk(create_if_missing=True)
    if changes:
        store.update(changes)
    store.tmp = tmp            # keep the folder alive as long as the store
    return store


def merge(base, changes):
    out = dict(base)
    for k, v in changes.items():
        out[k] = merge(base.get(k, {}), v) if isinstance(v, dict) else v
    return out


def patch(store, changes):
    """Change some settings (store.update replaces ALL settings: gotchas #89)."""
    store.update(merge(store.get(), changes))


class FakeCamera:
    """Stands in for app.camera.Camera: frames from the simulator (or a blank image), scripted errors."""

    fail_open = None           # error text -> open() raises CameraError
    grab_errors = []           # texts popped by grab() before it returns frames again
    frame = None               # imaging.Frame returned by every grab (else simulator / blank image)
    gate = None                # threading.Event: grab() waits for it (a slow camera)
    instances = []
    opened = 0

    def __init__(self, cam_settings, simulator=None, sim_settings=None):
        self.settings = dict(cam_settings)
        self.simulated = cam_settings["source"] == "simulated"
        self.simulator = simulator
        self.sim_settings = sim_settings or {"width": 640, "height": 480, "codes": 2}
        self.control_lost = threading.Event()
        self.last_expected = []
        self.info = {}
        self.exposure_applied = 0
        self.closed = False
        FakeCamera.instances.append(self)

    @classmethod
    def reset(cls):
        cls.fail_open = None
        cls.grab_errors = []
        cls.frame = None
        cls.gate = None
        cls.instances = []
        cls.opened = 0

    def open(self):
        if FakeCamera.fail_open:
            raise CameraError(FakeCamera.fail_open)
        FakeCamera.opened += 1
        self.info = {"vendor": "Test", "model": "Fake", "address": "192.0.2.5", "simulated": self.simulated}
        return self.info

    def close(self):
        self.closed = True

    def apply_exposure(self, s):
        self.exposure_applied += 1

    def stats(self):
        return {"completed": 0}

    def grab(self, timeout_ms):
        if FakeCamera.gate is not None:
            FakeCamera.gate.wait(10)
        if FakeCamera.grab_errors:
            raise CameraError(FakeCamera.grab_errors.pop(0))
        from app import imaging
        if FakeCamera.frame is not None:
            return imaging.Frame(FakeCamera.frame.data, FakeCamera.frame.pixel_format)
        if self.simulator is not None:
            s = self.sim_settings
            scene = self.simulator.next_scene(s["width"], s["height"], s["codes"])
            self.last_expected = scene.expected
            return scene.frame
        _, np = imaging.cv()
        return imaging.Frame(np.full((120, 160), 128, np.uint8), "Mono8")
