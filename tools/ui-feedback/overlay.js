/* UI feedback overlay of the ctrlX app kit - DEV ONLY, never part of a snap.
 *
 * run-local.sh sets APP_DEV_FEEDBACK to this file; the app's dev web server (src/app/dev_feedback.py) then adds
 * <script src="/<snap>/__dev/feedback.js" defer> to its pages and keeps the notes in <app>/.dev/feedback.json.
 *
 *   Alt+click an element (Option+click on a Mac)   -> type a note -> Save (Ctrl+Enter) or Cancel (Esc)
 *   "Feedback" button, bottom left                  -> open notes, "Pick an element" (no Alt needed), delete a note
 *   numbered pins                                   -> where the open notes are (hover: the note)
 * The skill /ui-feedback works through the open notes and marks them done; reload the page to see its changes.
 * Plain JavaScript, no libraries; its own UI lives in a shadow root, so the app's CSS doesn't touch it.
 */
(() => {
  "use strict";
  if (window.__kitUiFeedback) return;          // the page may load it twice
  window.__kitUiFeedback = true;

  const API = new URL("feedback", document.currentScript ? document.currentScript.src : location.href).href;
  const A = "#e8590c";                          // accent: orange, readable on light and dark app themes

  let items = [];          // open notes from the server
  let doneCount = 0;
  let picking = false;     // "Pick an element": the next click picks, no Alt needed
  let altHeld = false;
  let hoverEl = null;      // element under the pointer
  let target = null;       // element of the note being written
  let picked = null;       // what it was when picked: the app may render it again before Save (tables do)
  let hlEl = null;         // element the highlight box shows
  let lastX = -1, lastY = -1;
  let showPins = true;
  try { showPins = localStorage.getItem("kitUiFeedbackPins") !== "0"; } catch (_) { /* storage blocked */ }

  // ---- the overlay's own DOM -------------------------------------------------------------------------------
  const host = document.createElement("kit-ui-feedback");
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `<style>
    :host { all: initial; }
    * { box-sizing: border-box; }
    [hidden] { display: none !important; }
    .ui { font: 13px/1.4 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; color: #f1f3f5; }
    .mono { font: 11px/1.4 ui-monospace, SFMono-Regular, Consolas, monospace; }
    .hl { position: fixed; z-index: 2147483640; pointer-events: none; border: 2px solid ${A};
          background: rgba(232, 89, 12, .10); border-radius: 3px; }
    .hl-label { position: fixed; z-index: 2147483641; pointer-events: none; max-width: min(70vw, 560px);
                padding: 1px 6px; border-radius: 3px; background: ${A}; color: #fff; white-space: nowrap;
                overflow: hidden; text-overflow: ellipsis; }
    .pin { position: fixed; z-index: 2147483642; width: 18px; height: 18px; margin: -12px 0 0 -12px;
           border-radius: 50%; background: ${A}; color: #fff; font: 700 10px/18px system-ui, sans-serif;
           text-align: center; box-shadow: 0 0 0 2px #fff, 0 1px 4px rgba(0, 0, 0, .4); cursor: pointer; }
    .box { position: fixed; z-index: 2147483645; background: #212529; border: 1px solid #495057; border-radius: 8px;
           box-shadow: 0 8px 28px rgba(0, 0, 0, .45); padding: 10px 12px; }
    .panel { left: 12px; bottom: 52px; width: min(380px, calc(100vw - 24px)); max-height: min(60vh, 480px);
             overflow: auto; }
    .dlg { width: min(360px, calc(100vw - 16px)); }
    .row { display: flex; align-items: center; gap: 8px; }
    .grow { flex: 1; }
    .muted { color: #adb5bd; }
    p { margin: 6px 0 8px; }
    .dlg-sel { color: #ffc078; word-break: break-all; margin-bottom: 4px; }
    .dlg-text { font-size: 12px; margin-bottom: 6px; max-height: 3.4em; overflow: hidden; }
    textarea { display: block; width: 100%; min-height: 76px; resize: vertical; margin: 0 0 8px; padding: 6px 8px;
               border-radius: 5px; border: 1px solid #495057; background: #343a40; color: #f8f9fa;
               font: 13px/1.4 system-ui, sans-serif; }
    textarea:focus { outline: 2px solid ${A}; outline-offset: 0; }
    button { font: inherit; color: #f1f3f5; background: #343a40; border: 1px solid #495057; border-radius: 5px;
             padding: 4px 10px; cursor: pointer; white-space: nowrap; }
    button:hover { background: #495057; }
    button.primary { background: ${A}; border-color: ${A}; color: #fff; }
    button.x { padding: 0 6px; border: 0; background: transparent; color: #adb5bd; }
    button.x:hover { color: #fff; }
    .badge { position: fixed; z-index: 2147483644; left: 12px; bottom: 12px; padding: 5px 10px; border-radius: 16px;
             background: #212529; border: 1px solid ${A}; box-shadow: 0 2px 8px rgba(0, 0, 0, .35); }
    .badge.picking { background: ${A}; color: #fff; }
    .n { display: inline-block; min-width: 18px; padding: 0 5px; margin-left: 4px; border-radius: 9px;
         background: ${A}; color: #fff; font-weight: 700; font-size: 11px; text-align: center; }
    .n:empty { display: none; }
    .badge.picking .n { background: #fff; color: ${A}; }
    .list { margin: 8px 0 0; padding: 0; list-style: none; }
    .list li { display: flex; gap: 8px; align-items: flex-start; padding: 6px 0; border-top: 1px solid #343a40; }
    .list .id { flex: none; width: 20px; height: 20px; border-radius: 50%; background: ${A}; color: #fff;
                font: 700 11px/20px system-ui, sans-serif; text-align: center; }
    .list .body { flex: 1; min-width: 0; cursor: pointer; }
    .list .note { white-space: pre-wrap; overflow-wrap: anywhere; }
    .list .sel { color: #adb5bd; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .list .gone { color: #ffa8a8; }
    .err { color: #ffa8a8; margin: -2px 0 8px; font-size: 12px; }
    .toast { position: fixed; z-index: 2147483646; left: 12px; bottom: 52px; padding: 6px 12px; border-radius: 6px;
             background: #2b8a3e; color: #fff; box-shadow: 0 4px 14px rgba(0, 0, 0, .35); }
    label { display: inline-flex; align-items: center; gap: 4px; color: #ced4da; font-size: 12px; }
  </style>
  <div class="hl" hidden></div><div class="hl-label mono" hidden></div>
  <div class="pins ui"></div>
  <div class="box panel ui" hidden>
    <div class="row"><b>UI feedback</b><span class="muted count"></span><span class="grow"></span>
      <button type="button" class="x" data-act="close" title="Close" aria-label="Close">✕</button></div>
    <p class="muted">Alt+click any element to add a note. Notes go to <span class="mono">.dev/feedback.json</span>;
      then ask Claude Code for <span class="mono">/ui-feedback</span>. Dev server only.</p>
    <div class="row"><button type="button" data-act="pick">Pick an element</button><span class="grow"></span>
      <label><input type="checkbox" class="pins-on"> Pins</label></div>
    <ol class="list"></ol>
  </div>
  <form class="box dlg ui" hidden>
    <div class="dlg-sel mono"></div>
    <div class="dlg-text muted"></div>
    <textarea placeholder="What should change? (Ctrl+Enter saves, Esc cancels)"></textarea>
    <div class="err" hidden></div>
    <div class="row"><button type="button" data-act="parent" title="Select the element around this one">↑ Parent</button>
      <span class="grow"></span>
      <button type="button" data-act="cancel">Cancel</button><button type="submit" class="primary">Save note</button></div>
  </form>
  <button type="button" class="badge ui" data-act="toggle" title="UI feedback (dev server only)"><span class="lbl">✎ Feedback</span><span class="n"></span></button>
  <div class="toast ui" hidden></div>`;
  document.documentElement.appendChild(host);

  const $ = (sel) => root.querySelector(sel);
  const hl = $(".hl"), hlLabel = $(".hl-label"), pins = $(".pins"), panel = $(".panel"), dlg = $(".dlg");
  const badge = $(".badge"), toast = $(".toast"), textarea = $("textarea"), list = $(".list"), err = $(".err");
  $(".pins-on").checked = showPins;

  // Keys and clicks inside the overlay stay there: the app's shortcuts and "click outside closes the menu"
  // handlers don't see them (they would otherwise get them with the overlay host as target).
  for (const type of ["keydown", "keyup", "keypress", "input", "pointerdown", "mousedown", "pointerup", "mouseup",
                      "click", "dblclick"]) {
    root.addEventListener(type, (ev) => ev.stopPropagation());
  }

  // ---- elements and selectors ------------------------------------------------------------------------------
  const ours = (ev) => ev.composedPath().includes(host);

  function normalize(el) {
    if (!(el instanceof Element) || el === host) return null;
    while (el.ownerSVGElement) el = el.ownerSVGElement;      // an icon: the whole <svg>, not its <use>/<path>
    return el;
  }

  function eventElement(ev) {                                // deepest element, also inside open shadow roots
    return normalize(ev.composedPath().find((n) => n instanceof Element));
  }

  function elementAt(x, y) {
    let el = document.elementFromPoint(x, y);
    if (el === host) return null;
    while (el && el.shadowRoot) {
      const inner = el.shadowRoot.elementFromPoint(x, y);
      if (!inner || inner === el) break;
      el = inner;
    }
    return normalize(el);
  }

  function isUnique(scope, sel, node) {
    try {
      const found = scope.querySelectorAll(sel);
      return found.length === 1 && found[0] === node;
    } catch (_) {
      return false;
    }
  }

  // Shortest "a > b > c" path (id, classes, :nth-of-type where needed) that finds exactly `el` in its own
  // document or shadow root.
  function segment(el) {
    const scope = el.getRootNode();
    const parts = [];
    for (let cur = el; cur && cur.nodeType === 1; cur = cur.parentElement) {
      if (cur.id && isUnique(scope, "#" + CSS.escape(cur.id), cur)) {
        parts.unshift("#" + CSS.escape(cur.id));
        break;
      }
      let part = CSS.escape(cur.localName) +
        Array.from(cur.classList).slice(0, 3).map((c) => "." + CSS.escape(c)).join("");
      const parent = cur.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children);
        const sameTag = siblings.filter((c) => c.localName === cur.localName);
        if (sameTag.length > 1 && siblings.filter((c) => c.matches(part)).length > 1) {
          part += ":nth-of-type(" + (sameTag.indexOf(cur) + 1) + ")";
        }
      }
      parts.unshift(part);
      if (isUnique(scope, parts.join(" > "), el)) break;
    }
    return parts.join(" > ");
  }

  // Through shadow roots: "host-selector >>> selector-inside-it"
  function selectorOf(el) {
    const segs = [];
    for (let cur = el; cur;) {
      segs.unshift(segment(cur));
      const rootNode = cur.getRootNode();
      cur = rootNode instanceof ShadowRoot ? rootNode.host : null;
    }
    return segs.join(" >>> ");
  }

  // Full structure from the nearest ancestor with an id (or the root), :nth-of-type at every level that needs it:
  // where the element sits, also in a table or list the app renders again
  function pathOf(el) {
    const segs = [];
    for (let cur = el; cur;) {
      const scope = cur.getRootNode();
      const parts = [];
      for (let e = cur; e && e.nodeType === 1; e = e.parentElement) {
        if (e.id && isUnique(scope, "#" + CSS.escape(e.id), e)) {
          parts.unshift("#" + CSS.escape(e.id));
          break;
        }
        let part = CSS.escape(e.localName);
        const parent = e.parentElement;
        if (parent) {
          const sameTag = Array.from(parent.children).filter((c) => c.localName === e.localName);
          if (sameTag.length > 1) part += ":nth-of-type(" + (sameTag.indexOf(e) + 1) + ")";
        }
        parts.unshift(part);
      }
      segs.unshift(parts.join(" > "));
      cur = scope instanceof ShadowRoot ? scope.host : null;
    }
    return segs.join(" >>> ");
  }

  function find(sel) {
    let scope = document, el = null;
    for (const seg of String(sel).split(" >>> ")) {
      if (!scope) return null;
      try {
        el = scope.querySelector(seg);
      } catch (_) {
        return null;
      }
      if (!el) return null;
      scope = el.shadowRoot;
    }
    return el;
  }

  const locate = (it) => (it.path && find(it.path)) || find(it.selector);    // a stored note's element now

  const textOf = (el) => (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();

  // ---- highlight box -----------------------------------------------------------------------------------------
  function showHighlight(el) {
    if (!el || !el.isConnected) {
      hl.hidden = hlLabel.hidden = true;
      hlEl = null;
      return;
    }
    const r = el.getBoundingClientRect();
    Object.assign(hl.style, { left: r.left - 2 + "px", top: r.top - 2 + "px",
                              width: r.width + 4 + "px", height: r.height + 4 + "px" });
    if (el !== hlEl) hlLabel.textContent = selectorOf(el);
    hlLabel.style.left = Math.max(4, Math.min(r.left, innerWidth - 120)) + "px";
    hlLabel.style.top = (r.top > 24 ? r.top - 22 : Math.min(r.bottom + 4, innerHeight - 22)) + "px";
    hl.hidden = hlLabel.hidden = false;
    hlEl = el;
  }

  // The picked element, found again by its path when the app replaced it with a new one
  function liveTarget() {
    if (target && !target.isConnected && picked) target = find(picked.path || picked.selector) || target;
    return target;
  }

  const refresh = () => showHighlight(picking || altHeld ? hoverEl : liveTarget());

  // ---- picking: Alt+click, or "Pick an element" ---------------------------------------------------------------
  function setPicking(on) {
    picking = on;
    badge.classList.toggle("picking", on);
    $(".lbl").textContent = on ? "Click an element · Esc cancels" : "✎ Feedback";
    if (on) panel.hidden = true;
    refresh();
  }

  window.addEventListener("pointermove", (ev) => {
    lastX = ev.clientX;
    lastY = ev.clientY;
    altHeld = ev.altKey;
    hoverEl = ours(ev) ? null : eventElement(ev);
    if (picking || altHeld || hlEl !== target) refresh();
  }, { capture: true, passive: true });

  window.addEventListener("keydown", (ev) => {
    if (ev.key === "Alt") {
      altHeld = true;
      hoverEl = lastX >= 0 ? elementAt(lastX, lastY) : null;
      refresh();
    } else if (ev.key === "Escape" && (picking || !dlg.hidden || !panel.hidden)) {
      ev.preventDefault();
      ev.stopImmediatePropagation();
      if (!dlg.hidden) closeDialog();
      else if (picking) setPicking(false);
      else panel.hidden = true;
    }
  }, true);
  window.addEventListener("keyup", (ev) => {
    if (ev.key === "Alt") {
      altHeld = false;
      refresh();
    }
  }, true);
  window.addEventListener("blur", () => {
    altHeld = false;
    refresh();
  });

  // While picking, the app gets none of these events: no focus, no menu, no navigation (Chrome downloads a link on
  // Alt+click otherwise).
  const picks = (ev) => (ev.altKey || picking) && !ours(ev);
  for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "dblclick", "auxclick", "contextmenu"]) {
    window.addEventListener(type, (ev) => {
      if (picks(ev)) {
        ev.preventDefault();
        ev.stopImmediatePropagation();
      }
    }, true);
  }
  window.addEventListener("click", (ev) => {
    if (!picks(ev)) return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    const el = eventElement(ev);
    setPicking(false);
    if (el) openDialog(el);
  }, true);

  // ---- the note dialog ---------------------------------------------------------------------------------------
  function snapshot(el) {
    const r = el.getBoundingClientRect(), outer = el.outerHTML || "";
    const selector = selectorOf(el), path = pathOf(el);
    return {
      selector,
      path: path !== selector ? path : undefined,
      text: textOf(el).slice(0, 300),
      html: outer.slice(0, outer.indexOf(">") + 1).slice(0, 500),
      rect: { x: Math.round(r.left), y: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height) },
    };
  }

  function openDialog(el) {                         // keeps a note already typed when another element is picked
    target = el;
    picked = snapshot(el);
    $(".dlg-sel").textContent = picked.selector;
    const text = picked.text;
    $(".dlg-text").textContent = text ? "“" + text.slice(0, 140) + (text.length > 140 ? "…" : "") + "”" : "";
    $("[data-act=parent]").disabled = !parentOf(el);
    err.hidden = true;
    dlg.hidden = false;
    placeDialog();
    refresh();
    textarea.focus();
  }

  function parentOf(el) {                           // up to <body>; out of a shadow root to its host
    const rootNode = el.getRootNode();
    const up = el.parentElement || (rootNode instanceof ShadowRoot ? rootNode.host : null);
    return up && up !== document.documentElement ? up : null;
  }

  function placeDialog() {
    if (!liveTarget() || dlg.hidden) return;
    const r = target.getBoundingClientRect(), w = dlg.offsetWidth, h = dlg.offsetHeight;
    let top = r.bottom + 8;
    if (top + h > innerHeight - 8) top = r.top - h - 8;
    if (top < 8) top = Math.max(8, innerHeight - h - 8);
    dlg.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + "px";
    dlg.style.top = top + "px";
  }

  function closeDialog() {
    dlg.hidden = true;
    target = picked = null;
    textarea.value = "";
    refresh();
  }

  function pagePath() {
    const u = new URL(location.href);
    u.searchParams.delete("token");                 // never store a login token in the notes
    return u.pathname + u.search + u.hash;
  }

  function localIso(d) {                            // local time with its UTC offset
    const p = (n) => String(Math.floor(Math.abs(n))).padStart(2, "0");
    const off = -d.getTimezoneOffset();
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + "T" + p(d.getHours()) + ":" +
      p(d.getMinutes()) + ":" + p(d.getSeconds()) + (off >= 0 ? "+" : "-") + p(off / 60) + ":" + p(off % 60);
  }

  dlg.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const note = textarea.value.trim();
    if (!note || !picked) {
      textarea.focus();
      return;
    }
    const theme = document.documentElement.getAttribute("data-theme");
    const body = {
      note,
      ...picked,                                    // selector, path, text, html, rect - as picked
      page: pagePath(),
      viewport: { width: innerWidth, height: innerHeight, dpr: devicePixelRatio,
                  scrollX: Math.round(scrollX), scrollY: Math.round(scrollY) },
      colorScheme: theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"),
      timestamp: localIso(new Date()),
    };
    try {
      const res = await fetch(API, { method: "POST", headers: { "Content-Type": "application/json" },
                                     body: JSON.stringify(body) });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || data.title || "HTTP " + res.status);
      closeDialog();
      flash("Note #" + data.id + " saved");
      await reload();
    } catch (e) {
      err.textContent = "Not saved: " + e.message;
      err.hidden = false;
    }
  });

  textarea.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
      ev.preventDefault();
      dlg.requestSubmit();
    }
  });

  let toastTimer = 0;
  function flash(text) {
    toast.textContent = text;
    toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toast.hidden = true; }, 2200);
  }

  // ---- open notes: list, pins --------------------------------------------------------------------------------
  async function reload() {
    try {
      const res = await fetch(API, { cache: "no-store" });
      if (!res.ok) return;
      const all = ((await res.json()).items || []).filter((i) => i && typeof i === "object");
      items = all.filter((i) => i.status === "open");
      doneCount = all.length - items.length;
    } catch (_) {
      return;                                       // dev server restarting
    }
    renderList();
    renderPins();
  }

  function renderList() {
    $(".n").textContent = items.length ? String(items.length) : "";
    $(".count").textContent = items.length + " open" + (doneCount ? " · " + doneCount + " handled" : "");
    list.textContent = "";
    for (const it of items) {
      const li = document.createElement("li");
      li.innerHTML = '<span class="id"></span><div class="body"><div class="note"></div><div class="sel mono"></div>' +
        '</div><button type="button" class="x" title="Delete this note" aria-label="Delete this note">✕</button>';
      li.querySelector(".id").textContent = it.id;
      li.querySelector(".note").textContent = it.note;
      const sel = li.querySelector(".sel");
      sel.textContent = it.selector;
      sel.title = it.selector;
      if (!locate(it)) {
        sel.classList.add("gone");
        sel.textContent = "not on the page now · " + it.selector;
      }
      li.querySelector(".body").addEventListener("click", () => {
        const el = locate(it);
        if (!el) return;
        el.scrollIntoView({ block: "center" });
        showHighlight(el);
        setTimeout(refresh, 1500);
      });
      li.querySelector("button").addEventListener("click", () => removeItem(it.id));
      list.appendChild(li);
    }
  }

  async function removeItem(id) {
    try {
      const res = await fetch(API + "/" + id, { method: "DELETE" });
      if (!res.ok && res.status !== 404) throw new Error("HTTP " + res.status);
      flash("Note #" + id + " deleted");
    } catch (e) {
      flash("Not deleted: " + e.message);
    }
    await reload();
  }

  function renderPins() {
    pins.textContent = "";
    if (!showPins) return;
    for (const it of items) {
      const pin = document.createElement("div");
      pin.className = "pin";
      pin.textContent = it.id;
      pin.title = "#" + it.id + ": " + it.note;
      pin.dataset.sel = it.selector;
      if (it.path) pin.dataset.path = it.path;
      pin.addEventListener("click", () => {
        panel.hidden = false;
        reload();
      });
      pins.appendChild(pin);
    }
    placePins();
  }

  function placePins() {
    for (const pin of pins.children) {
      const el = locate({ path: pin.dataset.path, selector: pin.dataset.sel });
      const r = el && el.getBoundingClientRect();
      if (!r || (!r.width && !r.height) || r.bottom < 0 || r.top > innerHeight) {
        pin.hidden = true;
        continue;
      }
      pin.hidden = false;
      pin.style.left = Math.min(Math.max(r.left, 10), innerWidth - 10) + "px";
      pin.style.top = Math.min(Math.max(r.top, 10), innerHeight - 10) + "px";
    }
  }

  // ---- buttons, layout updates -------------------------------------------------------------------------------
  root.addEventListener("click", (ev) => {
    const act = ev.target.closest && ev.target.closest("[data-act]");
    if (!act) return;
    const what = act.getAttribute("data-act");
    if (what === "toggle") {
      if (picking) setPicking(false);
      else {
        panel.hidden = !panel.hidden;
        if (!panel.hidden) reload();
      }
    } else if (what === "close") panel.hidden = true;
    else if (what === "pick") setPicking(true);
    else if (what === "cancel") closeDialog();
    else if (what === "parent" && liveTarget() && parentOf(target)) openDialog(parentOf(target));
  });

  $(".pins-on").addEventListener("change", (ev) => {
    showPins = ev.target.checked;
    try { localStorage.setItem("kitUiFeedbackPins", showPins ? "1" : "0"); } catch (_) { /* storage blocked */ }
    renderPins();
  });

  let frame = 0;
  const relayout = () => {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      placePins();
      placeDialog();
      refresh();
    });
  };
  window.addEventListener("scroll", relayout, { capture: true, passive: true });
  window.addEventListener("resize", relayout);
  setInterval(relayout, 1000);                                  // the app re-renders: pins follow
  setInterval(() => { if (!document.hidden) reload(); }, 5000); // the skill marks notes done
  reload();
})();
