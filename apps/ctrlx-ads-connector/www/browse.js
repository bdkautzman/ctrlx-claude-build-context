// ADS Connector - PLC browser of one TwinCAT controller (browse.html?controller=<name>[#<TwinCAT path>]).
// Tree on the left: namespaces (MAIN, GVL, ...) -> variables -> struct members / array elements, loaded on expand;
// values in the tree, read while visible. Details of the selected variable on the right: type, place in the PLC,
// live value, write (operate role), Data Layer nodes (configure role: add / remove). Paths use the TwinCAT syntax
// (MAIN.stAxis.fPos, GVL.aValues[3], GVL.aGrid[1,2]). Layout and keys like Core Connector's Data Layer browser.
"use strict";

(function () {
  const { $, el, icon, api, can, showBanner } = window.Kit;
  const { stateClass, browseUrl, ctrlPill } = window.ADS;

  const ctrlName = new URLSearchParams(location.search).get("controller") || "";
  const CTRL_API = "/controllers/" + encodeURIComponent(ctrlName);
  let readMs = 1000;           // settings browser.refreshMs: tree values and the selected variable's value
  const MAX_REFRESH_ROWS = 300;
  const ICONS = { folder: "folder", struct: "struct", array: "array", string: "text", wstring: "text", pointer: "pointer" };

  let ctrl = null;
  const nodes = new Map();   // path -> {entry, row, group, valEl, tagsEl, expanded, loaded, depth}
  let selected = null;       // path of the selected row
  let detail = null;         // {path, data, valueEl, timeEl}
  let mapped = new Map();    // lower-case TwinCAT path -> [Data Layer addresses] of this controller
  let pending = 0;

  // ---- busy marker for tools/ui_check.py ----------------------------------
  function busy(delta) {
    pending = Math.max(0, pending + delta);
    document.body.dataset.uiReady = pending ? "false" : "true";
  }
  async function track(promise) { busy(1); try { return await promise; } finally { busy(-1); } }

  function q(path) { return "?path=" + encodeURIComponent(path); }
  // TwinCAT paths: MAIN.stAxis.fPos -> MAIN.stAxis, GVL.aValues[3] -> GVL.aValues
  function parentOf(path) {
    if (path.endsWith("]")) return path.slice(0, path.lastIndexOf("["));
    const i = path.lastIndexOf(".");
    return i < 0 ? "" : path.slice(0, i);
  }
  function nameOf(path) {
    if (path.endsWith("]")) return path.slice(path.lastIndexOf("["));
    return path.slice(path.lastIndexOf(".") + 1);
  }
  function errText(e) { return e.data && e.data.detail ? e.data.detail : e.message || String(e); }
  // the server's default Data Layer address for a path (plc_nodes.default_address)
  function defaultAddress(path) {
    const text = path.replace(/\[([^\]]*)\]/g, (m, i) => "." + i.replace(/ /g, "").replace(/,/g, "_"));
    return text.split(".").filter(Boolean).map((s) => s.replace(/[^A-Za-z0-9_-]/g, "_").slice(0, 64)).join("/");
  }

  // ---- value formatting ----------------------------------------------------
  function fmtScalar(v) {
    if (typeof v === "string") return v === "" ? '""' : v;
    if (v === null || v === undefined) return "–";
    return String(v);
  }
  function fmtTreeValue(e) {
    if (e.valueError) return { text: e.valueError, cls: "val val-error" };
    if (e.kind === "struct") return { text: "{…}", cls: "val val-muted" };
    if (e.kind === "pointer") return { text: "pointer", cls: "val val-muted" };
    if (!("value" in e)) return { text: "", cls: "val" };
    let text = Array.isArray(e.value) ? "[" + e.value.map(fmtScalar).join(", ") + (e.valueCut ? ", …" : "") + "]"
      : fmtScalar(e.value);
    if (e.enum) { const n = e.enum.find((x) => x[1] === e.value); if (n) text = n[0] + " (" + e.value + ")"; }
    if (e.unit && !Array.isArray(e.value)) text += " " + e.unit;
    return { text, cls: "val" };
  }

  // ---- header (controller, states) ---------------------------------------------
  async function loadController() {
    let list;
    try { list = await api("GET", "/controllers"); } catch (e) { return; }
    ctrl = list.find((c) => c.name === ctrlName) || null;
    if (!ctrl) { showBanner("There is no controller named “" + ctrlName + "” (any more). Go back to the list of controllers."); return; }
    document.title = ctrl.name + " - PLC - ADS Connector";
    $("ctrl-title").textContent = ctrl.name + (ctrl.hostname && ctrl.hostname !== ctrl.name ? " · " + ctrl.hostname : "");
    const pill = ctrlPill(ctrl.state || "connecting");
    $("ctrl-state").className = pill.cls;
    $("ctrl-state").textContent = pill.text;
    $("ctrl-state").title = ctrl.error || (ctrl.host + " · " + (ctrl.netId || "") + " · port " + ctrl.amsPort);
    const sys = ctrl.systemStateName;
    $("system-state").hidden = !sys;
    $("system-state").textContent = "TwinCAT " + (sys || "");
    $("system-state").className = "pill state-pill " + stateClass(sys);
    const plc = ctrl.plcError ? "not running" : ctrl.plcStateName;
    $("plc-state").hidden = !plc;
    $("plc-state").textContent = "PLC " + (plc || "");
    $("plc-state").className = "pill state-pill " + (ctrl.plcError ? "st-other" : stateClass(plc));
    $("plc-state").title = ctrl.plcError || ("PLC runtime " + ctrl.amsPort + (ctrl.plcName ? ": " + ctrl.plcName : ""));
    showBanner(ctrl.state !== "connected" ? ctrl.name + " is not connected" + (ctrl.error ? ": " + ctrl.error : "") +
      (ctrl.localNetId ? " - the controller needs a route for AMS Net ID " + ctrl.localNetId + " at " + ctrl.localIp + "." : "")
      : ctrl.plcError || "");
  }

  async function loadStatus() {
    const title = document.title;
    const s = await Kit.refreshStatus();   // role, role pill, colors, About (it sets the app's title - this page keeps its own)
    document.title = title;
    if (s && Number.isFinite(s.browserRefreshMs) && s.browserRefreshMs > 0) readMs = s.browserRefreshMs;
  }
  // fn every readMs, the next run planned when this one is done (a slow controller never piles up reads)
  function every(fn) {
    (function next() { setTimeout(async () => { try { await fn(); } finally { next(); } }, readMs); })();
  }

  async function loadMapped() {
    let list;
    try { list = await api("GET", "/nodes?controller=" + encodeURIComponent(ctrlName)); } catch (e) { return; }
    mapped = new Map();
    for (const n of list) {
      const key = n.symbol.toLowerCase();
      if (!mapped.has(key)) mapped.set(key, []);
      mapped.get(key).push(n.address);
    }
    for (const [path, n] of nodes) setTags(n, path);
  }

  // ---- tree ----------------------------------------------------------------
  function setTags(n, path) {
    const e = n.entry;
    n.tagsEl.replaceChildren();
    if (e.type) n.tagsEl.append(el("span", { class: "tag", text: e.type, title: e.type + (e.size ? " · " + e.size + " bytes" : "") }));
    if (mapped.has(path.toLowerCase())) {
      n.tagsEl.append(el("span", { class: "tag tag-dl", text: "DL", title: "In the Data Layer: " + mapped.get(path.toLowerCase()).join(", ") }));
    }
    if (e.readonly) n.tagsEl.append(el("span", { class: "tag tag-ro", text: "RO", title: "Read-only in the PLC (constant)" }));
  }

  function makeNode(entry, depth) {
    const caret = el("span", { class: "caret" + (entry.hasChildren ? "" : " caret-leaf") }, icon("chevron"));
    const valEl = el("span", { class: "val" });
    const tagsEl = el("span", { class: "tags" });
    const row = el("div", { class: "row", role: "treeitem", tabindex: "-1", "aria-level": String(depth + 1),
      "aria-selected": "false", "data-path": entry.path, title: entry.path + (entry.comment ? "\n" + entry.comment : "") },
    caret, icon(ICONS[entry.kind] || "variable", "node-ic"), el("span", { class: "name", text: entry.name }), valEl, tagsEl);
    row.style.setProperty("--depth", depth);
    if (entry.hasChildren) row.setAttribute("aria-expanded", "false");
    const group = el("div", { class: "group", role: "group", hidden: true });
    const wrap = el("div", { class: "tn", role: "none" }, row, group);
    const n = { entry, row, group, valEl, tagsEl, wrap, caret, expanded: false, loaded: false, depth };
    caret.onclick = (ev) => { ev.stopPropagation(); toggle(entry.path); };
    row.onclick = () => select(entry.path);
    row.ondblclick = () => toggle(entry.path);
    setValue(n, entry);
    setTags(n, entry.path);
    return n;
  }

  function setValue(n, e) {
    const f = fmtTreeValue(Object.assign({}, n.entry, e));
    n.valEl.textContent = f.text;
    n.valEl.className = f.cls;
    n.read = "value" in e || !!e.valueError;
  }

  function removeSubtree(path) {
    for (const p of Array.from(nodes.keys())) {
      if (p !== path && (path === "" || p.startsWith(path + ".") || p.startsWith(path + "["))) nodes.delete(p);
    }
  }

  async function loadChildren(path) {
    const container = path ? nodes.get(path).group : $("tree");
    const depth = path ? nodes.get(path).depth + 1 : 0;
    let data;
    try {
      data = await track(api("GET", CTRL_API + "/children" + q(path)));
    } catch (e) {
      container.replaceChildren(el("div", { class: "row row-msg error", style: "--depth:" + depth,
        text: (e.status === 503 ? "" : "Cannot browse: ") + errText(e) }));
      return false;
    }
    removeSubtree(path);
    const items = data.children.map((entry) => { const n = makeNode(entry, depth); nodes.set(entry.path, n); return n.wrap; });
    if (!items.length) items.push(el("div", { class: "row row-msg muted", style: "--depth:" + depth, text: path ? "no members" : "no symbols" }));
    if (data.truncated) items.push(el("div", { class: "row row-msg muted", style: "--depth:" + depth, text: "only the first " + data.children.length + " elements are shown - type an index into the Variable field for the others" }));
    container.replaceChildren(...items);
    if (path) {
      const n = nodes.get(path);
      n.loaded = true;
      if (!data.children.length) { n.entry.hasChildren = false; n.caret.classList.add("caret-leaf"); n.row.removeAttribute("aria-expanded"); }
    }
    if (selected && nodes.has(selected)) markSelected(selected);
    applyFilter();
    return true;
  }

  async function expand(path) {
    const n = nodes.get(path);
    if (!n || !n.entry.hasChildren) return;
    if (!n.loaded) {
      n.group.hidden = false;
      n.group.replaceChildren(el("div", { class: "row row-msg muted", style: "--depth:" + (n.depth + 1), text: "Loading…" }));
      await loadChildren(path);
    }
    n.expanded = true;
    n.group.hidden = false;
    n.row.setAttribute("aria-expanded", "true");
    n.row.classList.add("open");
    applyFilter();
  }

  function collapse(path) {
    const n = nodes.get(path);
    if (!n) return;
    n.expanded = false;
    n.group.hidden = true;
    if (n.entry.hasChildren) n.row.setAttribute("aria-expanded", "false");
    n.row.classList.remove("open");
    applyFilter();
  }

  function toggle(path) { const n = nodes.get(path); if (n) (n.expanded ? collapse(path) : expand(path)); }

  // reload the tree and open again what was open
  async function reloadAll() {
    const open = Array.from(nodes.entries()).filter(([, n]) => n.expanded).map(([p]) => p)
      .sort((a, b) => a.length - b.length);
    await loadChildren("");
    for (const p of open) if (nodes.has(p)) await expand(p);
    await loadMapped();
  }

  function collapseAll() { for (const [p, n] of nodes) if (n.expanded) collapse(p); }

  // filter: loaded variables whose path contains the text, plus their ancestors
  function applyFilter() {
    const text = $("filter").value.trim().toLowerCase();
    if (!text) {
      for (const n of nodes.values()) { n.wrap.hidden = false; n.group.hidden = !n.expanded; }
      $("tree-status").textContent = "";
      return;
    }
    const keep = new Set();
    let hits = 0;
    for (const p of nodes.keys()) {
      if (!p.toLowerCase().includes(text)) continue;
      hits++;
      for (let a = p; a; a = parentOf(a)) keep.add(a);
    }
    for (const [p, n] of nodes) {
      n.wrap.hidden = !keep.has(p);
      n.group.hidden = !(n.expanded || Array.from(keep).some((k) => k !== p && parentOf(k) === p));
    }
    $("tree-status").textContent = hits + " of " + nodes.size + " loaded variables match";
  }

  // values of the visible tree rows, every readMs
  let refreshing = false;
  async function refreshTreeValues() {
    if (refreshing || document.hidden) return;
    const box = $("tree").getBoundingClientRect();
    const paths = [];
    for (const [p, n] of nodes) {
      if (!n.read || !n.row.offsetParent) continue;
      const r = n.row.getBoundingClientRect();
      if (r.bottom < box.top || r.top > box.bottom) continue;
      paths.push(p);
      if (paths.length >= MAX_REFRESH_ROWS) break;
    }
    if (!paths.length) return;
    refreshing = true;
    try {
      const values = await api("POST", CTRL_API + "/values", { paths });
      for (const v of values) {
        const n = nodes.get(v.path);
        if (n) setValue(n, v);
      }
    } catch (e) { /* next round */ } finally { refreshing = false; }
  }

  // ---- keyboard in the tree --------------------------------------------------
  function visibleRows() { return Array.from($("tree").querySelectorAll(".row[data-path]")).filter((r) => r.offsetParent); }
  function onTreeKey(ev) {
    const rows = visibleRows();
    if (!rows.length) return;
    let i = rows.findIndex((r) => r.dataset.path === selected);
    const path = i >= 0 ? rows[i].dataset.path : null;
    const n = path !== null ? nodes.get(path) : null;
    if (ev.key === "ArrowDown") i = Math.min(rows.length - 1, i + 1);
    else if (ev.key === "ArrowUp") i = Math.max(0, i - 1);
    else if (ev.key === "Home") i = 0;
    else if (ev.key === "End") i = rows.length - 1;
    else if (ev.key === "ArrowRight" && n) {
      if (n.entry.hasChildren && !n.expanded) { expand(path); ev.preventDefault(); return; }
      i = Math.min(rows.length - 1, i + 1);
    } else if (ev.key === "ArrowLeft" && n) {
      if (n.expanded) { collapse(path); ev.preventDefault(); return; }
      const parent = parentOf(path);
      if (parent && nodes.has(parent)) { select(parent); nodes.get(parent).row.focus(); }
      ev.preventDefault();
      return;
    } else if (ev.key === "Enter" && n) { toggle(path); ev.preventDefault(); return; }
    else return;
    ev.preventDefault();
    if (i < 0) i = 0;
    select(rows[i].dataset.path);
    rows[i].focus();
  }

  // ---- selection and details ---------------------------------------------------
  function markSelected(path) {
    for (const r of $("tree").querySelectorAll(".row.selected")) { r.classList.remove("selected"); r.setAttribute("aria-selected", "false"); }
    const n = nodes.get(path);
    if (n) { n.row.classList.add("selected"); n.row.setAttribute("aria-selected", "true"); }
  }

  function deselect() {
    if (selected === null) return;
    selected = null;
    detail = null;
    markSelected(null);
    $("goto").value = "";
    $("detail").replaceChildren(el("p", { class: "muted", text: "Select a variable in the tree." }));
    if ($("tree").contains(document.activeElement)) $("tree").focus();
  }

  async function select(path) {
    selected = path;
    markSelected(path);
    $("goto").value = path;
    const n = nodes.get(path);
    if (n) n.row.scrollIntoView({ block: "nearest" });
    let data;
    try {
      data = await track(api("GET", CTRL_API + "/symbol" + q(path)));
    } catch (e) {
      if (selected !== path) return;
      detail = null;
      $("detail").replaceChildren(el("h2", { class: "detail-name", text: path ? nameOf(path) : "(root)" }),
        el("p", { class: "error", text: "Cannot read this variable: " + errText(e) }));
      return;
    }
    if (selected !== path) return;   // another row was clicked meanwhile
    renderDetail(data);
  }

  function valueText(d) {
    if (d.valueError) return d.valueError;
    if (!("value" in d)) return "";
    if (d.value !== null && typeof d.value === "object") return JSON.stringify(d.value, null, 2);
    if (d.enum) { const n = d.enum.find((x) => x[1] === d.value); if (n) return n[0] + " (" + d.value + ")"; }
    return fmtScalar(d.value) + (d.unit ? " " + d.unit : "");
  }

  function kvRow(dl, label, value) {
    if (value === undefined || value === null || value === "") return;
    dl.append(el("dt", { text: label }), el("dd", {}, value instanceof Node ? value : document.createTextNode(String(value))));
  }
  function hex(n) { return "0x" + Number(n).toString(16).toUpperCase(); }

  function canAdd(d) { return !!d && (!!d.dlType || (d.leafCount > 0 && d.leafCount <= 500)); }
  function renderDetail(d) {
    const box = $("detail");
    const kind = d.kind || "variable";
    const isMapped = mapped.has((d.path || "").toLowerCase());   // the DL mark: the variable itself is a node
    const head = el("div", { class: "detail-head" }, icon(ICONS[kind] || "variable", "node-ic"),
      el("h2", { class: "detail-name", text: d.path ? d.name || nameOf(d.path) : "(root)" }),
      el("span", { class: "pill pill-unknown", text: kind === "folder" ? "namespace" : kind }),
      d.type ? el("span", { class: "tag", text: d.type }) : null,
      isMapped ? el("span", { class: "tag tag-dl", text: "DL", title: "In the Data Layer" }) : null,
      d.readonly ? el("span", { class: "tag tag-ro", text: "RO", title: "Read-only in the PLC (constant)" }) : null);
    const parts = [head];
    if (d.comment) parts.push(el("p", { class: "detail-desc", text: d.comment }));

    let valueEl = null, timeEl = null;
    if (kind !== "folder" && ("value" in d || d.valueError)) {
      valueEl = el("pre", { class: "value" + (d.valueError ? " error" : ""), text: valueText(d) });
      timeEl = el("span", { class: "muted small", text: "" });
      parts.push(el("section", { class: "value-box" },
        el("div", { class: "value-head" }, el("h3", { text: "Value" }), d.unit ? el("span", { class: "muted", text: d.unit }) : null, timeEl),
        valueEl));
    }
    if (d.writable) {
      if (can("operate")) parts.push(writeForm(d));
      else parts.push(el("p", { class: "muted small", text: "Writing needs the permission “Operate ADS Connector”." }));
    }

    const dl = el("dl", { class: "kv meta" });
    kvRow(dl, kind === "folder" ? "Namespace" : "Path", d.path || "(root)");
    if (kind === "folder") {
      kvRow(dl, "Entries", d.childCount);
      kvRow(dl, "Values", d.leafCount > 500 ? "more than 500" : d.leafCount);
    } else {
      kvRow(dl, "PLC type", d.type);
      kvRow(dl, "Data Layer type", d.dlType || (d.leafCount ? "- (its " + (d.leafCount > 500 ? "500+" : d.leafCount) + " values: one node each)" : "- (not possible)"));
      kvRow(dl, "Size", d.size + (d.size === 1 ? " byte" : " bytes"));
      kvRow(dl, "Index group / offset", hex(d.ig) + " / " + hex(d.io) + (d.bit != null ? " · bit " + d.bit : ""));
      if (d.dims) kvRow(dl, "Array", d.dims.map((x) => x[0] + ".." + (x[0] + x[1] - 1)).join(", ") + " of " + d.elemType + " (" + d.count + " elements)");
      kvRow(dl, "Members", d.members);
      kvRow(dl, "Max. characters", d.maxChars);
      if (d.enum) kvRow(dl, "Values", d.enum.map((x) => x[0] + " = " + x[1]).join(", "));
      kvRow(dl, "Unit", d.unit);
      if (d.symbol && d.symbol !== d.path) kvRow(dl, "Symbol", d.symbol);
      for (const [k, v] of d.attributes || []) kvRow(dl, "{attribute '" + k + "'}", v || "(set)");
    }
    parts.push(el("h3", { text: kind === "folder" ? "Namespace" : "Variable" }), dl);

    // Data Layer: the section only once the variable (or a value below it) is a node - its address(es) with a copy
    // button and "Remove"; before that only "Add to Data Layer" (configure) - user's notes 2026-11-25
    const addrs = d.mapped || [];
    if (addrs.length) {
      const full = (a) => Kit.PREFIX.slice(1) + "/" + ADS.DL_BASE + "/" + ctrlName + "/" + a;
      const fields = el("div", { class: "dl-addrs" + (addrs.length > 3 ? " dl-addrs-many" : "") }, ...addrs.map((a) => addrField(full(a))));
      const dlBox = el("section", { class: "dl-actions" }, el("h3", { text: "Data Layer" }),
        el("dl", { class: "kv meta" }, el("dt", { text: addrs.length > 1 ? "Addresses (" + addrs.length + ")" : "Address" }),
          el("dd", {}, fields)));
      if (can("configure")) {
        const del = el("button", { type: "button", class: "btn btn-danger btn-with-icon", id: "btn-detail-remove" }, icon("trash"),
          el("span", { text: addrs.length > 1 ? "Remove these " + addrs.length + " nodes from the Data Layer" : "Remove from Data Layer" }));
        del.onclick = () => removeNodes(d.path, addrs);
        dlBox.append(el("div", { class: "actions" }, del));
      }
      parts.push(dlBox);
    } else if (can("configure") && canAdd(d)) {
      const add = el("button", { type: "button", class: "btn btn-primary btn-with-icon", id: "btn-detail-add",
        title: d.dlType ? "Make " + d.path + " a node in the ctrlX Data Layer"
          : "One Data Layer node for each of the " + d.leafCount + " values of " + (d.path || "the root") },
        icon("plus"), el("span", { text: d.dlType ? "Add to Data Layer" : "Add its " + d.leafCount + " values to the Data Layer" }));
      add.onclick = openAddNode;
      parts.push(el("div", { class: "actions dl-add" }, add));
    }

    box.replaceChildren(...parts);
    detail = { path: d.path, data: d, valueEl, timeEl, live: !!valueEl };
  }

  // a Data Layer address: read-only field + copy button
  function addrField(text) {
    const input = el("input", { type: "text", class: "addr-field", readonly: true, value: text, spellcheck: "false",
      "aria-label": "Data Layer address" });
    const copy = el("button", { type: "button", class: "tool tool-bare btn-copy-addr", title: "Copy the address",
      "aria-label": "Copy the address " + text }, icon("copy"));
    copy.onclick = async () => {
      try { await navigator.clipboard.writeText(text); copy.title = "Copied"; }
      catch (e) { input.focus(); input.select(); }   // no clipboard (http, older browser): selected for Ctrl+C
    };
    return el("div", { class: "addr-row" }, input, copy);
  }

  function writeForm(d) {
    const isArray = !!d.dims;
    let input;
    if (d.dlType === "bool8") {
      input = el("select", { id: "w-value", "aria-label": "New value" },
        el("option", { value: "true", text: "TRUE" }), el("option", { value: "false", text: "FALSE" }));
      input.value = d.value === true ? "true" : "false";
    } else if (d.enum) {
      input = el("select", { id: "w-value", "aria-label": "New value" }, ...d.enum.map((x) => el("option", { value: String(x[1]), text: x[0] + " (" + x[1] + ")" })));
      input.value = String(d.value);
    } else if (isArray) {
      input = el("textarea", { id: "w-value", rows: "4", spellcheck: "false", "aria-label": "New value (JSON array)" });
      input.value = "value" in d ? JSON.stringify(d.value) : "";
    } else {
      input = el("input", { id: "w-value", type: "text", spellcheck: "false", autocomplete: "off", "aria-label": "New value" });
      input.value = "value" in d && d.value !== null ? String(d.value) : "";
    }
    const state = el("span", { class: "muted small", id: "w-state", "aria-live": "polite" });
    const btn = el("button", { type: "submit", class: "btn btn-primary", id: "btn-write", text: "Write" });
    const hint = isArray ? "JSON array, all " + d.count + " elements" : d.maxChars != null ? "text, at most " + d.maxChars + " characters"
      : d.dlType;
    const form = el("form", { class: "write-form", novalidate: true },
      el("h3", { text: "Write" }), el("div", { class: "write-row" }, input, btn), state,
      el("div", { class: "muted small", text: hint }));
    form.onsubmit = async (ev) => {
      ev.preventDefault();
      btn.disabled = true;
      state.className = "muted small";
      state.textContent = "Writing…";
      try {
        const res = await track(api("PUT", CTRL_API + "/symbol" + q(d.path), { value: input.value }));
        state.textContent = "Written " + new Date().toLocaleTimeString();
        if (detail && detail.path === d.path) updateDetailValue(res);
      } catch (e) {
        state.className = "error small";
        state.textContent = errText(e);
      } finally { btn.disabled = false; }
    };
    return form;
  }

  function updateDetailValue(v) {
    if (!detail || !detail.valueEl) return;
    detail.valueEl.textContent = valueText(Object.assign({}, detail.data, { valueError: undefined }, v));
    detail.valueEl.classList.toggle("error", !!v.valueError);
    detail.timeEl.textContent = "read " + new Date().toLocaleTimeString();
    const n = nodes.get(detail.path);
    if (n && n.read && !v.valueError && (v.value === null || typeof v.value !== "object" || Array.isArray(v.value))) setValue(n, v);
  }

  let detailBusy = false;
  async function refreshDetail() {
    if (!detail || !detail.live || detailBusy || document.hidden) return;
    const path = detail.path;
    detailBusy = true;
    try {
      const v = await api("GET", CTRL_API + "/value" + q(path));
      if (detail && detail.path === path) updateDetailValue(v);
    } catch (e) { /* shown on the next selection */ } finally { detailBusy = false; }
  }

  // go to a variable: the server spells the path (case), the namespaces / structs on the way open, then it is selected
  async function gotoPath(path) {
    path = path.trim();
    if (!path) return;
    let canonical = path;
    try { canonical = (await track(api("GET", CTRL_API + "/symbol" + q(path)))).path; } catch (e) { /* select() shows it */ }
    const chain = [];
    for (let p = parentOf(canonical); p; p = parentOf(p)) chain.unshift(p);
    for (const p of chain) { if (!nodes.has(p)) break; await expand(p); }
    await select(canonical);
  }

  // ---- Data Layer: add / remove ---------------------------------------------------------
  const dialog = $("node-dialog");
  const writableSwitch = Kit.switchInput(false, { id: "n-writable" });
  $("n-writable-slot").replaceWith(writableSwitch.node);
  let planTimer = null;
  function showNodeErrors(errors) {
    const list = $("node-errors");
    list.replaceChildren(...(errors || []).map((msg) => el("li", { text: msg })));
    list.hidden = !errors || !errors.length;
    $("n-address").closest(".field").classList.toggle("invalid", (errors || []).some((m) => m.startsWith("address")));
  }
  function nodeBody() {
    const d = detail.data;
    return { controller: ctrlName, path: d.path, address: $("n-address").value.trim().replace(/^\/+|\/+$/g, ""),
      writable: writableSwitch.value, description: $("n-description").value };
  }
  async function updatePlan() {
    const d = detail && detail.data;
    if (!d) return;
    const many = !d.dlType;
    $("n-preview").textContent = Kit.PREFIX.slice(1) + "/" + ADS.DL_BASE + "/" + ctrlName + "/" + ($("n-address").value.trim() || "…") + (many ? "/…" : "");
    try {
      const plan = await api("POST", "/nodes/plan", nodeBody());
      const list = $("n-plan");
      list.hidden = !many;
      if (many) {
        const shown = plan.nodes.slice(0, 8).map((n) => el("li", { text: n.address + "  (" + n.type + ")" }));
        if (plan.nodes.length > 8) shown.push(el("li", { class: "muted", text: "… " + (plan.nodes.length - 8) + " more" }));
        list.replaceChildren(...shown);
      }
      showNodeErrors(plan.conflicts.length ? ["address: already a node - " + plan.conflicts.slice(0, 3).join(", ") +
        (plan.conflicts.length > 3 ? " …" : "")] : []);
    } catch (e) {
      showNodeErrors((e.data && e.data.errors) || [errText(e)]);
    }
  }
  function openAddNode() {
    const d = detail && detail.data;
    if (!canAdd(d)) return;
    $("node-form").reset();
    writableSwitch.value = false;
    $("n-symbol").textContent = d.path || "(root)";
    $("n-ctrl").textContent = ctrlName;
    $("n-what").textContent = d.dlType ? "one node of type " + d.dlType
      : d.leafCount + " nodes - one for each value below it, with the same structure";
    $("n-address").value = defaultAddress(d.path);
    $("n-description").value = d.dlType ? (d.comment || "").slice(0, 200) : "";
    for (const f of $("node-form").querySelectorAll(".one-only")) f.hidden = !d.dlType;
    $("node-state").textContent = "";
    showNodeErrors([]);
    updatePlan();
    dialog.showModal();
    $("n-address").focus();
  }
  async function submitAddNode(ev) {
    ev.preventDefault();
    $("btn-node-ok").disabled = true;
    $("node-state").textContent = "Adding…";
    try {
      const res = await track(api("POST", "/nodes", nodeBody()));
      dialog.close();
      await loadMapped();
      if (detail) await select(detail.path);
      $("tree-status").textContent = res.added.length + " Data Layer node(s) added";
    } catch (e) {
      showNodeErrors((e.data && e.data.errors && e.data.errors.length) ? e.data.errors : [errText(e)]);
    } finally { $("btn-node-ok").disabled = false; $("node-state").textContent = ""; }
  }
  async function removeNodes(path, addrs) {
    if (!confirm("Remove the Data Layer node(s) of " + path + "?\n\n" + addrs.map((a) => ADS.DL_BASE + "/" + ctrlName + "/" + a).join("\n") +
      "\n\nThe PLC variable is not changed.")) return;
    try {
      await track(api("POST", "/nodes/delete", { controller: ctrlName, addresses: addrs }));
      await loadMapped();
      await select(path);
    } catch (e) { alert("Cannot remove: " + errText(e)); }
  }

  // ---- start ---------------------------------------------------------------------
  $("tree").addEventListener("keydown", onTreeKey);
  $("tree").addEventListener("click", (ev) => { if (!ev.target.closest(".row")) deselect(); });
  $("tree").addEventListener("focus", () => { if (selected !== null && nodes.has(selected)) nodes.get(selected).row.focus(); });
  $("filter").oninput = applyFilter;
  $("btn-collapse").onclick = collapseAll;
  $("btn-reload").onclick = reloadAll;
  $("goto-form").onsubmit = (ev) => { ev.preventDefault(); gotoPath($("goto").value); };
  $("node-form").onsubmit = submitAddNode;
  $("n-address").oninput = () => { clearTimeout(planTimer); planTimer = setTimeout(updatePlan, 300); };
  $("btn-node-cancel").onclick = () => dialog.close();

  // ---- tutorial (help menu) ------------------------------------------------------------------
  const shownRow = (sel) => [...$("tree").querySelectorAll(sel)].map((e) => e.closest(".row"))
    .find((r) => r && r.getClientRects().length) || null;
  const readText = () => readMs % 1000 ? readMs + " ms" : readMs === 1000 ? "second" : readMs / 1000 + " seconds";
  const firstLeaf = () => [...nodes.entries()].find(([, n]) => n.entry.dlType && n.row.getClientRects().length);
  const help = Kit.setupHelp([
    { title: "PLC browser",
      text: () => "The PLC variables of " + (ctrl ? ctrl.name : "this controller") + " (TwinCAT symbols). Step through " +
        "the tour with the arrows (or the ← → keys); the control of each step blinks. Everything stays usable while " +
        "the tour is open." },
    { title: "Tree", target: () => $("tree"),
      text: () => "Namespaces (MAIN, the GVLs, ...), their variables, the members of structs and function blocks and " +
        "the elements of arrays. The arrow in front opens one (also a double-click, or the → key). Values are shown " +
        "and read every " + readText() + " while they are visible (Settings → Browser). Marks: the PLC type, " +
        "DL = in the Data Layer, RO = a constant." },
    { title: "Filter and reload", target: () => [$("filter"), $("btn-collapse"), $("btn-reload")],
      text: "Filter the loaded variables by name, close everything, or load the symbols again (after a download to " +
        "the PLC the app loads them again by itself)." },
    { title: "Variable", target: () => $("goto-form"),
      text: "Type a TwinCAT path, e.g. MAIN.fbAxis.fPosition or GVL.aValues[3], and press Enter: the tree " +
        "opens on the way and the variable is selected." },
    { title: "Details", target: () => $("detail"), canAct: () => !!firstLeaf(), act: () => select(firstLeaf()[0]),
      text: () => "The selected variable: its value (read every " + readText() + "), PLC type, Data Layer type, size and " +
        "place in the PLC (index group / offset), comment and attributes. “Show me” selects a variable." },
    { title: "Write", when: () => can("operate"), target: () => document.querySelector(".write-form") || $("detail"),
      text: "A variable with a value of its own (not a struct) gets a “Write” field: numbers, TRUE/FALSE, enum " +
        "values, text, arrays as JSON with all their elements. “Write” sends it to the PLC at once - like a " +
        "write in TwinCAT's online view." },
    { title: "Add to Data Layer", when: () => can("configure"), target: () => $("btn-detail-add") || $("detail"),
      text: "“Add to Data Layer” below the details makes the selected variable a node in the ctrlX Data Layer of this device (connections/<controller>/<address>), " +
        "with the type of the PLC variable. For a struct, a function block, an array of structs or a whole " +
        "namespace it adds one node for each value below it. Writable nodes write into the PLC." },
    { title: "Data Layer nodes", target: () => shownRow(".tag-dl") || $("detail"),
      text: "Variables with the DL mark are in the Data Layer. Their details show the Data Layer address (the " +
        "button next to it copies it)" + (can("configure") ? " and “Remove from Data Layer”." : ".") + " All nodes are on the start page, tab " +
        "“Data Layer nodes”." },
    { title: "This controller", target: () => [$("ctrl-state"), $("system-state"), $("plc-state")],
      text: "The connection to the controller, the state of TwinCAT (RUN, CONFIG) and of the PLC runtime (RUN, STOP)." },
    { title: "Back", target: () => $("btn-back"), text: "Back to the list of all controllers." },
    { title: "Help", target: () => $("btn-help"), text: "Start this tour again, or open the app's page on GitHub." },
  ]);

  (async function init() {
    busy(1);
    try {
      if (!ctrlName) { showBanner("No controller given - go back to the list of controllers."); return; }
      await loadStatus();
      await Promise.all([loadController(), loadChildren(""), loadMapped()]);
      const start = location.hash.length > 1 ? decodeURIComponent(location.hash.slice(1)) : "";
      if (start) await gotoPath(start);
    } finally { busy(-1); }
    if (help.handedOver) help.start();   // continued from the start page's tutorial
    setInterval(() => { loadController(); }, 3000);
    every(refreshTreeValues);
    every(refreshDetail);
  })();
})();
