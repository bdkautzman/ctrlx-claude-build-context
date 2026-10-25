#!/usr/bin/env python3
"""Run one scan on a device (devices.toml) and measure it: what an installed app really does.

    python3 scripts/device_scan.py --device virtual --profile quick [--via datalayer] [--pdf out.pdf]

Starts the scan through the app's API (or by writing the profile ID to the Data Layer node
scan/start with --via datalayer), follows the progress, samples the app process' CPU (% of one core)
and memory over SSH every few seconds, then prints the result summary, the hosts found and the
Data Layer nodes of the newest scan; --pdf downloads the report. Changes nothing but adding the scan
to the app's history. Scans the profile's targets FROM THE DEVICE - only run it where the network
owner agreed to scans.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(KIT, "tools"))
import ctrlx  # noqa: E402  the kit's REST client: devices.toml, login

SNAP = "ctrlx-network-scanner"
API = "/" + SNAP + "/api/v1"


class Device:
    def __init__(self, name):
        self.t = ctrlx.resolve_target(ctrlx.load_devices(ctrlx.DEFAULT_DEVICES_FILE), os.environ, name)
        self.dev = ctrlx.Device(self.t["api_host"], self.t["api_user"], self.t["api_password"], self.t["verify_tls"])

    def api(self, method, path, body=None, raw=False):
        status, headers, content = self.dev.request(method, API + path, body, raw=raw, timeout=120)
        return content

    def node(self, name):
        _, _, body = self.dev.request("GET", "/automation/api/v2/nodes/" + SNAP + "/" + name)
        return body.get("value") if isinstance(body, dict) else body

    def ssh(self, command, timeout=60):
        t = self.t
        argv = ["ssh", "-p", str(t["ssh_port"]), "-o", "StrictHostKeyChecking=accept-new",
                "{}@{}".format(t["ssh_user"], t["ssh_host"]), command]
        env = dict(os.environ)
        if t["ssh_password"] and shutil.which("sshpass"):
            argv = ["sshpass", "-e"] + argv
            env["SSHPASS"] = t["ssh_password"]
        return subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout).stdout

    def proc_sample(self, seconds=3):
        """(CPU % of one core over `seconds`, RSS MB, peak RSS MB) of the app's main process."""
        out = self.ssh("pid=$(systemctl show -p MainPID --value snap.{0}.service.service); "
                       "a=$(cut -d' ' -f14,15 /proc/$pid/stat); sleep {1}; b=$(cut -d' ' -f14,15 /proc/$pid/stat); "
                       "echo $a $b $(getconf CLK_TCK) $(awk '/VmRSS/ {{print $2}}' /proc/$pid/status) "
                       "$(awk '/VmHWM/ {{print $2}}' /proc/$pid/status)".format(SNAP, seconds)).split()
        try:
            u0, s0, u1, s1, tck, rss, hwm = (float(v) for v in out[-7:])
        except ValueError:
            return None
        return round((u1 + s1 - u0 - s0) / tck / seconds * 100, 1), round(rss / 1024, 1), round(hwm / 1024, 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device")
    ap.add_argument("--profile", default="quick")
    ap.add_argument("--via", choices=("api", "datalayer"), default="api")
    ap.add_argument("--pdf", help="download the report to this file")
    ap.add_argument("--timeout", type=int, default=1800)
    args = ap.parse_args()
    d = Device(args.device)

    before = d.proc_sample(2)
    print("app before: cpu {}% rss {} MB (peak {} MB)".format(*before) if before else "app before: (no SSH sample)")
    t0 = time.monotonic()
    if args.via == "api":
        scan_id = d.api("POST", "/scans", {"profile": args.profile})["id"]
    else:
        d.dev.request("PUT", "/automation/api/v2/nodes/{}/scan/start".format(SNAP), {"type": "string", "value": args.profile})
        time.sleep(1)
        scan_id = d.node("scan/id")
    print("scan", scan_id, "started via", args.via)
    samples = []
    while time.monotonic() - t0 < args.timeout:
        cur = d.api("GET", "/scans/current")
        if not cur.get("running"):
            break
        p = cur["progress"]
        s = d.proc_sample(3)
        if s:
            samples.append(s)
        print("  {:6.1f}s {:9} {:5.1f}%  hosts {:3}  open {:3}  attempts {:6}  | cpu {}% rss {} MB".format(
            time.monotonic() - t0, p["phase"], p["percent"], p["hostsUp"], p["openPorts"], p["probes"],
            *(s[:2] if s else ("?", "?"))))
    after = d.proc_sample(2)
    result = d.api("GET", "/scans/" + scan_id)
    s = result["summary"]
    print("\nstatus {} in {}s: {} of {} addresses up, {} open TCP, {} open UDP, {} attempts".format(
        result["status"], result["durationS"], s["hostsUp"], s["targets"], s["openTcp"], s["openUdp"], result["probes"]))
    print("rating: forbidden {forbidden}, expected {ok}, not listed {unlisted}".format(**s["rating"]))
    for note in result.get("notes", []):
        print("note:", note)
    if result.get("error"):
        print("error:", result["error"])
    for h in result["hosts"]:
        names = ",".join(n["name"] for n in h["names"])
        ports = " ".join("{}/{}{}".format(p["proto"], p["port"], "(" + p["service"] + ")" if p.get("service") else "")
                         for p in h["ports"])
        print("  {:15} {:17} {:28} {:25} via {:12} {}".format(h["ip"], h["mac"], h["vendor"][:28], names[:25],
                                                              ",".join(h["via"]), ports))
    if samples:
        print("\napp during scan: cpu max {}% avg {:.1f}%, rss max {} MB; after: rss {} MB, peak (VmHWM) {} MB".format(
            max(x[0] for x in samples), sum(x[0] for x in samples) / len(samples), max(x[1] for x in samples),
            after[1] if after else "?", after[2] if after else "?"))
    print("\nData Layer: last/id={} last/hosts-up={} last/open-ports={} last/alarms={} scan/state={}".format(
        d.node("last/id"), d.node("last/hosts-up"), d.node("last/open-ports"), d.node("last/alarms"), d.node("scan/state")))
    if args.pdf:
        data = d.api("GET", "/scans/{}/report.pdf".format(scan_id), raw=True)
        with open(args.pdf, "wb") as f:
            f.write(data)
        print("PDF: {} bytes -> {}".format(len(data), args.pdf))
    return 0


if __name__ == "__main__":
    sys.exit(main())
