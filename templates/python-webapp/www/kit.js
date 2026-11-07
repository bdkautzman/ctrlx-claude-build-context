// Shared UI parts of the kit's apps (templates/python-webapp/www/kit.js) - plain JS, no build step, no CDN.
// Load it before app.js; the app's own code uses window.Kit. Keep app code out of this file, so a newer
// kit.js from the template can replace it (docs/ctrlx/web-ui-and-reverse-proxy.md "Shared UI parts").
//
//   Kit.api(method, path, body)       JSON API call below /<snap>/api/v1 with the user's token; at most 4 at
//                                     once (more queue up: the reverse proxy answered 502 to ~50 parallel
//                                     requests, gotchas #102); errors: Error with .status and .data (Problem JSON)
//   Kit.download(path, fallbackName)  file download from the API (an <a href> can't send the token)
//   Kit.upload(method, path, file, onProgress)   file upload with progress (server: _receive_file)
//   Kit.refreshStatus()               GET /status -> title, version, Data Layer pill, role pill, role-based
//                                     visibility; returns the status for the app's own fields
//   Kit.can("operate"|"configure")    the user's role allows it (from the last /status)
//   [data-role="operate"|"configure"] elements only users with that role see (hidden for the others)
//   Kit.setupSettings({onSaved})      settings drawer: form generated from /settings/meta (configure role)
//   Kit.setupHelp(steps)              help menu (app page, tutorial) and the step-by-step tutorial
//   Kit.ready(true|false)             <body data-ui-ready> for tools/ui_check.py
//   Kit.poll(fn, ms)                  run fn every ms while the page is visible
//   Kit.$, Kit.el, Kit.icon, Kit.showBanner, Kit.fmtDuration, Kit.humanize
//
// Auth: the ctrlX menu opens "/<snap>/?token=${bearertoken}". The token moves from the URL into
// sessionStorage (not left in history/bookmarks) and is sent as "Authorization: Bearer" on every API call.
// The reverse proxy validates it because "/<snap>/api" is listed as restricted in the package manifest.
"use strict";

window.Kit = (function () {
  const PREFIX = "/" + location.pathname.split("/").filter(Boolean)[0]; // "/<snap-name>"
  const API = PREFIX + "/api/v1";
  const TOKEN_KEY = PREFIX.slice(1) + ".token";
  const ROLES = ["view", "operate", "configure"];
  const MAX_PARALLEL = 4;

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

  // ---- DOM helpers -------------------------------------------------------------------------------
  const $ = (id) => document.getElementById(id);
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on") && typeof v === "function") node[k] = v;
      else if (v === true) node.setAttribute(k, "");
      else if (v !== false && v !== null && v !== undefined) node.setAttribute(k, v);
    }
    for (const c of children) if (c !== null && c !== undefined && c !== false) node.append(c);
    return node;
  }
  // icons: <svg class="ic"><use href="#i-NAME"/></svg>; the symbols are added to the page below
  const ICONS = {
    sliders: '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
    close: '<path d="M6 6l12 12M18 6L6 18"/>',
    external: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    help: '<circle cx="12" cy="12" r="9"/><path d="M9.6 9.3a2.5 2.5 0 0 1 4.8.9c0 1.7-2.4 2.2-2.4 3.8"/><path d="M12 17.3h.01"/>',
    play: '<circle cx="12" cy="12" r="9"/><path d="M10 8.5l5 3.5-5 3.5z"/>',
    left: '<path d="M19 12H5M11 6l-6 6 6 6"/>',
    right: '<path d="M5 12h14M13 6l6 6-6 6"/>',
    download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
    upload: '<path d="M12 20V9M7 14l5-5 5 5M5 4h14"/>',
  };
  (function addIconSymbols() {
    const sprite = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    sprite.setAttribute("aria-hidden", "true");
    sprite.style.cssText = "position:absolute;width:0;height:0;overflow:hidden";
    sprite.innerHTML = Object.entries(ICONS)
      .map(([name, body]) => '<symbol id="i-' + name + '" viewBox="0 0 24 24">' + body + "</symbol>").join("");
    document.body.prepend(sprite);
  })();
  function icon(name, cls) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "ic" + (cls ? " " + cls : ""));
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "#i-" + name);
    svg.append(use);
    return svg;
  }
  function showBanner(msg) { const b = $("banner"); if (b) { b.textContent = msg; b.hidden = !msg; } }
  function fmtDuration(s) {
    const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
    return (d ? d + "d " : "") + (h || d ? h + "h " : "") + m + "m " + (s % 60) + "s";
  }
  function humanize(key) { return key.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/^./, (c) => c.toUpperCase()); }
  function ready(on) { document.body.dataset.uiReady = on === false ? "false" : "true"; }
  function poll(fn, ms) { return setInterval(() => { if (!document.hidden) fn(); }, ms); }

  // ---- API ---------------------------------------------------------------------------------------
  let running = 0;
  const waiting = [];
  function acquire() {
    if (running < MAX_PARALLEL) { running++; return Promise.resolve(); }
    return new Promise((resolve) => waiting.push(resolve));
  }
  function release() { const next = waiting.shift(); if (next) next(); else running--; }   // hand the slot on

  function authHeaders(extra) {
    const headers = Object.assign({ "Accept": "application/json" }, extra || {});
    if (token) headers["Authorization"] = "Bearer " + token;
    return headers;
  }
  function apiError(status, statusText, data) {
    if (status === 401) showBanner("Session expired or no token - please reopen this app from the ctrlX menu.");
    const msg = (data && data.title) ? data.title + (data.detail ? " - " + data.detail : "") : statusText || "HTTP " + status;
    const err = new Error(msg); err.status = status; err.data = data;
    return err;
  }
  function parse(text) {
    if (!text) return null;
    try { return JSON.parse(text); } catch (e) { return { title: text }; }
  }
  async function api(method, path, body) {
    await acquire();
    try {
      const headers = authHeaders(body !== undefined ? { "Content-Type": "application/json" } : null);
      const res = await fetch(API + path, {
        method, headers, body: body === undefined ? undefined : JSON.stringify(body),
        credentials: "same-origin", cache: "no-store",
      });
      const data = parse(await res.text());
      if (!res.ok) throw apiError(res.status, res.statusText, data);
      return data;
    } finally { release(); }
  }
  // the server names the file (Content-Disposition, Handler._send_bytes(..., filename=...))
  async function download(path, fallbackName) {
    await acquire();
    let res, blob;
    try {
      res = await fetch(API + path, { headers: authHeaders(), credentials: "same-origin", cache: "no-store" });
      if (!res.ok) throw apiError(res.status, res.statusText, parse(await res.text()));
      blob = await res.blob();
    } finally { release(); }
    const disp = res.headers.get("Content-Disposition") || "";
    const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(disp), plain = /filename="([^"]+)"/i.exec(disp);
    const name = utf8 ? decodeURIComponent(utf8[1]) : plain ? plain[1] : fallbackName || "download";
    const url = URL.createObjectURL(blob);
    const a = el("a", { href: url, download: name, hidden: true });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    return name;
  }
  // XMLHttpRequest: fetch reports no upload progress. onProgress(0..1) is optional.
  async function upload(method, path, file, onProgress) {
    await acquire();
    try {
      return await new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open(method, API + path);
        for (const [k, v] of Object.entries(authHeaders({ "Content-Type": file.type || "application/octet-stream" }))) {
          xhr.setRequestHeader(k, v);
        }
        if (onProgress) xhr.upload.onprogress = (ev) => { if (ev.lengthComputable) onProgress(ev.loaded / ev.total); };
        xhr.onload = () => {
          const data = parse(xhr.responseText);
          if (xhr.status >= 200 && xhr.status < 300) resolve(data);
          else reject(apiError(xhr.status, xhr.statusText, data));
        };
        xhr.onerror = () => reject(new Error("the connection failed"));
        xhr.send(file);
      });
    } finally { release(); }
  }

  // ---- status, roles -----------------------------------------------------------------------------
  let role = null;   // view | operate | configure (null until /status answered)
  function can(needed) { return role !== null && ROLES.indexOf(role) >= ROLES.indexOf(needed); }
  function applyRole() {
    for (const node of document.querySelectorAll("[data-role]")) node.hidden = !can(node.dataset.role);
    const pill = $("role");
    if (pill) {                      // shown when the user may not do everything
      pill.hidden = role === null || role === "configure";
      pill.textContent = role === "view" ? "View only" : "Operator";
      pill.title = role === "view" ? "You may look, but not change anything (permission: view)"
        : "You may operate the app, but not change its settings (permission: operate)";
    }
    document.body.dataset.role = role || "";
  }
  async function refreshStatus() {
    try {
      const s = await api("GET", "/status");
      const before = role;
      role = s.role;
      document.title = s.title;
      if ($("app-title")) $("app-title").textContent = s.title;
      if ($("app-version")) $("app-version").textContent = /^\d/.test(s.version) ? "v" + s.version : s.version;
      const conn = $("conn");
      if (conn) {
        if (s.datalayer.connected) { conn.className = "pill pill-ok"; conn.textContent = "Data Layer: connected"; }
        else { conn.className = "pill pill-warn"; conn.textContent = s.datalayer.enabled ? "Data Layer: connecting…" : "Data Layer: off"; }
      }
      if (role !== before) { applyRole(); if (settings && !can("configure")) settings.close(); }
      showBanner("");
      return s;
    } catch (e) {
      if (e.status !== 401) showBanner("Cannot reach the app: " + e.message);
      return null;
    }
  }

  // ---- settings drawer (form generated from defaults + rules, GET /settings/meta) -------------------
  // Markup (index.html): #btn-settings (data-role="configure"), #drawer-backdrop, #drawer with
  // #btn-close-settings, #settings-form, #settings-fields, #settings-errors, #btn-save, #btn-revert,
  // #btn-defaults, #save-state. Opens with the button or the URL hash #settings.
  let settings = null;
  function setupSettings(opts) {
    opts = opts || {};
    let meta = null, loaded = null;
    const drawer = $("drawer");

    function buildFields(container, defaults, path) {
      for (const [key, def] of Object.entries(defaults)) {
        const full = path ? path + "." + key : key;
        const rule = (meta.rules && meta.rules[full]) || {};
        const label = rule.label || humanize(key);
        if (rule.hidden) continue; // value is kept unchanged (readForm starts from the loaded settings)
        if (def !== null && typeof def === "object" && !Array.isArray(def)) {
          const inner = el("div", { class: "fields" });
          container.append(el("fieldset", {}, el("legend", { text: label }), inner));
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
          input = el("input", { id, type: rule.secret ? "password" : "text", maxlength: rule.maxLength,
            autocomplete: rule.secret ? "new-password" : "off" });
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
    const getAt = (obj, path) => path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
    function setAt(obj, path, value) {
      const parts = path.split("."); let o = obj;
      for (const p of parts.slice(0, -1)) { if (typeof o[p] !== "object" || o[p] === null) o[p] = {}; o = o[p]; }
      o[parts[parts.length - 1]] = value;
    }
    const inputs = () => document.querySelectorAll("#settings-fields [data-path]");
    function fillForm(values) {
      for (const input of inputs()) {
        const v = getAt(values, input.dataset.path);
        if (input.type === "checkbox") input.checked = !!v;
        else if (input.dataset.kind === "array") input.value = JSON.stringify(v ?? []);
        else input.value = v ?? "";
        input.closest(".field").classList.remove("invalid");
      }
    }
    function readForm() {
      const result = JSON.parse(JSON.stringify(loaded)); // keeps unknown and hidden keys
      for (const input of inputs()) {
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
      for (const input of inputs()) {
        const bad = (errors || []).some((m) => m.startsWith(input.dataset.path + ":") || m.startsWith(input.dataset.path + "["));
        input.closest(".field").classList.toggle("invalid", bad);
      }
    }
    async function load() {
      try {
        if (!meta) {
          meta = await api("GET", "/settings/meta");
          $("settings-fields").replaceChildren();
          buildFields($("settings-fields"), meta.defaults, "");
        }
        loaded = await api("GET", "/settings");
        fillForm(loaded);
        showErrors([]);
      } catch (e) {
        meta = null;
        $("settings-fields").replaceChildren(el("p", { class: "error", text: "Cannot load settings: " + e.message }));
      }
    }
    async function save(ev) {
      ev.preventDefault();
      if (!loaded) return;
      $("save-state").textContent = "Saving…";
      try {
        loaded = await api("PUT", "/settings", readForm());
        fillForm(loaded);
        showErrors([]);
        $("save-state").textContent = "Saved " + new Date().toLocaleTimeString();
        if (opts.onSaved) opts.onSaved(loaded);
      } catch (e) {
        showErrors((e.data && e.data.errors) || [e.message]);
        $("save-state").textContent = "Not saved";
      }
    }
    async function resetDefaults() {
      if (!confirm(opts.resetQuestion || "Reset all settings to their defaults?")) return;
      try {
        loaded = await api("POST", "/settings/reset");
        fillForm(loaded); showErrors([]);
        $("save-state").textContent = "Defaults restored";
        if (opts.onSaved) opts.onSaved(loaded);
      } catch (e) { showErrors([e.message]); }
    }
    const isOpen = () => drawer.classList.contains("open");
    function open() {
      if (!can("configure")) return;   // the API answers 403 anyway
      drawer.classList.add("open");
      drawer.removeAttribute("inert");
      drawer.setAttribute("aria-hidden", "false");
      $("drawer-backdrop").hidden = false;
      $("btn-settings").setAttribute("aria-expanded", "true");
      $("save-state").textContent = "";
      load();
      $("btn-close-settings").focus({ preventScroll: true });
    }
    function close() {
      if (!isOpen()) return;
      drawer.classList.remove("open");
      drawer.setAttribute("inert", "");
      drawer.setAttribute("aria-hidden", "true");
      $("drawer-backdrop").hidden = true;
      $("btn-settings").setAttribute("aria-expanded", "false");
      $("btn-settings").focus({ preventScroll: true });
    }
    // one handler per element (onclick/onsubmit): gotchas #35
    $("settings-form").onsubmit = save;
    $("btn-revert").onclick = () => { if (loaded) fillForm(loaded); showErrors([]); $("save-state").textContent = ""; };
    $("btn-defaults").onclick = resetDefaults;
    $("btn-settings").onclick = () => (isOpen() ? close() : open());
    $("btn-close-settings").onclick = close;
    $("drawer-backdrop").onclick = close;
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape" && isOpen() && !document.querySelector("dialog[open]")) close();
    });
    settings = { open, close, isOpen, reload: load };
    return settings;
  }

  // ---- help menu and tutorial ----------------------------------------------------------------------
  // The help button in the top bar opens a menu: the app's page (link in index.html) and the tutorial - a card
  // at the bottom of the window that steps through the controls of the page; the control of each step blinks
  // (a ring laid over it, position: fixed, so nothing clips it; it stays under the card). The card moves to the
  // top only when it would hide the control completely. Everything stays usable while the card is open; "Show
  // me" does the step's action. Steps:
  //   {title, text: string | () => string, target: () => element | element[] | null,
  //    when: () => bool (step shown?), act: () => void ("Show me"), canAct: () => bool (button shown?)}
  // Markup (index.html): .menu-wrap with #btn-help and #help-menu (role=menu) holding #btn-tutorial.
  // A tour can continue on another page of the app: open it with ?tour=1 and check .handedOver there.
  function setupHelp(steps) {
    const helpBtn = $("btn-help"), helpMenu = $("help-menu");
    function setHelpMenu(open) {
      helpMenu.hidden = !open;
      helpBtn.setAttribute("aria-expanded", String(open));
      if (open) helpMenu.querySelector("[role=menuitem]").focus();
    }
    helpBtn.onclick = () => setHelpMenu(helpMenu.hidden);
    helpMenu.addEventListener("click", () => setHelpMenu(false));     // an item was chosen
    helpMenu.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") { ev.stopPropagation(); setHelpMenu(false); helpBtn.focus(); return; }
      if (ev.key !== "ArrowDown" && ev.key !== "ArrowUp") return;
      ev.preventDefault();
      const items = [...helpMenu.querySelectorAll("[role=menuitem]")];
      const i = items.indexOf(document.activeElement);
      items[(i + (ev.key === "ArrowDown" ? 1 : items.length - 1)) % items.length].focus();
    });
    document.addEventListener("pointerdown", (ev) => {
      if (!helpMenu.hidden && !ev.target.closest(".menu-wrap")) setHelpMenu(false);
    });

    const navBtn = (id, cls, label, ...kids) => el("button", { type: "button", id, class: cls, title: label, "aria-label": label }, ...kids);
    const prev = navBtn("tour-prev", "btn btn-arrow", "Previous step", icon("left"));
    const next = navBtn("tour-next", "btn btn-primary btn-arrow", "Next step", icon("right"), el("span", { class: "tour-done", text: "Done" }));
    const close = navBtn("tour-close", "tool tool-bare", "End the tutorial", icon("close"));
    const tryBtn = el("button", { type: "button", id: "tour-try", class: "btn btn-small", hidden: true, text: "Show me" });
    const card = el("div", { id: "tour", class: "tour", role: "dialog", "aria-labelledby": "tour-title",
      "aria-describedby": "tour-text", hidden: true },
    el("div", { class: "tour-head" }, el("span", { id: "tour-count", class: "muted" }), close),
    el("h3", { id: "tour-title" }), el("p", { id: "tour-text", "aria-live": "polite" }),
    el("div", { class: "tour-nav" }, prev, next, el("span", { class: "spacer" }), tryBtn));
    const ring = el("div", { id: "tour-ring", class: "tour-ring", hidden: true, "aria-hidden": "true" });
    document.body.append(card, ring);

    let tour = null;   // {i, step}
    const shown = () => steps.filter((st) => !st.when || st.when());
    function render() {
      const list = shown();
      tour.i = Math.max(0, Math.min(tour.i, list.length - 1));
      const st = tour.step = list[tour.i];
      $("tour-count").textContent = "Step " + (tour.i + 1) + " of " + list.length;
      $("tour-title").textContent = st.title;
      $("tour-text").textContent = typeof st.text === "function" ? st.text() : st.text;
      prev.disabled = tour.i === 0;
      const last = tour.i === list.length - 1;
      card.classList.toggle("last", last);
      next.setAttribute("aria-label", last ? "End the tutorial" : "Next step");
      next.title = last ? "End the tutorial" : "Next step";
      tryBtn.hidden = !st.act || (st.canAct && !st.canAct());
      ring.classList.remove("blink");
      void ring.offsetWidth;                  // restart the blinking
      ring.classList.add("blink");
      place();
    }
    // every frame while open: the ring follows its control (data updated, page scrolled)
    function place() {
      if (!tour) return;
      const t = tour.step.target ? tour.step.target() : null;
      const els = (Array.isArray(t) ? t : [t]).filter((e) => e && e.getClientRects().length);
      if (!els.length) { ring.hidden = true; card.classList.remove("at-top"); return; }
      const rs = els.map((e) => e.getBoundingClientRect());
      const x0 = Math.max(2, Math.min(...rs.map((r) => r.left)) - 4), y0 = Math.max(2, Math.min(...rs.map((r) => r.top)) - 4);
      const x1 = Math.min(innerWidth - 2, Math.max(...rs.map((r) => r.right)) + 4);
      const y1 = Math.min(innerHeight - 2, Math.max(...rs.map((r) => r.bottom)) + 4);
      Object.assign(ring.style, { left: x0 + "px", top: y0 + "px", width: (x1 - x0) + "px", height: (y1 - y0) + "px" });
      ring.hidden = false;
      const cw = card.offsetWidth, ch = card.offsetHeight;
      const cx0 = (innerWidth - cw) / 2, cy1 = innerHeight - 8, cy0 = cy1 - ch - 8;
      const hides = x0 >= cx0 && x1 <= cx0 + cw && y0 >= cy0 && y1 <= cy1;
      card.classList.toggle("at-top", hides);
    }
    function loop() { if (tour) { place(); requestAnimationFrame(loop); } }
    function start() {
      const running = !!tour;
      tour = { i: 0, step: null };
      card.hidden = false;
      render();
      if (!running) requestAnimationFrame(loop);
      next.focus();
    }
    function end() {
      tour = null;
      card.hidden = true;
      ring.hidden = true;
    }
    function step(delta) {
      if (tour.i + delta >= shown().length) { end(); return; }
      tour.i = Math.max(0, tour.i + delta);
      render();
    }
    prev.onclick = () => step(-1);
    next.onclick = () => step(1);
    close.onclick = end;
    tryBtn.onclick = () => { if (tour && tour.step.act) tour.step.act(); };
    $("btn-tutorial").onclick = start;
    // ← → step, Escape ends - not while typing or in a dialog/the settings drawer
    document.addEventListener("keydown", (ev) => {
      if (!tour || !["ArrowLeft", "ArrowRight", "Escape"].includes(ev.key) || ev.altKey || ev.ctrlKey || ev.metaKey) return;
      if (ev.target.closest("input, textarea, select, [role=tree], dialog, .drawer")) return;
      if (ev.key === "Escape" && document.querySelector("dialog[open], .drawer.open")) return;
      ev.preventDefault();
      if (ev.key === "Escape") end(); else step(ev.key === "ArrowRight" ? 1 : -1);
    });
    const url = new URL(location.href);
    const handedOver = url.searchParams.get("tour") === "1";
    if (handedOver) {
      url.searchParams.delete("tour");
      history.replaceState(null, "", url.pathname + url.search + url.hash);
    }
    return { start, end, refresh: () => { if (tour) render(); }, handedOver };
  }

  return {
    PREFIX, API, token, $, el, icon, showBanner, fmtDuration, humanize, ready, poll,
    api, download, upload, refreshStatus, can, get role() { return role; }, setupSettings, setupHelp,
  };
})();
