// Core Connector - start page: one tile per core (this device first), settings in a drawer, help menu
// with the tutorial (it continues in the Data Layer browser). Helpers (token, api, el, help/tutorial, ...)
// are in common.js. Plain JS, no build step, no CDN.
"use strict";

(function () {
  const { $, api, el, icon, showBanner, fmtDuration, webUrl, coreTitle, setupHelp, LOCAL_ID } = window.CC;

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
      const conn = $("conn");
      if (s.datalayer.connected) { conn.className = "pill pill-ok"; conn.textContent = "Data Layer: connected"; }
      else { conn.className = "pill pill-warn"; conn.textContent = s.datalayer.enabled ? "Data Layer: connecting…" : "Data Layer: off"; }
      const errText = s.settings.error ? "Settings: " + s.settings.error : (s.datalayer.error || "");
      $("app-error").textContent = errText;
      $("app-error").hidden = !errText;
      $("btn-settings").hidden = !canWrite;
      $("btn-add").hidden = !canWrite;
      if (!canWrite && $("drawer").classList.contains("open")) closeSettings();
      showBanner("");
    } catch (e) {
      if (e.status !== 401) showBanner("Cannot reach the app: " + e.message);
    }
  }

  // ---- core tiles ------------------------------------------------------------
  // Tiles are created once per core and updated in place (a rebuild every 2 s would swallow clicks).
  const STATE_TEXT = { connected: "connected", connecting: "connecting…", error: "error", offline: "offline" };
  const tiles = new Map();   // id -> view

  function browseUrl(id) { return "browse.html?core=" + encodeURIComponent(id); }

  function meterView(label) {
    const bar = el("div", { class: "meter-bar" });
    const num = el("span", { class: "num", text: "–" });
    const node = el("div", { class: "meter" },
      el("div", { class: "meter-head" }, el("span", { text: label }), num),
      el("div", { class: "meter-track" }, bar));
    return {
      node,
      set(percent, detail) {
        const p = typeof percent === "number" ? Math.max(0, Math.min(100, percent)) : null;
        bar.style.width = (p === null ? 0 : p) + "%";
        bar.classList.toggle("meter-high", p !== null && p >= 90);
        num.textContent = (p === null ? "–" : p.toFixed(1) + " %") + (detail ? " · " + detail : "");
      },
    };
  }

  function createTile(core) {
    const id = core.name;
    const v = { id, rows: {} };
    v.link = el("a", { class: "core-link", target: "_blank", rel: "noopener noreferrer" });
    v.linkText = el("span");
    v.link.append(v.linkText, icon("external"));
    v.sub = el("div", { class: "muted small sub" });
    v.pill = el("span", { class: "pill" });
    v.kv = el("dl", { class: "kv" });
    for (const [key, label] of [["hostname", "Host name"], ["ip", "IP address"], ["opState", "Operating state"],
      ["fw", "Firmware"], ["arch", "Architecture"], ["uptime", "Uptime"]]) {
      v.rows[key] = el("dd", { text: "–" });
      v.kv.append(el("dt", { text: label }), v.rows[key]);
    }
    v.cpu = meterView("CPU");
    v.mem = meterView("Memory");
    v.error = el("p", { class: "error small", hidden: true });
    const browse = el("a", { class: "browse-link", href: browseUrl(id) }, icon("tree"), el("span", { text: "Browse Data Layer" }));
    v.remove = el("button", { type: "button", class: "btn btn-small btn-ghost", text: "Remove" });
    v.remove.onclick = () => removeCore(id);
    v.remove.hidden = !!core.local;
    v.node = el("article", { class: "tile", tabindex: "0", "data-core": id },
      el("div", { class: "tile-head" }, el("div", { class: "tile-title" }, v.link, v.sub), v.pill),
      v.kv, v.cpu.node, v.mem.node, v.error,
      el("div", { class: "tile-foot" }, browse, v.remove));
    // the whole tile opens the browser; its name link and the buttons keep their own action
    v.node.onclick = (ev) => { if (!ev.target.closest("a, button")) location.href = browseUrl(id); };
    v.node.onkeydown = (ev) => { if (ev.key === "Enter" && ev.target === v.node) location.href = browseUrl(id); };
    return v;
  }

  function updateTile(v, c) {
    const state = c.state || "connecting";
    const title = coreTitle(c);
    v.node.className = "tile tile-" + state + (c.local ? " tile-local" : "");
    v.node.setAttribute("aria-label", "Browse the Data Layer of " + title);
    v.linkText.textContent = title;
    v.link.href = webUrl(c);
    v.link.title = "Open the ctrlX web UI of " + title + " in a new tab";
    v.sub.textContent = c.local ? "the device this app runs on" : c.remote;
    v.pill.className = "pill pill-" + ({ connected: "ok", connecting: "unknown" }[state] || "warn");
    v.pill.textContent = STATE_TEXT[state] || state;
    const show = (key, value) => { v.rows[key].textContent = value == null || value === "" ? "–" : String(value); };
    show("hostname", c.hostname);
    show("ip", c.local ? location.host : (c.sslPort === 443 ? c.host : c.host + ":" + c.sslPort));
    show("opState", c.operatingState);
    v.rows.opState.className = "op-state op-" + String(c.operatingState || "").toLowerCase();
    show("fw", c.osVersion ? "ctrlX OS " + c.osVersion : "");
    show("arch", c.architecture);
    show("uptime", typeof c.uptimeS === "number" ? fmtDuration(c.uptimeS) : null);
    const mem = typeof c.memUsedMb === "number" && typeof c.memTotalMb === "number"
      ? Math.round(c.memUsedMb) + " / " + Math.round(c.memTotalMb) + " MB" : "";
    v.cpu.set(c.cpuPercent);
    v.mem.set(c.memUsedPercent, mem);
    v.error.textContent = c.error ? c.error + (c.result ? " (" + c.result + ")" : "") : "";
    v.error.hidden = !c.error;
    v.remove.hidden = !!c.local || !canWrite;
  }

  async function refreshCores() {
    let cores;
    try { cores = await api("GET", "/cores"); } catch (e) { return; }
    const configured = cores.filter((c) => !c.local);
    // this device counts too: "(2 of 3 connected)"
    $("cores-count").textContent = "(" + cores.filter((c) => c.state === "connected").length + " of " + cores.length + " connected)";
    const box = $("cores");
    const seen = new Set();
    const order = [];
    for (const c of cores) {
      let v = tiles.get(c.name);
      if (!v) { v = createTile(c); tiles.set(c.name, v); }
      updateTile(v, c);
      seen.add(c.name);
      order.push(v.node);
    }
    for (const [id, v] of tiles) if (!seen.has(id)) { v.node.remove(); tiles.delete(id); }
    let empty = box.querySelector(".empty");
    if (!configured.length) {
      if (!empty) empty = el("p", { class: "muted empty" });
      empty.textContent = canWrite ? "No other cores yet - add one with “+ Add core”." : "No other cores configured.";
      order.push(empty);
    }
    // re-append only when the order changed (keeps focus and hover)
    const current = Array.from(box.children);
    if (current.length !== order.length || current.some((n, i) => n !== order[i])) box.replaceChildren(...order);
    document.body.dataset.uiReady = "true";
  }

  async function removeCore(name) {
    if (!confirm("Remove the connection to “" + name + "”? Its Data Layer remote is deleted.")) return;
    try { await api("DELETE", "/cores/" + encodeURIComponent(name)); }
    catch (e) { alert("Cannot remove: " + e.message); }
    refreshCores();
  }

  // ---- add dialog ----------------------------------------------------------
  const dialog = $("add-dialog");
  function showAddErrors(errors) {
    const list = $("add-errors");
    list.replaceChildren(...(errors || []).map((m) => el("li", { text: m })));
    list.hidden = !errors || !errors.length;
    for (const input of $("add-form").querySelectorAll("input[name]")) {
      const bad = (errors || []).some((m) => m.startsWith(input.name + ":"));
      input.closest(".field").classList.toggle("invalid", bad);
    }
  }
  function openAdd() {
    $("add-form").reset();
    showAddErrors([]);
    dialog.showModal();
    $("a-name").focus();
  }
  async function submitAdd(ev) {
    ev.preventDefault(); // keep the dialog open until the server accepted the entry
    const f = $("add-form").elements;
    const entry = {
      name: f.name.value.trim(), host: f.host.value.trim(), user: f.user.value.trim(), password: f.password.value,
      skipCertificateCheck: f.skipCertificateCheck.checked,   // the host field may carry ":port"
    };
    $("btn-add-ok").disabled = true;
    try {
      await api("POST", "/cores", entry);
      dialog.close();
      refreshCores();
    } catch (e) {
      showAddErrors((e.data && e.data.errors) || [e.message]);
    } finally {
      $("btn-add-ok").disabled = false;
    }
  }

  // ---- settings drawer (form generated from defaults + rules) ---------------
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
    if (!confirm("Reset all settings to their defaults? This also removes ALL core connections.")) return;
    try { loaded = await api("POST", "/settings/reset"); fillForm(loaded); showErrors([]); $("save-state").textContent = "Defaults restored"; }
    catch (e) { showErrors([e.message]); }
    refreshCores();
  }

  const drawer = $("drawer");
  function openSettings() {
    if (!canWrite) return;   // read-only users: no settings (the API answers 403 anyway)
    drawer.classList.add("open");
    drawer.removeAttribute("inert");
    drawer.setAttribute("aria-hidden", "false");
    $("drawer-backdrop").hidden = false;
    $("btn-settings").setAttribute("aria-expanded", "true");
    $("save-state").textContent = "";
    loadSettings();
    $("btn-close-settings").focus({ preventScroll: true });
  }
  function closeSettings() {
    drawer.classList.remove("open");
    drawer.setAttribute("inert", "");
    drawer.setAttribute("aria-hidden", "true");
    $("drawer-backdrop").hidden = true;
    $("btn-settings").setAttribute("aria-expanded", "false");
    $("btn-settings").focus({ preventScroll: true });
  }

  // ---- start ---------------------------------------------------------------
  // one handler per element (onclick/onsubmit): see docs/ctrlx/gotchas.md #35
  $("settings-form").onsubmit = saveSettings;
  $("btn-revert").onclick = () => { fillForm(loaded); showErrors([]); $("save-state").textContent = ""; };
  $("btn-defaults").onclick = resetDefaults;
  $("btn-settings").onclick = openSettings;
  $("btn-close-settings").onclick = closeSettings;
  $("drawer-backdrop").onclick = closeSettings;
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && drawer.classList.contains("open") && !dialog.open) closeSettings();
  });
  $("btn-add").onclick = openAdd;
  $("add-form").onsubmit = submitAdd;
  $("btn-add-cancel").onclick = () => dialog.close();

  // ---- tutorial (help menu) ------------------------------------------------------------------
  const localTile = () => document.querySelector(".tile-local");
  const otherTile = () => document.querySelector(".tile:not(.tile-local)");
  setupHelp([
    { title: "Welcome",
      text: "This tour shows the controls of Core Connector: this page with the cores, then the Data Layer " +
        "browser. Step through it with the arrows (or the ← → keys); the control of each step blinks. " +
        "Everything stays usable - try the controls while the tour is open." },
    { title: "Cores", target: () => $("cores"),
      text: "One tile per ctrlX CORE: this device first - it is always there and can't be removed - then the " +
        "cores added here. The heading counts how many of them deliver data, this device included." },
    { title: "Status of a core", target: localTile,
      text: "Host name, IP address, operating state (OPERATING, SETUP, SERVICE), firmware, architecture, " +
        "uptime, CPU and memory, read as often as set in Settings (Poll interval). A value a core does not " +
        "provide shows “–”." },
    { title: "Web UI", target: () => localTile() && localTile().querySelector(".core-link"),
      text: "The name of a core opens its own ctrlX web UI in a new tab." },
    { title: "Connection", target: () => [...document.querySelectorAll(".tile .tile-head > .pill")],
      text: "connected, connecting…, error or offline. When a connection fails, a red line below the values " +
        "says why - e.g. “login rejected” for a wrong user or password." },
    { title: "Add core", when: () => canWrite, target: () => $("btn-add"),
      text: "Connects another ctrlX CORE: a name, its IP address or host name (another HTTPS port after a " +
        "colon, e.g. 192.168.1.1:8443), user and password. The app creates the Data Layer remote connection " +
        "devices/remotes/cm-<name> on this device for it." },
    { title: "Remove", when: () => canWrite && !!otherTile(), target: () => otherTile().querySelector(".tile-foot .btn"),
      text: "Removes the connection to that core (and its remote connection). The core itself is not changed." },
    { title: "Settings", when: () => canWrite, target: () => $("btn-settings"),
      text: "Log level, how often the cores are read, and how often the Data Layer browser reads its values. " +
        "Only users who may change settings see this button." },
    { title: "Data Layer", target: () => $("conn"),
      text: "The connection of this app to the Data Layer of this device - the other cores are reached " +
        "through it as well." },
    { title: "Home screen widget",
      text: "Core Connector also brings a widget for the ctrlX home screen, “Connected cores”: every core with " +
        "its operating state and a button that opens this app. Add it to the home screen from its widget list." },
    { title: "Help", target: () => $("btn-help"), text: "Start this tour again, or open the app's page on GitHub." },
    { title: "Data Layer browser", target: () => localTile() && localTile().querySelector(".browse-link"),
      text: "Click a tile - anywhere except its name - or “Browse Data Layer” to see the Data Layer of that " +
        "core. “Show me” opens it for this device and continues the tour there.",
      act: () => { location.href = browseUrl(LOCAL_ID) + "&tour=1"; } },
  ]);

  (async function init() {
    await refreshStatus();
    await refreshCores();
    if (location.hash === "#settings") openSettings();
    setInterval(refreshStatus, 5000);
    setInterval(() => { if (!document.hidden) refreshCores(); }, 2000);
  })();
})();
