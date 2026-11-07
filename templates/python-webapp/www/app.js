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
