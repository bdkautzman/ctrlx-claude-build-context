// ADS Connector - start page: one tile per TwinCAT controller, the Data Layer nodes of PLC variables, settings in a
// drawer, help menu with the tutorial (it continues in the PLC browser), About, light/dark. The kit's shared parts are
// in kit.js, this app's helpers in common.js. Plain JS, no build step, no CDN.
// Roles: view sees everything, configure adds/changes/removes controllers, routes, nodes and settings.
"use strict";

(function () {
  const { $, el, icon, api, can, fmtDuration } = window.Kit;
  const { stateClass, fmtNodeValue, browseUrl, ctrlPill } = window.ADS;

  // ---- status --------------------------------------------------------------
  async function refreshStatus() {
    const s = await Kit.refreshStatus();   // title, version, Data Layer pill, role pill, [data-role] elements, colors
    if (!s) return;
    const errText = s.settings.error ? "Settings: " + s.settings.error
      : s.ads && s.ads.error ? "ADS: " + s.ads.error : (s.datalayer.error || "");
    $("app-error").textContent = errText;
    $("app-error").hidden = !errText;
  }

  // ---- controller tiles ----------------------------------------------------------
  // Tiles are created once per controller and updated in place (a rebuild every 2 s would swallow clicks).
  const tiles = new Map();   // name -> view
  let controllers = [];

  function createTile(c) {
    const v = { name: c.name, rows: {} };
    v.title = el("div", { class: "tile-name", text: c.name });
    v.sub = el("div", { class: "muted small sub" });
    v.pill = el("span", { class: "pill" });
    v.kv = el("dl", { class: "kv" });
    for (const [key, label] of [["device", "Device"], ["twincat", "TwinCAT"], ["system", "TwinCAT state"],
      ["plc", "PLC"], ["symbols", "Symbols"], ["nodes", "Data Layer nodes"], ["poll", "Poll"]]) {
      v.rows[key] = el("dd", { text: "–" });
      v.kv.append(el("dt", { text: label }), v.rows[key]);
    }
    v.route = el("div", { class: "route-hint" });
    v.error = el("p", { class: "error small", hidden: true });
    const browse = el("a", { class: "browse-link", href: browseUrl(c.name) }, icon("tree"), el("span", { text: "Browse PLC" }));
    v.edit = el("button", { type: "button", class: "btn btn-small btn-ghost", text: "Edit" });
    v.edit.onclick = () => openController(v.name);
    v.addRoute = el("button", { type: "button", class: "btn btn-small btn-ghost", text: "Add route",
      title: "Add a route for this device on the controller (needs an administrator of the controller)" });
    v.addRoute.onclick = () => openRoute(v.name);
    v.remove = el("button", { type: "button", class: "btn btn-small btn-ghost", text: "Remove" });
    v.remove.onclick = () => removeController(v.name);
    v.actions = el("div", { class: "tile-actions" }, v.edit, v.addRoute, v.remove);
    v.node = el("article", { class: "tile", tabindex: "0", "data-controller": c.name },
      el("div", { class: "tile-head" }, el("div", { class: "tile-title" }, v.title, v.sub), v.pill),
      v.kv, v.route, v.error, el("div", { class: "tile-foot" }, browse, v.actions));
    // the whole tile opens the browser; its buttons keep their own action
    v.node.onclick = (ev) => { if (!ev.target.closest("a, button")) location.href = browseUrl(v.name); };
    v.node.onkeydown = (ev) => { if (ev.key === "Enter" && ev.target === v.node) location.href = browseUrl(v.name); };
    return v;
  }

  function stateText(name, extra) {
    const span = el("span", { class: "state " + stateClass(name), text: name || "–" });
    return extra ? [span, document.createTextNode(" · " + extra)] : [span];
  }

  function updateTile(v, c) {
    const state = c.state || "connecting";
    v.node.className = "tile tile-" + state;
    v.node.setAttribute("aria-label", "Browse the PLC of " + c.name);
    v.sub.textContent = c.host + (c.netId ? " · " + c.netId : "") + " · port " + c.amsPort;
    const pill = ctrlPill(state);
    v.pill.className = pill.cls;
    v.pill.textContent = pill.text;
    const show = (key, value) => { v.rows[key].textContent = value == null || value === "" ? "–" : String(value); };
    show("device", c.hostname);
    show("twincat", [c.twincat, c.os].filter(Boolean).join(" · "));
    v.rows.system.replaceChildren(...stateText(c.systemStateName));
    v.rows.plc.replaceChildren(...(c.plcError ? [el("span", { class: "state st-other", text: "not running" })]
      : stateText(c.plcStateName, c.plcName)));
    show("symbols", c.symbols != null ? c.symbols + " (version " + c.symbolVersion + ")" : "");
    show("nodes", c.nodes ? c.nodesOk + " of " + c.nodes + " with a value" : "none");
    show("poll", c.pollIntervalMs + " ms" + (c.cycleMs != null && state === "connected" ? " · last cycle " + c.cycleMs + " ms" : ""));
    // the route the controller needs for this device (known once a connection was tried)
    const route = c.localNetId && c.localIp;
    v.route.hidden = !route;
    if (route) {
      v.route.replaceChildren(document.createTextNode("Route on the controller: AMS Net ID "),
        el("code", { text: c.localNetId }), document.createTextNode(" at "), el("code", { text: c.localIp }));
    }
    const err = c.plcError || c.error;
    v.error.textContent = err || "";
    v.error.hidden = !err;
    v.actions.hidden = !can("configure");
  }

  async function refreshControllers() {
    try { controllers = await api("GET", "/controllers"); } catch (e) { return; }
    const ok = controllers.filter((c) => c.state === "connected" && c.plcStateName).length;
    $("ctrl-count").textContent = controllers.length ? "(" + ok + " of " + controllers.length + " connected)" : "";
    const box = $("controllers");
    const seen = new Set();
    const order = [];
    for (const c of controllers) {
      let v = tiles.get(c.name);
      if (!v) { v = createTile(c); tiles.set(c.name, v); }
      updateTile(v, c);
      seen.add(c.name);
      order.push(v.node);
    }
    for (const [name, v] of tiles) if (!seen.has(name)) { v.node.remove(); tiles.delete(name); }
    if (!controllers.length) {
      order.push(el("p", { class: "muted empty", text: can("configure")
        ? "No controllers yet - add one with “+ Add controller”." : "No controllers configured." }));
    }
    const current = Array.from(box.children);
    if (current.length !== order.length || current.some((n, i) => n !== order[i])) box.replaceChildren(...order);
    syncControllerFilter();
  }

  async function removeController(name) {
    const c = controllers.find((x) => x.name === name);
    const n = c ? c.nodes : 0;
    if (!confirm("Remove the controller “" + name + "”?" + (n ? "\n\nIts " + n + " Data Layer node(s) are removed too." : "") +
      "\n\nThe controller itself is not changed.")) return;
    try { await api("DELETE", "/controllers/" + encodeURIComponent(name)); }
    catch (e) { alert("Cannot remove: " + e.message); }
    refreshControllers();
    refreshNodes();
  }

  // ---- add / edit controller ---------------------------------------------------------
  const cDialog = $("controller-dialog");
  const enabledSwitch = Kit.switchInput(true, { id: "c-enabled" });
  $("c-enabled-slot").replaceWith(enabledSwitch.node);
  let editing = null;      // name of the controller being edited, null = add

  function showErrors(listId, form, errors) {
    const list = $(listId);
    list.replaceChildren(...(errors || []).map((m) => el("li", { text: m })));
    list.hidden = !errors || !errors.length;
    for (const input of form.querySelectorAll("input[name]")) {
      const bad = (errors || []).some((m) => m.startsWith(input.name + ":"));
      const field = input.closest(".field");
      if (field) field.classList.toggle("invalid", bad);
    }
  }
  function openController(name) {
    const form = $("controller-form");
    form.reset();
    editing = name || null;
    const c = name ? controllers.find((x) => x.name === name) : null;
    $("controller-h").textContent = c ? "Edit controller " + c.name : "Add controller";
    $("btn-controller-ok").textContent = c ? "Save" : "Add";
    const f = form.elements;
    f.name.disabled = !!c;
    if (c) {
      f.name.value = c.name; f.host.value = c.host; f.amsNetId.value = c.amsNetId;
      f.amsPort.value = c.amsPort; f.pollIntervalMs.value = c.pollIntervalMs;
    }
    enabledSwitch.value = c ? c.enabled : true;
    $("c-found").textContent = "";
    $("c-found").className = "found";
    showErrors("controller-errors", form, []);
    cDialog.showModal();
    (c ? f.host : f.name).focus();
  }
  async function submitController(ev) {
    ev.preventDefault();   // keep the dialog open until the server accepted the entry
    const f = $("controller-form").elements;
    const entry = { host: f.host.value.trim(), amsNetId: f.amsNetId.value.trim(), amsPort: Number(f.amsPort.value),
      pollIntervalMs: Number(f.pollIntervalMs.value), enabled: enabledSwitch.value };
    $("btn-controller-ok").disabled = true;
    try {
      if (editing) await api("PUT", "/controllers/" + encodeURIComponent(editing), entry);
      else await api("POST", "/controllers", Object.assign({ name: f.name.value.trim() }, entry));
      cDialog.close();
      refreshControllers();
    } catch (e) {
      showErrors("controller-errors", $("controller-form"), (e.data && e.data.errors) || [e.message]);
    } finally { $("btn-controller-ok").disabled = false; }
  }
  // "Find": TwinCAT's UDP server info - fills the Net ID and shows name and version
  async function findController() {
    const f = $("controller-form").elements;
    const host = f.host.value.trim();
    const out = $("c-found");
    if (!host) { out.className = "found bad"; out.textContent = "Enter the IP address first."; return; }
    out.className = "found";
    out.textContent = "Asking " + host + " …";
    $("btn-find").disabled = true;
    try {
      const info = await api("POST", "/discover", { host });
      f.amsNetId.value = info.amsNetId;
      out.className = "found ok";
      out.textContent = "Found: " + [info.hostname, info.twincat && "TwinCAT " + info.twincat, info.os].filter(Boolean).join(" · ") +
        (info.localIp ? " - this device is " + info.localIp + " toward it" : "");
    } catch (e) {
      out.className = "found bad";
      out.textContent = (e.data && e.data.title) ? e.data.title.replace(/^PLC: /, "") : e.message;
    } finally { $("btn-find").disabled = false; }
  }

  // ---- add route ---------------------------------------------------------------------
  const rDialog = $("route-dialog");
  let routeFor = null;
  function openRoute(name) {
    const c = controllers.find((x) => x.name === name);
    if (!c) return;
    routeFor = name;
    $("route-form").reset();
    $("r-ctrl").textContent = c.name + " (" + c.host + ")";
    $("r-netid").textContent = c.localNetId || "(set at the first connection)";
    $("r-ip").textContent = c.localIp || "(this device's address toward " + c.host + ")";
    $("r-routeName").value = "ctrlX-" + (c.localIp || "CORE");
    $("route-state").textContent = "";
    showErrors("route-errors", $("route-form"), []);
    rDialog.showModal();
    $("r-password").focus();
  }
  async function submitRoute(ev) {
    ev.preventDefault();
    const f = $("route-form").elements;
    $("btn-route-ok").disabled = true;
    $("route-state").textContent = "Sending…";
    try {
      const res = await api("POST", "/controllers/" + encodeURIComponent(routeFor) + "/route",
        { user: f.user.value.trim(), password: f.password.value, routeName: f.routeName.value.trim() });
      $("route-state").textContent = "";
      rDialog.close();
      alert("The controller added the route “" + res.routeName + "” for " + res.netId + " at " + res.address + ".");
      refreshControllers();
    } catch (e) {
      $("route-state").textContent = "";
      showErrors("route-errors", $("route-form"), (e.data && e.data.errors) || [e.message]);
    } finally { $("btn-route-ok").disabled = false; }
  }

  // ---- Data Layer nodes ---------------------------------------------------------------
  let nodes = [];
  function syncControllerFilter() {
    const sel = $("nodes-controller");
    const names = controllers.map((c) => c.name);
    const have = Array.from(sel.options).slice(1).map((o) => o.value);
    if (names.join("\n") === have.join("\n")) return;
    const keep = sel.value;
    sel.replaceChildren(el("option", { value: "", text: "All controllers" }), ...names.map((n) => el("option", { value: n, text: n })));
    sel.value = names.includes(keep) ? keep : "";
  }
  function renderNodes() {
    const text = $("nodes-filter").value.trim().toLowerCase();
    const ctrl = $("nodes-controller").value;
    const shown = nodes.filter((n) => (!ctrl || n.controller === ctrl) &&
      (!text || (n.controller + "/" + n.address + " " + n.symbol).toLowerCase().includes(text)));
    const bad = nodes.filter((n) => !n.ok).length;
    $("nodes-count").textContent = nodes.length ? "(" + nodes.length + (bad ? ", " + bad + " without a value" : "") + ")" : "";
    const body = $("nodes-body");
    if (!shown.length) {
      body.replaceChildren(el("tr", { class: "empty" }, el("td", { colspan: "6",
        text: nodes.length ? "No node matches the filter." : "No Data Layer nodes yet - add PLC variables in the PLC browser of a controller (“Browse PLC” on its tile)." })));
      return;
    }
    const rows = shown.map((n) => {
      const val = n.ok ? el("td", { class: "val", text: fmtNodeValue(n.value, n.type) + (n.unit ? " " + n.unit : "") })
        : el("td", { class: "val bad", text: n.error || "no value" });
      const act = el("td", { class: "act" });
      if (can("configure")) {
        const del = el("button", { type: "button", class: "tool tool-bare", title: "Remove this Data Layer node",
          "aria-label": "Remove " + n.controller + "/" + n.address }, icon("trash"));
        del.onclick = () => removeNode(n);
        act.append(del);
      }
      return el("tr", { title: n.description || "" },
        el("td", { class: "addr", text: "plc/" + n.controller + "/" + n.address }),
        el("td", { class: "sym" }, el("a", { href: browseUrl(n.controller, n.symbol), text: n.symbol,
          title: "Show it in the PLC browser of " + n.controller })),
        el("td", { class: "type", text: n.type }), val,
        el("td", {}, el("span", { class: "access" + (n.writable ? " access-rw" : ""), text: n.writable ? "read/write" : "read",
          title: n.writable ? "Data Layer clients may write it into the PLC" : "Read-only in the Data Layer" })), act);
    });
    body.replaceChildren(...rows);
  }
  async function refreshNodes() {
    try { nodes = await api("GET", "/nodes"); } catch (e) { return; }
    renderNodes();
  }
  async function removeNode(n) {
    if (!confirm("Remove the Data Layer node plc/" + n.controller + "/" + n.address + "?\n\nThe PLC variable " + n.symbol +
      " is not changed.")) return;
    try { await api("POST", "/nodes/delete", { controller: n.controller, addresses: [n.address] }); }
    catch (e) { alert("Cannot remove: " + e.message); }
    refreshNodes();
  }

  // ---- pages, settings drawer -----------------------------------------------------------
  const tabs = Kit.setupTabs({ onShow: (page) => { if (page === "nodes") refreshNodes(); } });
  const settings = Kit.setupSettings({ onSaved: () => refreshControllers() });

  // one handler per element (onclick/onsubmit): docs/ctrlx/gotchas.md #35
  $("btn-add").onclick = () => openController(null);
  $("controller-form").onsubmit = submitController;
  $("btn-controller-cancel").onclick = () => cDialog.close();
  $("btn-find").onclick = findController;
  $("route-form").onsubmit = submitRoute;
  $("btn-route-cancel").onclick = () => rDialog.close();
  $("nodes-filter").oninput = renderNodes;
  $("nodes-controller").onchange = renderNodes;

  // ---- tutorial (help menu) ------------------------------------------------------------------
  const firstTile = () => document.querySelector(".tile");
  const show = (page) => () => tabs.show(page);
  Kit.setupHelp([
    { title: "Welcome",
      text: "This tour shows the controls of the ADS Connector: the TwinCAT controllers, the Data Layer nodes of PLC " +
        "variables, then the PLC browser. Step through it with the arrows (or the ← → keys); the control of each " +
        "step blinks. Everything stays usable while the tour is open." },
    { title: "Controllers", target: () => $("controllers"), page: "controllers",
      text: "One tile per Beckhoff TwinCAT controller, reached over ADS. The heading counts how many of them are " +
        "connected with their PLC running." },
    { title: "Status of a controller", target: firstTile, page: "controllers",
      text: "Its name, IP address, AMS Net ID and AMS port; device name and TwinCAT version; the state of TwinCAT " +
        "(RUN, CONFIG) and of the PLC (RUN, STOP); the number of PLC symbols; how many of its Data Layer nodes have " +
        "a value; the poll interval. A red line says what is wrong, e.g. a missing route or a PLC that isn't running." },
    { title: "Route", target: () => document.querySelector(".route-hint:not([hidden])"), page: "controllers",
      text: "A TwinCAT controller only answers devices it has a route for. This line names the route it needs for " +
        "this device: the AMS Net ID and the address. Add it in TwinCAT (XAE: Router → Edit Routes, or the CX's " +
        "own route dialog) - or with “Add route” on the tile." },
    { title: "Add controller", when: () => can("configure"), target: () => $("btn-add"), page: "controllers",
      text: "Adds a TwinCAT controller: a name (also its Data Layer folder), its IP address, the AMS Net ID (empty or " +
        "“Find”: read from the controller), the AMS port of the PLC runtime (851) and how often its Data Layer nodes " +
        "are read." },
    { title: "Edit, Add route, Remove", when: () => can("configure") && !!firstTile(), page: "controllers",
      target: () => firstTile().querySelector(".tile-actions"),
      text: "Edit changes the controller's settings (not its name). Add route asks the controller to add the route " +
        "for this device - with the user name and password of an administrator of the controller; it changes the " +
        "controller's route table. Remove removes the controller and its Data Layer nodes from this app." },
    { title: "Data Layer nodes", target: () => $("nodes-card"), page: "nodes", act: show("nodes"),
      text: () => "The PLC variables in the ctrlX Data Layer of this device, below " + Kit.PREFIX.slice(1) +
        "/plc/<controller>/. Each one has the type of its PLC variable (REAL = float32, INT = int16, ...) and is " +
        "read with the controller's poll interval; a writable one (read/write) writes straight into the PLC." +
        (can("configure") ? " The bin removes a node (not the PLC variable)." : "") },
    { title: "Filter", target: () => [$("nodes-filter"), $("nodes-controller")], page: "nodes",
      text: "Show only the nodes whose address or PLC variable contains the text, or the nodes of one controller." },
    { title: "Settings", when: () => can("configure"), target: () => $("btn-settings"),
      text: "Log level, the ADS timeout, this device's AMS Net ID, the PLC browser's read interval and the page " +
        "colors. Only users who may change settings see this button.", act: () => settings.open() },
    { title: "Your permission", when: () => !can("configure"), target: () => $("role"),
      text: () => can("operate") ? "Operator: you may write PLC variables in the PLC browser, but not change " +
        "controllers, Data Layer nodes or the settings." : "View only: you see the controllers, their PLC variables " +
        "and the Data Layer nodes, but can't change anything." },
    { title: "Data Layer", target: () => $("conn"),
      text: "The connection of this app to the ctrlX Data Layer of this device, where it provides the nodes." },
    { title: "Light or dark", target: () => $("btn-theme"),
      text: "Switches the page between light and dark mode. Each browser keeps its own choice." },
    { title: "Home screen widget",
      text: "The app also brings a widget for the ctrlX home screen, “TwinCAT controllers”: every controller with " +
        "the state of TwinCAT and its PLC. Add it from the widget list of the home screen's edit mode." },
    { title: "Help", target: () => $("btn-help"),
      text: "Start this tour again, open the app's page on GitHub, or see About: version, author, license." },
    { title: "PLC browser", target: () => firstTile() && firstTile().querySelector(".browse-link"), page: "controllers",
      canAct: () => !!firstTile(),
      text: "Click a tile - or “Browse PLC” - to see the PLC variables of that controller. “Show me” opens it " +
        "for the first controller and continues the tour there.",
      act: () => { location.href = browseUrl(firstTile().dataset.controller) + "&tour=1"; } },
  ]);

  (async function init() {
    await refreshStatus();
    await Promise.all([refreshControllers(), refreshNodes()]);
    Kit.ready();
    if (location.hash === "#settings") settings.open();
    setInterval(refreshStatus, 5000);
    Kit.poll(refreshControllers, 2000);
    Kit.poll(() => { if (tabs.current === "nodes") refreshNodes(); }, 1000);
  })();
})();
