"""The active map file: lookup, loading and reloading on change (the I/O side of occupancy_map).

Setting map.file names a file that is looked up, in this order, in
  1. the app data directory (<active configuration>/<snap>/) - your own maps, uploaded via
     Settings -> Manage app data; saved and loaded with ctrlX configurations
  2. the maps bundled with the snap ($SNAP/defaults/maps/)
poll() is called every 2 s (main loop) and reloads when the file, its mtime/size, or
map.resolution changes - this is the hook for maps that change while the app runs.
A file that breaks keeps the last good version of the same file on screen (with the error).

Loading runs in a background thread (a big map takes seconds on a ctrlX CORE): the app starts and a
settings save answers at once; info() says "loading" meanwhile and the previous map stays until the
new one is ready. Afterwards the thread builds the bitmaps of all tile levels ("prepared"), so the
first tile of a level doesn't wait for that either.
"""

import logging
import os
import threading
import time

from app.occupancy_map import MapError, OccupancyMap

log = logging.getLogger("map")


class MapSource:
    def __init__(self, search_dirs, background=True):
        """search_dirs: list of (label, directory), searched in order.
        background=False loads in the calling thread (simple tests)."""
        self._dirs = list(search_dirs)
        self._background = background
        self._file = ""
        self._resolution = 0.0
        self._map = None
        self._source = ""
        self._error = "no map loaded yet"
        self._loaded_key = None
        self._loading = None          # key of the file being loaded in the background
        self._pending = False         # poll() wanted a check while a load was running
        self._idle = threading.Event()
        self._idle.set()
        self._lock = threading.Lock()
        self._poll_lock = threading.RLock()
        self._listeners = []

    def add_listener(self, callback):
        """callback(info) after every change of the map, its error or its loading state."""
        self._listeners.append(callback)

    def configure(self, file_name, resolution):
        with self._lock:
            self._file = file_name.strip()
            self._resolution = float(resolution)
        self.poll()

    def current(self):
        with self._lock:
            return self._map

    def info(self):
        with self._lock:
            info = {"file": self._file, "source": self._source, "loaded": self._map is not None,
                    "loading": self._loading is not None,
                    "prepared": self._map is not None and self._map.prepared, "error": self._error}
            if self._map is not None:
                info.update(self._map.meta())
        return info

    def wait_idle(self, timeout=None):
        """Wait until no load (and level preparation) is running - for tests and tools."""
        return self._idle.wait(timeout)

    def _resolve(self, file_name):
        if os.path.basename(file_name) != file_name or file_name.startswith("."):
            return None, None
        for label, directory in self._dirs:
            path = os.path.join(directory, file_name)
            if os.path.isfile(path):
                return path, label
        return None, None

    def poll(self):
        with self._poll_lock:  # main loop, settings listener and the loader may all call this
            self._poll()

    def _poll(self):
        with self._lock:
            file_name, resolution = self._file, self._resolution
        if not file_name:
            return self._set(None, "", "no map file configured (setting map.file)", None, file_name)
        path, source = self._resolve(file_name)
        if path is None:
            return self._set(None, "", "map file '{}' not found in the app data or the bundled maps"
                             .format(file_name), None, file_name)
        try:
            st = os.stat(path)
        except OSError as exc:
            return self._set(None, "", str(exc), None, file_name)
        key = (path, st.st_mtime_ns, st.st_size, resolution)
        with self._lock:
            if key == self._loaded_key or key == self._loading:
                return None
            if self._loading is not None:
                self._pending = True      # check again when the running load is done
                return None
            self._loading = key
            self._idle.clear()
        self._notify()
        args = (key, path, source, file_name, resolution)
        if self._background:
            threading.Thread(target=self._load, args=args, name="map-load", daemon=True).start()
        else:
            self._load(*args)
        return None

    def _load(self, key, path, source, file_name, resolution):
        try:
            started = time.monotonic()
            try:
                new_map = OccupancyMap.from_file(path, resolution, name=file_name)
            except (OSError, MapError) as exc:
                log.error("Map %s (%s) not loaded: %s", file_name, source, exc)
                with self._lock:
                    keep = self._map if self._map is not None and self._map.name == file_name else None
                self._set(keep, source, "map file '{}': {}".format(file_name, exc), key, file_name)
                return
            log.info("Map %s (%s) loaded: %d x %d cells of %g, %d points, %d levels, %.0f ms",
                     file_name, source, new_map.width, new_map.height, new_map.resolution,
                     new_map.points, new_map.levels, (time.monotonic() - started) * 1000)
            self._set(new_map, source, "", key, file_name)
            started = time.monotonic()
            new_map.prepare()
            log.info("Map %s: all %d tile levels prepared in %.0f ms", file_name, new_map.levels,
                     (time.monotonic() - started) * 1000)
        except Exception:
            log.exception("loading map %s failed", file_name)
        finally:
            with self._lock:
                self._loading = None
                pending, self._pending = self._pending, False
            if pending:
                self.poll()
            with self._lock:
                idle = self._loading is None
            self._notify()
            if idle:
                self._idle.set()

    def _set(self, new_map, source, error, key, file_name):
        with self._lock:
            if file_name != self._file:
                return None  # configure() changed the file meanwhile; the next poll() handles it
            changed = (new_map is not self._map) or error != self._error
            self._map, self._source, self._error, self._loaded_key = new_map, source, error, key
        if changed:
            if error and new_map is None:
                log.warning("No map: %s", error)
            self._notify()
        return None

    def _notify(self):
        info = self.info()
        for callback in self._listeners:
            try:
                callback(info)
            except Exception:
                log.exception("map listener failed")
