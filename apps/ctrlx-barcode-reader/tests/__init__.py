"""The Python package lives in src/ (snap part "app" builds only that folder, gotchas #47):
make `import app` work for `python3 -m unittest discover -s tests -t .` from the project root.

OpenCV, numpy, zxing-cpp and Aravis come from .dev/ when it exists (README.md "Local development"),
so `tools/check.sh` runs the decoding and camera tests too; without .dev/ they are skipped.
"""
import ctypes
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

_pylib = os.path.join(ROOT, ".dev", "pylib")
if os.path.isdir(_pylib) and _pylib not in sys.path:
    sys.path.insert(1, _pylib)
for _lib in glob.glob(os.path.join(ROOT, ".dev", "aravis", "usr", "lib", "*-linux-gnu")):
    if "GI_TYPELIB_PATH" not in os.environ:
        os.environ["GI_TYPELIB_PATH"] = os.path.join(_lib, "girepository-1.0")
        # LD_LIBRARY_PATH can't change in a running process: load the library by path, gi then finds
        # it by its soname
        for _so in glob.glob(os.path.join(_lib, "libaravis-0.8.so.0")):
            ctypes.CDLL(_so, mode=ctypes.RTLD_GLOBAL)
