// Barcode Reader web UI - plain JS, no build step, no CDN (devices are often offline).
//
// Auth: the ctrlX menu opens "/<snap>/?token=${bearertoken}". We move the token from the URL
// into sessionStorage (so it is not left in history/bookmarks) and send it as
// "Authorization: Bearer" on every API call. The reverse proxy validates it because
// "/<snap>/api" is listed as restricted in the package manifest. Images are API data too:
// they are fetched with the token and shown through object URLs.
"use strict";

(function () {
  const PREFIX = "/" + location.pathname.split("/").filter(Boolean)[0]; // "/<snap-name>"
  const API = PREFIX + "/api/v1";
  const TOKEN_KEY = PREFIX.slice(1) + ".token";
  const SVG_NS = "http://www.w3.org/2000/svg";

  // ---- token ---------------------------------------------------------------
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

  function authHeaders(extra) {
    const headers = Object.assign({ "Accept": "application/json" }, extra || {});
    if (token) headers["Authorization"] = "Bearer " + token;
    return headers;
  }

  async function api(method, path, body) {
    const headers = authHeaders(body === undefined ? {} : { "Content-Type": "application/json" });
    const res = await fetch(API + path, {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin", cache: "no-store",
    });
    let data = null;
    const text = await res.text();
    if (text) { try { data = JSON.parse(text); } catch (e) { data = { title: text }; } }
    if (res.status === 401) showBanner("Session expired or no token - please reopen this app from the ctrlX menu.");
    if (!res.ok) { const err = new Error((data && (data.detail || data.title)) || res.statusText); err.status = res.status; err.data = data; throw err; }
    return data;
  }

  // binary data (images) -> {status, blob, headers}; 204 -> blob null
  async function apiBlob(path) {
    const res = await fetch(API + path, { headers: authHeaders({ "Accept": "image/*" }), credentials: "same-origin", cache: "no-store" });
    if (res.status === 401) showBanner("Session expired or no token - please reopen this app from the ctrlX menu.");
    if (res.status === 204) return { status: 204, blob: null, headers: res.headers };
    if (!res.ok) { const err = new Error(res.statusText); err.status = res.status; throw err; }
    return { status: res.status, blob: await res.blob(), headers: res.headers };
  }

  // ---- helpers -------------------------------------------------------------
  const $ = (id) => document.getElementById(id);
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (v === true) node.setAttribute(k, "");
      else if (v !== false && v !== null && v !== undefined) node.setAttribute(k, v);
    }
    for (const c of children) if (c) node.append(c);
    return node;
  }
  function svg(tag, attrs, text) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function showBanner(msg) { const b = $("banner"); b.textContent = msg; b.hidden = !msg; }
  function humanize(key) { return key.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/^./, (c) => c.toUpperCase()); }
  // the device's own time (ISO 8601 with its offset), like the Data Layer node and a PLC log - not the browser's zone
  function fmtTime(iso) {
    if (!iso) return "–";
    const m = /T(\d\d:\d\d:\d\d(\.\d+)?)/.exec(iso);
    return m ? m[1] : iso;
  }
  function fmtMs(ms) { return ms === null || ms === undefined ? "–" : (ms >= 1000 ? (ms / 1000).toFixed(2) + " s" : Math.round(ms) + " ms"); }
  const SOURCES = { ui: "Web UI", trigger: "Data Layer trigger", request: "Data Layer request", continuous: "Continuous" };
  function sourceText(r) { return (SOURCES[r.source] || r.source) + (r.requestId ? " #" + r.requestId : ""); }
  function resultState(r) { return r.error ? "error" : (r.ok ? "ok" : "noread"); }

  let canWrite = false;
  let formats = [];               // [{name, label, kind}]
  let settingsNow = null;         // settings as last read (region drawing, live interval)
  let reader = null;              // last /reader answer
  let shown = null;               // {kind: "result"|"live", result?, width, height}
  let viewSeq = 0;                // sequence pinned from the history (0 = follow the newest)
  let imgUrl = null;

  // ---- status / permissions ---------------------------------------------------
  async function refreshStatus() {
    try {
      const s = await api("GET", "/status");
      const before = canWrite;
      canWrite = !!s.canWrite;
      document.title = s.title;
      $("app-title").textContent = s.title;
      $("app-version").textContent = /^\d/.test(s.version) ? "v" + s.version : s.version;
      const conn = $("conn");
      if (s.datalayer.connected) { conn.className = "pill pill-ok"; conn.textContent = "Data Layer: connected"; }
      else { conn.className = "pill pill-warn"; conn.textContent = s.datalayer.enabled ? "Data Layer: connecting…" : "Data Layer: off"; }
      const libs = s.libraries || {};
      conn.title = "Data Layer connection of this app · " + Object.entries(libs).map(([k, v]) => k + " " + v).join(", ");
      for (const b of document.querySelectorAll("[data-write]")) if (b.id !== "btn-region-clear") b.hidden = !canWrite;
      $("btn-settings").hidden = !canWrite;
      if (!canWrite && $("drawer").classList.contains("open")) closeSettings();
      if (before !== canWrite) updateRegionButtons();
      showBanner("");
    } catch (e) {
      if (e.status !== 401) showBanner("Cannot reach the app: " + e.message);
    }
  }

  async function refreshSettingsNow() {
    try { settingsNow = await api("GET", "/settings"); } catch (e) { /* shown elsewhere */ }
    updateRegionButtons();
  }
  function currentRoi() {
    const r = settingsNow && settingsNow.decode && settingsNow.decode.roi;
    return r && r.enabled ? [r.x, r.y, r.width, r.height] : null;
  }
  function updateRegionButtons() { $("btn-region-clear").hidden = !canWrite || !currentRoi(); }

  // ---- reader state -------------------------------------------------------------
  const CAM_STATES = { connected: ["pill-ok", "connected"], connecting: ["pill-unknown", "connecting…"],
                       disconnected: ["pill-unknown", "disconnected"], error: ["pill-warn", "error"] };
  async function refreshReader() {
    let r;
    try { r = await api("GET", "/reader"); } catch (e) { return; }
    reader = r;
    const cam = r.camera || {};
    const info = cam.info || {};
    const [cls, word] = CAM_STATES[cam.state] || ["pill-unknown", cam.state || "?"];
    const pill = $("cam-pill");
    pill.className = "pill " + cls;
    pill.textContent = "Camera: " + (cam.state === "connected" ? (info.model || "connected") + (r.source === "simulated" ? " (simulated)" : "") : word);
    pill.title = cam.error || "Camera";
    renderCamera(r);
    setPressed($("btn-continuous"), r.continuous);
    $("btn-live").disabled = r.continuous;
    if (r.continuous && liveOn) stopLive();
    const last = r.last;
    if (last && !liveOn && !viewSeq && (!shown || shown.kind !== "result" || shown.result.sequence !== last.sequence)) {
      showResult(last);
    }
    if (last && last.sequence !== historySeq) refreshHistory();
    $("btn-download").disabled = !last || !last.width || (viewSeq && viewSeq !== last.sequence) || liveOn;
  }

  function renderCamera(r) {
    const cam = r.camera || {};
    const info = cam.info || {};
    const stats = cam.stats || {};
    const rows = [["State", cam.state + (r.source === "simulated" ? " · simulated" : "")]];
    if (info.model) rows.push(["Model", [info.vendor, info.model].filter(Boolean).join(" ")]);
    if (info.serial) rows.push(["Serial", info.serial + (info.firmware ? " · fw " + info.firmware : "")]);
    if (info.address) rows.push(["Address", info.address + (info.interface ? " (via " + info.interface + ")" : "")]);
    if (info.width) rows.push(["Image", info.width + " × " + info.height + " · " + (info.pixelFormat || "?")]);
    if (info.exposureUs !== undefined && info.exposureUs !== null) rows.push(["Exposure / gain", Math.round(info.exposureUs) + " µs · " + info.gain]);
    if (info.acquisition) rows.push(["Acquisition", info.acquisition]);
    if (info.packetSize) rows.push(["Stream", "port " + info.streamPort + " · packets " + info.packetSize + " B" +
      (info.packetDelayUs !== undefined ? " · delay " + info.packetDelayUs + " µs" : "")]);
    rows.push(["Images", cam.frames + " received · " + cam.failures + " failed" +
      (stats.missingPackets ? " · " + stats.missingPackets + " packets missing" : "") +
      (stats.resentPackets ? " · " + stats.resentPackets + " resent" : "")]);
    $("camera-kv").replaceChildren(...rows.flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: String(v) })]));
    $("camera-error").textContent = cam.error || "";
    $("camera-error").hidden = !cam.error;
    const warnings = info.warnings || [];
    $("camera-warnings").replaceChildren(...warnings.map((w) => el("li", { text: w })));
    $("camera-warnings").hidden = !warnings.length;
    const st = r.storage;
    let line = "Images are kept in RAM only (history).";
    if (st && st.target === "medium") {
      line = "Saving " + (st.save === "noread" ? "no-reads" : "all reads") + " to " + (st.path || "/media/" + st.medium) +
        ": " + st.state + (st.error ? " (" + st.error + ")" : "") + " · " + st.saved + " saved" +
        (st.dropped ? " · " + st.dropped + " not saved" : "") + (st.bytes ? " · " + (st.bytes / 1048576).toFixed(1) + " MB" : "");
    }
    $("storage-line").textContent = line;
  }

  function setPressed(btn, on) { btn.setAttribute("aria-pressed", on ? "true" : "false"); }

  // ---- image + overlay ------------------------------------------------------------
  function setImage(blob) {
    const url = URL.createObjectURL(blob);
    const img = $("img");
    return new Promise((resolve) => {
      img.onload = () => { if (imgUrl) URL.revokeObjectURL(imgUrl); imgUrl = url; img.hidden = false; $("placeholder").hidden = true; placeOverlay(); resolve(); };
      img.onerror = () => { URL.revokeObjectURL(url); resolve(); };
      img.src = url;
    });
  }

  function placeOverlay() {
    const img = $("img"), ov = $("overlay");
    if (img.hidden || !shown) { ov.replaceChildren(); return; }
    ov.style.left = img.offsetLeft + "px";
    ov.style.top = img.offsetTop + "px";
    ov.style.width = img.clientWidth + "px";
    ov.style.height = img.clientHeight + "px";
    ov.setAttribute("viewBox", "0 0 " + shown.width + " " + shown.height);
    ov.setAttribute("preserveAspectRatio", "none");
    drawOverlay();
  }

  function drawOverlay(dragRect) {
    const ov = $("overlay");
    ov.replaceChildren();
    if (!shown) return;
    const W = shown.width, H = shown.height;
    const scale = W / Math.max(1, $("img").clientWidth);      // image pixels per screen pixel
    const roi = dragRect || (shown.kind === "result" && viewSeq ? shown.result.region : currentRoi());
    if (roi) {
      const [x, y, w, h] = roi;
      ov.append(svg("path", { class: "roi-shade", "fill-rule": "evenodd",
        d: `M0 0H${W}V${H}H0Z M${x} ${y}V${y + h}H${x + w}V${y}Z` }));
      ov.append(svg("rect", { class: "roi", x, y, width: w, height: h }));
    }
    if (shown.kind !== "result") return;
    shown.result.codes.forEach((c, i) => {
      const pts = c.corners.map((p) => p.join(",")).join(" ");
      ov.append(svg("polygon", { class: "code-shape", points: pts }));
      const top = c.corners.reduce((a, p) => (p[1] < a[1] || (p[1] === a[1] && p[0] < a[0]) ? p : a), c.corners[0]);
      const label = (i + 1) + " " + c.label;
      const fs = 13 * scale, pad = 4 * scale;
      const tw = label.length * fs * 0.6 + 2 * pad;
      const ty = Math.max(0, top[1] - fs - 2 * pad - 2 * scale);
      const tx = Math.max(0, Math.min(top[0], W - tw));      // keep the tag inside the image
      ov.append(svg("rect", { class: "code-tag", x: tx, y: ty, width: tw, height: fs + 2 * pad, rx: 3 * scale }));
      ov.append(svg("text", { class: "code-text", x: tx + pad, y: ty + pad + fs * 0.85, "font-size": fs }, label));
    });
  }

  async function showResult(result) {
    shown = { kind: "result", result, width: result.width, height: result.height };
    renderResult(result);
    if (result.width) {
      try {
        const { blob } = await apiBlob("/images/" + result.sequence + "/display");
        if (shown.kind === "result" && shown.result.sequence === result.sequence) await setImage(blob);
      } catch (e) { /* dropped from the history meanwhile */ }
    } else {          // the read got no image (camera error): don't leave the previous one on screen
      $("img").hidden = true;
      $("placeholder").hidden = false;
      $("placeholder").textContent = "No image: " + (result.error || "unknown error");
      placeOverlay();
    }
    const parts = ["#" + result.sequence];
    if (result.width) parts.push(result.width + " × " + result.height + " " + result.pixelFormat);
    if (result.grabMs !== null && result.grabMs !== undefined) parts.push("image " + fmtMs(result.grabMs));
    parts.push("decode " + fmtMs(result.decodeMs));
    if (result.region) parts.push("region " + result.region.join(", "));
    if (result.saved) parts.push("saved to the medium");
    $("img-info").textContent = parts.join(" · ");
  }

  function renderResult(r) {
    const state = resultState(r);
    const badge = $("result-badge");
    badge.className = "badge badge-" + state;
    badge.textContent = { ok: r.count > 1 ? r.count + " CODES" : "OK", noread: "NO READ", error: "ERROR" }[state];
    const list = $("codes");
    if (r.codes.length) {
      list.replaceChildren(...r.codes.map((c, i) => el("li", {},
        el("span", { class: "fmt", text: (i + 1) + " · " + c.label }), el("span", { class: "txt", text: c.text }))));
    } else {
      list.replaceChildren(el("li", { class: "muted", text: r.error ? "No image decoded." : "No code found in the image." }));
    }
    $("result-error").textContent = r.error || "";
    $("result-error").hidden = !r.error;
    $("r-seq").textContent = "#" + r.sequence;
    $("r-source").textContent = sourceText(r);
    $("r-time").textContent = fmtTime(r.timestamp);
    $("r-duration").textContent = fmtMs(r.durationMs);
    const viewing = viewSeq && reader && reader.last && viewSeq !== reader.last.sequence;
    $("viewing-note").hidden = !viewSeq;
    $("viewing-text").textContent = viewSeq ? "Showing read #" + viewSeq + " from the history" + (viewing ? "" : " (the newest)") : "";
  }

  // ---- read / continuous / live ------------------------------------------------------
  async function doRead() {
    const btn = $("btn-read");
    btn.disabled = true;
    viewSeq = 0;
    if (liveOn) stopLive();
    try {
      const r = await api("POST", "/read", { wait: true });
      await showResult(r);
      refreshHistory();
    } catch (e) {
      showBanner("Read failed: " + e.message);
    } finally {
      btn.disabled = false;
    }
  }

  async function toggleContinuous() {
    const on = $("btn-continuous").getAttribute("aria-pressed") !== "true";
    viewSeq = 0;
    try { await api("POST", "/continuous", { enabled: on }); setPressed($("btn-continuous"), on); }
    catch (e) { showBanner("Continuous reading: " + e.message); }
    refreshReader();
  }

  let liveOn = false, liveSeq = 0, liveTimer = null;
  function startLive() {
    liveOn = true;
    viewSeq = 0;
    setPressed($("btn-live"), true);
    $("btn-live").classList.add("live-on");
    $("viewing-note").hidden = true;
    liveSeq = 0;
    liveTick();
  }
  function stopLive() {
    liveOn = false;
    clearTimeout(liveTimer);
    setPressed($("btn-live"), false);
    $("btn-live").classList.remove("live-on");
    if (reader && reader.last) showResult(reader.last);
  }
  async function liveTick() {
    if (!liveOn) return;
    const interval = Math.max(150, ((settingsNow && settingsNow.live && settingsNow.live.intervalMs) || 500) / 2);
    try {
      const res = await apiBlob("/live?after=" + liveSeq);
      if (res.blob && liveOn) {
        liveSeq = Number(res.headers.get("X-Live-Seq")) || liveSeq + 1;
        shown = { kind: "live", width: Number(res.headers.get("X-Image-Width")), height: Number(res.headers.get("X-Image-Height")) };
        await setImage(res.blob);
        $("img-info").textContent = "Live " + shown.width + " × " + shown.height + " · not decoded · frame " + liveSeq;
      }
    } catch (e) {
      if (e.status === 403) { stopLive(); return; }
    }
    if (liveOn) liveTimer = setTimeout(liveTick, interval);
  }

  // ---- decode region: drawn on the image ---------------------------------------------
  let drawing = false, dragStart = null, dragRect = null;
  function toggleRegion() {
    drawing = !drawing;
    setPressed($("btn-region"), drawing);
    $("stage").classList.toggle("drawing", drawing);
    if (drawing && !shown) showBanner("Read an image or start the live picture first, then draw the region on it.");
  }
  function toImage(ev) {
    const r = $("overlay").getBoundingClientRect();
    const x = Math.round((ev.clientX - r.left) / r.width * shown.width);
    const y = Math.round((ev.clientY - r.top) / r.height * shown.height);
    return [Math.max(0, Math.min(shown.width, x)), Math.max(0, Math.min(shown.height, y))];
  }
  function onDown(ev) {
    if (!drawing || !shown) return;
    ev.preventDefault();
    $("overlay").setPointerCapture(ev.pointerId);
    dragStart = toImage(ev);
    dragRect = [dragStart[0], dragStart[1], 0, 0];
  }
  function onMove(ev) {
    if (!dragStart) return;
    const p = toImage(ev);
    dragRect = [Math.min(p[0], dragStart[0]), Math.min(p[1], dragStart[1]), Math.abs(p[0] - dragStart[0]), Math.abs(p[1] - dragStart[1])];
    drawOverlay(dragRect);
  }
  async function onUp() {
    if (!dragStart) return;
    const rect = dragRect;
    dragStart = dragRect = null;
    if (!rect || rect[2] < 16 || rect[3] < 16) { drawOverlay(); return; }
    await saveRegion({ enabled: true, x: rect[0], y: rect[1], width: rect[2], height: rect[3] });
    toggleRegion();
  }
  async function saveRegion(roi) {
    try {
      const s = await api("GET", "/settings");
      s.decode.roi = Object.assign({}, s.decode.roi, roi);
      settingsNow = await api("PUT", "/settings", s);
      if (loaded) { loaded = settingsNow; fillForm(loaded); }
    } catch (e) {
      showBanner("Region not saved: " + ((e.data && e.data.errors && e.data.errors.join("; ")) || e.message));
    }
    updateRegionButtons();
    drawOverlay();
  }

  // ---- history --------------------------------------------------------------------
  let historySeq = -1;
  const thumbs = new Map();       // sequence -> object URL
  async function refreshHistory() {
    let list;
    try { list = await api("GET", "/history"); } catch (e) { return; }
    historySeq = list.length ? list[0].sequence : 0;
    $("history-count").textContent = list.length ? "(" + list.length + ")" : "";
    const keep = new Set(list.map((r) => r.sequence));
    for (const [seq, url] of thumbs) if (!keep.has(seq)) { URL.revokeObjectURL(url); thumbs.delete(seq); }
    const body = $("history-body");
    if (!list.length) { body.replaceChildren(el("tr", {}, el("td", { colspan: "6", class: "muted", text: "No reads yet." }))); return; }
    body.replaceChildren(...list.map((r) => {
      const state = resultState(r);
      const img = el("img", { class: "thumb", alt: "" });
      if (thumbs.has(r.sequence)) img.src = thumbs.get(r.sequence);
      else if (r.width) loadThumb(r.sequence, img);
      const codes = el("td", { class: "codes-cell" });
      if (r.codes.length) r.codes.forEach((c, i) => { if (i) codes.append(document.createTextNode("  ")); codes.append(el("span", { class: "fmt", text: c.label }), document.createTextNode(c.text)); });
      else codes.append(el("span", { class: "st-" + state, text: r.error || "no read" }));
      const tr = el("tr", { tabindex: "0", class: r.sequence === (viewSeq || (shown && shown.result && shown.result.sequence)) ? "selected" : "" },
        el("td", { class: "num", text: String(r.sequence) }), el("td", { text: fmtTime(r.timestamp) }),
        el("td", { text: sourceText(r) }), el("td", {}, img), codes, el("td", { class: "num", text: fmtMs(r.durationMs) }));
      tr.onclick = () => pinHistory(r);
      tr.onkeydown = (ev) => { if (ev.key === "Enter") pinHistory(r); };
      return tr;
    }));
  }
  // at most 3 thumbnail requests at a time: a full history would otherwise open 50 connections at once
  const thumbQueue = [];
  let thumbsLoading = 0;
  function loadThumb(seq, img) {
    thumbQueue.push([seq, img]);
    pumpThumbs();
  }
  async function pumpThumbs() {
    while (thumbsLoading < 3 && thumbQueue.length) {
      const [seq, img] = thumbQueue.shift();
      if (thumbs.has(seq)) { img.src = thumbs.get(seq); continue; }
      thumbsLoading++;
      apiBlob("/images/" + seq + "/thumb")
        .then(({ blob }) => { const url = URL.createObjectURL(blob); thumbs.set(seq, url); img.src = url; })
        .catch(() => { /* dropped from the history meanwhile */ })
        .finally(() => { thumbsLoading--; pumpThumbs(); });
    }
  }
  function pinHistory(r) {
    if (liveOn) stopLive();
    viewSeq = r.sequence;
    showResult(r);
    for (const tr of $("history-body").children) tr.classList.toggle("selected", tr.firstChild && tr.firstChild.textContent === String(r.sequence));
  }

  async function download() {
    const last = reader && reader.last;
    if (!last) return;
    try {
      const { blob } = await apiBlob("/images/" + last.sequence + "/full");
      const a = el("a", { href: URL.createObjectURL(blob), download: "barcode-reader-" + last.sequence + ".png" });
      document.body.append(a);
      a.click();
      setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    } catch (e) {
      showBanner("Download failed: " + (e.status === 404 ? "only the newest read has a full-size image" : e.message));
    }
  }

  // ---- settings form (generated from defaults + rules) ---------------------
  let meta = null;
  let loaded = null;

  function enumText(full, opt) {
    if (opt === "" && full === "camera.pixelFormat") return "(keep the camera's format)";
    return String(opt);
  }

  function formatsField(full, label, rule) {
    const box = el("div", { class: "formats", id: "f-decode-formats" });
    box.dataset.path = full;
    box.dataset.kind = "formats";
    for (const kind of ["1D", "2D"]) {
      box.append(el("h4", { text: kind === "1D" ? "1D (linear)" : "2D (matrix, stacked)" }));
      for (const f of formats.filter((x) => x.kind === kind)) {
        const id = "f-fmt-" + f.name;
        box.append(el("label", { class: "checkbox", for: id }, el("input", { id, type: "checkbox", value: f.name }), document.createTextNode(f.label)));
      }
    }
    const all = el("button", { type: "button", class: "btn btn-small", text: "All" });
    const none = el("button", { type: "button", class: "btn btn-small", text: "None" });
    all.onclick = () => box.querySelectorAll("input").forEach((i) => { i.checked = true; });
    none.onclick = () => box.querySelectorAll("input").forEach((i) => { i.checked = false; });
    box.append(el("div", { class: "formats-actions" }, all, none));
    const field = el("div", { class: "field field-wide" }, el("label", { text: label }), box);
    if (rule.help) field.append(el("div", { class: "help", text: rule.help }));
    return field;
  }

  function addressField(id, input, label, rule) {
    const btn = el("button", { type: "button", class: "btn btn-small btn-with-icon", title: "Look for GigE Vision cameras" });
    btn.append(svg("svg", { class: "ic" }), document.createTextNode("Discover"));
    btn.firstChild.append(svg("use", { href: "#i-search" }));
    const found = el("ul", { class: "found", hidden: true });
    btn.onclick = async () => {
      btn.disabled = true;
      found.hidden = false;
      found.replaceChildren(el("li", { class: "empty", text: "Searching…" }));
      try {
        const list = await api("POST", "/cameras/discover");
        found.replaceChildren(...(list.length ? list.map((c) => {
          const li = el("li", { text: [c.vendor, c.model].filter(Boolean).join(" ") + " · " + (c.address || c.id) + (c.serial ? " · " + c.serial : "") });
          li.onclick = () => { input.value = c.protocol === "Fake" ? c.id : (c.address || c.id); found.hidden = true; };
          return li;
        }) : [el("li", { class: "empty", text: "No camera answered. Cameras on other networks: enter the IP address." })]));
      } catch (e) {
        found.replaceChildren(el("li", { class: "empty", text: "Discovery failed: " + e.message }));
      }
      btn.disabled = false;
    };
    const field = el("div", { class: "field field-wide" }, el("label", { for: id, text: label }), el("div", { class: "with-button" }, input, btn), found);
    if (rule.help) field.append(el("div", { class: "help", text: rule.help }));
    return field;
  }

  async function fillMedia(select) {
    const current = (loaded && loaded.storage && loaded.storage.medium) || "";
    // the current value right away: a Save before the list arrives must not clear it
    select.replaceChildren(el("option", { value: "", text: "(none)" }), el("option", { value: current, text: current || "…" }));
    select.value = current;
    let media = [];
    try { media = (await api("GET", "/media")).media; } catch (e) { /* keep the current value */ }
    const opts = [el("option", { value: "", text: "(none)" })];
    for (const m of media) {
      opts.push(el("option", { value: m.name, text: m.name + (m.freeMb !== null ? " · " + Math.round(m.freeMb) + " MB free" : "") + (m.writable ? "" : " · read-only") }));
    }
    if (current && !media.some((m) => m.name === current)) opts.push(el("option", { value: current, text: current + " (not mounted)" }));
    select.replaceChildren(...opts);
    select.value = current;
  }

  function buildFields(container, defaults, path) {
    for (const [key, def] of Object.entries(defaults)) {
      const full = path ? path + "." + key : key;
      const rule = (meta.rules && meta.rules[full]) || {};
      const label = rule.label || humanize(key);
      if (rule.hidden) continue; // value is kept unchanged (readForm starts from the loaded settings)
      if (full === "decode.formats") { container.append(formatsField(full, label, rule)); continue; }
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
        for (const opt of rule.enum) input.append(el("option", { value: String(opt), text: enumText(full, opt) }));
      } else if (full === "storage.medium") {
        input = el("select", { id });
      } else if (typeof def === "boolean") {
        input = el("input", { id, type: "checkbox" });
      } else if (typeof def === "number") {
        const isInt = (meta.types || {})[full] === "integer";
        input = el("input", { id, type: "number", step: isInt ? "1" : "any", min: rule.min, max: rule.max });
      } else if (Array.isArray(def)) {
        input = el("input", { id, type: "text", placeholder: "JSON array" });
      } else {
        input = el("input", { id, type: "text", maxlength: rule.maxLength, spellcheck: "false" });
      }
      input.dataset.path = full;
      input.dataset.kind = Array.isArray(def) ? "array" : typeof def;
      if (full === "camera.address") { container.append(addressField(id, input, label, rule)); continue; }
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
      if (input.dataset.kind === "formats") {
        for (const cb of input.querySelectorAll("input")) { cb.checked = (v || []).includes(cb.value); cb.disabled = !canWrite; }
      } else if (input.type === "checkbox") input.checked = !!v;
      else if (input.dataset.kind === "array") input.value = JSON.stringify(v ?? []);
      else if (input.dataset.path === "storage.medium") fillMedia(input);
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
      if (input.dataset.kind === "formats") v = [...input.querySelectorAll("input:checked")].map((cb) => cb.value);
      else if (input.type === "checkbox") v = input.checked;
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
      const bad = (errors || []).some((m) => m.startsWith(input.dataset.path + ":") || m.startsWith(input.dataset.path + " "));
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
      settingsNow = loaded;
      fillForm(loaded);
      showErrors([]);
      $("save-state").textContent = "Saved " + new Date().toLocaleTimeString();
    } catch (e) {
      showErrors((e.data && e.data.errors) || [e.message]);
      $("save-state").textContent = "Not saved";
    }
    updateRegionButtons();
    drawOverlay();
    refreshReader();
  }

  async function resetDefaults() {
    if (!confirm("Reset all settings to their defaults?")) return;
    try { loaded = await api("POST", "/settings/reset"); settingsNow = loaded; fillForm(loaded); showErrors([]); $("save-state").textContent = "Defaults restored"; }
    catch (e) { showErrors([e.message]); }
    updateRegionButtons();
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
    if (ev.key === "Escape" && drawer.classList.contains("open")) closeSettings();
    else if (ev.key === "Escape" && drawing) toggleRegion();
  });
  $("btn-read").onclick = doRead;
  $("btn-continuous").onclick = toggleContinuous;
  $("btn-live").onclick = () => (liveOn ? stopLive() : startLive());
  $("btn-region").onclick = toggleRegion;
  $("btn-region-clear").onclick = () => saveRegion({ enabled: false });
  $("btn-download").onclick = download;
  $("btn-newest").onclick = () => { viewSeq = 0; $("viewing-note").hidden = true; if (reader && reader.last) showResult(reader.last); refreshHistory(); };
  $("btn-reconnect").onclick = async () => { try { await api("POST", "/camera/reconnect"); } catch (e) { showBanner(e.message); } refreshReader(); };
  const ov = $("overlay");
  ov.onpointerdown = onDown;
  ov.onpointermove = onMove;
  ov.onpointerup = onUp;
  ov.onpointercancel = () => { dragStart = dragRect = null; drawOverlay(); };
  new ResizeObserver(placeOverlay).observe($("stage"));

  (async function init() {
    await refreshStatus();
    try { formats = await api("GET", "/formats"); } catch (e) { formats = []; }
    await refreshSettingsNow();
    await refreshReader();
    await refreshHistory();
    if (reader && !reader.last) $("placeholder").textContent = canWrite ? "No image yet - press Read." : "No image yet.";
    document.body.dataset.uiReady = "true";  // for tools/ui_check.py (screenshots wait for it)
    setInterval(refreshStatus, 5000);
    setInterval(refreshSettingsNow, 15000);
    (function poll() {
      refreshReader().finally(() => setTimeout(poll, reader && reader.continuous ? 400 : 1000));
    })();
  })();
})();
