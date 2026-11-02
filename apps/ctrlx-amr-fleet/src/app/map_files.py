"""Map files for the settings drop-down: the maps the app can load, and uploads into the app data.

list_maps(): the *.txt maps in MapSource's search directories (app data first, then the bundled maps), each
name once - the file MapSource loads for it - with source, size and modification time. Other app data files
(amr-list.txt) are not maps. save(): an upload streamed into <name>.tmp in the app data (appPrivateFiles: never
part of a configuration), checked, then renamed - a big map never sits in memory. The whole map is checked when
MapSource loads it (errors show in the map status); an upload only has to be a .txt name the setting map.file
accepts, at most MAX_FILE_BYTES, and text with map points in its first 64 KB.
"""

import os
import re
import stat

from app.occupancy_map import MAX_FILE_BYTES, parse_points

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")     # = settings_rules "map.file" pattern
CHUNK_BYTES = 1024 * 1024
HEAD_BYTES = 64 * 1024


class MapFileError(Exception):
    """An upload that is not stored; status = the HTTP status for the web API."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class MapFiles:
    def __init__(self, search_dirs, upload_dir, not_maps=()):
        """search_dirs: [(label, directory)] as for MapSource; upload_dir: the app data folder."""
        self._dirs = list(search_dirs)
        self._upload_dir = upload_dir
        self._not_maps = {n.lower() for n in not_maps}

    def is_map_name(self, name):
        return bool(NAME.fullmatch(name)) and name.lower().endswith(".txt") and name.lower() not in self._not_maps

    def list_maps(self):
        seen, out = set(), []
        for label, directory in self._dirs:
            try:
                names = os.listdir(directory)
            except OSError:
                continue                       # e.g. no app data yet
            for name in names:
                if name in seen or not self.is_map_name(name):
                    continue
                try:
                    st = os.stat(os.path.join(directory, name))
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode):
                    seen.add(name)
                    out.append({"name": name, "source": label, "size": st.st_size, "modified": int(st.st_mtime)})
        out.sort(key=lambda m: m["name"].lower())
        return out

    def check(self, name, length, replace):
        """Before the body is read: MapFileError if this upload can't be stored."""
        if not self.is_map_name(name):
            raise MapFileError(400, "'{}': a map file name ends with .txt and has 1..100 characters "
                                    "A-Z a-z 0-9 . _ - (first a letter or digit)".format(name))
        if length <= 0:
            raise MapFileError(400, name + ": the file is empty")
        if length > MAX_FILE_BYTES:
            raise MapFileError(413, "{}: larger than {} MB".format(name, MAX_FILE_BYTES // (1024 * 1024)))
        if not replace and os.path.exists(os.path.join(self._upload_dir, name)):
            raise MapFileError(409, name + " is in the app data already (replace=1 replaces it)")

    def save(self, name, read, length, replace=False):
        """Store an upload: read(n) returns the next bytes of the body, length = Content-Length.
        Returns the list entry of the stored file."""
        self.check(name, length, replace)
        os.makedirs(self._upload_dir, exist_ok=True)
        path = os.path.join(self._upload_dir, name)
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                head, remaining = b"", length
                while remaining:
                    chunk = read(min(CHUNK_BYTES, remaining))
                    if not chunk:
                        raise MapFileError(400, "{}: upload incomplete ({} of {} bytes)".format(
                            name, length - remaining, length))
                    if len(head) < HEAD_BYTES:
                        head += chunk[:HEAD_BYTES - len(head)]
                    f.write(chunk)
                    remaining -= len(chunk)
                f.flush()
                os.fsync(f.fileno())
            self._check_head(name, head, complete=len(head) == length)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        st = os.stat(path)
        return {"name": name, "source": self._label(), "size": st.st_size, "modified": int(st.st_mtime)}

    def _label(self):
        return next((label for label, d in self._dirs if d == self._upload_dir), "app data")

    @staticmethod
    def _check_head(name, head, complete):
        if b"\0" in head:
            raise MapFileError(400, name + ": not a text file")
        text = head.decode("utf-8", errors="replace")
        if not complete and "\n" in text:
            text = text[:text.rindex("\n") + 1]           # without the cut last line
        xs, _ = parse_points(text)
        if not len(xs):
            raise MapFileError(400, name + ": no map points at the start of the file - a map has one occupied "
                                           "cell per line: x<TAB>y (or ; , space as separator)")
