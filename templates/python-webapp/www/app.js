// Template App web UI - this app's page. Plain JS, no build step, no CDN (devices are often offline).
// The shared parts (token + API calls, roles, settings drawer, help menu + tutorial, ...) are in kit.js:
// see the list at its top. Roles: view < operate < configure (GET /status says which one the user has).
"use strict";

(function () {
  const { $, el, api, can, fmtDuration } = window.Kit;

  // ---- status --------------------------------------------------------------
  async function refreshStatus() {
    const s = await Kit.refreshStatus();   // title, version, Data Layer pill, role pill, [data-role] elements
    if (!s) return;
    $("st-uptime").textContent = fmtDuration(s.uptimeSeconds);
    $("st-source").textContent = s.settings.source;
    $("st-persisted").textContent = s.settings.persisted ? "yes" : (s.settings.storageAvailable ? "no" : "no (app data not available)");
    $("st-nodes").textContent = s.datalayer.registered;
    const errText = s.settings.error || s.datalayer.error || "";
    $("st-error").textContent = errText; $("st-error").hidden = !errText;
  }

  // ---- values --------------------------------------------------------------
  // GET /values lists the nodes the user may see; canWrite says whether they may write one (operate role).
  // Shown as stored, except float32 (PLC REAL): with the fewest digits that give the same float32 - the node holds
  // 0.10000000149011612 for 0.1, the PLC shows 0.1. Arrays as JSON. int64/uint64 above 2^53 lose digits in the
  // browser (JavaScript numbers): don't edit such values here.
  const isArray = (type) => type.startsWith("array-of-");
  function f32(x) {
    if (typeof x !== "number" || !isFinite(x)) return x;
    for (let p = 1; p <= 9; p++) { const s = Number(x.toPrecision(p)); if (Math.fround(s) === x) return s; }
    return x;
  }
  function show(type, value) {
    const elemType = isArray(type) ? type.slice(9) : type;
    const one = (x) => (elemType === "float32" ? f32(x) : x);
    return isArray(type) ? JSON.stringify(value.map(one)) : String(one(value));
  }
  let editing = null; // name of the value currently edited (do not overwrite while typing)
  async function refreshValues() {
    if (editing) return;
    let values;
    try { values = await api("GET", "/values"); } catch (e) { return; }
    if (editing) return; // an edit may have started while the request was running
    const body = $("values-body");
    body.replaceChildren();
    for (const [name, v] of Object.entries(values)) {
      const full = show(v.type, v.value);
      const valueCell = el("td", { class: "val", text: full.length > 80 ? full.slice(0, 77) + "…" : full, title: full });
      const actionCell = el("td");
      if (v.canWrite) {
        const btn = el("button", { type: "button", class: "btn btn-small", text: "Edit" });
        // onclick (not addEventListener): startEdit replaces it with "Set" - with two listeners a
        // click on "Set" ran startEdit again first and wrote the old value back (gotchas #35)
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
      : el("input", { type: v.type === "string" || isArray(v.type) ? "text" : "number", step: "any" });
    input.value = show(v.type, v.value);
    cell.replaceChildren(input);
    input.focus();
    btn.textContent = "Set";
    const done = async (commit) => {
      if (commit) {
        let value = input.value;
        try {
          if (v.type === "bool8") value = value === "true";
          else if (isArray(v.type)) value = JSON.parse(value);          // e.g. [1.5, 2]
          else if (v.type !== "string") value = Number(value);
          await api("PUT", "/values/" + encodeURIComponent(name), { value });
        } catch (e) { alert("Write rejected: " + e.message); }
      }
      editing = null;
      refreshValues();
    };
    btn.onclick = () => done(true);
    input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") done(true); if (ev.key === "Escape") done(false); });
  }

  // ---- settings drawer, help ---------------------------------------------------
  const settings = Kit.setupSettings({ onSaved: () => { refreshStatus(); refreshValues(); } });
  Kit.setupHelp([
    { title: "Welcome",
      text: "This tour shows the controls of the Template App. Step through it with the arrows (or the ← → keys); " +
        "the control of each step blinks. Everything stays usable while the tour is open." },
    { title: "Status", target: () => $("status-card"),
      text: "Uptime of the app, where its settings come from, whether they are stored in the ctrlX app data, " +
        "and how many Data Layer nodes it provides." },
    { title: "Data Layer values", target: () => $("values-card"),
      text: () => "The nodes this app provides in the ctrlX Data Layer (" + Kit.PREFIX.slice(1) + "/…). " +
        (can("operate") ? "Writable ones have an Edit button: the value holds until the app restarts."
          : "Users with the operate permission can change the writable ones here.") },
    { title: "A command", target: () => [...document.querySelectorAll("#values-body tr")]
        .filter((tr) => tr.textContent.includes("/commands/set-setpoint/")),
      text: "commands/set-setpoint shows how a PLC starts something in the app (Execute / Busy / Done / Error): write " +
        "target, then execute true; busy is true while it works, then done (or error with error-text). Writing " +
        "execute false resets done and error. Here it copies target into values/setpoint after 1 s." },
    { title: "Settings", when: () => can("configure"), target: () => $("btn-settings"),
      text: "Opens the settings. They are stored in the ctrlX app data, so they are saved and loaded with " +
        "configurations. Only users who may change settings see this button.",
      act: () => settings.open() },
    { title: "Your permission", when: () => !can("configure"), target: () => $("role"),
      text: () => can("operate") ? "Operator: you may change values and run the app, but not change its settings."
        : "View only: you see the app's data, but can't change anything. Ask an administrator for more." },
    { title: "Data Layer", target: () => $("conn"),
      text: "The connection of this app to the ctrlX Data Layer." },
    { title: "Home screen widget",
      text: "The app also brings a widget for the ctrlX home screen: add “Template App” from the widget list " +
        "of the home screen's edit mode." },
    { title: "Help", target: () => $("btn-help"), text: "Start this tour again, or open the app's page." },
  ]);

  (async function init() {
    await refreshStatus();
    await refreshValues();
    Kit.ready();                                   // for tools/ui_check.py (screenshots wait for it)
    if (location.hash === "#settings") settings.open();
    setInterval(refreshStatus, 5000);
    Kit.poll(refreshValues, 2000);
  })();
})();
