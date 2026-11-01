// Shared by the pages of Core Connector (index.html, browse.html) - plain JS, no build step, no CDN.
//
// Auth: the ctrlX menu opens "/<snap>/?token=${bearertoken}". The token moves from the URL into
// sessionStorage (so it is not left in history/bookmarks; the browser page in the same tab finds it
// there) and is sent as "Authorization: Bearer" on every API call. The reverse proxy validates it
// because "/<snap>/api" is listed as restricted in the package manifest.
"use strict";

window.CC = (function () {
  const PREFIX = "/" + location.pathname.split("/").filter(Boolean)[0]; // "/<snap-name>"
  const API = PREFIX + "/api/v1";
  const TOKEN_KEY = PREFIX.slice(1) + ".token";
  const LOCAL_ID = "_local";

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

  const $ = (id) => document.getElementById(id);
  function showBanner(msg) { const b = $("banner"); b.textContent = msg; b.hidden = !msg; }

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
    if (!res.ok) {
      const msg = (data && data.title) ? data.title + (data.detail ? " - " + data.detail : "") : res.statusText;
      const err = new Error(msg); err.status = res.status; err.data = data; throw err;
    }
    return data;
  }

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (v === true) node.setAttribute(k, "");
      else if (v !== false && v !== null && v !== undefined) node.setAttribute(k, v);
    }
    for (const c of children) if (c !== null && c !== undefined && c !== false) node.append(c);
    return node;
  }
  // <svg class="ic"><use href="#i-name"/></svg> - the symbols are in each page's <svg> sprite
  function icon(name, cls) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "ic" + (cls ? " " + cls : ""));
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "#i-" + name);
    svg.append(use);
    return svg;
  }
  function fmtDuration(s) {
    const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
    return (d ? d + "d " : "") + (h || d ? h + "h " : "") + m + "m " + (s % 60) + "s";
  }
  // web UI of a core: configured ones from the server, this device = the page's own origin
  function webUrl(core) { return core.local ? location.origin + "/" : core.webUrl; }
  function coreTitle(core) { return core.local ? "This device" : core.name; }

  // ---- help menu and tutorial (as in the AMR Fleet View) ----------------------------------------
  // The help button in the top bar opens a menu: the app's page on GitHub and the tutorial - a card at the
  // bottom of the window that steps through the controls of the page; the control of each step blinks (a
  // ring laid over it, position: fixed, so nothing clips it; it stays under the card). The card stays at the
  // bottom unless it would hide the control completely. Everything stays usable while the card is open;
  // "Show me" does the step's action. Each page passes its steps:
  //   {title, text: string | () => string, target: () => element | element[] | null,
  //    when: () => bool (step shown?), act: () => void ("Show me"), canAct: () => bool (button shown?)}
  function setupHelp(steps) {
    const helpBtn = $("btn-help"), helpMenu = $("help-menu");
    function setHelpMenu(open) {
      helpMenu.hidden = !open;
      helpBtn.setAttribute("aria-expanded", String(open));
      if (open) helpMenu.querySelector("[role=menuitem]").focus();
    }
    helpBtn.addEventListener("click", () => setHelpMenu(helpMenu.hidden));
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
    // every frame while open: the ring follows its control (tiles updated, tree loaded, scrolled)
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
      // the card moves to the top only when it would hide the control completely (its place at the bottom)
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
    prev.addEventListener("click", () => step(-1));
    next.addEventListener("click", () => step(1));
    close.addEventListener("click", end);
    tryBtn.addEventListener("click", () => { if (tour && tour.step.act) tour.step.act(); });
    $("btn-tutorial").addEventListener("click", start);
    // ← → step, Escape ends - not while typing, in the tree (it has its own arrow keys) or in a dialog/drawer
    document.addEventListener("keydown", (ev) => {
      if (!tour || !["ArrowLeft", "ArrowRight", "Escape"].includes(ev.key) || ev.altKey || ev.ctrlKey || ev.metaKey) return;
      if (ev.target.closest("input, textarea, select, [role=tree], dialog, .drawer")) return;
      if (ev.key === "Escape" && document.querySelector("dialog[open], .drawer.open")) return;
      ev.preventDefault();
      if (ev.key === "Escape") end(); else step(ev.key === "ArrowRight" ? 1 : -1);
    });
    // a tour handed over from another page ("Show me" on the start page): browse.html?...&tour=1
    const url = new URL(location.href);
    const handedOver = url.searchParams.get("tour") === "1";
    if (handedOver) {
      url.searchParams.delete("tour");
      history.replaceState(null, "", url.pathname + url.search + url.hash);
    }
    return { start, end, refresh: () => { if (tour) render(); }, handedOver };
  }

  return { API, LOCAL_ID, token, $, api, el, icon, showBanner, fmtDuration, webUrl, coreTitle, setupHelp };
})();
