// ADS Connector - helpers of its pages (index.html, browse.html). The kit's shared UI parts (token + API calls,
// roles, settings drawer, help menu + tutorial, About, light/dark) are in kit.js, loaded before this file.
"use strict";

window.ADS = (function () {
  // TwinCAT / PLC state -> CSS class: RUN green, STOP orange, CONFIG violet, anything else red
  function stateClass(name) {
    if (!name) return "";
    return { RUN: "st-run", STOP: "st-stop", CONFIG: "st-config" }[name] || "st-other";
  }
  // float32 (PLC REAL) with the fewest digits that give the same float32: 0.1, not 0.10000000149011612
  function f32(x) {
    if (typeof x !== "number" || !isFinite(x)) return x;
    for (let p = 1; p <= 9; p++) { const s = Number(x.toPrecision(p)); if (Math.fround(s) === x) return s; }
    return x;
  }
  function fmtScalar(v) {
    if (typeof v === "string") return v === "" ? '""' : v;
    if (v === null || v === undefined) return "–";
    return String(v);
  }
  // a Data Layer node's value (type from the node): float32 shortest, arrays as lists
  function fmtNodeValue(value, type) {
    const elem = type.startsWith("array-of-") ? type.slice(9) : type;
    const one = (x) => fmtScalar(elem === "float32" ? f32(x) : x);
    return Array.isArray(value) ? "[" + value.map(one).join(", ") + "]" : one(value);
  }
  function browseUrl(controller, path) {
    return "browse.html?controller=" + encodeURIComponent(controller) + (path ? "#" + encodeURIComponent(path) : "");
  }
  const CTRL_STATE = { connected: "connected", connecting: "connecting…", error: "error", offline: "offline",
    disabled: "disabled" };
  function ctrlPill(state) {
    return { cls: "pill pill-" + ({ connected: "ok", connecting: "unknown", disabled: "unknown" }[state] || "warn"),
      text: CTRL_STATE[state] || state };
  }
  return { stateClass, f32, fmtScalar, fmtNodeValue, browseUrl, ctrlPill };
})();
