// Template App - widget for the ctrlX home screen (dashboard): the app's state and a few of its values, and a
// button that opens the app. A Web Component the ctrlX UI loads from this file (package manifest
// uiExtensions.dashboard, tag "ctrlx-acme-template-widget"). Plain JS, no framework, no CDN. Adapt ROWS (and the
// title) to the app; docs/ctrlx/web-ui-and-reverse-proxy.md "Home screen widgets".
//
// It runs INSIDE the ctrlX UI (same origin as /ctrlx-acme-template), so it uses the ctrlX UI's login: the bearer
// token in sessionStorage "idm_token". Shadow DOM keeps our CSS and the ctrlX UI's CSS apart. It polls only while it
// is on the page and the page is visible, and rebuilds its rows only when something visible changed (the home
// screen is a heavy page; churning elements also breaks hover and Playwright's waits).
"use strict";

(function () {
  const BASE = "/ctrlx-acme-template";
  const API = BASE + "/api/v1";
  const TAG = "ctrlx-acme-template-widget";
  const TITLE = "Template App";
  const POLL_MS = 3000;
  // the nodes shown (names below the app's Data Layer root) and their labels; users see only what their role allows
  const ROWS = [["status/state", "State"], ["values/label", "Label"], ["values/setpoint", "Setpoint"],
    ["values/enabled", "Enabled"]];

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
    .w { --accent: #007bc0; --ok: #00884a; --warn: #c76b00;
      --muted: color-mix(in srgb, currentColor 60%, transparent); --line: color-mix(in srgb, currentColor 14%, transparent);
      box-sizing: border-box; height: 100%; display: flex; flex-direction: column; font: inherit; font-size: 13px;
      line-height: 1.35; color: inherit; padding: 4px 6px; }
    .head { display: flex; align-items: center; gap: 8px; padding: 2px 2px 6px; border-bottom: 1px solid var(--line); }
    .title { font-weight: 600; }
    .status { margin-right: auto; font-size: 11px; font-weight: 600; padding: 1px 8px; border-radius: 999px;
      border: 1px solid var(--line); color: var(--muted); white-space: nowrap; }
    .status.ok { color: var(--ok); border-color: color-mix(in srgb, var(--ok) 45%, transparent); }
    .status.warn, .status.error { color: var(--warn); border-color: color-mix(in srgb, var(--warn) 45%, transparent); }
    .open { border: 1px solid var(--line); background: none; color: var(--accent); font: inherit; font-size: 12px;
      padding: 2px 8px; border-radius: 4px; cursor: pointer; white-space: nowrap; }
    .open:hover { border-color: var(--accent); }
    dl { margin: 0; padding: 4px 2px; display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 4px 12px;
      overflow-y: auto; flex: 1; min-height: 0; align-content: start; }
    dt { color: var(--muted); }
    dd { margin: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-variant-numeric: tabular-nums; }
    .msg { margin: 10px 2px; color: var(--muted); }
  `;

  class TemplateWidget extends HTMLElement {
    constructor() {
      super();
      const root = this.attachShadow({ mode: "open" });
      const style = el("style");
      style.textContent = CSS;
      const box = el("div", "w");
      const head = el("div", "head");
      this.statusEl = el("span", "status", "…");
      const open = el("button", "open", "Open");
      open.type = "button";
      open.title = "Open " + TITLE;
      open.addEventListener("click", () => window.open(BASE + "/?token=" + encodeURIComponent(token()), "_blank", "noopener"));
      head.append(el("span", "title", TITLE), this.statusEl, open);
      this.list = el("dl");
      this.msg = el("p", "msg");
      this.msg.hidden = true;
      box.append(head, this.list, this.msg);
      root.append(style, box);
      this.data = null;
      this.error = "";
      this.shownKey = "";
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
      if (document.hidden && this.data) return;
      try {
        const [status, values] = await Promise.all([get("/status"), get("/values")]);
        this.data = { connected: status.datalayer.connected, values };
        this.error = "";
      } catch (e) {
        this.error = e.status === 401 ? "Session expired - please log in again."
          : e.status === 403 ? "No permission to view " + TITLE + "."
            : TITLE + " is not reachable.";
      }
      this.render();
    }

    render() {
      const rows = this.error || !this.data ? []
        : ROWS.filter(([name]) => this.data.values[name]).map(([name, label]) => [label, String(this.data.values[name].value)]);
      const state = this.error ? ["error", "error"] : !this.data ? ["", "…"]
        : this.data.connected ? ["ok", "running"] : ["warn", "no Data Layer"];
      const key = JSON.stringify([state, rows, this.error]);
      if (key === this.shownKey) return;          // nothing visible changed
      this.shownKey = key;
      this.statusEl.className = "status " + state[0];
      this.statusEl.textContent = state[1];
      this.list.replaceChildren(...rows.flatMap(([label, value]) => [el("dt", "", label), el("dd", "", value)]));
      this.msg.textContent = this.error;
      this.msg.hidden = !this.error;
    }
  }

  if (!customElements.get(TAG)) customElements.define(TAG, TemplateWidget);   // the file may be loaded twice
})();
