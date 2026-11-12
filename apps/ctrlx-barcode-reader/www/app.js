// Barcode Reader web UI - this app's page. Plain JS, no build step, no CDN (devices are often offline).
// The shared parts (token + API calls, roles, settings drawer, help menu + tutorial, About, light/dark) are in
// kit.js - see the list at its top. Roles: view < operate < configure (GET /status says which one the user has).
// Images are API data too: they are fetched with the token (apiBlob) and shown through object URLs.
"use strict";

(function () {
  const { $, el, api, can, showBanner } = Kit;
  const SVG_NS = "http://www.w3.org/2000/svg";

  // binary data (images) -> {status, blob, headers}; 204 -> blob null
  async function apiBlob(path) {
    const headers = { "Accept": "image/*" };
    if (Kit.token) headers["Authorization"] = "Bearer " + Kit.token;
    const res = await fetch(Kit.API + path, { headers, credentials: "same-origin", cache: "no-store" });
    if (res.status === 401) showBanner("Session expired or no token - please reopen this app from the ctrlX menu.");
    if (res.status === 204) return { status: 204, blob: null, headers: res.headers };
    if (!res.ok) { const err = new Error(res.statusText); err.status = res.status; throw err; }
    return { status: res.status, blob: await res.blob(), headers: res.headers };
  }

  // ---- helpers -------------------------------------------------------------
  function svg(tag, attrs, text) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
    if (text !== undefined) node.textContent = text;
    return node;
  }
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

  let formats = [];               // [{name, label, kind}]
  let reader = null;              // last /reader answer
  let shown = null;               // {kind: "result"|"live", result?, width, height}
  let viewSeq = 0;                // sequence pinned from the history (0 = follow the newest)
  let imgUrl = null;

  // ---- status / permissions ---------------------------------------------------
  async function refreshStatus() {
    const s = await Kit.refreshStatus();   // title, version, Data Layer pill, role pill, [data-role] elements, colors
    if (!s) return;
    const libs = s.libraries || {};
    $("conn").title = "Data Layer connection of this app · " + Object.entries(libs).map(([k, v]) => k + " " + v).join(", ");
    updateRegionButtons();
  }

  // what the page needs from the settings comes with GET /reader ("view": decode region, live interval) - the
  // settings themselves are for the configure role only
  const view = () => (reader && reader.view) || {};
  function currentRoi() {
    const r = view().roi;
    return r && r.enabled ? [r.x, r.y, r.width, r.height] : null;
  }
  function cameraRegionOn() {
    const r = view().roi;
    return !!(r && r.enabled && r.onCamera);
  }
  function updateRegionButtons() { $("btn-region-clear").hidden = !can("configure") || !currentRoi(); }

  // ---- reader state -------------------------------------------------------------
  const CAM_STATES = { connected: ["pill-ok", "connected"], connecting: ["pill-unknown", "connecting…"],
                       disconnected: ["pill-unknown", "disconnected"], error: ["pill-warn", "error"] };
  async function refreshReader() {
    let r;
    try { r = await api("GET", "/reader"); } catch (e) { return; }
    const roiBefore = JSON.stringify(view().roi);
    reader = r;
    if (JSON.stringify(view().roi) !== roiBefore) { updateRegionButtons(); drawOverlay(); }
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
    if (info.width && info.height) $("stage").style.setProperty("--img-ratio", info.width + " / " + info.height);   // empty stage
    if (info.width) rows.push(["Image", info.width + " × " + info.height + " · " + (info.pixelFormat || "?") +
      (info.cameraRegion ? " · camera region at " + info.cameraRegion[0] + ", " + info.cameraRegion[1] : "")]);
    if (info.exposureUs !== undefined && info.exposureUs !== null) rows.push(["Exposure / gain", Math.round(info.exposureUs) + " µs · " + info.gain]);
    if (info.acquisition) rows.push(["Acquisition", info.acquisition]);
    if (info.packetSize) rows.push(["Stream", "port " + info.streamPort + " · packets " + info.packetSize + " B" +
      (info.packetDelayUs !== undefined ? " · delay " + info.packetDelayUs + " µs" + (info.packetDelayAuto ? " (automatic)" : "") : "")]);
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
      img.onload = () => { if (imgUrl) URL.revokeObjectURL(imgUrl); imgUrl = url; img.hidden = false; $("placeholder").hidden = true;
        $("stage").classList.remove("empty"); placeOverlay(); resolve(); };
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
    ov.setAttribute("viewBox", shown.x + " " + shown.y + " " + shown.width + " " + shown.height);
    ov.setAttribute("preserveAspectRatio", "none");
    drawOverlay();
  }

  function drawOverlay(dragRect) {
    const ov = $("overlay");
    ov.replaceChildren();
    if (!shown) return;
    const W = shown.width, H = shown.height, X = shown.x, Y = shown.y;   // the image's place on the sensor
    const scale = W / Math.max(1, $("img").clientWidth);      // image pixels per screen pixel
    const roi = dragRect || (shown.kind === "result" && viewSeq ? shown.result.region : currentRoi());
    if (roi) {
      const [x, y, w, h] = roi;
      ov.append(svg("path", { class: "roi-shade", "fill-rule": "evenodd",
        d: `M${X} ${Y}H${X + W}V${Y + H}H${X}Z M${x} ${y}V${y + h}H${x + w}V${y}Z` }));
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
      const ty = Math.max(Y, top[1] - fs - 2 * pad - 2 * scale);
      const tx = Math.max(X, Math.min(top[0], X + W - tw));  // keep the tag inside the image
      ov.append(svg("rect", { class: "code-tag", x: tx, y: ty, width: tw, height: fs + 2 * pad, rx: 3 * scale }));
      ov.append(svg("text", { class: "code-text", x: tx + pad, y: ty + pad + fs * 0.85, "font-size": fs }, label));
    });
  }

  async function showResult(result) {
    const origin = result.origin || [0, 0];
    shown = { kind: "result", result, width: result.width, height: result.height, x: origin[0], y: origin[1] };
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
    const interval = Math.max(150, (view().liveIntervalMs || 500) / 2);
    try {
      const res = await apiBlob("/live?after=" + liveSeq);
      if (res.blob && liveOn) {
        liveSeq = Number(res.headers.get("X-Live-Seq")) || liveSeq + 1;
        shown = { kind: "live", width: Number(res.headers.get("X-Image-Width")), height: Number(res.headers.get("X-Image-Height")),
                  x: Number(res.headers.get("X-Image-X")) || 0, y: Number(res.headers.get("X-Image-Y")) || 0 };
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
    if (!drawing && cameraRegionOn()) {
      showBanner("The camera sends only the region. Clear it to get the full frame, then draw a new one.");
      return;
    }
    drawing = !drawing;
    setPressed($("btn-region"), drawing);
    $("stage").classList.toggle("drawing", drawing);
    if (drawing && !shown) showBanner("Read an image or start the live picture first, then draw the region on it.");
  }
  function toImage(ev) {
    const r = $("overlay").getBoundingClientRect();
    const x = Math.round((ev.clientX - r.left) / r.width * shown.width);
    const y = Math.round((ev.clientY - r.top) / r.height * shown.height);
    return [shown.x + Math.max(0, Math.min(shown.width, x)), shown.y + Math.max(0, Math.min(shown.height, y))];
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
  async function saveRegion(roi) {     // configure role: the region is a setting
    try {
      const s = await api("GET", "/settings");
      s.decode.roi = Object.assign({}, s.decode.roi, roi);
      await api("PUT", "/settings", s);
      if (settings.isOpen()) settings.reload();
      await refreshReader();
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
    $("history-tab-count").textContent = list.length ? String(list.length) : "";
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
    location.hash = "#reader";                     // the image is on the Reader page
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

  // ---- settings drawer (kit.js) with this app's own fields: formats, camera address + Discover, medium ---------
  function formatsField(id) {
    const box = el("div", { class: "formats", id });
    for (const kind of ["1D", "2D"]) {
      box.append(el("h4", { text: kind === "1D" ? "1D (linear)" : "2D (matrix, stacked)" }));
      for (const f of formats.filter((x) => x.kind === kind)) {
        const cid = "f-fmt-" + f.name;
        box.append(el("label", { class: "checkbox", for: cid }, el("input", { id: cid, type: "checkbox", value: f.name }), document.createTextNode(f.label)));
      }
    }
    const all = el("button", { type: "button", class: "btn btn-small", text: "All" });
    const none = el("button", { type: "button", class: "btn btn-small", text: "None" });
    all.onclick = () => box.querySelectorAll("input").forEach((i) => { i.checked = true; });
    none.onclick = () => box.querySelectorAll("input").forEach((i) => { i.checked = false; });
    box.append(el("div", { class: "formats-actions" }, all, none));
    box.getValue = () => [...box.querySelectorAll("input:checked")].map((cb) => cb.value);
    box.setValue = (v) => { for (const cb of box.querySelectorAll("input")) cb.checked = (v || []).includes(cb.value); };
    return { node: box, input: box, wide: true };
  }

  function addressField(id) {
    const input = el("input", { id, type: "text", spellcheck: "false", autocomplete: "off" });
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
    return { node: el("div", {}, el("div", { class: "with-button" }, input, btn), found), input, wide: true };
  }

  async function fillMedia(select, current) {
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
  function mediumField(id) {
    const select = el("select", { id });
    select.setValue = (v) => fillMedia(select, v || "");
    select.getValue = () => select.value;
    return { node: select, input: select };
  }

  const settings = Kit.setupSettings({
    field: (path, rule, def, id) => (path === "decode.formats" ? formatsField(id)
      : path === "camera.address" ? addressField(id) : path === "storage.medium" ? mediumField(id) : null),
    onSaved: async () => { await refreshReader(); updateRegionButtons(); drawOverlay(); },
  });

  // ---- help menu, tutorial ----------------------------------------------------------
  Kit.setupHelp([
    { title: "Welcome",
      text: "This tour shows the controls of the Barcode Reader. Step through it with the arrows (or the ← → keys); " +
        "the control of each step blinks. Everything stays usable while the tour is open." },
    { page: "reader", title: "Read", when: () => can("operate"), target: () => $("btn-read"),
      text: "Takes one image and decodes it. A PLC can trigger reads too, through the Data Layer (trigger / request)." },
    { page: "reader", title: "Live picture", when: () => can("operate"), target: () => $("btn-live"),
      text: "A live picture for setting up focus, exposure and the region - it is not decoded." },
    { page: "reader", title: "Continuous", when: () => can("operate"), target: () => $("btn-continuous"),
      text: "Reads again and again (the interval is in the settings) until you switch it off." },
    { page: "reader", title: "Decode region", when: () => can("configure"), target: () => $("btn-region"),
      text: "Draw the part of the image that is decoded; a smaller region decodes faster. It is stored in the settings." },
    { title: "Pages", target: () => $("tabs"),
      text: "The reader with the image and the result, and the history of the last reads. Each page has its own address." },
    { title: "Image", page: "reader", target: () => $("stage"),
      text: "The newest image with the codes found (green) and the decode region (yellow, dashed)." },
    { page: "reader", title: "Result", target: () => $("result-h").closest(".card"),
      text: "The codes of the shown read, where the read came from, and how long it took." },
    { page: "reader", title: "Camera", target: () => $("camera-h").closest(".card"),
      text: () => "The camera and its state." + (can("operate") ? " Reconnect closes and opens it again." : "") },
    { title: "History", page: "history", target: () => $("history-h").closest(".card"),
      text: "The last reads (kept in RAM). Click a row to show its image on the Reader page." },
    { title: "Settings", when: () => can("configure"), target: () => $("btn-settings"),
      text: "Camera, decoding, live picture and storage. Stored in the ctrlX app data, so they are saved and loaded with " +
        "configurations.", act: () => settings.open() },
    { title: "Your permission", when: () => !can("configure"), target: () => $("role"),
      text: () => can("operate") ? "Operator: you may read codes and run the camera, but not change the settings or the region."
        : "View only: you see the reads, but can't start any. Ask an administrator for more." },
    { title: "Data Layer", target: () => $("conn"), text: "The connection of this app to the ctrlX Data Layer." },
    { title: "Light or dark", target: () => $("btn-theme"),
      text: "Switches the page between light and dark mode. Each browser keeps its own choice." },
    { title: "Help", target: () => $("btn-help"),
      text: "Start this tour again, open the app's page, or see About: version, author, license." },
  ]);

  // ---- start ---------------------------------------------------------------
  // one handler per element (onclick/onsubmit): see docs/ctrlx/gotchas.md #35
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && drawing && !settings.isOpen()) toggleRegion();
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
    Kit.setupTabs({ onShow: (page) => { if (page === "reader") placeOverlay(); } });   // the overlay needs the shown image
    try { formats = await api("GET", "/formats"); } catch (e) { formats = []; }
    await refreshReader();
    await refreshHistory();
    if (reader && !reader.last) $("placeholder").textContent = can("operate") ? "No image yet - press Read." : "No image yet.";
    Kit.ready();                                   // for tools/ui_check.py (screenshots wait for it)
    if (location.hash === "#settings") settings.open();
    setInterval(refreshStatus, 5000);
    (function poll() {
      refreshReader().finally(() => setTimeout(poll, reader && reader.continuous ? 400 : 1000));
    })();
  })();
})();
