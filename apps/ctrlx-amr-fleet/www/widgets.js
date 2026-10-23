// AMR Fleet View - widget for the ctrlX home screen (dashboard): the available AMRs with name, state
// and battery. A Web Component the ctrlX UI loads from this file (package manifest
// uiExtensions.dashboard, tag "ctrlx-amr-fleet-widget"). Plain JS, no framework, no CDN.
//
// It runs INSIDE the ctrlX UI (same origin as /ctrlx-amr-fleet), so it uses the ctrlX UI's login: the
// bearer token in sessionStorage "idm_token" - where the ctrlX UI and its own widgets keep it
// (ctrlX OS 4.6). Shadow DOM keeps our CSS and the ctrlX UI's CSS apart. It polls every 2 s and only
// while the page is visible. The AMR colors come from the app's styling settings (/view-config).
"use strict";

(function () {
  const BASE = "/ctrlx-amr-fleet";
  const API = BASE + "/api/v1";
  const TAG = "ctrlx-amr-fleet-widget";
  const POLL_MS = 2000;
  const DEFAULT_COLORS = ["#1f6feb", "#d1242f", "#1a7f37", "#bf8700", "#8250df", "#0a8a9a", "#cf2a93", "#57801c", "#d4570a", "#3e4bd1"];

  function token() {
    try { return sessionStorage.getItem("idm_token") || ""; } catch (e) { return ""; }
  }
  async function get(path) {
    const headers = { "Accept": "application/json" };
    const t = token();
    if (t) headers["Authorization"] = "Bearer " + t;
    const res = await fetch(API + path, { headers, cache: "no-store", credentials: "same-origin" });
    if (!res.ok) { const err = new Error("HTTP " + res.status); err.status = res.status; throw err; }
    return res.json();
  }
  function hash(id) {   // same as the map view, so an AMR has the same color everywhere
    let h = 2166136261;
    for (let i = 0; i < id.length; i++) { h ^= id.charCodeAt(i); h = Math.imul(h, 16777619); }
    return h >>> 0;
  }
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  const CSS = `
    :host { display: block; height: 100%; min-height: 0; }
    .w { --accent: #007bc0; --stale: #9aa3ad; --ok: #00884a; --warn: #c76b00; --err: #c8102e;
      --muted: color-mix(in srgb, currentColor 60%, transparent); --line: color-mix(in srgb, currentColor 14%, transparent);
      box-sizing: border-box; height: 100%; display: flex; flex-direction: column; font: inherit; font-size: 13px;
      line-height: 1.35; color: inherit; padding: 4px 6px; }
    .head { display: flex; align-items: center; gap: 8px; padding: 2px 2px 6px; border-bottom: 1px solid var(--line); }
    .title { font-weight: 600; }
    .count { color: var(--muted); }
    .status { display: inline-flex; align-items: center; gap: 5px; color: var(--muted); font-size: 12px; margin-left: auto; white-space: nowrap; }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--stale); }
    .status.live .dot { background: var(--ok); }
    .status.stale .dot { background: var(--warn); }
    .status.error .dot { background: var(--err); }
    .open { border: 1px solid var(--line); background: none; color: var(--accent); font: inherit; font-size: 12px;
      padding: 2px 8px; border-radius: 4px; cursor: pointer; white-space: nowrap; }
    .open:hover { border-color: var(--accent); }
    ul { list-style: none; margin: 0; padding: 0; overflow-y: auto; flex: 1; min-height: 0; }
    li { display: grid; grid-template-columns: 4px 1fr auto; gap: 2px 10px; align-items: center; padding: 6px 2px;
      border-bottom: 1px solid var(--line); }
    .swatch { align-self: stretch; border-radius: 2px; }
    .stale .swatch { background: var(--stale) !important; }
    .stale .name, .stale .state, .stale .pct { opacity: .6; }
    .name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .name span { font-weight: 400; color: var(--muted); }
    .state { font-size: 12px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .bat { display: flex; align-items: center; gap: 6px; grid-row: span 2; }
    .gauge { width: 30px; height: 11px; border: 1px solid var(--muted); border-radius: 2px; padding: 1px; box-sizing: border-box; position: relative; }
    .gauge::after { content: ""; position: absolute; right: -3px; top: 3px; width: 2px; height: 3px; background: var(--muted); }
    .gauge span { display: block; height: 100%; background: var(--ok); border-radius: 1px; }
    .gauge.mid span { background: var(--warn); }
    .gauge.low span { background: var(--err); }
    .pct { font-size: 12px; min-width: 34px; text-align: right; font-variant-numeric: tabular-nums; }
    .msg { margin: 10px 2px; color: var(--muted); }
  `;

  class AmrFleetWidget extends HTMLElement {
    constructor() {
      super();
      const root = this.attachShadow({ mode: "open" });
      const style = el("style");
      style.textContent = CSS;
      this.box = el("div", "w");
      const head = el("div", "head");
      this.countEl = el("span", "count");
      this.statusEl = el("span", "status");
      this.statusEl.append(el("span", "dot"), el("span", "label", ""));
      const open = el("button", "open", "Open");
      open.type = "button";
      open.title = "Open the AMR Fleet View";
      open.addEventListener("click", () => window.open(BASE + "/?token=" + encodeURIComponent(token()), "_blank", "noopener"));
      head.append(el("span", "title", "AMRs"), this.countEl, this.statusEl, open);
      this.list = el("ul");
      this.msg = el("p", "msg");
      this.msg.hidden = true;
      this.box.append(head, this.list, this.msg);
      root.append(style, this.box);
      this.revision = -1;
      this.rowsKey = "";
      this.amrs = [];
      this.palette = DEFAULT_COLORS;
      this.timer = null;
    }

    connectedCallback() {
      this.loadStyle();
      this.poll();
      this.timer = setInterval(() => this.poll(), POLL_MS);
    }

    disconnectedCallback() {
      clearInterval(this.timer);
      this.timer = null;
    }

    async loadStyle() {
      try {
        const v = await get("/view-config");
        if (v.style.amrColors && v.style.amrColors.length) this.palette = v.style.amrColors;
        this.box.style.setProperty("--accent", v.style.accent);
        this.box.style.setProperty("--stale", v.style.staleAmr);
        this.render();
      } catch (e) { /* keep the defaults; poll() reports access problems */ }
    }

    async poll() {
      if (document.hidden && this.revision >= 0) return;
      try {
        const d = await get("/fleet?since=" + this.revision);
        this.revision = d.revision;
        if (!d.unchanged) this.amrs = d.amrs;
        this.fleet = d;
        this.error = "";
      } catch (e) {
        this.error = e.status === 401 ? "Session expired - please log in again."
          : e.status === 403 ? "No permission to view the AMR fleet."
            : "AMR Fleet View is not reachable.";
      }
      this.render();
    }

    render() {
      const d = this.fleet;
      const status = this.statusEl;
      if (this.error) { status.className = "status error"; status.lastChild.textContent = "error"; }
      else if (!d || !d.revision) { status.className = "status"; status.lastChild.textContent = "no data"; }
      else if (d.stale) { status.className = "status stale"; status.lastChild.textContent = "no update " + Math.round(d.ageMs / 1000) + " s"; }
      else { status.className = "status live"; status.lastChild.textContent = "live"; }
      this.countEl.textContent = this.error ? "" : String(this.amrs.length);
      const stale = !d || d.stale;
      const amrs = this.error ? [] : [...this.amrs].sort((a, b) => a.id.localeCompare(b.id, undefined, { numeric: true }));
      this.msg.hidden = !this.error && amrs.length > 0;
      this.msg.textContent = this.error || "No AMRs reported.";
      // rebuild the rows only when something visible changed (positions change every poll, rows don't)
      const key = JSON.stringify([amrs.map((a) => [a.id, a.name, a.state,
        typeof a.battery === "number" ? Math.round(a.battery) : null]), stale, this.palette]);
      if (key === this.rowsKey) return;
      this.rowsKey = key;
      const rows = amrs.map((a) => {
        const li = el("li", stale ? "stale" : "");
        const swatch = el("span", "swatch");
        swatch.style.background = this.palette[hash(a.id) % this.palette.length];
        const name = el("div", "name", a.id);
        if (a.name) name.append(el("span", "", " · " + a.name));
        const bat = el("div", "bat");
        if (typeof a.battery === "number") {
          const gauge = el("span", "gauge" + (a.battery < 20 ? " low" : a.battery < 40 ? " mid" : ""));
          const fill = el("span");
          fill.style.width = Math.max(0, Math.min(100, a.battery)) + "%";
          gauge.append(fill);
          bat.append(gauge, el("span", "pct", Math.round(a.battery) + " %"));
        }
        swatch.style.gridRow = "span 2";
        li.append(swatch, name, bat, el("div", "state", a.state || "–"));   // state goes under the name
        return li;
      });
      this.list.replaceChildren(...rows);
    }
  }

  if (!customElements.get(TAG)) customElements.define(TAG, AmrFleetWidget);
})();
