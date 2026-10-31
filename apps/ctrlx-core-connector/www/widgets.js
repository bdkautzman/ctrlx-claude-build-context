// Core Connector - widget for the ctrlX home screen (dashboard): this device and every connected core
// with its ctrlX operating state, and a button that opens Core Connector. A Web Component the ctrlX UI
// loads from this file (package manifest uiExtensions.dashboard, tag "ctrlx-core-connector-widget").
// Plain JS, no framework, no CDN.
//
// It runs INSIDE the ctrlX UI (same origin as /ctrlx-core-connector), so it uses the ctrlX UI's login: the
// bearer token in sessionStorage "idm_token" (ctrlX OS 4.6, docs/ctrlx/web-ui-and-reverse-proxy.md →
// "Home screen widgets"). Shadow DOM keeps our CSS and the ctrlX UI's CSS apart. It polls only while it
// is on the page and the page is visible, and rebuilds its rows only when something visible changed.
"use strict";

(function () {
  const BASE = "/ctrlx-core-connector";
  const API = BASE + "/api/v1";
  const TAG = "ctrlx-core-connector-widget";
  const POLL_MS = 3000;
  const LOCAL_ID = "_local";

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
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  const CSS = `
    :host { display: block; height: 100%; min-height: 0; }
    .w { --accent: #007bc0; --ok: #00884a; --warn: #c76b00; --err: #c8102e; --service: #8250df;
      --muted: color-mix(in srgb, currentColor 60%, transparent); --line: color-mix(in srgb, currentColor 14%, transparent);
      box-sizing: border-box; height: 100%; display: flex; flex-direction: column; font: inherit; font-size: 13px;
      line-height: 1.35; color: inherit; padding: 4px 6px; }
    .head { display: flex; align-items: center; gap: 8px; padding: 2px 2px 6px; border-bottom: 1px solid var(--line); }
    .title { font-weight: 600; }
    .count { color: var(--muted); margin-right: auto; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .open { border: 1px solid var(--line); background: none; color: var(--accent); font: inherit; font-size: 12px;
      padding: 2px 8px; border-radius: 4px; cursor: pointer; white-space: nowrap; }
    .open:hover { border-color: var(--accent); }
    ul { list-style: none; margin: 0; padding: 0; overflow-y: auto; flex: 1; min-height: 0; }
    li { display: grid; grid-template-columns: 8px minmax(0, 1fr) auto; gap: 1px 8px; align-items: center; padding: 6px 2px;
      border-bottom: 1px solid var(--line); }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); grid-row: span 2; }
    .connected .dot { background: var(--ok); }
    .error .dot, .offline .dot { background: var(--warn); }
    .name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: inherit; text-decoration: none; }
    a.name:hover { color: var(--accent); text-decoration: underline; }
    .sub { font-size: 12px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .state { grid-row: span 2; font-size: 11px; font-weight: 600; letter-spacing: .02em; padding: 1px 8px; border-radius: 999px;
      border: 1px solid var(--line); color: var(--muted); white-space: nowrap; }
    .op-operating { color: var(--ok); border-color: color-mix(in srgb, var(--ok) 45%, transparent); }
    .op-setup { color: var(--warn); border-color: color-mix(in srgb, var(--warn) 45%, transparent); }
    .op-service { color: var(--service); border-color: color-mix(in srgb, var(--service) 45%, transparent); }
    .not-connected { font-weight: 400; }
    .msg { margin: 10px 2px; color: var(--muted); }
  `;
  const STATE_TEXT = { connecting: "connecting…", error: "not connected", offline: "offline" };

  class CoreConnectorWidget extends HTMLElement {
    constructor() {
      super();
      const root = this.attachShadow({ mode: "open" });
      const style = el("style");
      style.textContent = CSS;
      const box = el("div", "w");
      const head = el("div", "head");
      this.countEl = el("span", "count");
      const open = el("button", "open", "Open");
      open.type = "button";
      open.title = "Open Core Connector";
      open.addEventListener("click", () => window.open(BASE + "/?token=" + encodeURIComponent(token()), "_blank", "noopener"));
      head.append(el("span", "title", "Cores"), this.countEl, open);
      this.list = el("ul");
      this.msg = el("p", "msg");
      this.msg.hidden = true;
      box.append(head, this.list, this.msg);
      root.append(style, box);
      this.cores = null;
      this.error = "";
      this.rowsKey = "";
      this.timer = null;
    }

    connectedCallback() {
      this.poll();
      this.timer = setInterval(() => this.poll(), POLL_MS);
    }

    disconnectedCallback() {
      clearInterval(this.timer);
      this.timer = null;
    }

    async poll() {
      if (document.hidden && this.cores) return;
      try {
        this.cores = await get("/cores");
        this.error = "";
      } catch (e) {
        this.error = e.status === 401 ? "Session expired - please log in again."
          : e.status === 403 ? "No permission to view Core Connector."
            : "Core Connector is not reachable.";
      }
      this.render();
    }

    render() {
      const cores = this.error ? [] : (this.cores || []);
      this.countEl.textContent = this.error || !this.cores ? ""   // this device included
        : cores.filter((c) => c.state === "connected").length + " of " + cores.length + " connected";
      this.msg.hidden = !this.error;
      this.msg.textContent = this.error;
      const key = JSON.stringify(cores.map((c) => [c.name, c.state, c.operatingState, c.hostname, c.host, c.sslPort, c.webUrl]));
      if (key === this.rowsKey) return;   // nothing visible changed
      this.rowsKey = key;
      this.list.replaceChildren(...cores.map((c) => {
        const state = c.state || "connecting";
        const li = el("li", state);
        let name;
        if (c.local) {
          name = el("span", "name", "This device");
        } else {
          name = el("a", "name", c.name);
          name.href = c.webUrl;
          name.target = "_blank";
          name.rel = "noopener noreferrer";
          name.title = "Open the ctrlX web UI of " + c.name;
        }
        const where = c.local ? "" : (c.sslPort === 443 ? c.host : c.host + ":" + c.sslPort);
        const sub = el("span", "sub", [c.hostname, where].filter(Boolean).join(" · ") || (c.local ? location.host : ""));
        const op = state === "connected" ? c.operatingState : null;
        const badge = el("span", "state" + (op ? " op-" + op.toLowerCase() : " not-connected"),
          op || (state === "connected" ? "–" : STATE_TEXT[state] || state));
        badge.title = op ? "ctrlX operating state" : (c.error || "");
        li.append(el("span", "dot"), name, badge, sub);
        return li;
      }));
    }
  }

  if (!customElements.get(TAG)) customElements.define(TAG, CoreConnectorWidget);
})();
