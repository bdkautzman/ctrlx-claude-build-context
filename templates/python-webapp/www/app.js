// Template App web UI - plain JS, no build step, no CDN (devices are often offline).
//
// Auth: the ctrlX menu opens "/<snap>/?token=${bearertoken}". We move the token from the URL
// into sessionStorage (so it is not left in history/bookmarks) and send it as
// "Authorization: Bearer" on every API call. The reverse proxy validates it because
// "/<snap>/api" is listed as restricted in the package manifest.
"use strict";

(function () {
  const PREFIX = "/" + location.pathname.split("/").filter(Boolean)[0]; // "/<snap-name>"
  const API = PREFIX + "/api/v1";
  const TOKEN_KEY = PREFIX.slice(1) + ".token";

  // ---- token ---------------------------------------------------------------
  function captureToken() {
    const url = new URL(location.href);
    const fromUrl = url.searchParams.get("token") || url.searchParams.get("access_token");
    if (fromUrl) {
      try { sessionStorage.setItem(TOKEN_KEY, fromUrl); } catch (e) { /* storage blocked */ }
      url.searchParams.delete("token");
      url.searchParams.delete("access_token");
      history.replaceState(null, "", url.pathname + url.search + url.hash);
      return fromUrl;
    }
    try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch (e) { return ""; }
  }
  const token = captureToken();

  async function api(method, path, body) {
    const headers = { "Accept": "application/json" };
    if (token) headers["Authorization"] = "Bearer " + token;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const res = await fetch(API + path, {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin", cache: "no-store",
    });
    let data = null;
    const text = await res.text();
    if (text) { try { data = JSON.parse(text); } catch (e) { data = { title: text }; } }
    if (res.status === 401) showBanner("Session expired or no token - please reopen this app from the ctrlX menu.");
    if (!res.ok) { const err = new Error((data && (data.detail || data.title)) || res.statusText); err.status = res.status; err.data = data; throw err; }
    return data;
  }

  // ---- helpers -------------------------------------------------------------
  const $ = (id) => document.getElementById(id);
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (v === true) node.setAttribute(k, "");
      else if (v !== false && v !== null && v !== undefined) node.setAttribute(k, v);
    }
    for (const c of children) if (c) node.append(c);
    return node;
  }
  function showBanner(msg) { const b = $("banner"); b.textContent = msg; b.hidden = !msg; }
  function fmtDuration(s) {
    const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
    return (d ? d + "d " : "") + (h || d ? h + "h " : "") + m + "m " + (s % 60) + "s";
  }
  function humanize(key) { return key.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/^./, (c) => c.toUpperCase()); }

  let canWrite = false;

  // ---- status --------------------------------------------------------------
  async function refreshStatus() {
    try {
      const s = await api("GET", "/status");
      canWrite = !!s.canWrite;
      document.title = s.title;
      $("app-title").textContent = s.title;
      $("app-version").textContent = /^\d/.test(s.version) ? "v" + s.version : s.version;
      $("st-uptime").textContent = fmtDuration(s.uptimeSeconds);
      $("st-source").textContent = s.settings.source;
      $("st-persisted").textContent = s.settings.persisted ? "yes" : (s.settings.storageAvailable ? "no" : "no (app data not available)");
      $("st-nodes").textContent = s.datalayer.registered;
      const conn = $("conn");
      if (s.datalayer.connected) { conn.className = "pill pill-ok"; conn.textContent = "Data Layer: connected"; }
      else { conn.className = "pill pill-warn"; conn.textContent = s.datalayer.enabled ? "Data Layer: connecting…" : "Data Layer: off"; }
      const errText = s.settings.error || s.datalayer.error || "";
      $("st-error").textContent = errText; $("st-error").hidden = !errText;
      if (!token || s) showBanner("");
    } catch (e) {
      if (e.status !== 401) showBanner("Cannot reach the app: " + e.message);
    }
  }

  // ---- values --------------------------------------------------------------
  let editing = null; // name of the value currently edited (do not overwrite while typing)
  async function refreshValues() {
    if (editing) return;
    let values;
    try { values = await api("GET", "/values"); } catch (e) { return; }
    if (editing) return; // an edit may have started while the request was running
    const body = $("values-body");
    body.replaceChildren();
    for (const [name, v] of Object.entries(values)) {
      const full = String(v.value);
      const valueCell = el("td", { class: "val", text: full.length > 80 ? full.slice(0, 77) + "…" : full, title: full });
      const actionCell = el("td");
      if (v.writable && canWrite) {
        const btn = el("button", { type: "button", class: "btn btn-small", text: "Edit" });
        // onclick (not addEventListener): startEdit replaces it with "Set" - with two listeners a
        // click on "Set" ran startEdit again first and wrote the old value back
        btn.onclick = () => startEdit(name, v, valueCell, btn);
        actionCell.append(btn);
      }
      body.append(el("tr", { title: v.description || "" },
        el("td", { class: "addr", text: v.address }), el("td", { text: v.type }), valueCell, actionCell));
    }
  }
  function startEdit(name, v, cell, btn) {
    editing = name;
    const input = v.type === "bool8"
      ? el("select", {}, el("option", { value: "true", text: "true" }), el("option", { value: "false", text: "false" }))
      : el("input", { type: v.type === "string" ? "text" : "number", step: "any" });
    input.value = String(v.value);
    cell.replaceChildren(input);
    input.focus();
    btn.textContent = "Set";
    const done = async (commit) => {
      if (commit) {
        let value = input.value;
        if (v.type === "bool8") value = value === "true";
        else if (v.type !== "string") value = Number(value);
        try { await api("PUT", "/values/" + encodeURIComponent(name), { value }); }
        catch (e) { alert("Write rejected: " + e.message); }
      }
      editing = null;
      refreshValues();
    };
    btn.onclick = () => done(true);
    input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") done(true); if (ev.key === "Escape") done(false); });
  }

  // ---- settings form (generated from defaults + rules) ---------------------
  let meta = null;
  let loaded = null;

  function buildFields(container, defaults, path) {
    for (const [key, def] of Object.entries(defaults)) {
      const full = path ? path + "." + key : key;
      const rule = (meta.rules && meta.rules[full]) || {};
      const label = rule.label || humanize(key);
      if (rule.hidden) continue; // value is kept unchanged (readForm starts from the loaded settings)
      if (def !== null && typeof def === "object" && !Array.isArray(def)) {
        const inner = el("div", { class: "fields" });
        container.append(el("fieldset", {}, el("legend", { text: rule.label || humanize(key) }), inner));
        buildFields(inner, def, full);
        continue;
      }
      const id = "f-" + full.replace(/\./g, "-");
      let input;
      if (Array.isArray(rule.enum)) {
        input = el("select", { id });
        for (const opt of rule.enum) input.append(el("option", { value: String(opt), text: String(opt) }));
      } else if (typeof def === "boolean") {
        input = el("input", { id, type: "checkbox" });
      } else if (typeof def === "number") {
        const isInt = (meta.types || {})[full] === "integer";
        input = el("input", { id, type: "number", step: isInt ? "1" : "any", min: rule.min, max: rule.max });
      } else if (Array.isArray(def)) {
        input = el("input", { id, type: "text", placeholder: "JSON array" });
      } else {
        input = el("input", { id, type: "text", maxlength: rule.maxLength });
      }
      input.dataset.path = full;
      input.dataset.kind = Array.isArray(def) ? "array" : typeof def;
      const field = typeof def === "boolean"
        ? el("div", { class: "field" }, el("label", { class: "checkbox", for: id }, input, document.createTextNode(label)))
        : el("div", { class: "field" }, el("label", { for: id, text: label }), input);
      if (rule.help) field.append(el("div", { class: "help", text: rule.help }));
      container.append(field);
    }
  }

  function getAt(obj, path) { return path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj); }
  function setAt(obj, path, value) {
    const parts = path.split("."); let o = obj;
    for (const p of parts.slice(0, -1)) { if (typeof o[p] !== "object" || o[p] === null) o[p] = {}; o = o[p]; }
    o[parts[parts.length - 1]] = value;
  }

  function fillForm(settings) {
    for (const input of document.querySelectorAll("#settings-fields [data-path]")) {
      const v = getAt(settings, input.dataset.path);
      if (input.type === "checkbox") input.checked = !!v;
      else if (input.dataset.kind === "array") input.value = JSON.stringify(v ?? []);
      else input.value = v ?? "";
      input.disabled = !canWrite;
      input.closest(".field").classList.remove("invalid");
    }
    for (const id of ["btn-save", "btn-revert", "btn-defaults"]) $(id).disabled = !canWrite;
  }

  function readForm() {
    const result = JSON.parse(JSON.stringify(loaded)); // keeps unknown keys
    for (const input of document.querySelectorAll("#settings-fields [data-path]")) {
      let v;
      if (input.type === "checkbox") v = input.checked;
      else if (input.dataset.kind === "number") v = input.value === "" ? NaN : Number(input.value);
      else if (input.dataset.kind === "array") { try { v = JSON.parse(input.value || "[]"); } catch (e) { v = input.value; } }
      else v = input.value;
      setAt(result, input.dataset.path, v);
    }
    return result;
  }

  function showErrors(errors) {
    const list = $("settings-errors");
    list.replaceChildren(...(errors || []).map((m) => el("li", { text: m })));
    list.hidden = !errors || !errors.length;
    for (const input of document.querySelectorAll("#settings-fields [data-path]")) {
      const bad = (errors || []).some((m) => m.startsWith(input.dataset.path + ":"));
      input.closest(".field").classList.toggle("invalid", bad);
    }
  }

  async function loadSettings() {
    try {
      if (!meta) {
        meta = await api("GET", "/settings/meta");
        const container = $("settings-fields");
        container.replaceChildren();
        buildFields(container, meta.defaults, "");
      }
      loaded = await api("GET", "/settings");
      fillForm(loaded);
      showErrors([]);
    } catch (e) {
      $("settings-fields").replaceChildren(el("p", { class: "error", text: "Cannot load settings: " + e.message }));
    }
  }

  async function saveSettings(ev) {
    ev.preventDefault();
    $("save-state").textContent = "Saving…";
    try {
      loaded = await api("PUT", "/settings", readForm());
      fillForm(loaded);
      showErrors([]);
      $("save-state").textContent = "Saved " + new Date().toLocaleTimeString();
    } catch (e) {
      showErrors((e.data && e.data.errors) || [e.message]);
      $("save-state").textContent = "Not saved";
    }
    refreshStatus();
  }

  async function resetDefaults() {
    if (!confirm("Reset all settings to their defaults?")) return;
    try { loaded = await api("POST", "/settings/reset"); fillForm(loaded); showErrors([]); $("save-state").textContent = "Defaults restored"; }
    catch (e) { showErrors([e.message]); }
  }

  // ---- start ---------------------------------------------------------------
  $("settings-form").addEventListener("submit", saveSettings);
  $("btn-revert").addEventListener("click", () => { fillForm(loaded); showErrors([]); $("save-state").textContent = ""; });
  $("btn-defaults").addEventListener("click", resetDefaults);

  (async function init() {
    await refreshStatus();
    await loadSettings();
    await refreshValues();
    if (location.hash === "#settings") $("settings").scrollIntoView();
    setInterval(refreshStatus, 5000);
    setInterval(refreshValues, 2000);
  })();
})();
