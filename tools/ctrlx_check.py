#!/usr/bin/env python3
"""ctrlX OS app compliance checker (static, runs before/without snapcraft).

    python3 tools/ctrlx_check.py apps/<app>               # normal check
    python3 tools/ctrlx_check.py apps/<app> --release     # stricter (grade, TODOs, validation files)
    python3 tools/ctrlx_check.py apps/<app> --snap x.snap # also inspect a built snap (needs unsquashfs)
    python3 tools/ctrlx_check.py apps/<app> --json        # machine readable

Exit code 0 = no errors (warnings allowed), 1 = errors, 2 = usage problem.

Sources of the rules (all in this kit):
  docs/ctrlx/upstream-sdk-4.6.0/appdevguide.md, appdevguide_basechecks.md,
  appdevguide_reserved-interfaces.md (-> tools/data/reserved-interfaces.json),
  package-assets.md, persistdata.md, and tools/schemas/*.json (Bosch Rexroth json-schema repo).
Each finding has a stable code (e.g. SNAP-BASE) so docs/ctrlx/gotchas.md can reference it.

Requirements: Python 3.8+, PyYAML (python3-yaml). jsonschema is optional but recommended
(python3-jsonschema) - without it the schema validations are skipped with a warning.
Suppress a source-code finding on one line with the comment:  ctrlx-check: ignore
"""

import argparse
import glob
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SCHEMAS = os.path.join(HERE, "schemas")
RESERVED_FILE = os.path.join(HERE, "data", "reserved-interfaces.json")

EXPECTED_BASE = "core24"          # ctrlX OS 4.x = Ubuntu Core 24
MANIFEST_SCHEMA = "package-manifest.v1.7.schema.json"
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
SNAP_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
KNOWN_SYSTEM_SCOPES = {"rexroth-device.all.rwx", "solutions.r", "solutions.rw"}


class Report:
    def __init__(self):
        self.items = []

    def add(self, level, code, msg, where=""):
        self.items.append({"level": level, "code": code, "message": msg, "where": where})

    def error(self, code, msg, where=""):
        self.add("ERROR", code, msg, where)

    def warn(self, code, msg, where=""):
        self.add("WARN", code, msg, where)

    def info(self, code, msg, where=""):
        self.add("INFO", code, msg, where)

    @property
    def errors(self):
        return [i for i in self.items if i["level"] == "ERROR"]

    @property
    def warnings(self):
        return [i for i in self.items if i["level"] == "WARN"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def load_yaml(path, rep):
    try:
        import yaml
    except ImportError:
        rep.error("TOOL-YAML", "PyYAML missing: sudo apt-get install -y python3-yaml (or pip install pyyaml)")
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception as exc:
        rep.error("YAML-PARSE", "cannot parse: {}".format(exc), path)
        return None


def read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_json(path, rep, code="JSON-PARSE"):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        rep.error(code, "cannot parse JSON: {}".format(exc), path)
        return None


def schema_validate(doc, schema_file, rep, code, where):
    try:
        import jsonschema
    except ImportError:
        rep.warn("TOOL-JSONSCHEMA", "jsonschema not installed - skipped schema validation of {} "
                 "(sudo apt-get install -y python3-jsonschema)".format(os.path.basename(where)))
        return
    with open(schema_file, "r", encoding="utf-8") as f:
        schema = json.load(f)
    cls = jsonschema.validators.validator_for(schema)
    validator = cls(schema)
    for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path)):
        path = "/".join(str(p) for p in err.absolute_path) or "<root>"
        msg = err.message
        if err.validator == "oneOf" and "items" in json.dumps(err.schema.get("oneOf", ""))[:200]:
            msg += "  (hint: every menu entry needs \"items\": [])"
        if len(msg) > 300:
            msg = msg[:300] + "..."
        rep.error(code, "{}: {}".format(path, msg), where)


def as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def rel(path, root):
    try:
        return os.path.relpath(path, root)
    except ValueError:
        return path


# ---------------------------------------------------------------------------
# snapcraft.yaml
# ---------------------------------------------------------------------------

def collect_interfaces(snap):
    """Return dict name -> {'direction', 'interface', 'attrs', 'top_level'} for plugs and slots."""
    result = {}
    for direction in ("plugs", "slots"):
        top = snap.get(direction) or {}
        if isinstance(top, dict):
            for name, attrs in top.items():
                attrs = attrs if isinstance(attrs, dict) else {}
                result[(direction, name)] = {"interface": attrs.get("interface", name), "attrs": attrs, "top_level": True}
        for app in (snap.get("apps") or {}).values():
            for name in as_list((app or {}).get(direction)):
                result.setdefault((direction, name), {"interface": name, "attrs": {}, "top_level": False})
    return result


def check_reserved(interfaces, rep, where):
    with open(RESERVED_FILE, "r", encoding="utf-8") as f:
        reserved = json.load(f)["interfaces"]
    for (direction, name), info in interfaces.items():
        iface = info["interface"]
        attrs = info["attrs"]
        d = direction[:-1]  # plug / slot
        for r in reserved:
            if r["interface"] != iface or r["direction"] != d:
                continue
            params = r.get("parameters")
            if params:  # e.g. content=package-run
                key, _, val = params.partition("=")
                if str(attrs.get(key, "")) != val:
                    continue
            exc = r.get("exceptions")
            if iface == "system-files":
                paths = [str(p) for k in ("read", "write") for p in as_list(attrs.get(k))]
                if paths and all(p == "/dev/shm" or p.startswith("/dev/shm/") for p in paths):
                    continue
            if iface == "shared-memory" and attrs.get("shared-memory") == "datalayer-shm":
                continue
            if iface == "mount-control":
                rep.warn("IFACE-RESERVED", "{} '{}' uses mount-control: only allowed for the storage-extension "
                         "concept (docs/ctrlx/upstream-sdk-4.6.0/storage-extension.md)".format(d, name), where)
                continue
            rep.error("IFACE-RESERVED", "{} '{}' (interface {}{}) is reserved/denied on ctrlX OS: {}{}".format(
                d, name, iface, " " + params if params else "", r["reason"],
                " (exception: {})".format(exc) if exc else ""), where)


def check_snapcraft(app_dir, rep, ctx, release):
    path = os.path.join(app_dir, "snap", "snapcraft.yaml")
    if not os.path.isfile(path):
        rep.error("SNAP-MISSING", "snap/snapcraft.yaml not found", app_dir)
        return None
    where = rel(path, app_dir)
    snap = load_yaml(path, rep)
    if not isinstance(snap, dict):
        return None
    ctx["snap"] = snap

    name = snap.get("name")
    ctx["name"] = name
    if not isinstance(name, str) or not name:
        rep.error("SNAP-NAME", "name missing", where)
    else:
        if not name.startswith("ctrlx-"):
            rep.error("SNAP-NAME", "name '{}' must start with 'ctrlx-' (ctrlX App Guide 4.3)".format(name), where)
        if not SNAP_NAME.match(name):
            rep.error("SNAP-NAME", "name '{}' must be lowercase letters/digits separated by single '-'".format(name), where)
        if len(name) > 32:
            rep.error("SNAP-NAME", "name '{}' longer than 32 characters".format(name), where)
        elif len(name) > 29:
            rep.warn("SNAP-NAME", "name longer than 29 characters: the reverse-proxy socket path "
                     "package-run/<name>/web.sock exceeds 50 chars - use run/<name>/web.sock (see docs)", where)

    title = snap.get("title")
    if not isinstance(title, str) or not 3 <= len(title) <= 40:
        rep.error("SNAP-TITLE", "title must be 3..40 characters (got {!r})".format(title), where)

    version = snap.get("version")
    if not isinstance(version, str):
        rep.error("SNAP-VERSION", "version must be a quoted string like '1.2.3' (YAML parsed {!r} as {})".format(
            version, type(version).__name__), where)
    elif not SEMVER.match(version) or len(version) > 32:
        rep.error("SNAP-VERSION", "version '{}' must be semantic MAJOR.MINOR.PATCH (<= 32 chars)".format(version), where)
    ctx["version"] = version

    summary = snap.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        rep.error("SNAP-SUMMARY", "summary missing", where)
    elif len(summary) > 78:
        rep.error("SNAP-SUMMARY", "summary longer than 78 characters (snapcraft limit)", where)

    desc = snap.get("description") or ""
    m = re.search(r"<!--(.*?)-->", desc, re.S)
    if not m or not re.search(r"^\s*author\s*:", m.group(1), re.M) or not re.search(r"^\s*url\s*:", m.group(1), re.M):
        rep.error("SNAP-DESCRIPTION", "description must start with an HTML comment containing 'author:' and 'url:' "
                  "lines (App Guide 4.3)", where)
    text = re.sub(r"<!--.*?-->", "", desc, flags=re.S).strip()
    if len(text) < 16:
        rep.error("SNAP-DESCRIPTION", "description text (outside the comment) must have >= 16 characters", where)

    base = snap.get("base")
    if base != EXPECTED_BASE:
        rep.error("SNAP-BASE", "base is '{}' - ctrlX OS 4.x requires base: {} (core20 = ctrlX OS 1.x, "
                  "core22 = 2.x/3.x)".format(base, EXPECTED_BASE), where)
    if snap.get("confinement") != "strict":
        rep.error("SNAP-CONFINEMENT", "confinement must be 'strict'", where)
    grade = snap.get("grade")
    if grade != "stable":
        (rep.error if release else rep.warn)("SNAP-GRADE", "grade is '{}' - releases require grade: stable".format(grade), where)

    if "architectures" in snap:
        rep.error("SNAP-PLATFORMS", "'architectures:' is the core22 syntax - core24 uses 'platforms:'", where)
    platforms = snap.get("platforms") or {}
    if isinstance(platforms, dict) and "architectures" not in snap:
        for arch in ("amd64", "arm64"):
            if arch not in platforms:
                rep.warn("SNAP-PLATFORMS", "platform {} missing (arm64 = ctrlX CORE hardware, amd64 = COREvirtual)".format(arch), where)

    raw = read_text(path)
    if "SNAPCRAFT_PROJECT_NAME" in raw:
        rep.warn("SNAP-ENV", "SNAPCRAFT_PROJECT_NAME is deprecated with core24 - use CRAFT_PROJECT_NAME", where)

    apps = snap.get("apps") or {}
    if not apps:
        rep.error("SNAP-APPS", "no apps defined", where)
    for app_name, app in apps.items():
        app = app or {}
        if app.get("daemon"):
            delay = app.get("restart-delay") or (app.get("passthrough") or {}).get("restart-delay")
            seconds = None
            if isinstance(delay, str):
                mm = re.match(r"^(\d+)(ms|s|m)?$", delay.strip())
                if mm:
                    n, unit = int(mm.group(1)), mm.group(2) or "s"
                    seconds = n / 1000 if unit == "ms" else n * 60 if unit == "m" else n
            if seconds is None or seconds < 5:
                rep.error("SNAP-RESTART-DELAY", "app '{}': restart-delay must be >= 5s (got {!r})".format(app_name, delay), where)
            if not (app.get("restart-condition") or (app.get("passthrough") or {}).get("restart-condition")):
                rep.warn("SNAP-RESTART", "app '{}': set restart-condition (e.g. always)".format(app_name), where)
        cmd = app.get("command", "")
        ctx.setdefault("commands", []).append(cmd)

    interfaces = collect_interfaces(snap)
    ctx["interfaces"] = interfaces
    check_reserved(interfaces, rep, where)
    plugs = [n for (d, n) in interfaces if d == "plugs"]
    slots = [n for (d, n) in interfaces if d == "slots"]
    if len(plugs) > 8 or len(slots) > 8:
        rep.warn("IFACE-COUNT", "more than 8 plugs or slots (validation guideline: keep interfaces minimal)", where)

    def content_iface(direction, name):
        info = interfaces.get((direction, name))
        return info if info and info["interface"] == "content" else None

    # package-assets slot
    pa = content_iface("slots", "package-assets")
    if not pa or pa["attrs"].get("content") != "package-assets":
        rep.error("IFACE-PACKAGE-ASSETS", "slot 'package-assets' (interface content, content package-assets) missing - "
                  "the package manifest is never seen by ctrlX OS", where)
    else:
        reads = [str(p) for p in as_list((pa["attrs"].get("source") or {}).get("read"))]
        if not any(re.fullmatch(r"\$SNAP/package-assets/\$\{(CRAFT|SNAPCRAFT)_PROJECT_NAME\}", p) for p in reads):
            rep.error("IFACE-PACKAGE-ASSETS", "slot package-assets source.read must be $SNAP/package-assets/${CRAFT_PROJECT_NAME}", where)
    if "'package-assets/*': package-assets/${" not in raw.replace('"', "'") and \
            "package-assets/*: package-assets/${" not in raw:
        rep.warn("PART-CONFIGS", "no dump part organizing 'package-assets/*' -> package-assets/${CRAFT_PROJECT_NAME}/ found", where)

    ctx["has_plug"] = lambda n, content=None: (("plugs", n) in interfaces and
                                               (content is None or interfaces[("plugs", n)]["attrs"].get("content") == content))
    ctx["has_slot"] = lambda n, content=None: (("slots", n) in interfaces and
                                               (content is None or interfaces[("slots", n)]["attrs"].get("content") == content))

    # stage-packages / python
    parts = snap.get("parts") or {}
    ctx["stage_packages"] = [p for part in parts.values() for p in as_list((part or {}).get("stage-packages"))]
    ctx["python_part"] = any((part or {}).get("plugin") == "python" for part in parts.values())
    return snap


# ---------------------------------------------------------------------------
# package manifest
# ---------------------------------------------------------------------------

# Settings/system page of the ctrlX OS 4.6 UI (rexroth-deviceadmin): tiles are grouped by `category`,
# shown as core.settings.categories.<category without "N_" prefix>; only these names are translated.
SETTINGS_CATEGORIES = {"apps", "device", "system", "security", "network"}


def check_settings_tiles(kind, entries, rep, where):
    for e in entries or []:
        eid = e.get("id", "")
        target = e.get("target")
        if target and target != "embedded":
            # the page builds tiles from the entry's url only; a target turns it into a click callback
            rep.error("MENU-SETTINGS", "menus.{} entry '{}' has target '{}': the ctrlX Settings page cannot open it "
                      "(tile without link). Omit target (same tab) or use 'embedded'".format(kind, eid, target), where)
        category = e.get("category")
        short = re.sub(r"^\d*_?", "", (category or "").lower())
        if short not in SETTINGS_CATEGORIES:
            rep.warn("MENU-SETTINGS", "menus.{} entry '{}': category {} is shown as 'core.settings.categories.{}' - "
                     "use one of 0_apps, 0_device, 1_system, 2_security, 3_network".format(
                         kind, eid, json.dumps(category), short or "undefined"), where)


def check_manifest(app_dir, rep, ctx, schemas):
    name = ctx.get("name") or ""
    assets = os.path.join(app_dir, "configs", "package-assets")
    files = [f for f in glob.glob(os.path.join(assets, "*package-manifest*.json"))
             if not re.search(r"\.package-manifest\.[a-z]{2}(-[A-Za-z]{2})?\.json$", f)]  # skip i18n files
    expected = os.path.join(assets, name + ".package-manifest.json")
    if not files:
        rep.warn("MANIFEST-MISSING", "no configs/package-assets/<name>.package-manifest.json - no menus, proxy, scopes", app_dir)
        return None
    if expected not in files:
        rep.error("MANIFEST-FILENAME", "manifest file must be named {}.package-manifest.json (found {})".format(
            name, ", ".join(os.path.basename(f) for f in files)), rel(assets, app_dir))
        path = files[0]
    else:
        path = expected
    where = rel(path, app_dir)
    man = load_json(path, rep, "MANIFEST-PARSE")
    if not isinstance(man, dict):
        return None
    ctx["manifest"] = man
    schema_validate(man, os.path.join(schemas, MANIFEST_SCHEMA), rep, "MANIFEST-SCHEMA", where)
    sref = man.get("$schema", "")
    if sref and "package-manifest" not in sref:
        rep.warn("MANIFEST-SCHEMA-REF", "$schema should reference the package-manifest schema", where)

    if man.get("id") != name:
        rep.error("MANIFEST-ID", "id '{}' must equal the snap name '{}'".format(man.get("id"), name), where)

    # scopes
    declared_scopes = set()
    for sset in man.get("scopes-declaration", []) or []:
        ident = sset.get("identifier", "")
        if not ident.startswith(name + "."):
            rep.warn("MANIFEST-SCOPES", "scope set '{}' should start with '{}.' (copied from a sample?)".format(ident, name), where)
        for sc in sset.get("scopes", []) or []:
            sid = sc.get("identifier", "")
            declared_scopes.add(sid)
            if not sid.startswith(ident + "."):
                rep.warn("MANIFEST-SCOPES", "scope '{}' should start with its set identifier '{}.'".format(sid, ident), where)
            if sid.split(".")[-1] not in ("r", "w", "rw", "x", "rwx"):
                rep.warn("MANIFEST-SCOPES", "scope '{}' should end with an access type .r/.w/.rw/.x/.rwx".format(sid), where)

    # proxy
    proxy_urls = []
    restricted_paths = []
    for pm in ((man.get("services") or {}).get("proxyMapping") or []):
        pname, url, binding = pm.get("name", ""), pm.get("url", ""), pm.get("binding", "")
        if pname != pname.lower() or ".web" not in pname:
            rep.error("PROXY-NAME", "proxyMapping name '{}' must be lowercase and contain '.web' (e.g. {}.web)".format(pname, name), where)
        if not url.startswith("/") or url != url.lower():
            rep.error("PROXY-URL", "proxyMapping url '{}' must be lowercase and start with '/'".format(url), where)
        elif not (url.rstrip("/") == "/" + name or url.startswith("/" + name + "/")):
            rep.warn("PROXY-URL", "proxyMapping url '{}' should be '/{}' to avoid collisions".format(url, name), where)
        proxy_urls.append(url.rstrip("/"))
        if not binding:
            rep.error("PROXY-BINDING", "proxyMapping binding is empty", where)
        elif binding.startswith("unix://"):
            if "${SNAP" in binding:
                rep.error("PROXY-BINDING", "binding uses ${SNAP_DATA}; the manifest syntax is {$SNAP_DATA}", where)
            sock = binding[len("unix://"):].replace("{$SNAP_DATA}", "/var/snap/{}/current".format(name))
            m = re.match(r"^/var/snap/[^/]+/current/(.*)$", sock)
            tail = m.group(1) if m else sock
            if len(sock) > 107 or len(tail) > 50:
                rep.error("PROXY-SOCKET-LEN", "socket path too long ({} chars, part after current/ {} chars; limits 107/50): {}".format(
                    len(sock), len(tail), sock), where)
            if not re.match(r"^(package-run|run)/{}/".format(re.escape(name)), tail):
                rep.error("PROXY-SOCKET-DIR", "socket must be in package-run/{0}/ (or run/{0}/), got {1}".format(name, tail), where)
            if not ctx.get("has_slot", lambda *a, **k: False)("package-run", "package-run"):
                rep.error("IFACE-PACKAGE-RUN", "unix socket binding requires slot 'package-run' (content package-run, "
                          "source.write $SNAP_DATA/package-run/${CRAFT_PROJECT_NAME})", "snap/snapcraft.yaml")
        else:
            rep.warn("PROXY-BINDING", "binding '{}' is a TCP port - a Unix socket is strongly recommended (App Guide 8.1)".format(binding), where)
        restricted = pm.get("restricted") or []
        for r in restricted:
            restricted_paths.append(r if isinstance(r, str) else r.get("path", ""))
        if not restricted:
            rep.warn("PROXY-RESTRICTED", "proxyMapping '{}' has no restricted paths - the reverse proxy will not validate "
                     "tokens for your API (list e.g. '{}/api')".format(pname, url.rstrip("/")), where)

    def is_restricted(u):
        return any(u == p or u.startswith(p.rstrip("/") + "/") for p in restricted_paths if p)

    # menus
    for kind, entries in (man.get("menus") or {}).items():
        seen = set()

        def walk(items):
            for e in items or []:
                eid = e.get("id", "")
                if eid in seen:
                    rep.error("MENU-ID", "duplicate menu id '{}' in menus.{}".format(eid, kind), where)
                seen.add(eid)
                if not eid.startswith(name):
                    rep.warn("MENU-ID", "menu id '{}' should start with the snap name '{}'".format(eid, name), where)
                link = e.get("link", "")
                if link and link.startswith("/") and not any(link.startswith(u) for u in proxy_urls) \
                        and not link.startswith("/solutions"):
                    rep.warn("MENU-LINK", "menu link '{}' does not point to the proxy url {}".format(link, proxy_urls), where)
                for perm in e.get("permissions", []) or []:
                    if perm not in declared_scopes and perm not in KNOWN_SYSTEM_SCOPES:
                        rep.warn("MENU-PERMISSION", "menu permission '{}' is not a declared scope".format(perm), where)
                walk(e.get("items"))
        walk(entries)
        if kind in ("settings", "system"):
            check_settings_tiles(kind, entries, rep, where)

    # app data commands + directories
    cmds = ((man.get("commands") or {}).get("activeConfiguration") or {})
    for op in ("save", "load"):
        ids = set()
        for c in cmds.get(op, []) or []:
            if c.get("id") in ids:
                rep.error("APPDATA-CMD", "duplicate {} command id {}".format(op, c.get("id")), where)
            ids.add(c.get("id"))
            u = c.get("url", "")
            path_only = re.sub(r"^https?://[^/]+", "", u)
            if proxy_urls and not any(path_only.startswith(p + "/") for p in proxy_urls):
                rep.warn("APPDATA-CMD", "{} url '{}' is not below the proxy url".format(op, u), where)
            if restricted_paths and not is_restricted(path_only):
                rep.warn("APPDATA-CMD", "{} url '{}' is not restricted - token not validated by the proxy".format(op, u), where)
    if (cmds or (man.get("configuration") or {}).get("appDirectories")) and \
            not ctx.get("has_plug", lambda *a, **k: False)("active-solution", "solutions"):
        rep.error("IFACE-ACTIVE-SOLUTION", "app data commands/appDirectories need plug 'active-solution' "
                  "(content solutions, target $SNAP_COMMON/solutions)", "snap/snapcraft.yaml")
    if cmds and not (cmds.get("save") and cmds.get("load")):
        rep.warn("APPDATA-CMD", "register BOTH save and load commands", where)
    appdirs = (man.get("configuration") or {}).get("appDirectories") or []
    for d in appdirs:
        dn = d.get("name", "")
        if dn != dn.lower() or not dn.startswith(name):
            rep.warn("APPDATA-DIR", "appDirectory '{}' should be lowercase and start with the snap name".format(dn), where)
        if d.get("copyOnLoad") is not True and cmds:
            rep.info("APPDATA-DIR", "appDirectory '{}' copyOnLoad is not true: your load phase must copy the data itself".format(dn), where)
    for rx in (man.get("configuration") or {}).get("appPrivateFiles") or []:
        try:
            re.compile(rx)
        except re.error as exc:
            rep.error("APPDATA-PRIVATE", "appPrivateFiles regex '{}' invalid: {}".format(rx, exc), where)
        if appdirs and not any(rx.startswith("^" + d.get("name", "") + "/") for d in appdirs):
            rep.warn("APPDATA-PRIVATE", "appPrivateFiles '{}' should start with ^<appDirectory>/".format(rx), where)

    ctx["proxy_urls"] = proxy_urls
    ctx["restricted"] = restricted_paths
    return man


# ---------------------------------------------------------------------------
# fossinfo, validation descriptions
# ---------------------------------------------------------------------------

def check_fossinfo(app_dir, rep, ctx, schemas, release):
    name = ctx.get("name") or ""
    path = os.path.join(app_dir, "configs", "package-assets", name + ".fossinfo.json")
    if not os.path.isfile(path):
        (rep.error if release else rep.warn)("FOSS-MISSING", "configs/package-assets/{}.fossinfo.json missing "
                                             "(FOSS license info is mandatory for apps)".format(name), app_dir)
        return
    where = rel(path, app_dir)
    doc = load_json(path, rep, "FOSS-PARSE")
    if doc is None:
        return
    schema_validate(doc, os.path.join(schemas, "fossinfo.v1.schema.json"), rep, "FOSS-SCHEMA", where)
    if isinstance(doc, dict) and "foss_components" in doc:
        rep.error("FOSS-FORMAT", "'foss_components' is the wrong format from package-assets.md - use 'components' "
                  "(tools/schemas/fossinfo.v1.schema.json)", where)
    comps = {c.get("name", "").lower() for c in (doc.get("components", []) if isinstance(doc, dict) else [])}
    if "TODO" in json.dumps(doc):
        (rep.error if release else rep.warn)("FOSS-TODO", "fossinfo contains TODO placeholders "
                                             "(run tools/foss_from_prime.py after a build)", where)
    for dep in ctx.get("py_deps", []):
        if dep.lower() not in comps:
            rep.warn("FOSS-DEPS", "python dependency '{}' (pyproject.toml) is not listed in fossinfo".format(dep), where)


def check_validation_files(app_dir, rep, ctx, schemas, release):
    vdir = os.path.join(app_dir, "validation")
    if not os.path.isdir(vdir):
        (rep.error if release else rep.info)("VALIDATION-MISSING", "validation/ folder with slotplug/unixsocket/portlist "
                                             "descriptions missing (needed for Bosch app validation)", app_dir)
        return
    with open(RESERVED_FILE, "r", encoding="utf-8") as f:
        reserved_ports = json.load(f)["ports"]
    blocked = set()
    for vals in reserved_ports.values():
        for v in vals:
            if isinstance(v, list):
                blocked.update(range(v[0], v[1] + 1))
            else:
                blocked.add(v)

    sp = os.path.join(vdir, "slotplug-description.json")
    if os.path.isfile(sp):
        doc = load_json(sp, rep)
        if doc is not None:
            schema_validate(doc, os.path.join(schemas, "slotplug-description.v1.0.schema.json"), rep, "VAL-SLOTPLUG", rel(sp, app_dir))
            interfaces = ctx.get("interfaces", {})
            for direction in ("plugs", "slots"):
                described = {e.get("name") for e in doc.get(direction, [])}
                used = {n for (d, n) in interfaces if d == direction}
                for missing in sorted(used - described):
                    rep.error("VAL-SLOTPLUG", "{} '{}' used in snapcraft.yaml but not described".format(direction[:-1], missing), rel(sp, app_dir))
                for extra in sorted(described - used):
                    rep.error("VAL-SLOTPLUG", "{} '{}' described but not in snapcraft.yaml".format(direction[:-1], extra), rel(sp, app_dir))
                for e in doc.get(direction, []):
                    if len(e.get("purpose", "")) < 16:
                        rep.error("VAL-SLOTPLUG", "purpose of '{}' must have >= 16 characters".format(e.get("name")), rel(sp, app_dir))
                    if e.get("name", "") != e.get("name", "").lower():
                        rep.error("VAL-SLOTPLUG", "name '{}' must be lowercase".format(e.get("name")), rel(sp, app_dir))
    elif release:
        rep.error("VALIDATION-MISSING", "validation/slotplug-description.json missing", app_dir)

    us = os.path.join(vdir, "unixsocket-description.json")
    if os.path.isfile(us):
        doc = load_json(us, rep)
        if doc is not None:
            schema_validate(doc, os.path.join(schemas, "unixsocket-description.v1.0.schema.json"), rep, "VAL-SOCKET", rel(us, app_dir))
            names = [s.get("name", "") for s in doc.get("sockets", [])]
            for s in doc.get("sockets", []):
                n = s.get("name", "")
                if n != n.lower() or not (n.endswith(".sock") or n.endswith(".socket")):
                    rep.error("VAL-SOCKET", "socket name '{}' must be lowercase and end with .sock".format(n), rel(us, app_dir))
                if len(s.get("purpose", "")) < 16:
                    rep.error("VAL-SOCKET", "purpose of '{}' must have >= 16 characters".format(n), rel(us, app_dir))
            if len(names) != len(set(names)):
                rep.error("VAL-SOCKET", "duplicate socket names", rel(us, app_dir))
    elif release:
        rep.error("VALIDATION-MISSING", "validation/unixsocket-description.json missing (may be empty)", app_dir)

    pl = os.path.join(vdir, "portlist-description.json")
    if os.path.isfile(pl):
        doc = load_json(pl, rep)
        if doc is not None:
            schema_validate(doc, os.path.join(schemas, "portlist-description.v1.0.schema.json"), rep, "VAL-PORTS", rel(pl, app_dir))
            ports = doc.get("ports", [])
            if len(ports) > 3:
                rep.warn("VAL-PORTS", "more than 3 open ports (validation guideline)", rel(pl, app_dir))
            for p in ports:
                num = p.get("number")
                if num in blocked:
                    rep.error("VAL-PORTS", "port {} is reserved on ctrlX OS".format(num), rel(pl, app_dir))
                sn = p.get("service-name", "")
                if not re.match(r"^(ctrlx|bosch)-[a-z0-9.-]+$", sn):
                    rep.error("VAL-PORTS", "service-name '{}' must start with ctrlx- or bosch- and be lowercase".format(sn), rel(pl, app_dir))
                if p.get("routing") == "external" and p.get("binding") == "IP" and \
                        str(p.get("application-protocol", "")).upper() in ("HTTP", "HTTPS"):
                    rep.warn("VAL-PORTS", "HTTP(S) port {} exposed externally - use the reverse proxy instead".format(num), rel(pl, app_dir))
    elif release:
        rep.error("VALIDATION-MISSING", "validation/portlist-description.json missing (may contain an empty list)", app_dir)


# ---------------------------------------------------------------------------
# source tree
# ---------------------------------------------------------------------------

SOURCE_RULES = [
    (r"/var/snap/rexroth-solutions", "ERROR", "SRC-SOLUTIONS-PATH",
     "hardcoded path into another snap - use $SNAP_COMMON/solutions/activeConfiguration (active-solution plug)"),
    (r"(Rotating)?FileHandler\(", "WARN", "SRC-LOGFILE",
     "log files on internal flash wear it out (App Guide 6.2.2) - log to stdout/journald"),
    (r"HTTPServer\(\s*\(\s*['\"](0\.0\.0\.0)?['\"]", "WARN", "SRC-BIND-ALL",
     "web server bound to all interfaces - in the snap bind to the package-run Unix socket"),
    (r"verify_signature\"?\s*:\s*False", "WARN", "SRC-JWT-NOVERIFY",
     "JWT decoded without signature check - only acceptable for paths listed as 'restricted' in the manifest"),
    (r"boschrexroth", "WARN", "SRC-CREDENTIALS",
     "default ctrlX credentials in code - must never be used inside the snap (ipc:// needs none)"),
    (r"(password|passwd)\s*=\s*['\"][^'\"]+['\"]", "WARN", "SRC-PASSWORD", "hardcoded password"),
]


def walk_files(app_dir, skip_dirs):
    """Files below app_dir; never descends into skip_dirs (snapcraft output has thousands of files)."""
    for root, dirs, files in os.walk(app_dir):
        dirs[:] = sorted(d for d in dirs if d not in skip_dirs)
        for fn in sorted(files):
            yield os.path.join(root, fn)


def check_sources(app_dir, rep, ctx):
    name = ctx.get("name") or ""
    py_files = [f for f in walk_files(app_dir, {"tests", "venv", ".venv", "parts", "stage", "prime", "build"})
                if f.endswith(".py")]
    uses_dl = False
    for f in py_files:
        try:
            src = read_text(f)
        except UnicodeDecodeError:
            rep.warn("SRC-ENCODING", "not UTF-8", rel(f, app_dir))
            continue
        try:
            compile(src, f, "exec")
        except SyntaxError as exc:
            rep.error("SRC-SYNTAX", "syntax error line {}: {}".format(exc.lineno, exc.msg), rel(f, app_dir))
        if re.search(r"^\s*(import|from)\s+ctrlxdatalayer", src, re.M) or "import ctrlxdatalayer" in src:
            uses_dl = True
        hits = {}
        for lineno, line in enumerate(src.splitlines(), 1):
            if "ctrlx-check: ignore" in line:
                continue
            for pattern, level, code, msg in SOURCE_RULES:
                if re.search(pattern, line):
                    hits.setdefault((level, code, msg), []).append(str(lineno))
        for (level, code, msg), lines in hits.items():  # one finding per file and rule
            rep.add(level, code, msg, "{}:{}".format(rel(f, app_dir), ",".join(lines[:8]) + ("..." if len(lines) > 8 else "")))
    if uses_dl:
        if not ctx.get("has_plug", lambda *a, **k: False)("datalayer", "datalayer"):
            rep.error("IFACE-DATALAYER", "code imports ctrlxdatalayer but plug 'datalayer' (content datalayer, "
                      "target $SNAP_DATA/.datalayer) is missing", "snap/snapcraft.yaml")
        if "ctrlx-datalayer" not in ctx.get("stage_packages", []):
            rep.error("PART-DATALAYER", "code imports ctrlxdatalayer but stage-packages lacks 'ctrlx-datalayer' "
                      "(native libcomm_datalayer)", "snap/snapcraft.yaml")

    # pyproject consistency
    pp = os.path.join(app_dir, "pyproject.toml")
    if os.path.isfile(pp):
        txt = read_text(pp)
        m = re.search(r'^name\s*=\s*"([^"]+)"', txt, re.M)
        if m and name and m.group(1) != name:
            rep.warn("PY-NAME", "pyproject name '{}' differs from snap name '{}'".format(m.group(1), name), "pyproject.toml")
        m = re.search(r'^version\s*=\s*"([^"]+)"', txt, re.M)
        if m and ctx.get("version") and m.group(1) != ctx["version"]:
            rep.warn("PY-VERSION", "pyproject version {} != snap version {}".format(m.group(1), ctx["version"]), "pyproject.toml")
        deps_block = re.search(r"dependencies\s*=\s*\[(.*?)\]", txt, re.S)
        if deps_block:
            ctx["py_deps"] = [re.split(r"[<>=!~;\[ ]", d.strip().strip('"').strip("'"))[0]
                              for d in re.findall(r"[\"']([^\"']+)[\"']", deps_block.group(1))]
        pk = re.search(r"packages\s*=\s*\[(.*?)\]", txt, re.S)
        if pk:
            declared = set(re.findall(r"[\"']([^\"']+)[\"']", pk.group(1)))
            for init in glob.glob(os.path.join(app_dir, "app", "**", "__init__.py"), recursive=True):
                pkg = os.path.relpath(os.path.dirname(init), app_dir).replace(os.sep, ".")
                if pkg not in declared:
                    rep.error("PY-PACKAGES", "package '{}' is not listed in pyproject [tool.setuptools] packages - "
                              "it will be missing in the snap".format(pkg), "pyproject.toml")

    # wrapper / shell scripts: exec bit + LF line endings (Windows checkouts break both)
    for cmd in ctx.get("commands", []):
        if cmd.startswith("bin/"):
            src_candidates = [os.path.join(app_dir, "wrapper", os.path.basename(cmd)),
                              os.path.join(app_dir, cmd)]
            for c in src_candidates:
                if os.path.isfile(c):
                    if not os.access(c, os.X_OK):
                        rep.error("SRC-EXEC-BIT", "{} is not executable - the daemon cannot start. chmod +x it "
                                  "(git: git update-index --chmod=+x)".format(rel(c, app_dir)), rel(c, app_dir))
                    break
    for f in walk_files(app_dir, {".git", "parts", "stage", "prime", "venv", ".venv"}):
        if f.endswith((".sh", ".py", ".yaml", ".json")) or os.path.basename(os.path.dirname(f)) == "wrapper":
            with open(f, "rb") as fh:
                head = fh.read(65536)
            if b"\r\n" in head:
                level = rep.error if f.endswith(".sh") or "wrapper" in f or f.endswith(".py") else rep.warn
                level("SRC-CRLF", "Windows line endings (CRLF) - scripts fail with 'python3\\r: not found'. "
                      "Convert to LF (git config core.autocrlf false; dos2unix)", rel(f, app_dir))

    # defaults
    dflt = os.path.join(app_dir, "defaults", "settings.json")
    if os.path.isfile(dflt):
        d = load_json(dflt, rep, "SETTINGS-DEFAULTS")
        if d is not None and not isinstance(d, dict):
            rep.error("SETTINGS-DEFAULTS", "defaults/settings.json root must be an object", "defaults/settings.json")


def check_build_env(rep, ctx):
    if "ctrlx-datalayer" in ctx.get("stage_packages", []) and shutil.which("apt-cache"):
        try:
            out = subprocess.run(["apt-cache", "policy", "ctrlx-datalayer"], capture_output=True, text=True, timeout=20).stdout
        except Exception:
            out = ""
        if "Candidate:" not in out or "Candidate: (none)" in out:
            rep.error("ENV-DATALAYER-DEB", "apt cannot find 'ctrlx-datalayer' - install the SDK and run "
                      "ctrlx-automation-sdk/scripts/install-ctrlx-datalayer.sh <dir-with-deb> (see docs/ctrlx/build-deploy-test.md)")
    if not shutil.which("snapcraft"):
        rep.warn("ENV-SNAPCRAFT", "snapcraft not found (SDK script install-snapcraft.sh installs 8.x)")


def check_snap_file(snap_file, rep, ctx):
    if not shutil.which("unsquashfs"):
        rep.warn("SNAPFILE-TOOL", "unsquashfs not installed - cannot inspect the built snap (sudo apt-get install squashfs-tools)")
        return
    name = ctx.get("name") or ""
    size_mb = os.path.getsize(snap_file) / 1e6
    if size_mb > 100:
        rep.warn("SNAPFILE-SIZE", "snap is {:.0f} MB - guideline: < 100 MB".format(size_mb), snap_file)
    tmp = tempfile.mkdtemp()
    try:
        subprocess.run(["unsquashfs", "-q", "-n", "-d", os.path.join(tmp, "s"), snap_file],
                       check=True, capture_output=True, timeout=300)
        root = os.path.join(tmp, "s")
        must = ["package-assets/{0}/{0}.package-manifest.json".format(name), "meta/snap.yaml"]
        for m in must:
            if not os.path.isfile(os.path.join(root, m)):
                rep.error("SNAPFILE-CONTENT", "{} missing inside the snap".format(m), snap_file)
        for cmd in ctx.get("commands", []):
            p = os.path.join(root, cmd.split()[0]) if cmd else ""
            if p and (not os.path.isfile(p) or not os.stat(p).st_mode & stat.S_IXUSR):
                rep.error("SNAPFILE-COMMAND", "command '{}' missing or not executable in the snap".format(cmd), snap_file)
        if not glob.glob(os.path.join(root, "package-assets", name, "*.fossinfo.json")):
            rep.warn("SNAPFILE-FOSS", "no fossinfo.json in package-assets of the snap", snap_file)
        if "ctrlx-datalayer" in ctx.get("stage_packages", []) and \
                not glob.glob(os.path.join(root, "usr", "lib", "*", "libcomm_datalayer.so*")):
            rep.error("SNAPFILE-DATALAYER", "libcomm_datalayer.so not inside the snap", snap_file)
    except subprocess.CalledProcessError as exc:
        rep.error("SNAPFILE-UNSQUASH", "unsquashfs failed: {}".format(exc.stderr.decode(errors="replace")[:200]), snap_file)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("app_dir")
    ap.add_argument("--release", action="store_true", help="release rules (grade stable, no TODOs, validation files)")
    ap.add_argument("--pre-build", action="store_true", help="also check the build environment (apt, snapcraft)")
    ap.add_argument("--snap", help="also inspect this built .snap file")
    ap.add_argument("--schemas", default=DEFAULT_SCHEMAS)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("-q", "--quiet", action="store_true", help="only print errors and the summary")
    args = ap.parse_args(argv)

    app_dir = os.path.abspath(args.app_dir)
    if not os.path.isdir(app_dir):
        print("not a directory: " + app_dir, file=sys.stderr)
        return 2
    rep, ctx = Report(), {}
    if check_snapcraft(app_dir, rep, ctx, args.release) is not None:
        check_sources(app_dir, rep, ctx)  # before fossinfo: collects py_deps
        check_manifest(app_dir, rep, ctx, args.schemas)
        check_fossinfo(app_dir, rep, ctx, args.schemas, args.release)
        check_validation_files(app_dir, rep, ctx, args.schemas, args.release)
        if args.pre_build:
            check_build_env(rep, ctx)
        if args.snap:
            check_snap_file(args.snap, rep, ctx)

    if args.json:
        print(json.dumps({"app": ctx.get("name"), "errors": len(rep.errors), "warnings": len(rep.warnings),
                          "items": rep.items}, indent=1))
    else:
        order = {"ERROR": 0, "WARN": 1, "INFO": 2}
        for it in sorted(rep.items, key=lambda i: order[i["level"]]):
            if args.quiet and it["level"] != "ERROR":
                continue
            where = " ({})".format(it["where"]) if it["where"] else ""
            print("{:5} {:22} {}{}".format(it["level"], it["code"], it["message"], where))
        print("\n{}: {} error(s), {} warning(s){}".format(ctx.get("name") or os.path.basename(app_dir),
                                                       len(rep.errors), len(rep.warnings),
                                                       " [release mode]" if args.release else ""))
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
