"""The active map file: lookup, loading and reloading on change (the I/O side of occupancy_map).

Setting map.file names a file that is looked up, in this order, in
  1. the app data directory (<active configuration>/<snap>/) - your own maps, uploaded via
     Settings -> Manage app data; saved and loaded with ctrlX configurations
  2. the maps bundled with the snap ($SNAP/defaults/maps/)
poll() is called every 2 s (main loop) and reloads when the file, its mtime/size, or
map.resolution changes - this is the hook for maps that change while the app runs.
A file that breaks keeps the last good version of the same file on screen (with the error).
"""

import logging
import os
import threading
import time

from app.occupancy_map import MapError, OccupancyMap

log = logging.getLogger("map")


class MapSource:
    def __init__(self, search_dirs):
        """search_dirs: list of (label, directory), searched in order."""
        self._dirs = list(search_dirs)
        self._file = ""
        self._resolution = 0.0
        self._map = None
        self._source = ""
        self._error = "no map loaded yet"
        self._loaded_key = None
        self._lock = threading.Lock()
        self._poll_lock = threading.Lock()
        self._listeners = []

    def add_listener(self, callback):
        """callback(info) after every change of the map or its error."""
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
                    "error": self._error}
            if self._map is not None:
                info.update(self._map.meta())
        return info

    def _resolve(self, file_name):
        if os.path.basename(file_name) != file_name or file_name.startswith("."):
            return None, None
        for label, directory in self._dirs:
            path = os.path.join(directory, file_name)
            if os.path.isfile(path):
                return path, label
        return None, None

    def poll(self):
        with self._poll_lock:  # main loop and settings listener may both call this
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
            if key == self._loaded_key:
                return None
        started = time.monotonic()
        try:
            new_map = OccupancyMap.from_file(path, resolution, name=file_name)
        except (OSError, MapError) as exc:
            log.error("Map %s (%s) not loaded: %s", file_name, source, exc)
            with self._lock:
                keep = self._map if self._map is not None and self._map.name == file_name else None
            return self._set(keep, source, "map file '{}': {}".format(file_name, exc), key, file_name)
        log.info("Map %s (%s) loaded: %d x %d cells of %g, %d points, %d levels, %.0f ms",
                 file_name, source, new_map.width, new_map.height, new_map.resolution,
                 new_map.points, new_map.levels, (time.monotonic() - started) * 1000)
        return self._set(new_map, source, "", key, file_name)

    def _set(self, new_map, source, error, key, file_name):
        with self._lock:
            if file_name != self._file:
                return None  # configure() changed the file meanwhile; its own poll() wins
            changed = (new_map is not self._map) or error != self._error
            self._map, self._source, self._error, self._loaded_key = new_map, source, error, key
        if changed:
            if error and new_map is None:
                log.warning("No map: %s", error)
            info = self.info()
            for callback in self._listeners:
                try:
                    callback(info)
                except Exception:
                    log.exception("map listener failed")
        return None
