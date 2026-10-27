"""Which app a process belongs to - from its systemd cgroup, the way `systemctl status` sees it.

  /system.slice/snap.<snap>.<app>.service        snap <snap>, service <app>
  /system.slice/snap.<snap>.<app>-<uuid>.scope   snap <snap> (a command or hook started by snapd)
  /system.slice/.../<unit>.service               system service <unit> (snapd, journald, ssh, ...)
  /user.slice/...                                SSH / console logins
  /init.scope                                    systemd (PID 1)
  kernel threads (PF_KTHREAD in stat flags)      one "kernel" group

Group.key is used in series ids, API calls and file names: it never contains a dot or a slash.
"""

import re
from collections import namedtuple

# key: "snap:<name>" | "system:<unit>" | "kernel" | "user" | "init" | "other"
# file: file name on the external medium (without .csv)
# unit: True when the process belongs to a .service unit (restart events), False for scopes
Group = namedtuple("Group", "key name kind service file unit")

KERNEL = Group("kernel", "Kernel threads", "kernel", "", "kernel", False)
USER = Group("user", "User sessions", "user", "", "user-sessions", False)
INIT = Group("init", "systemd (PID 1)", "init", "", "systemd-init", False)
OTHER = Group("other", "Other", "other", "", "other", False)

_SNAP_UNIT = re.compile(r"^snap\.([a-z0-9][a-z0-9_-]*)\.(.+)\.(service|scope)$")  # _: parallel installs
_UUID_SUFFIX = re.compile(r"-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_UNSAFE = re.compile(r"[^A-Za-z0-9@_+=-]")


def safe(name):
    """A unit/app name usable in keys and file names (dots, slashes, escapes -> '_')."""
    return _UNSAFE.sub("_", name)[:80] or "_"


def file_for(key):
    """Group key -> file name on the external medium (Group.file)."""
    kind, _, name = key.partition(":")
    if kind == "snap":
        return name
    if kind == "system":
        return "system-" + name
    return {"kernel": KERNEL.file, "user": USER.file, "init": INIT.file}.get(key, safe(key))


def classify(cgroup, kernel_thread=False):
    """cgroup path (procfs.parse_cgroup) -> Group."""
    if kernel_thread:
        return KERNEL
    if not cgroup or cgroup == "/":
        return OTHER
    if cgroup.startswith("/user.slice"):
        return USER
    if cgroup.startswith("/init.scope"):
        return INIT
    units = [p for p in cgroup.split("/") if p.endswith(".service") or p.endswith(".scope")]
    if not units:
        return OTHER
    # the outermost unit: /system.slice/systemd-udevd.service/udev -> systemd-udevd.service
    unit = units[0]
    m = _SNAP_UNIT.match(unit)
    if m:
        snap, app, kind = m.groups()
        app = _UUID_SUFFIX.sub("", app)
        return Group("snap:" + snap, snap, "snap", app, snap, kind == "service")
    name, _, kind = unit.rpartition(".")
    s = safe(name)
    return Group("system:" + s, name, "system", name, "system-" + s, kind == "service")
