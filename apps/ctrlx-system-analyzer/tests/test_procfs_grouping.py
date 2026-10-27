"""/proc parsers and the process -> app grouping (fixtures as seen on the COREvirtual, gotchas #67)."""

import os
import tempfile
import unittest

from app import grouping, procfs
from tests.helpers import stat_line


class ProcfsTest(unittest.TestCase):
    def test_stat_with_spaces_and_parens_in_comm(self):
        st = procfs.parse_stat(stat_line(3519, "a (b) c", utime=52, stime=15, threads=66, starttime=5085,
                                         nice=-5, processor=3, rt_priority=62, policy=1).encode())
        self.assertEqual((st.comm, st.utime, st.stime, st.threads, st.starttime), ("a (b) c", 52, 15, 66, 5085))
        self.assertEqual((st.nice, st.processor, st.rt_priority, st.policy), (-5, 3, 62, 1))
        self.assertIsNone(procfs.parse_stat(b"12 (x) S 1"))
        self.assertIsNone(procfs.parse_stat(None))

    def test_device_stat_line(self):
        # a real thread of the automationcore (COREvirtual, 2026-10-27), fields up to policy appended
        line = (b"3519 (schedBackground) S 1 1220 1220 0 -1 4194368 5 5085 0 62 52 15 14 126 -63 0 66 0 2600 "
                b"551243 58504 18446744073709551615 1 1 0 0 0 0 0 0 0 0 0 0 -1 2 62 1 0 0 0")
        st = procfs.parse_stat(line)
        self.assertEqual((st.comm, st.utime, st.stime, st.threads, st.processor, st.rt_priority, st.policy),
                         ("schedBackground", 52, 15, 66, 2, 62, 1))

    def test_status_and_pss(self):
        data = b"Name:\tx\nVmHWM:\t  234016 kB\nVmRSS:\t  234016 kB\nRssAnon:\t  125520 kB\nRssFile:\t 108296 kB\n" \
               b"RssShmem:\t     200 kB\nVmSwap:\t       0 kB\nThreads:\t66\n"
        self.assertEqual(procfs.parse_status(data), {"hwm": 234016, "rss": 234016, "anon": 125520, "file": 108296,
                                                     "shmem": 200, "swap": 0})
        self.assertEqual(procfs.parse_status(b"Name:\tkthreadd\nThreads:\t1\n"), {})
        self.assertEqual(procfs.parse_pss(b"00400000 [rollup]\nRss:  234016 kB\nPss:  221833 kB\nPss_Anon: 1 kB\n"), 221833)
        self.assertIsNone(procfs.parse_pss(None))

    def test_cgroup_v1_hybrid_and_v2(self):
        v1 = b"10:freezer:/\n1:name=systemd:/system.slice/snap.rexroth-automationcore.control.service\n0::/\n"
        self.assertEqual(procfs.parse_cgroup(v1), "/system.slice/snap.rexroth-automationcore.control.service")
        self.assertEqual(procfs.parse_cgroup(b"0::/system.slice/ssh.service\n"), "/system.slice/ssh.service")
        self.assertEqual(procfs.parse_cgroup(None), "")

    def test_system_files(self):
        ps = procfs.parse_proc_stat(b"cpu  10 0 5 100 3 1 1 0 0 0\ncpu0 10 0 5 100 3 1 1 0 0 0\nbtime 1700\n")
        self.assertEqual(ps["all"], (120, 103))
        self.assertEqual(len(ps["cores"]), 1)
        self.assertEqual(ps["btime"], 1700)
        mem = procfs.parse_meminfo(b"MemTotal:  4017712 kB\nMemFree: 2011480 kB\nMemAvailable: 3000000 kB\nFoo: 1 kB\n")
        self.assertEqual(mem, {"MemTotal": 4017712, "MemFree": 2011480, "MemAvailable": 3000000})
        self.assertEqual(procfs.parse_pressure(b"some avg10=1.25 avg60=0.00 avg300=0.00 total=0\n"), 1.25)
        self.assertIsNone(procfs.parse_pressure(None))

    def test_cmdline_and_revision(self):
        cmd = procfs.parse_cmdline(b"/snap/rexroth-automationcore/2302/rexroth-automation-frame\0/var/x\0")
        self.assertEqual(cmd, "/snap/rexroth-automationcore/2302/rexroth-automation-frame /var/x")
        self.assertEqual(procfs.snap_revision(cmd), ("rexroth-automationcore", "2302"))
        self.assertEqual(procfs.snap_revision("/snap/ctrlx-x/x8/bin/python3"), ("ctrlx-x", "x8"))
        self.assertIsNone(procfs.snap_revision("/usr/bin/bash"))

    def test_read_missing_and_large(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "big")
            with open(path, "wb") as f:
                f.write(b"x" * 200000)
            self.assertEqual(len(procfs.read(path)), 200000)
            self.assertEqual(len(procfs.read(path, 1000)), 1000)
            self.assertIsNone(procfs.read(os.path.join(tmp, "missing")))


class GroupingTest(unittest.TestCase):
    def test_classify(self):
        g = grouping.classify("/system.slice/snap.rexroth-automationcore.control.service")
        self.assertEqual((g.key, g.name, g.kind, g.service, g.file, g.unit),
                         ("snap:rexroth-automationcore", "rexroth-automationcore", "snap", "control",
                          "rexroth-automationcore", True))
        g = grouping.classify("/system.slice/snap.ctrlx-amr-fleet.service.service")
        self.assertEqual((g.key, g.service), ("snap:ctrlx-amr-fleet", "service"))
        g = grouping.classify("/system.slice/snap.foo.hook.configure-0c9a4b1e-1f2a-4c3d-8e9f-0a1b2c3d4e5f.scope")
        self.assertEqual((g.key, g.service, g.unit), ("snap:foo", "hook.configure", False))
        g = grouping.classify("/system.slice/systemd-udevd.service/udev")
        self.assertEqual((g.key, g.file, g.unit), ("system:systemd-udevd", "system-systemd-udevd", True))
        g = grouping.classify("/system.slice/system-getty.slice/getty@tty1.service")
        self.assertEqual(g.key, "system:getty@tty1")
        self.assertEqual(grouping.classify("/user.slice/user-1001.slice/session-3.scope"), grouping.USER)
        self.assertEqual(grouping.classify("/init.scope"), grouping.INIT)
        self.assertEqual(grouping.classify("/", kernel_thread=True), grouping.KERNEL)
        self.assertEqual(grouping.classify(""), grouping.OTHER)

    def test_keys_have_no_dots_and_files_match(self):
        g = grouping.classify("/system.slice/my.odd.unit.service")
        self.assertNotIn(".", g.key)
        for cg in ("/system.slice/snap.a-b.c.service", "/system.slice/my.odd.unit.service", "/init.scope",
                   "/user.slice/x"):
            g = grouping.classify(cg)
            self.assertEqual(grouping.file_for(g.key), g.file)
        self.assertEqual(grouping.file_for("kernel"), "kernel")


if __name__ == "__main__":
    unittest.main()
