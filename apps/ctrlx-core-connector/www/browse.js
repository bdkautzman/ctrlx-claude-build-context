// Core Connector - Data Layer browser of one core (browse.html?core=<name>, "_local" = this device).
// Tree on the left (children loaded on expand, simple values shown and refreshed while visible),
// details of the selected node on the right (metadata, live value, write), KVD nodes add/delete,
// help menu with the tutorial (also started by the start page's tutorial: ?tour=1).
// Paths are relative to the core's root; the server maps them to devices/remotes/cm-<name>/...
"use strict";

(function () {
  const { $, api, el, icon, showBanner, webUrl, coreTitle, setupHelp, LOCAL_ID } = window.CC;

  const coreId = new URLSearchParams(location.search).get("core") || LOCAL_ID;
  const CORE_API = "/cores/" + encodeURIComponent(coreId);
  let readMs = 1000;           // settings browser.refreshMs: tree values and the selected node's value
  const MAX_REFRESH_ROWS = 300;
  const ICONS = { Folder: "folder", Variable: "variable", Resource: "resource", Collection: "collection", Type: "type", Method: "method" };

  let canWrite = false;
  let core = null;
  let kvd = { available: false, nodes: new Set(), error: null };
  const nodes = new Map();   // path -> {entry, row, group, valEl, tagsEl, expanded, loaded, depth}
  let selected = null;       // path of the selected node
  let detail = null;         // {path, data, valueEl, timeEl}
  let pending = 0;

  // ---- busy marker for tools/ui_check.py ----------------------------------
  function busy(delta) {
    pending = Math.max(0, pending + delta);
    document.body.dataset.uiReady = pending ? "false" : "true";
  }
  async function track(promise) { busy(1); try { return await promise; } finally { busy(-1); } }

  function q(path) { return "?path=" + encodeURIComponent(path); }
  function parentOf(path) { const i = path.lastIndexOf("/"); return i < 0 ? "" : path.slice(0, i); }
  function nameOf(path) { return path.slice(path.lastIndexOf("/") + 1); }
  function errText(e) { return e.message || String(e); }

  // ---- value formatting ----------------------------------------------------
  function fmtScalar(v) {
    if (typeof v === "string") return v === "" ? '""' : v;
    if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(Math.round(v * 1e6) / 1e6);
    return String(v);
  }
  function fmtTreeValue(e) {
    if (e.valueError) return { text: e.valueError, cls: "val val-error" };
    if (e.valueType) return { text: e.valueType === "flatbuffers" ? "{…}" : e.valueType, cls: "val val-muted" };
    if (!("value" in e)) return { text: "", cls: "val" };
    let text = Array.isArray(e.value) ? "[" + e.value.map(fmtScalar).join(", ") + (e.valueCut ? ", …" : "") + "]"
      : fmtScalar(e.value) + (e.valueCut ? "…" : "");
    if (e.unit) text += " " + e.unit;
    return { text, cls: "val" };
  }

  // ---- header (core name, state, web UI link, KVD) ----------------------------
  async function loadCore() {
    try {
      const cores = await api("GET", "/cores");
      core = cores.find((c) => c.name === coreId) || null;
    } catch (e) { return; }
    if (!core) { showBanner("There is no core named “" + coreId + "” (any more). Go back to the list of cores."); return; }
    const title = coreTitle(core);
    document.title = title + " - Data Layer - Core Connector";
    $("core-title").textContent = title + (core.hostname ? " · " + core.hostname : "");
    const state = core.state || "connecting";
    $("core-state").className = "pill pill-" + ({ connected: "ok", connecting: "unknown" }[state] || "warn");
    $("core-state").textContent = state;
    $("core-state").title = core.error ? core.error + (core.result ? " (" + core.result + ")" : "") : "Data Layer connection";
    $("web-link").href = webUrl(core);
    const op = core.operatingState;
    $("op-state").hidden = !op;
    $("op-state").textContent = op || "";
    $("op-state").className = "pill op-pill op-" + String(op || "").toLowerCase();
  }

  async function loadStatus() {
    try { canWrite = !!(await api("GET", "/status")).canWrite; } catch (e) { /* banner from api() */ }
  }

  // the read interval (settings, readable for every user of the app) - checked again every 5 s
  async function loadReadInterval() {
    try {
      const ms = (await api("GET", "/settings")).browser.refreshMs;
      if (Number.isFinite(ms) && ms > 0) readMs = ms;
    } catch (e) { /* keep the last one */ }
  }
  // fn every readMs, the next run planned when this one is done (a slow core never piles up reads)
  function every(fn) {
    (function next() { setTimeout(async () => { try { await fn(); } finally { next(); } }, readMs); })();
  }

  async function loadKvd() {
    try {
      const k = await api("GET", CORE_API + "/kvd");
      kvd = { available: !!k.available, nodes: new Set(k.nodes || []), error: k.error || null };
    } catch (e) {
      kvd = { available: false, nodes: new Set(), error: null };
    }
    $("kvd-pill").hidden = !kvd.available;
    $("kvd-pill").title = kvd.error ? "Key Value Database: " + kvd.error
      : "Nodes can be added on this core: the Key Value Database app runs on it";
    $("kvd-pill").classList.toggle("pill-warn", !!kvd.error);
    const addBtn = $("btn-add-node");
    addBtn.hidden = !(kvd.available && canWrite);
    addBtn.disabled = !!kvd.error;
    addBtn.title = kvd.error ? "Not possible: " + kvd.error : "Add a node to the Key Value Database of this core";
    for (const [path, n] of nodes) setTags(n, path);
  }

  // ---- tree ----------------------------------------------------------------
  // a node a user added (defined in the Key Value Database's nodes.json): a person icon
  function userMark() {
    const title = "Added by a user (Key Value Database)";
    return el("span", { class: "tag-kvd", title, role: "img", "aria-label": title }, icon("user", "ic-fill"));
  }

  function setTags(n, path) {
    const e = n.entry;
    n.tagsEl.replaceChildren();
    if (e.type) n.tagsEl.append(el("span", { class: "tag", text: e.type.startsWith("types/") ? e.type.split("/").pop() : e.type, title: e.type }));
    if (kvd.nodes.has(path)) n.tagsEl.append(userMark());
    if (e.operations && e.operations.write) n.tagsEl.append(el("span", { class: "tag tag-w", text: "W", title: "Writable" }));
  }

  function makeNode(entry, depth) {
    const caret = el("span", { class: "caret" + (entry.hasChildren ? "" : " caret-leaf") }, icon("chevron"));
    const valEl = el("span", { class: "val" });
    const tagsEl = el("span", { class: "tags" });
    const row = el("div", { class: "row", role: "treeitem", tabindex: "-1", "aria-level": String(depth + 1),
      "aria-selected": "false", "data-path": entry.path, title: entry.path },
    caret, icon(ICONS[entry.nodeClass] || "variable", "node-ic"), el("span", { class: "name", text: entry.name }), valEl, tagsEl);
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
    const f = fmtTreeValue(e);
    n.valEl.textContent = f.text;
    n.valEl.className = f.cls;
    n.read = "value" in e || !!e.valueError || !!e.valueType;
  }

  function removeSubtree(path) {
    const prefix = path ? path + "/" : "";
    for (const p of Array.from(nodes.keys())) if (p !== path && p.startsWith(prefix)) nodes.delete(p);
  }

  async function loadChildren(path) {
    const container = path ? nodes.get(path).group : $("tree");
    const depth = path ? nodes.get(path).depth + 1 : 0;
    let data;
    try {
      data = await track(api("GET", CORE_API + "/children" + q(path)));
    } catch (e) {
      container.replaceChildren(el("div", { class: "row row-msg error", style: "--depth:" + depth, text: "Cannot browse: " + errText(e) }));
      return false;
    }
    removeSubtree(path);
    const items = data.children.map((entry) => { const n = makeNode(entry, depth); nodes.set(entry.path, n); return n.wrap; });
    if (!items.length) items.push(el("div", { class: "row row-msg muted", style: "--depth:" + depth, text: "no child nodes" }));
    if (data.truncated) items.push(el("div", { class: "row row-msg muted", style: "--depth:" + depth, text: "only the first " + data.children.length + " nodes are shown" }));
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

  // reload a folder (or the root) and open again what was open below it
  async function reload(path) {
    const prefix = path ? path + "/" : "";
    const open = Array.from(nodes.entries()).filter(([p, n]) => n.expanded && p.startsWith(prefix)).map(([p]) => p).sort();
    if (path && !nodes.has(path)) return reload(parentOf(path));
    if (path) nodes.get(path).loaded = false;
    await (path ? expand(path) : loadChildren(""));
    for (const p of open) if (nodes.has(p)) await expand(p);
  }

  function collapseAll() { for (const [p, n] of nodes) if (n.expanded) collapse(p); }

  // filter: loaded nodes whose path contains the text, plus their ancestors (also inside collapsed folders)
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
      n.group.hidden = !(n.expanded || Array.from(keep).some((k) => k.startsWith(p + "/")));
    }
    $("tree-status").textContent = hits + " of " + nodes.size + " loaded nodes match";
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
      const values = await api("POST", CORE_API + "/values", { paths });
      for (const v of values) {
        const n = nodes.get(v.path);
        if (!n) continue;
        const e = Object.assign({}, v, { unit: n.entry.unit });
        setValue(n, e);
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

  // a click on the empty part of the tree: nothing selected (Add node then defaults to the top level)
  function deselect() {
    if (selected === null) return;
    selected = null;
    detail = null;
    markSelected(null);
    $("goto").value = "";
    $("detail").replaceChildren(el("p", { class: "muted", text: "Select a node in the tree." }));
    if ($("tree").contains(document.activeElement)) $("tree").focus();   // no focus ring on the old row
  }

  async function select(path) {
    selected = path;
    markSelected(path);
    $("goto").value = path;
    const n = nodes.get(path);
    if (n) n.row.scrollIntoView({ block: "nearest" });
    const box = $("detail");
    let data;
    try {
      data = await track(api("GET", CORE_API + "/node" + q(path)));
    } catch (e) {
      if (selected !== path) return;
      detail = null;
      box.replaceChildren(el("h2", { class: "detail-name", text: path ? nameOf(path) : "(root)" }),
        el("p", { class: "error", text: "Cannot read this node: " + errText(e) }));
      return;
    }
    if (selected !== path) return;   // another node was clicked meanwhile
    renderDetail(data);
  }

  function valueText(d) {
    if (d.valueError) return d.valueError + (d.valueErrorDetail ? " - " + d.valueErrorDetail : "");
    if (!("value" in d)) return "";
    if (d.value !== null && typeof d.value === "object") return JSON.stringify(d.value, null, 2);
    return fmtScalar(d.value);
  }

  function kvRow(dl, label, value) {
    if (value === undefined || value === null || value === "") return;
    dl.append(el("dt", { text: label }), el("dd", {}, value instanceof Node ? value : document.createTextNode(String(value))));
  }

  function renderDetail(d) {
    const m = d.metadata || {};
    const ops = m.operations || {};
    const box = $("detail");
    const head = el("div", { class: "detail-head" }, icon(ICONS[m.nodeClass] || "variable", "node-ic"),
      el("h2", { class: "detail-name", text: d.path ? nameOf(d.path) : "(root)" }),
      m.nodeClass ? el("span", { class: "pill pill-unknown", text: m.nodeClass }) : null,
      (d.type || d.valueType) ? el("span", { class: "tag", text: d.type || d.valueType }) : null,
      kvd.nodes.has(d.path) ? userMark() : null);
    const parts = [head];
    if (m.description) parts.push(el("p", { class: "detail-desc", text: m.description }));

    // value
    let valueEl = null, timeEl = null;
    if ("value" in d || d.valueError) {
      valueEl = el("pre", { class: "value" + (d.valueError ? " error" : ""), text: valueText(d) });
      timeEl = el("span", { class: "muted small", text: "" });
      parts.push(el("section", { class: "value-box" },
        el("div", { class: "value-head" }, el("h3", { text: "Value" }), m.unit ? el("span", { class: "muted", text: m.unit }) : null, timeEl),
        valueEl));
    }

    // write
    if (ops.write && d.path) {
      if (canWrite) parts.push(writeForm(d));
      else parts.push(el("p", { class: "muted small", text: "Writing needs the permission “Manage Core Connector”." }));
    }

    // metadata
    const dl = el("dl", { class: "kv meta" });
    kvRow(dl, "Address", d.path || "(root)");
    kvRow(dl, "Node class", m.nodeClass);
    kvRow(dl, "Unit", m.unit);
    kvRow(dl, "Display name", m.displayName);
    kvRow(dl, "Display format", m.displayFormat && m.displayFormat !== "Auto" ? m.displayFormat : "");
    const opsEl = el("span", { class: "ops" }, ...["read", "write", "create", "delete", "browse"].map((k) =>
      el("span", { class: "op" + (ops[k] ? " op-on" : ""), text: k, title: k + (ops[k] ? " allowed" : " not allowed") })));
    kvRow(dl, "Operations", opsEl);
    kvRow(dl, "Read type", m.readType);
    kvRow(dl, "Write type", m.writeType && m.writeType !== m.readType ? m.writeType : "");
    kvRow(dl, "Child nodes", d.childCount ? String(d.childCount) : "");
    if (m.descriptionUrl) kvRow(dl, "Documentation", m.descriptionUrl);
    for (const x of m.extensions || []) kvRow(dl, x.key, x.value);
    parts.push(el("h3", { text: "Metadata" }), dl);
    const refs = (m.references || []).filter((r) => !["readType", "writeType"].includes(r.type));
    if (refs.length) {
      parts.push(el("details", { class: "refs" }, el("summary", { text: "References (" + refs.length + ")" }),
        el("dl", { class: "kv" }, ...refs.flatMap((r) => [el("dt", { text: r.type }), el("dd", { class: "mono", text: r.targetAddress })]))));
    }

    // KVD: delete what is defined in nodes.json
    if (canWrite && kvd.available && kvd.nodes.has(d.path)) {
      const del = el("button", { type: "button", class: "btn btn-danger btn-with-icon", id: "btn-delete-node" }, icon("trash"), el("span", { text: "Delete KVD node" }));
      del.onclick = () => deleteNode(d.path);
      parts.push(el("div", { class: "actions kvd-actions" }, del,
        el("span", { class: "muted small", text: "Removes its definition from kvd/nodes.json of this core." })));
    }
    box.replaceChildren(...parts);
    detail = { path: d.path, data: d, valueEl, timeEl, live: !!valueEl && !(m.nodeClass === "Type") };
  }

  function writeForm(d) {
    const m = d.metadata;
    const vtype = m.writeType || m.readType || d.valueType || "";
    const isObject = (d.value !== null && typeof d.value === "object") || vtype.startsWith("types/") || vtype.startsWith("ar");
    let input;
    if (vtype === "bool8") {
      input = el("select", { id: "w-value", "aria-label": "New value" },
        el("option", { value: "true", text: "true" }), el("option", { value: "false", text: "false" }));
      input.value = d.value === true ? "true" : "false";
    } else if (isObject) {
      input = el("textarea", { id: "w-value", rows: "6", spellcheck: "false", "aria-label": "New value (JSON)" });
      input.value = "value" in d ? JSON.stringify(d.value, null, 2) : "";
    } else {
      input = el("input", { id: "w-value", type: "text", spellcheck: "false", autocomplete: "off", "aria-label": "New value" });
      input.value = "value" in d && d.value !== null ? String(d.value) : "";
    }
    const state = el("span", { class: "muted small", id: "w-state", "aria-live": "polite" });
    const btn = el("button", { type: "submit", class: "btn btn-primary", id: "btn-write", text: "Write" });
    const form = el("form", { class: "write-form", novalidate: true },
      el("h3", { text: "Write" }), el("div", { class: "write-row" }, input, btn), state,
      el("div", { class: "muted small", text: isObject ? "JSON" + (vtype ? " · " + vtype : "") : vtype }));
    form.onsubmit = async (ev) => {
      ev.preventDefault();
      btn.disabled = true;
      state.className = "muted small";
      state.textContent = "Writing…";
      try {
        const res = await track(api("PUT", CORE_API + "/node" + q(d.path), { value: input.value }));
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
    detail.valueEl.textContent = valueText(v);
    detail.valueEl.classList.toggle("error", !!v.valueError);
    detail.timeEl.textContent = "read " + new Date().toLocaleTimeString();
    const n = nodes.get(detail.path);
    if (n && n.read && !v.valueError && (v.value === null || typeof v.value !== "object" || Array.isArray(v.value))) {
      setValue(n, { value: v.value, unit: n.entry.unit });
    }
  }

  let detailBusy = false;
  async function refreshDetail() {
    if (!detail || !detail.live || detailBusy || document.hidden) return;
    const path = detail.path;
    detailBusy = true;
    try {
      const v = await api("GET", CORE_API + "/value" + q(path));
      if (detail && detail.path === path) updateDetailValue(v);
    } catch (e) { /* shown on the next selection */ } finally { detailBusy = false; }
  }

  // go to an address: open every folder on the way, then select it
  async function gotoPath(path) {
    path = path.trim().replace(/^\/+|\/+$/g, "");
    const parts = path ? path.split("/") : [];
    for (let i = 1; i < parts.length; i++) {
      const p = parts.slice(0, i).join("/");
      if (!nodes.has(p)) break;
      await expand(p);
    }
    await select(path);
  }

  // ---- KVD: add / delete ---------------------------------------------------------
  const dialog = $("node-dialog");
  const VALUE_HELP = {
    bool8: "true or false", string: "any text", float32: "a number", float64: "a number",
    arstring: 'JSON, e.g. ["a", "b"]', arbool8: "e.g. true, false", arfloat32: "e.g. 1.5, 2", arfloat64: "e.g. 1.5, 2",
  };
  function valueHelp(t) {
    if (VALUE_HELP[t]) return VALUE_HELP[t];
    const big = /int64$/.test(t) ? " - at most ±9007199254740991 here (KVD reads numbers as float64); write larger values afterwards" : "";
    if (t.startsWith("ar")) return "comma-separated, e.g. 1, 2, 3 (or a JSON array)" + big;
    return "an integer" + big;
  }
  function nodeAddress() {
    const f = $("node-form").elements;
    const parent = f.parent.value.trim().replace(/^\/+|\/+$/g, "");
    const name = f.name.value.trim();
    return parent ? parent + "/" + name : name;
  }
  function syncNodeForm() {
    const f = $("node-form").elements;
    const isVar = f.nodeClass.value === "Variable";
    for (const field of $("node-form").querySelectorAll(".var-only")) field.hidden = !isVar;
    $("n-value-help").textContent = "Empty = 0 / false / \"\". " + valueHelp(f.type.value);
    $("n-address").textContent = nodeAddress() || "…";
  }
  function showNodeErrors(errors) {
    const list = $("node-errors");
    list.replaceChildren(...(errors || []).map((msg) => el("li", { text: msg })));
    list.hidden = !errors || !errors.length;
    const fieldOf = { address: "name" };
    for (const input of $("node-form").querySelectorAll("[name]")) {
      const bad = (errors || []).some((msg) => { const k = msg.split(":")[0]; return (fieldOf[k] || k) === input.name; });
      input.closest(".field").classList.toggle("invalid", bad);
    }
  }
  function openAddNode() {
    $("node-form").reset();
    let parent = "";
    if (selected !== null && detail && detail.path === selected) {
      const m = detail.data.metadata || {};
      const folderish = ["Folder", "Collection"].includes(m.nodeClass) || detail.data.childCount > 0;
      parent = folderish ? selected : parentOf(selected);
    }
    $("n-parent").value = parent;
    $("n-core").textContent = core ? coreTitle(core) : coreId;
    $("node-state").textContent = "";
    showNodeErrors([]);
    syncNodeForm();
    dialog.showModal();
    $("n-name").focus();
  }
  async function submitAddNode(ev) {
    ev.preventDefault();
    const f = $("node-form").elements;
    const body = {
      address: nodeAddress(), nodeClass: f.nodeClass.value, type: f.type.value, value: f.value.value,
      unit: f.unit.value, description: f.description.value, readOnly: f.readOnly.checked,
    };
    if (body.nodeClass === "Folder") { delete body.type; delete body.value; delete body.unit; delete body.readOnly; }
    $("btn-node-ok").disabled = true;
    $("node-state").textContent = "Adding… (KVD creates the node)";
    try {
      await track(api("POST", CORE_API + "/kvd/nodes", body));
      dialog.close();
      await loadKvd();
      await reload("");   // folders may have appeared on the way; open ones stay open
      await gotoPath(body.address);
    } catch (e) {
      showNodeErrors((e.data && e.data.errors && e.data.errors.length) ? e.data.errors : [errText(e)]);
      $("node-state").textContent = "";
    } finally { $("btn-node-ok").disabled = false; }
  }
  async function deleteNode(path) {
    if (!confirm("Delete the KVD node “" + path + "” on " + (core ? coreTitle(core) : coreId) + "?\n\n" +
      "Its definition is removed from kvd/nodes.json. KVD keeps the last value in its database: a node " +
      "created again at this address starts with it. " +
      "All KVD nodes of this core are unavailable for about a second while KVD restarts them.")) return;
    try {
      const res = await track(api("DELETE", CORE_API + "/kvd/nodes?address=" + encodeURIComponent(path)));
      await loadKvd();
      await reload("");   // an implicit folder disappears with its last node
      if (res.note) alert(res.note);
      let next = path;
      while (next && !nodes.has(next)) next = parentOf(next);
      await select(next);
    } catch (e) { alert("Cannot delete: " + errText(e)); }
  }

  // ---- start ---------------------------------------------------------------------
  $("tree").addEventListener("keydown", onTreeKey);
  $("tree").addEventListener("click", (ev) => { if (!ev.target.closest(".row")) deselect(); });
  $("tree").addEventListener("focus", () => { if (selected !== null && nodes.has(selected)) nodes.get(selected).row.focus(); });
  $("filter").oninput = applyFilter;
  $("btn-collapse").onclick = collapseAll;
  $("btn-reload").onclick = () => { reload(""); loadKvd(); };
  $("goto-form").onsubmit = (ev) => { ev.preventDefault(); gotoPath($("goto").value); };
  $("btn-copy").onclick = async () => {
    try { await navigator.clipboard.writeText($("goto").value); $("btn-copy").title = "Copied"; }
    catch (e) { $("goto").select(); }
  };
  $("btn-add-node").onclick = openAddNode;
  $("node-form").onsubmit = submitAddNode;
  $("node-form").oninput = syncNodeForm;
  $("node-form").onchange = syncNodeForm;
  $("btn-node-cancel").onclick = () => dialog.close();

  // ---- tutorial (help menu) ------------------------------------------------------------------
  const EXAMPLE_NODE = "framework/metrics/system/cpu-utilisation-percent";   // on ctrlX OS 3.6 and 4.x
  const shownRow = (sel) => [...$("tree").querySelectorAll(sel)].map((e) => e.closest(".row"))
    .find((r) => r && r.getClientRects().length) || null;
  const kvdEditing = () => kvd.available && canWrite;
  const readText = () => readMs % 1000 ? readMs + " ms" : readMs === 1000 ? "second" : readMs / 1000 + " seconds";
  const firstKvdNode = () => [...kvd.nodes].filter((p) => !p.includes("*")).sort()[0];   // not KVD's "a/**" patterns
  const help = setupHelp([
    { title: "Data Layer browser",
      text: () => "The Data Layer of " + (core ? coreTitle(core) : "this core") + ", like the Data Layer view of " +
        "the ctrlX web UI. Step through the tour with the arrows (or the ← → keys); the control of each step " +
        "blinks. Everything stays usable while the tour is open." },
    { title: "Tree", target: () => $("tree"),
      text: () => "Every node of the core. The arrow in front of a folder opens it (also a double-click, or the → " +
        "key); its children are loaded then. Simple values are shown and read every " + readText() + " while " +
        "they are visible (Settings → Data Layer browser). Marks: the type, W (writable), and a person for " +
        "a node a user added (Key Value Database)." },
    { title: "Filter and reload", target: () => [$("filter"), $("btn-collapse"), $("btn-reload")],
      text: "Filter the loaded nodes by name, close all folders, or load the tree again." },
    { title: "Address", target: () => $("goto-form"),
      text: "Type an address and press Enter or “Go”: the folders on the way open and the node is selected. " +
        "The button next to it copies the address of the selected node." },
    { title: "Details", target: () => $("detail"), act: () => gotoPath(EXAMPLE_NODE),
      text: () => "The selected node: its value (read every " + readText() + "), node class, operations, types and references. " +
        "Click the empty part of the tree to deselect it. “Show me” selects the CPU load of the core." },
    { title: "Write", when: () => canWrite,
      target: () => document.querySelector(".write-form") || shownRow(".tag-w"),
      canAct: () => !!shownRow(".tag-w"), act: () => select(shownRow(".tag-w").dataset.path),
      text: "A writable node (tag W) gets a “Write” field in its details: numbers, true/false and text as " +
        "they are, arrays and objects as JSON. “Write” sends the value to the core at once. “Show me” " +
        "selects a writable node - it writes nothing." },
    { title: "Add node", target: () => $("btn-add-node"),
      text: () => "“Add node” is there for every core with the Key Value Database app (KVD) installed - its " +
        "status shows “Editable”: add a variable - any type, with initial value, unit, description, " +
        "read-only - or a folder. KVD restarts its nodes for every change: all of them are unavailable for " +
        "about a second." + (!canWrite ? " Adding needs the permission “Manage Core Connector”."
          : !kvd.available ? " This core has no KVD: the button is not shown here." : "") },
    { title: "Delete KVD node", when: kvdEditing,
      target: () => $("btn-delete-node") || shownRow(".tag-kvd"),
      canAct: () => !!firstKvdNode(), act: () => gotoPath(firstKvdNode()),
      text: "Nodes with the person mark were added by a user (KVD's nodes.json); “Delete KVD node” in their " +
        "details removes one. KVD keeps its last value: a node added again at that address starts with it. " +
        "“Show me” selects such a node - it deletes nothing." },
    { title: "This core", target: () => [$("core-state"), $("op-state"), $("kvd-pill")],
      text: "The connection to this core, its operating state, and “Editable” when nodes can be added (the " +
        "Key Value Database app runs on it)." },
    { title: "Web UI", target: () => $("web-link"), text: "Opens the ctrlX web UI of this core in a new tab." },
    { title: "Back", target: () => $("btn-back"), text: "Back to the list of all cores." },
    { title: "Help", target: () => $("btn-help"), text: "Start this tour again, or open the app's page on GitHub." },
  ]);

  (async function init() {
    busy(1);
    try {
      await Promise.all([loadStatus(), loadReadInterval()]);
      await Promise.all([loadCore(), loadChildren(""), loadKvd()]);
      const start = location.hash.length > 1 ? decodeURIComponent(location.hash.slice(1)) : "";
      if (start) await gotoPath(start);
    } finally { busy(-1); }
    if (help.handedOver) help.start();   // continued from the start page's tutorial
    setInterval(() => { loadCore(); loadReadInterval(); }, 5000);
    every(refreshTreeValues);
    every(refreshDetail);
  })();
})();
