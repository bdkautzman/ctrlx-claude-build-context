#!/usr/bin/env python3
"""Kit setup check: is everything there that the kit needs on this machine?

Run it once after loading the kit onto a (new) build machine - the App Build Environment VM of ctrlX WORKS,
or Ubuntu 24.04 set up the same way (docs/ctrlx/build-deploy-test.md section 0) - and again after changing
that setup. It is not part of builds: tools/check.sh and build-snap.sh do their own checks.

    python3 tools/kit_init.py           all checks (~30-60 s: apt, network, a browser start, a login per device)
    python3 tools/kit_init.py --json    the same as JSON

Every component gets one line:
    OK        there and usable
    MISSING   required: part of the kit does not work without it (exit code 1)
    OPTIONAL  missing, but needed only for the feature named (UI tests, test devices, signing, ...)
    WARN      there, but likely to cause trouble
    INFO      for the record (versions, virtualization, proxy)
Each problem comes with its fix. The tool changes nothing: commands that need root are printed, never run, and
the apt packages of all problems are collected into `sudo apt-get install` lines at the end.

Adding a check as the kit grows: a generator function decorated with @check("<group>") that yields results
(ok / missing / optional / warn / info, below). New group: add it to GROUPS. Read pinned versions from the
file that defines them (as the UI checks read tools/setup-ui-test.sh), never repeat them here.
"""

import argparse
import glob
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)

# Pinned baseline (docs/ctrlx/platform-baseline.md)
UBUNTU = "24.04"            # core24 snaps are built on the host (SNAPCRAFT_BUILD_ENVIRONMENT=host)
SNAPCRAFT_MAJOR = 8
SDK_TAG = "4.6.0"
MIN_PYTHON = (3, 11)        # tomllib: ctrlx.py (devices.toml), new_app.py (kit.toml)
SNAP_PYTHON = (3, 12)       # Python inside core24 snaps
# Environment of the SDK's install-snapcraft.sh: (name, value, required)
SDK_ENV = (("SNAPCRAFT_BUILD_ENVIRONMENT", "host", True),
           ("SNAPCRAFT_ENABLE_EXPERIMENTAL_TARGET_ARCH", "1", True),
           ("SNAPCRAFT_BUILD_INFO", "1", False))
# Build packages of snapcraft's python plugin; snapcraft run as a user cannot install them (gotchas #113)
BUILD_PACKAGES = ("findutils", "python3-dev", "python3-venv")
MIN_FREE_GB = 5             # a build folder takes ~0.2 GB per app, the UI test setup ~0.45 GB
PYPI_URL = "https://pypi.org/simple/setuptools/"

# The kit's own files: not reference/ (verbatim upstream), not build output, local venvs or screenshots
SKIP_DIRS = {".git", "parts", "stage", "prime", "overlay", ".venv", ".dev", ".craft", "__pycache__",
             "node_modules", ".ui-check"}
TEXT_EXT = (".py", ".sh", ".json", ".yaml", ".yml", ".md", ".html", ".js", ".css", ".toml")

GROUPS = (
    ("kit", "Kit files"),
    ("python", "Python and the kit's tools (check.sh, ctrlx.py, new_app.py)"),
    ("build", "Snap builds (build-snap-amd64.sh, build-snap-arm64.sh)"),
    ("network", "Network (builds download packages and wheels)"),
    ("packaging", "Packaging and signing (make-app.sh, check.sh --snap)"),
    ("ui", "UI tests (ui_check.py, pdf_check.py)"),
    ("config", "Your files (kit.toml, devices.toml)"),
    ("devices", "Test devices (ctrlx.py, ui_check.py --device)"),
)
CHECKS = []                 # (group, function), in order of definition

MISSING, OPTIONAL, WARN, INFO, OK = "MISSING", "OPTIONAL", "WARN", "INFO", "OK"
STATUSES = (MISSING, OPTIONAL, WARN, INFO, OK)


def check(group):
    assert group in dict(GROUPS), group
    def register(fn):
        CHECKS.append((group, fn))
        return fn
    return register


def result(status, name, detail="", fix="", apt=()):
    return {"status": status, "name": name, "detail": detail, "fix": fix, "apt": list(apt)}


def ok(name, detail=""):
    return result(OK, name, detail)


def info(name, detail=""):
    return result(INFO, name, detail)


def warn(name, detail, fix="", apt=()):
    return result(WARN, name, detail, fix, apt)


def missing(name, detail, fix="", apt=()):
    return result(MISSING, name, detail, fix, apt)


def optional(name, detail, fix="", apt=()):
    return result(OPTIONAL, name, detail, fix, apt)


def few(items, n=5):
    items = list(items)
    return ", ".join(items[:n]) + (" and {} more".format(len(items) - n) if len(items) > n else "")


# ---------------------------------------------------------------------------
# What the checks look at - tools/tests/test_kit_init.py replaces it with a fake

class Env:
    def __init__(self, kit=KIT, environ=None, home=None):
        self.kit = kit
        self.environ = dict(os.environ if environ is None else environ)
        self.home = home or os.path.expanduser("~")
        self.cache = {}

    def which(self, cmd):
        return shutil.which(cmd, path=self.environ.get("PATH"))

    def run(self, argv, timeout=30):
        """(exit code, stdout + stderr): 127 when the command does not exist, 124 on a time-out."""
        try:
            p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                               universal_newlines=True, timeout=timeout, env=self.environ)
            return p.returncode, p.stdout or ""
        except FileNotFoundError:
            return 127, ""
        except subprocess.TimeoutExpired:
            return 124, ""
        except OSError as exc:
            return 126, str(exc)

    def read(self, path):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return None

    def has_module(self, name):
        return importlib.util.find_spec(name) is not None

    def module_version(self, dist):
        try:
            import importlib.metadata as md
            return md.version(dist)
        except Exception:
            return ""

    def url(self, url, timeout=15):
        """(reachable, detail). Any HTTP answer counts; honours http(s)_proxy / no_proxy like pip and apt.
        GET, not HEAD: the App Build Environment's proxy took 6-22 s for a HEAD that a GET answers in 0.2 s."""
        try:
            with urllib.request.urlopen(url, timeout=timeout) as res:
                res.read(1)
                return True, "HTTP {}".format(res.status)
        except urllib.error.HTTPError as exc:
            return True, "HTTP {}".format(exc.code)
        except (urllib.error.URLError, OSError) as exc:
            return False, str(getattr(exc, "reason", exc))

    def tcp(self, host, port, timeout=5):
        try:
            socket.create_connection((host, int(port)), timeout=timeout).close()
            return True, ""
        except OSError as exc:
            return False, str(exc) or type(exc).__name__

    def dpkg_installed(self, packages):
        """The installed ones of these Debian packages; None when there is no dpkg."""
        rc, out = self.run(["dpkg-query", "-W", "-f=${Package} ${Status}\\n"] + list(packages))
        if rc == 127:
            return None
        return {line.split()[0].split(":")[0] for line in out.splitlines()
                if line.endswith(" installed") and " not-installed" not in line}

    def device(self, target):
        import ctrlx
        return ctrlx.Device(target["api_host"], target["api_user"], target["api_password"], target["verify_tls"])

    def disk_free(self, path):
        return shutil.disk_usage(path).free

    def ram_total(self):
        try:
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except (ValueError, OSError, AttributeError):
            return 0


def sdk_dir(env):
    return env.environ.get("CTRLX_SDK") or os.path.join(env.home, "ctrlx-automation-sdk")


def tilde(env, path):
    return "~" + path[len(env.home):] if path.startswith(env.home + os.sep) else path


def rel(env, path):
    return os.path.relpath(path, env.kit)


# ---------------------------------------------------------------------------
# Parsers (unit-tested)

def parse_os_release(text):
    out = {}
    for line in (text or "").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


def parse_environment_file(text):
    """/etc/environment: KEY=value lines, optionally quoted."""
    return parse_os_release(text)


def parse_apt_policy(text):
    """(installed, candidate) of `apt-cache policy <pkg>`; '' when apt does not know the package."""
    inst = re.search(r"^\s*Installed:\s*(\S+)", text or "", re.M)
    cand = re.search(r"^\s*Candidate:\s*(\S+)", text or "", re.M)
    installed = inst.group(1) if inst and inst.group(1) != "(none)" else ""
    candidate = cand.group(1) if cand and cand.group(1) != "(none)" else ""
    return installed, candidate


def parse_snapcraft_version(text):
    m = re.search(r"snapcraft,?\s+(?:version\s+)?'?(\d+)\.(\d+)(\.\d+)?", text or "")
    return (int(m.group(1)), m.group(0).split()[-1].strip("'")) if m else (None, "")


def parse_setup_ui(text):
    """Pins of tools/setup-ui-test.sh: PLAYWRIGHT_VERSION, PDFIUM_VERSION, APT_PACKAGES (list)."""
    text = (text or "").replace("\\\n", " ")
    out = {}
    for key in ("PLAYWRIGHT_VERSION", "PDFIUM_VERSION", "APT_PACKAGES"):
        m = re.search(r"^{}=(\"[^\"]*\"|\S+)".format(key), text, re.M)
        if m:
            out[key] = m.group(1).strip('"')
    out["APT_PACKAGES"] = out.get("APT_PACKAGES", "").split()
    return out


def apt_source_uris(texts):
    """http(s) URIs of apt sources: one-line format (deb [opts] uri suite ...) and deb822 (URIs: ...)."""
    uris = []
    for text in texts:
        for line in (text or "").splitlines():
            line = line.split("#", 1)[0].strip()
            words = []
            if line.startswith(("deb ", "deb-src ")):
                rest = re.sub(r"^deb(-src)?\s+(\[[^\]]*\]\s*)?", "", line)
                words = rest.split()[:1]
            elif line.startswith("URIs:"):
                words = line[5:].split()
            for w in words:
                if w.startswith(("http://", "https://")) and w.rstrip("/") not in uris:
                    uris.append(w.rstrip("/"))
    return uris


def must_be_executable(relpath, first_line):
    """Scripts the kit runs directly: *.sh (also wrapper/run.sh), the tools and Claude Code hooks with a #! line."""
    relpath = relpath.replace(os.sep, "/")
    if relpath.endswith(".sh"):
        return True
    top_tool = relpath.startswith("tools/") and relpath.count("/") == 1 and relpath.endswith(".py")
    return (top_tool or relpath.startswith(".claude/hooks/")) and first_line.startswith(b"#!")


def own_files(kit):
    for root, dirs, files in os.walk(kit):
        top = os.path.abspath(root) == os.path.abspath(kit)
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not (top and d == "reference"))
        for f in sorted(files):
            yield os.path.join(root, f)


# ---------------------------------------------------------------------------
# Kit files

@check("kit")
def kit_git(env):
    if not env.which("git"):
        yield info("git checkout", "git not installed - cannot tell")
        return
    rc, out = env.run(["git", "-C", env.kit, "rev-parse", "--abbrev-ref", "HEAD"])
    if rc != 0:
        yield info("git checkout", "not a git checkout (copied with scp/zip): works; git clone keeps executable "
                   "bits and line endings and gives you the history")
        return
    _, status = env.run(["git", "-C", env.kit, "status", "--porcelain"])
    changes = len([line for line in status.splitlines() if line.strip()])
    yield info("git checkout", "branch {}, {}".format(out.strip(), "{} uncommitted changes".format(changes)
                                                     if changes else "no uncommitted changes"))
    _, autocrlf = env.run(["git", "-C", env.kit, "config", "--get", "core.autocrlf"])
    if autocrlf.strip().lower() == "true":
        yield warn("core.autocrlf", "true: git writes CRLF line endings into the kit (scripts then fail)",
                   fix="git config core.autocrlf input   (then: bash tools/fix-permissions.sh)")
    _, filemode = env.run(["git", "-C", env.kit, "config", "--get", "core.fileMode"])
    if filemode.strip().lower() == "false":
        yield warn("core.fileMode", "false: git does not notice lost executable bits",
                   fix="git config core.fileMode true   (then: bash tools/fix-permissions.sh)")


@check("kit")
def kit_files(env):
    scripts, texts = [], []
    for path in own_files(env.kit):
        r = rel(env, path)
        try:
            with open(path, "rb") as f:
                head = f.read(2)
        except OSError:
            continue
        if must_be_executable(r, head):
            scripts.append(path)
        if path.endswith(TEXT_EXT):
            texts.append(path)
    lost = [rel(env, p) for p in scripts if not os.access(p, os.X_OK)]
    crlf = []
    for p in texts:
        try:
            with open(p, "rb") as f:
                if b"\r\n" in f.read():
                    crlf.append(rel(env, p))
        except OSError:
            pass
    fix = "bash tools/fix-permissions.sh"
    if "tools/fix-permissions.sh" in crlf:
        fix = "sed -i 's/\\r$//' tools/fix-permissions.sh && " + fix
    if lost:
        yield missing("executable bits", "{} of {} scripts not executable (copied through Windows?): {}".format(
            len(lost), len(scripts), few(lost)), fix=fix)
    else:
        yield ok("executable bits", "{} scripts".format(len(scripts)))
    if crlf:
        yield missing("line endings", "CRLF in {} of {} text files (copied through Windows?): {}".format(
            len(crlf), len(texts), few(crlf)), fix=fix)
    else:
        yield ok("line endings", "LF in {} text files".format(len(texts)))


# ---------------------------------------------------------------------------
# Python and the kit's tools

@check("python")
def python(env):
    v = sys.version_info
    where = "{} {}.{}.{}".format(sys.executable, v[0], v[1], v[2])
    if v[:2] < MIN_PYTHON:
        yield missing("python3", "{} - the kit's tools need >= 3.11 (tomllib)".format(where),
                      fix="Ubuntu 24.04 comes with Python 3.12")
    elif v[:2] != SNAP_PYTHON:
        yield warn("python3", "{} - snaps run 3.12; run-local.sh and the unit tests use this one".format(where))
    else:
        yield ok("python3", where + " (as in the snap)")
    if env.has_module("yaml"):
        yield ok("PyYAML", env.module_version("PyYAML") + " (ctrlx_check.py)")
    else:
        yield missing("PyYAML", "not installed - tools/check.sh (ctrlx_check.py) cannot read snapcraft.yaml",
                      apt=["python3-yaml"])
    if env.has_module("jsonschema"):
        yield ok("jsonschema", env.module_version("jsonschema") + " (schema checks of check.sh)")
    else:
        yield optional("jsonschema", "not installed - tools/check.sh skips the schema checks of the manifest, "
                       "fossinfo and validation files (recommended)", apt=["python3-jsonschema"])
    if env.has_module("pip"):
        yield ok("pip", env.module_version("pip"))
    else:
        yield optional("pip", "not installed - local runs of apps with their own wheels (e.g. ctrlx-ads-connector, "
                       "README 'Develop': pip install --target .dev/pylib)", apt=["python3-pip"])
    if env.which("git"):
        _, out = env.run(["git", "--version"])
        yield ok("git", out.strip().replace("git version ", ""))
    else:
        yield optional("git", "not installed - cloning and updating the kit, commits", apt=["git"])


# ---------------------------------------------------------------------------
# Snap builds

@check("build")
def build_host(env):
    osr = parse_os_release(env.read("/etc/os-release"))
    pretty = osr.get("PRETTY_NAME", "unknown OS")
    if osr.get("ID") == "ubuntu" and osr.get("VERSION_ID") == UBUNTU:
        yield ok("Ubuntu", pretty)
    else:
        yield missing("Ubuntu " + UBUNTU, "{} - core24 snaps are built on the host (SNAPCRAFT_BUILD_ENVIRONMENT="
                      "host), which needs Ubuntu 24.04".format(pretty),
                      fix="the App Build Environment of ctrlX WORKS, or Ubuntu 24.04 (build-deploy-test.md §0)")
    _, arch = env.run(["dpkg", "--print-architecture"])
    arch = arch.strip() or os.uname().machine
    env.cache["arch"] = arch
    yield info("architecture", arch + (" - arm64 snaps are cross builds" if arch == "amd64" else ""))
    rc, virt = env.run(["systemd-detect-virt"])
    virt = virt.strip()
    if virt == "qemu":
        yield info("virtualization", "QEMU without hardware acceleration (TCG): a full build takes ~17-20 min per "
                   "architecture (gotchas #47)")
    elif virt:
        yield info("virtualization", virt)
    ram = env.ram_total()
    yield info("CPU / RAM", "{} cores, {:.1f} GB RAM".format(os.cpu_count() or 0, ram / 1e9))
    free = env.disk_free(env.kit) / 1e9
    if free < MIN_FREE_GB:
        yield warn("free disk", "{:.1f} GB on the kit's file system - a build folder takes ~0.2 GB per app, the UI "
                   "test setup ~0.45 GB".format(free), fix="make room (apps/*/parts: snapcraft clean in the app)")
    else:
        yield ok("free disk", "{:.0f} GB on the kit's file system".format(free))


@check("build")
def snapcraft(env):
    sdk_script = os.path.join(tilde(env, sdk_dir(env)), "scripts", "install-snapcraft.sh")
    if not env.which("snapcraft"):
        yield missing("snapcraft", "not installed", fix="{}  (or: sudo snap install snapcraft --channel=8.x/stable "
                      "--classic)".format(sdk_script))
        return
    # `snap list` answers at once; `snapcraft --version` took 48 s in the emulated App Build Environment
    _, lst = env.run(["snap", "list", "snapcraft"])
    rows = [line.split() for line in lst.splitlines()[1:] if line.strip()]
    if rows and len(rows[0]) > 3:
        version, channel = rows[0][1], rows[0][3]
        major = int(version.split(".")[0]) if version.split(".")[0].isdigit() else None
    else:   # not installed as a snap
        _, out = env.run(["snapcraft", "--version"], timeout=120)
        (major, version), channel = parse_snapcraft_version(out), ""
    detail = version + (" ({})".format(channel) if channel else "")
    if major != SNAPCRAFT_MAJOR:
        yield missing("snapcraft", "{} - the kit is pinned to 8.x".format(detail or out.strip()[:60] or "unknown"),
                      fix="sudo snap refresh snapcraft --channel=8.x/stable")
    else:
        yield ok("snapcraft", detail)


@check("build")
def sdk_environment(env):
    etc = parse_environment_file(env.read("/etc/environment"))
    for var, want, required in SDK_ENV:
        now, saved = env.environ.get(var), etc.get(var)
        set_it = "echo {}={} | sudo tee -a /etc/environment   (the SDK's install-snapcraft.sh does it), " \
                 "then log in again".format(var, want)
        if now == want and saved == want:
            yield ok(var, want)
        elif saved == want:
            yield warn(var, "in /etc/environment, but not in this shell", fix="log out and in again (or reboot)")
        elif now == want:
            yield warn(var, "set in this shell only, not in /etc/environment", fix=set_it)
        else:
            what = "not set" if now is None else "= {} (needs {})".format(now, want)
            role = "snap builds need it" if required else "the SDK's setting: build information inside the snap"
            yield (missing if required else optional)(var, "{} - {}".format(what, role), fix=set_it)


@check("build")
def build_packages(env):
    have = env.dpkg_installed(BUILD_PACKAGES)
    if have is None:
        yield info("build packages", "no dpkg - cannot check " + ", ".join(BUILD_PACKAGES))
        return
    lack = [p for p in BUILD_PACKAGES if p not in have]
    if lack:
        yield missing("build packages", "{} not installed - snapcraft's python plugin needs them and cannot install "
                      "them as a user (gotchas #113); setup-ui-test.sh also needs python3-venv".format(", ".join(lack)),
                      apt=lack)
    else:
        yield ok("build packages", ", ".join(BUILD_PACKAGES))


@check("build")
def datalayer_deb(env):
    if not env.which("apt-cache"):
        yield missing("ctrlx-datalayer .deb", "no apt-cache - not Ubuntu?")
        return
    t0 = time.monotonic()
    rc, out = env.run(["apt-cache", "policy", "ctrlx-datalayer"], timeout=90)
    seconds = time.monotonic() - t0
    installed, candidate = parse_apt_policy(out)
    if rc == 124:
        yield missing("ctrlx-datalayer .deb", "apt-cache did not answer within 90 s - apt's cache was written by "
                      "another apt (sudo snapcraft? gotchas #113)", fix="sudo apt-get update")
        return
    if not candidate:
        sdk = sdk_dir(env)
        debs = sorted(glob.glob(os.path.join(sdk, "ctrlx-datalayer*.deb")) +
                      glob.glob(os.path.join(sdk, "*", "ctrlx-datalayer*.deb")))
        if debs:
            fix = "{}/scripts/install-ctrlx-datalayer.sh {}".format(tilde(env, sdk), tilde(env, os.path.dirname(debs[-1])))
        else:
            fix = "the SDK's clone-install-sdk.sh (tag 4.6.0), then ctrlx-automation-sdk/scripts/" \
                  "install-ctrlx-datalayer.sh <folder with the .deb> (build-deploy-test.md §0)"
        yield missing("ctrlx-datalayer .deb", "apt has no candidate - apps that stage ctrlx-datalayer (all of them) "
                      "cannot be built", fix=fix)
        return
    yield ok("ctrlx-datalayer .deb", "{} (apt candidate{})".format(candidate, ", installed" if installed else ""))
    if seconds > 10:
        yield warn("apt cache", "apt-cache took {:.0f} s - its cache is rebuilt on every call (written by sudo "
                   "snapcraft? gotchas #113); the pre-build check of build-snap.sh gives up after 20 s".format(seconds),
                   fix="sudo apt-get update")


@check("build")
def cross_builds(env):
    arch = env.cache.get("arch", "")
    other = {"amd64": "arm64", "arm64": "amd64"}.get(arch)
    if not other:
        yield info("cross builds", "host architecture {} - not checked".format(arch or "unknown"))
        return
    script = "{}/scripts/install-required-packages.sh".format(tilde(env, sdk_dir(env)))
    _, foreign = env.run(["dpkg", "--print-foreign-architectures"])
    if other not in foreign.split():
        yield missing("{} packages".format(other), "dpkg has no {0} architecture - ./build-snap-{0}.sh cannot stage {0} "
                      "packages".format(other), fix=script + "  (adds the architecture and its apt sources)")
        return
    _, out = env.run(["apt-cache", "policy", "libc6:" + other], timeout=90)
    _, candidate = parse_apt_policy(out)
    if not candidate:
        yield missing("{} packages".format(other), "apt has no {0} packages (libc6:{0}) - apt sources for {0} "
                      "missing or not updated".format(other), fix=script + "  (or: sudo apt-get update)")
    else:
        yield ok("{} packages".format(other), "from apt (libc6:{} {}) for ./build-snap-{}.sh".format(other, candidate, other))


@check("build")
def build_output(env):
    uid = os.getuid()
    paths = []
    for base in sorted(glob.glob(os.path.join(env.kit, "apps", "*")) + glob.glob(os.path.join(env.kit, "templates", "*"))):
        paths += [os.path.join(base, d) for d in ("parts", "stage", "prime") if os.path.exists(os.path.join(base, d))]
        paths += glob.glob(os.path.join(base, "*.snap"))
    foreign = [rel(env, p) for p in paths if os.lstat(p).st_uid != uid]
    if foreign:
        yield warn("build output", "owned by another user (built with sudo snapcraft? gotchas #113): {}".format(
            few(foreign)), fix="sudo chown -R $USER: " + " ".join(foreign[:5]))
    elif paths:
        yield ok("build output", "{} build folders and snaps, all yours".format(len(paths)))


# ---------------------------------------------------------------------------
# Network

@check("network")
def network(env):
    proxy = env.environ.get("https_proxy") or env.environ.get("HTTPS_PROXY") or env.environ.get("http_proxy") or \
        env.environ.get("HTTP_PROXY")
    yield info("proxy", "{} (the App Build Environment goes out through the Windows host, gotchas #21; a VPN on the "
               "host can break it, #119)".format(proxy) if proxy else "none")
    reachable, why = env.url(PYPI_URL)
    if reachable:
        yield ok("PyPI", "reachable (snapcraft's pip in every Python part, setup-ui-test.sh)")
    else:
        yield missing("PyPI", "{} not reachable: {} - builds fail in pip".format(PYPI_URL, why),
                      fix="check the network / proxy; a VPN on the host? (gotchas #119)")
    texts = [env.read("/etc/apt/sources.list")]
    texts += [env.read(p) for p in sorted(glob.glob("/etc/apt/sources.list.d/*.list") +
                                          glob.glob("/etc/apt/sources.list.d/*.sources"))]
    for uri in apt_source_uris(texts):
        reachable, why = env.url(uri + "/")
        host = uri.split("/")[2]
        if reachable:
            yield ok("apt " + host, "reachable (stage packages)")
        else:
            yield missing("apt " + host, "{} not reachable: {} - snapcraft cannot download stage packages".format(uri, why),
                          fix="check the network / proxy; a VPN on the host? (gotchas #119)")


# ---------------------------------------------------------------------------
# Packaging and signing

@check("packaging")
def packaging(env):
    if env.which("unsquashfs"):
        yield ok("unsquashfs", "check.sh --snap and make-app.sh look inside built snaps")
    else:
        yield optional("unsquashfs", "not installed - check.sh --snap and make-app.sh cannot look inside built snaps",
                       apt=["squashfs-tools"])
    sdk = sdk_dir(env)
    if not os.path.isdir(sdk):
        yield optional("SDK " + SDK_TAG, "no clone at {} - make-app.sh --sign uses its app-signing-tool.sh; the "
                       "ctrlx-datalayer .deb comes from it".format(tilde(env, sdk)),
                       fix="the SDK's clone-install-sdk.sh, tag 4.6.0 (build-deploy-test.md §0), or CTRLX_SDK=<folder>")
    else:
        rc, tag = env.run(["git", "-C", sdk, "describe", "--tags", "--exact-match"])
        tag = tag.strip() if rc == 0 else ""
        if tag == SDK_TAG:
            yield ok("SDK " + SDK_TAG, tilde(env, sdk))
        else:
            yield warn("SDK " + SDK_TAG, "{} is {} - the kit is pinned to 4.6.0".format(
                tilde(env, sdk), "at " + tag if tag else "not at a release tag"),
                fix="git -C {} checkout 4.6.0".format(tilde(env, sdk)))
        lack = [s for s in ("app-signing-tool.sh", "verify-apps.sh") if not os.path.isfile(os.path.join(sdk, "scripts", s))]
        if lack:
            yield optional("signing scripts", "{} missing in {}/scripts - make-app.sh --sign".format(
                ", ".join(lack), tilde(env, sdk)))
    if env.which("openssl"):
        yield ok("openssl", "for make-app.sh --sign")
    else:
        yield optional("openssl", "not installed - make-app.sh --sign (the SDK's signing scripts)", apt=["openssl"])


# ---------------------------------------------------------------------------
# UI tests

BROWSER_START = """
from playwright.sync_api import sync_playwright
with sync_playwright() as pw:
    browser = pw.chromium.launch(args=["--no-proxy-server"])
    page = browser.new_page()
    page.set_content("<p id=t>ok</p>")
    assert page.inner_text("#t") == "ok"
    print("browser", browser.version)
    browser.close()
"""


@check("ui")
def ui_tests(env):
    setup = os.path.join(env.kit, "tools", "setup-ui-test.sh")
    pins = parse_setup_ui(env.read(setup))
    if not pins.get("PLAYWRIGHT_VERSION") or not pins.get("PDFIUM_VERSION"):
        yield warn("UI test pins", "cannot read PLAYWRIGHT_VERSION / PDFIUM_VERSION from tools/setup-ui-test.sh")
        return
    fix = "tools/setup-ui-test.sh   (user level, no root; prints one sudo apt-get line if libraries are missing)"
    venv = env.environ.get("CTRLX_UI_VENV") or os.path.join(env.home, ".cache", "ctrlx-kit", "uivenv")
    py = os.path.join(venv, "bin", "python")
    fonts = [p for p in pins["APT_PACKAGES"] if p.startswith("fonts-")]
    libs = [p for p in pins["APT_PACKAGES"] if not p.startswith("fonts-")]
    ready = True
    if not os.path.exists(py):
        yield optional("UI test venv", "none at {}".format(tilde(env, venv)), fix=fix)
        ready = False
    else:
        for dist, pin in (("playwright", pins["PLAYWRIGHT_VERSION"]), ("pypdfium2", pins["PDFIUM_VERSION"])):
            rc, out = env.run([py, "-c", "import importlib.metadata as m; print(m.version({!r}))".format(dist)])
            have = out.strip() if rc == 0 else ""
            if have == pin:
                yield ok(dist, pin + " (pinned, in {})".format(tilde(env, venv)))
            else:
                yield optional(dist, "{} in {}, pinned {}".format(have or "missing", tilde(env, venv), pin), fix=fix)
                ready = ready and dist != "playwright"
    browser = ""
    if ready:
        _, out = env.run([os.path.join(venv, "bin", "playwright"), "install", "--dry-run", "--only-shell", "chromium"],
                         timeout=60)
        location = next((line.split()[-1] for line in out.splitlines()
                         if "Install location:" in line and "chromium_headless_shell" in line), "")
        for root, _, files in os.walk(location) if location else ():
            if "chrome-headless-shell" in files:
                browser = os.path.join(root, "chrome-headless-shell")
                break
        if browser:
            yield ok("headless browser", tilde(env, os.path.dirname(browser)))
        else:
            yield optional("headless browser", "not downloaded{}".format(" ({})".format(tilde(env, location))
                                                                       if location else ""), fix=fix)
            ready = False
    if browser:
        _, out = env.run(["ldd", browser])
        lack = sorted({line.split()[0] for line in out.splitlines() if "not found" in line})
        if lack:
            yield optional("browser libraries", "missing: {}".format(few(lack, 4)), apt=libs)
            ready = False
        else:
            yield ok("browser libraries", "all found")
    if glob.glob("/usr/share/fonts/**/*.ttf", recursive=True):
        yield ok("fonts", "installed")
    else:
        yield optional("fonts", "none - text renders as boxes in screenshots", apt=fonts or ["fonts-liberation"])
        ready = False
    if ready:
        rc, out = env.run([py, "-c", BROWSER_START], timeout=180)
        if rc == 0 and "browser" in out:
            yield ok("browser start", "Chrome Headless Shell {} starts".format(out.strip().split()[-1]))
        else:
            yield optional("browser start", "fails: {}".format((out.strip().splitlines() or ["time-out"])[-1][:200]),
                           fix=fix)


# ---------------------------------------------------------------------------
# Your files

def _load_toml(path):
    import tomllib
    with open(path, "rb") as f:
        return tomllib.load(f)


@check("config")
def kit_toml(env):
    path = os.path.join(env.kit, "kit.toml")
    if not os.path.isfile(path):
        yield optional("kit.toml", "none - tools/new_app.py then needs --author and --url (and --license for "
                       "another license than MIT)", fix="cp kit.example.toml kit.toml, then fill in author and url")
        return
    try:
        cfg = _load_toml(path)
    except ImportError:
        yield missing("kit.toml", "cannot be read with Python < 3.11 (tomllib)")
        return
    except Exception as exc:
        yield missing("kit.toml", "cannot be read: {} - new_app.py stops on it".format(exc))
        return
    try:
        example = _load_toml(os.path.join(env.kit, "kit.example.toml"))
    except Exception:
        example = {}
    same = [k for k in ("author", "url") if cfg.get(k) and cfg.get(k) == example.get(k)]
    empty = [k for k in ("author", "url") if not cfg.get(k)]
    if same or empty:
        yield warn("kit.toml", "{} - new apps get these in snapcraft.yaml, LICENSE and the About dialog".format(
            "; ".join(filter(None, ["still the example value: " + ", ".join(same) if same else "",
                                    "empty: " + ", ".join(empty) if empty else ""]))), fix="edit kit.toml")
    else:
        yield ok("kit.toml", "author and url set, license {}, company part: {}".format(
            cfg.get("license") or "MIT", cfg.get("company") or "none (ctrlx-<app>)"))


def devices_file(env):
    return env.environ.get("CTRLX_DEVICES_FILE") or os.path.join(env.kit, "devices.toml")


@check("config")
def devices_toml(env):
    path = devices_file(env)
    env.cache["devices"] = None
    if not os.path.isfile(path):
        yield optional("devices.toml", "none - tools/ctrlx.py knows only the COREvirtual default (10.0.2.2:8443, "
                       "user boschrexroth)", fix="cp devices.example.toml devices.toml, then fill in your devices "
                       "(build-deploy-test.md §3)")
        return
    sys.path.insert(0, HERE)
    import ctrlx
    try:
        devices = ctrlx.load_devices(path)
    except SystemExit as exc:
        yield missing("devices.toml", "cannot be read: {} - tools/ctrlx.py stops on it".format(exc))
        return
    names = ctrlx.device_names(devices)
    env.cache["devices"] = devices
    default = devices.get("default")
    if default and default not in names:
        yield warn("devices.toml", "default = '{}', but there is no [{}]".format(default, default),
                   fix="edit devices.toml")
    elif not names:
        yield optional("devices.toml", "has no devices", fix="add one per device (devices.example.toml)")
    else:
        yield ok("devices.toml", "{} device{}: {}; default: {}".format(
            len(names), "s" if len(names) > 1 else "", ", ".join(names), default or "none (COREvirtual default)"))


@check("config")
def git_ignored(env):
    if not env.which("git") or env.run(["git", "-C", env.kit, "rev-parse", "--git-dir"])[0] != 0:
        return
    bad = False
    for name in ("kit.toml", "devices.toml"):
        if env.run(["git", "-C", env.kit, "ls-files", "--error-unmatch", name])[0] == 0:
            yield missing(name + " in git", "committed - it holds your passwords / defaults",
                          fix="git rm --cached {}  (and change the passwords it held)".format(name))
            bad = True
        elif env.run(["git", "-C", env.kit, "check-ignore", "-q", name])[0] != 0:
            yield missing(name + " not ignored", "git would commit it - it holds your passwords / defaults",
                          fix="echo /{} >> .gitignore".format(name))
            bad = True
    if not bad:
        yield ok("git-ignored", "kit.toml, devices.toml")


# ---------------------------------------------------------------------------
# Test devices

@check("devices")
def devices(env):
    devs = env.cache.get("devices")
    if not devs:
        yield info("devices", "none to check (devices.toml above)")
        return
    import ctrlx
    sshpass = False
    for name in ctrlx.device_names(devs):
        t = ctrlx.resolve_target(devs, {}, device=name)
        sshpass = sshpass or bool(t["ssh_user"] and t["ssh_password"])
        for r in device_checks(env, name, t):
            yield r
    if sshpass and not env.which("sshpass"):
        yield optional("sshpass", "not installed - ctrlx.py ssh asks for the devices.toml SSH password",
                       apt=["sshpass"])


def device_checks(env, name, t):
    import ctrlx
    host, _, port = t["api_host"].partition(":")
    port = port or "443"
    reachable, why = env.tcp(host, port)
    if not reachable:
        yield optional(name, "{}:{} not reachable ({}) - device off or not started, another network, or a VPN on "
                       "the host (gotchas #119)?".format(host, port, why))
        return
    dev = env.device(t)
    try:
        dev.token(timeout=15)
        users = ctrlx._users(dev)
    except SystemExit as exc:
        yield optional(name, "{} answers, but the login as {} fails: {}".format(
            t["api_host"], t["api_user"], str(exc).splitlines()[0][:160]), fix="api_user / api_password in devices.toml")
        return
    finally:
        dev.logout()   # the identity manager allows only so many sessions (gotchas #110)
    arch = t["arch"] or "no arch"
    yield ok(name, "{} ({}), login as {} works".format(t["api_host"], arch, t["api_user"]))
    if not t["arch"]:
        yield warn(name + " arch", "no arch in devices.toml - ctrlx.py install cannot refuse a snap of the wrong "
                   "architecture", fix='arch = "amd64" or "arm64" in [{}]'.format(name))
    lack = [u[2] for u in ctrlx.TEST_USERS if u[2] not in users]
    if lack:
        yield optional(name + " test users", "missing: {} - role checks (ui_check.py --device {} --role ...) need "
                       "them".format(", ".join(lack), name),
                       fix="ask first, then: tools/ctrlx.py --device {} test-users create".format(name))
    else:
        yield ok(name + " test users", ", ".join(u[2] for u in ctrlx.TEST_USERS))
    if not t["ssh_user"]:
        yield info(name + " SSH", "no ssh_user - SSH-only checks run on the COREvirtual (build-deploy-test.md §3)")
        return
    reachable, why = env.tcp(t["ssh_host"], t["ssh_port"])
    if reachable:
        yield ok(name + " SSH", "{}@{}:{} answers".format(t["ssh_user"], t["ssh_host"], t["ssh_port"]))
    else:
        yield optional(name + " SSH", "{}:{} not reachable ({}) - SSH allowed on the device? ssh_port right?".format(
            t["ssh_host"], t["ssh_port"], why))


# ---------------------------------------------------------------------------

def run_checks(env, on_group=None, on_result=None):
    results = []
    for key, title in GROUPS:
        fns = [fn for g, fn in CHECKS if g == key]
        if on_group:
            on_group(title)
        for fn in fns:
            try:
                for r in fn(env):
                    r["group"] = key
                    results.append(r)
                    if on_result:
                        on_result(r)
            except Exception as exc:   # a broken check must not hide the others
                r = warn(fn.__name__, "check failed: {}: {}".format(type(exc).__name__, exc))
                r["group"] = key
                results.append(r)
                if on_result:
                    on_result(r)
    return results


def summary(results):
    counts = {s: sum(1 for r in results if r["status"] == s) for s in STATUSES}
    apt = {}
    for status in (MISSING, OPTIONAL, WARN):
        pkgs = []
        for r in results:
            if r["status"] == status:
                pkgs += [p for p in r["apt"] if p not in pkgs]
        apt[status] = pkgs
    return {"counts": counts, "apt": apt, "ready": counts[MISSING] == 0}


def print_result(r):
    print("  {:9} {:24}  {}".format(r["status"], r["name"], r["detail"]).rstrip())
    fix = r["fix"] or ("sudo apt-get install -y " + " ".join(r["apt"]) if r["apt"] else "")
    if fix and r["status"] != OK:
        print("  {:9} {:24}  fix: {}".format("", "", fix))
    sys.stdout.flush()


def print_summary(results, s):
    c = s["counts"]
    print("\nSummary: {} MISSING (required), {} OPTIONAL (missing), {} WARN, {} OK".format(
        c[MISSING], c[OPTIONAL], c[WARN], c[OK]))
    for status, label in ((MISSING, "Missing, required"), (OPTIONAL, "Missing, optional"), (WARN, "Warnings")):
        names = [r["name"] for r in results if r["status"] == status]
        if names:
            print("  {}: {}".format(label, ", ".join(names)))
    for status, label in ((MISSING, "required"), (OPTIONAL, "optional"), (WARN, "warnings")):
        if s["apt"][status]:
            print("apt packages ({}; needs root, run it yourself):\n  sudo apt-get install -y --no-install-recommends {}"
                  .format(label, " ".join(s["apt"][status])))
    if s["ready"]:
        print("RESULT: READY{}".format(" - the OPTIONAL lines name features that need more" if c[OPTIONAL] else ""))
    else:
        print("RESULT: NOT READY - fix the MISSING lines, then run python3 tools/kit_init.py again")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="print the results as JSON")
    args = ap.parse_args(argv)
    env = Env()
    if args.json:
        results = run_checks(env)
        s = summary(results)
        print(json.dumps({"kit": env.kit, "results": results, "counts": s["counts"], "apt": s["apt"],
                          "ready": s["ready"]}, indent=1))
        return 0 if s["ready"] else 1
    print("ctrlX app development kit - setup check of this machine")
    print("kit: {}   (run the fixes from this folder)".format(env.kit))
    results = run_checks(env, on_group=lambda title: print("\n" + title), on_result=print_result)
    s = summary(results)
    print_summary(results, s)
    return 0 if s["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
