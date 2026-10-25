// Network Scanner web UI - plain JS, no build step, no CDN (devices are often offline).
//
// Auth: the ctrlX menu opens "/<snap>/?token=${bearertoken}". We move the token from the URL
// into sessionStorage (so it is not left in history/bookmarks) and send it as
// "Authorization: Bearer" on every API call. The reverse proxy validates it because
// "/<snap>/api" is listed as restricted in the package manifest. Downloads (PDF/CSV/JSON) go
// through fetch() with the header too - a plain <a href> could not send it.
"use strict";

(function () {
  const PREFIX = "/" + location.pathname.split("/").filter(Boolean)[0]; // "/<snap-name>"
  const API = PREFIX + "/api/v1";
  const TOKEN_KEY = PREFIX.slice(1) + ".token";

  // ---- token / API -----------------------------------------------------------
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

  function headers(extra) {
    const h = Object.assign({ "Accept": "application/json" }, extra || {});
    if (token) h["Authorization"] = "Bearer " + token;
    return h;
  }

  async function api(method, path, body) {
    const res = await fetch(API + path, {
      method, headers: headers(body === undefined ? {} : { "Content-Type": "application/json" }),
      body: body === undefined ? undefined : JSON.stringify(body), credentials: "same-origin", cache: "no-store",
    });
    let data = null;
    const text = await res.text();
    if (text) { try { data = JSON.parse(text); } catch (e) { data = { title: text }; } }
    if (res.status === 401) showBanner("Session expired or no token - please reopen this app from the ctrlX menu.");
    if (!res.ok) {
      const err = new Error((data && (data.detail || data.title)) || res.statusText);
      err.status = res.status; err.data = data; throw err;
    }
    return data;
  }

  async function download(path, fallbackName) {
    const res = await fetch(API + path, { headers: headers(), credentials: "same-origin", cache: "no-store" });
    if (!res.ok) { alert("Download failed: " + res.status + " " + res.statusText); return; }
    const disp = res.headers.get("Content-Disposition") || "";
    const m = /filename="([^"]+)"/.exec(disp);
    const url = URL.createObjectURL(await res.blob());
    const a = el("a", { href: url, download: m ? m[1] : fallbackName });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  }

  // ---- helpers ------------------------------------------------------------------
  const $ = (id) => document.getElementById(id);
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on") && typeof v === "function") node[k] = v;
      else if (v === true) node.setAttribute(k, "");
      else if (v !== false && v !== null && v !== undefined) node.setAttribute(k, v);
    }
    for (const c of children) if (c !== null && c !== undefined && c !== false) node.append(c);
    return node;
  }
  const SVG_NS = "http://www.w3.org/2000/svg";
  function svg(tag, attrs) {
    const n = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
    return n;
  }
  function showBanner(msg) { const b = $("banner"); b.textContent = msg; b.hidden = !msg; }
  function fmtTime(iso) {
    if (!iso) return "–";
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleString([], { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }
  function fmtDur(s) {
    s = Math.round(s || 0);
    const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), r = s % 60;
    return h ? h + "h " + m + "m" : (m ? m + "m " + r + "s" : r + "s");
  }
  function fmtUptime(s) {
    const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
    return (d ? d + "d " : "") + (h || d ? h + "h " : "") + m + "m";
  }
  function fmtBytes(b) { return b > 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB"; }
  const clone = (o) => JSON.parse(JSON.stringify(o));
  const plural = (n, word) => n + " " + word + (n === 1 ? "" : (word.endsWith("s") ? "es" : "s"));
  const RATING_LABEL = { forbidden: "forbidden", ok: "expected", unlisted: "not listed" };
  const TRIGGER_LABEL = { manual: "manual", schedule: "schedule", datalayer: "Data Layer" };
  function badge(rating) { return el("span", { class: "badge " + (rating || ""), text: RATING_LABEL[rating] || rating || "" }); }
  function hostNames(h) { return (h.names || []).map((n) => n.name); }
  function portLabel(p) { return p.proto + "/" + p.port; }

  function productLine(p) {   // mirrors probes.product_line()
    const info = p.info || {}, http = p.http || {}, tls = p.tls || {};
    const parts = [];
    if (p.product) parts.push(p.product);
    if (info.product) parts.push(info.product);
    else if (info.vendor) parts.push([info.vendor, info.productCode, info.revision].filter(Boolean).join(" "));
    if (http.server) parts.push(http.server);
    if (http.title) parts.push("“" + http.title + "”");
    if (http.status && !(http.server || http.title)) parts.push("HTTP " + http.status + (http.location ? " → " + http.location : ""));
    if (tls.subject) parts.push("cert " + tls.subject);
    if (tls.version) parts.push(tls.version + (tls.notAfter ? ", valid until " + tls.notAfter.slice(0, 10) : "") + (tls.selfSigned ? ", self-signed" : ""));
    if (tls.error) parts.push("TLS: " + tls.error);
    if (info.netbiosName) parts.push("NetBIOS " + info.netbiosName);
    if (info.server) parts.push(info.server);
    if (info.protocol === "opc-ua" && !parts.length) parts.push("protocolVersion" in info ? "OPC UA (Hello acknowledged)" : "OPC UA");
    if (info.protocol === "iso-tsap" && !parts.length) parts.push("ISO-on-TCP, " + (info.cotp || "answered"));
    if (info.protocol === "bacnet" && "deviceInstance" in info) parts.push("BACnet device " + info.deviceInstance);
    if (info.protocol === "ntp") parts.push("NTP stratum " + info.stratum);
    if (info.mdnsServices) parts.push(info.mdnsServices.join(", "));
    if (info.protocol === "modbus" && "exception" in info) parts.push("Modbus exception " + info.exception);
    if (!parts.length && p.banner) parts.push(p.banner);
    return parts.join("; ");
  }

  // ---- state ----------------------------------------------------------------------
  const S = {
    status: null, settings: null, canWrite: false, network: null, history: [], usage: null,
    result: null, resultId: null, filter: "all", search: "", openHosts: new Set(),
    profileIdx: 0, profileDraft: null, profileDirty: false, listsDraft: null, listsDirty: false,
    setDirty: false, wasRunning: false, tab: "overview",
  };

  // ---- tabs -------------------------------------------------------------------------
  const TABS = ["overview", "results", "history", "profiles", "lists", "settings"];
  function showTab(name, push) {
    if (!TABS.includes(name)) name = "overview";
    S.tab = name;
    for (const t of document.querySelectorAll(".tab")) t.setAttribute("aria-selected", String(t.dataset.tab === name));
    for (const n of TABS) $("tab-" + n).hidden = n !== name;
    if (push !== false && location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
    if (name === "settings") refreshValues();
    if (name === "history") renderChart();
    if (name === "results" && !S.result && S.history.length) loadScan(S.history[0].id);
  }
  for (const t of document.querySelectorAll(".tab")) t.onclick = () => showTab(t.dataset.tab);

  // ---- status / overview --------------------------------------------------------------
  async function refreshStatus() {
    let s;
    try { s = await api("GET", "/status"); } catch (e) { if (e.status !== 401) showBanner("Cannot reach the app: " + e.message); return; }
    showBanner("");
    S.status = s;
    S.canWrite = !!s.canWrite;
    $("readonly-note").hidden = S.canWrite;
    $("app-version").textContent = /^\d/.test(s.version) ? "v" + s.version : s.version;
    const conn = $("conn");
    if (s.datalayer.connected) { conn.className = "pill pill-ok"; conn.textContent = "Data Layer: connected"; }
    else { conn.className = "pill pill-warn"; conn.textContent = s.datalayer.enabled ? "Data Layer: connecting…" : "Data Layer: off"; }
    renderScanState(s.scan);
    renderLast(s.last);
    renderSchedules();
    $("st-uptime").textContent = fmtUptime(s.uptimeSeconds);
    $("st-source").textContent = s.settings.source;
    $("st-persisted").textContent = s.settings.persisted ? "yes" : (s.settings.storageAvailable ? "no" : "no (app data not available)");
    $("st-history").textContent = s.history.count + " scans, " + fmtBytes(s.history.bytes) + (s.history.error ? " - " + s.history.error : "");
    $("st-user").textContent = s.user || "–";
    const errText = s.settings.error || s.datalayer.error || s.history.error || "";
    $("st-error").textContent = errText; $("st-error").hidden = !errText;
    applyPermissions();
    const running = !!(s.scan && s.scan.running);
    if (S.wasRunning && !running) {   // a scan just ended: show it
      await loadHistory();
      if (s.last && (S.tab === "results" || !S.result || S.resultId === null)) await loadScan(s.last.id);
    }
    S.wasRunning = running;
  }

  function renderScanState(scan) {
    const pill = $("scan-pill");
    const box = $("progress");
    if (scan && scan.running) {
      const p = scan.progress;
      pill.className = "pill pill-busy";
      pill.textContent = (scan.aborting ? "Aborting " : "Scanning ") + Math.round(p.percent) + " %";
      box.hidden = false;
      $("pg-title").textContent = p.profileName + " (" + (TRIGGER_LABEL[p.trigger] || p.trigger) + ")";
      $("pg-phase").textContent = scan.aborting ? "aborting…" : "phase: " + p.phase + (p.phaseTotal > 1 ? " " + p.phaseDone + "/" + p.phaseTotal : "");
      $("pg-bar").style.width = p.percent + "%";
      $("pg-bar").parentNode.setAttribute("aria-valuenow", String(Math.round(p.percent)));
      $("pg-hosts").textContent = p.hostsUp;
      $("pg-ports").textContent = p.openPorts;
      $("pg-probes").textContent = p.probes;
      $("pg-elapsed").textContent = fmtDur(p.elapsedS) + " · " + plural(p.targets, "address");
      $("btn-start").hidden = true;
      $("btn-abort").hidden = !S.canWrite;
      $("btn-abort").disabled = !!scan.aborting;
      refreshLive();
    } else {
      const last = S.status && S.status.last;
      pill.className = "pill " + (last && last.summary.rating && last.summary.rating.forbidden ? "pill-bad" : "pill-ok");
      pill.textContent = last && last.summary.rating && last.summary.rating.forbidden
        ? last.summary.rating.forbidden + " forbidden finding" + (last.summary.rating.forbidden > 1 ? "s" : "") : "Idle";
      box.hidden = true;
      $("btn-start").hidden = false;
      $("btn-abort").hidden = true;
    }
    const queued = scan && scan.queued && scan.queued.length ? "Queued: " + scan.queued.join(", ") : "";
    if (queued) $("pg-phase").textContent += " · " + queued;
  }

  async function refreshLive() {
    let cur;
    try { cur = await api("GET", "/scans/current?hosts=1"); } catch (e) { return; }
    if (!cur.running) return;
    const ul = $("pg-live");
    const items = (cur.hosts || []).map((h) => {
      const ports = (h.ports || []).map(portLabel).join(" ");
      const name = hostNames(h)[0] || h.vendor || "";
      return el("li", { text: h.ip + (name ? "  " + name : "") + (ports ? "  [" + ports + "]" : "") });
    });
    ul.replaceChildren(...items);
  }

  function tile(label, value, kind) {
    return el("div", { class: "tile" + (kind ? " t-" + kind : "") }, el("b", { text: String(value) }), el("span", { text: label }));
  }
  function summaryTiles(summary, changes) {
    const r = summary.rating || {};
    return [
      tile("Hosts up", summary.hostsUp || 0), tile("Open TCP", summary.openTcp || 0), tile("Open UDP", summary.openUdp || 0),
      tile("Forbidden", r.forbidden || 0, r.forbidden ? "bad" : "muted"), tile("Expected", r.ok || 0, "ok"),
      tile("Not listed", r.unlisted || 0, "muted"),
      tile("Changes", changes ? changes.total : "–", changes && changes.total ? "warn" : "muted"),
    ];
  }

  function renderLast(last) {
    $("last-empty").hidden = !!last;
    $("last-body").hidden = !last;
    if (!last) { $("last-when").textContent = ""; return; }
    $("last-when").textContent = last.profileName + " · " + fmtTime(last.finishedAt) + (last.status !== "completed" ? " · " + last.status : "");
    $("last-tiles").replaceChildren(...summaryTiles(last.summary, last.changes));
    const forbidden = (last.summary.rating || {}).forbidden || 0;
    const tab = document.querySelector('.tab[data-tab="results"]');
    tab.replaceChildren(document.createTextNode("Results"));
    if (forbidden) tab.append(el("span", { class: "count", text: String(forbidden), title: "forbidden findings in the last scan" }));
  }

  function renderSchedules() {
    if (!S.settings || !S.status) return;
    const next = S.status.nextRuns || {};
    const rows = S.settings.profiles.map((p) => {
      let what = "manual only";
      if (p.scheduleMode === "interval") what = "every " + p.scheduleIntervalMinutes + " min";
      if (p.scheduleMode === "daily") what = (p.scheduleDays ? p.scheduleDays : "daily") + " at " + p.scheduleTimes;
      return el("tr", {}, el("td", { text: p.name }), el("td", { text: what }), el("td", { text: next[p.id] ? fmtTime(next[p.id]) : "–" }));
    });
    $("sched-body").replaceChildren(...rows);
    $("sched-clock").textContent = "Times are shown in your browser's time zone; daily schedules use the device clock.";
  }

  async function loadNetwork() {
    try { S.network = await api("GET", "/network"); } catch (e) { return; }
    const rows = [];
    for (const i of S.network.interfaces) {
      if (!i.addresses.length) continue;
      i.addresses.forEach((a, k) => rows.push(el("tr", {},
        el("td", {}, document.createTextNode(k ? "" : i.name + (i.up ? "" : " (down)")),
          k ? null : el("span", { class: "sub mono", text: i.mac })),
        el("td", { class: "mono", text: a }), el("td", { class: "mono", text: i.networks[k] || "" }))));
    }
    $("net-body").replaceChildren(...(rows.length ? rows : [el("tr", {}, el("td", { colspan: "3", class: "muted", text: S.network.error || "No IPv4 networks." }))]));
    $("net-gw").textContent = S.network.gateways.length ? "Default gateway: " + S.network.gateways.map((g) => g.ip + " (" + g.interface + ")").join(", ") : "No default gateway.";
    renderTargetChips();
  }

  // ---- run / abort ---------------------------------------------------------------------
  function fillRunProfiles() {
    const sel = $("run-profile");
    const keep = sel.value;
    sel.replaceChildren(...S.settings.profiles.map((p) => el("option", { value: p.id, text: p.name })));
    if (keep && S.settings.profiles.some((p) => p.id === keep)) sel.value = keep;
    runEstimate();
  }
  // one counter per consumer (overview / profile editor): only the newest answer of each is shown
  const estimateSeq = {};
  async function estimateFor(p, who) {
    const seq = estimateSeq[who] = (estimateSeq[who] || 0) + 1;
    try {
      const e = await api("POST", "/estimate", { targets: p.targets, exclude: p.exclude, tcpPorts: p.tcpPorts, udpPorts: p.udpPorts });
      return seq === estimateSeq[who] ? e : null;
    } catch (err) { return null; }
  }
  function estimateText(p, e) {
    if (!e) return "";
    if (e.errors && e.errors.length) return e.errors.join("; ");
    const addr = e.addresses === null ? "?" : e.addresses;
    const perHost = (e.tcpPorts || 0) + (e.udpPorts || 0);
    const secs = perHost / Math.max(1, p.ratePerSecond);
    const max = S.settings ? S.settings.scan.maxAddresses : 0;
    let t = (addr === "?" ? "? addresses" : plural(addr, "address")) + (e.excluded ? " (minus " + e.excluded + " excluded)" : "") + " · " + e.tcpPorts + " TCP + " + e.udpPorts +
      " UDP ports per host up ≈ " + fmtDur(secs) + " per host at " + p.ratePerSecond + "/s";
    if (max && e.addresses > max) t += " · more than the limit of " + max + " addresses!";
    return t;
  }
  async function runEstimate() {
    const p = S.settings && S.settings.profiles.find((x) => x.id === $("run-profile").value);
    if (!p) return;
    const e = await estimateFor(p, "run");
    if (e) $("run-estimate").textContent = estimateText(p, e);
  }
  $("run-profile").onchange = runEstimate;
  $("btn-start").onclick = async () => {
    const id = $("run-profile").value;
    $("btn-start").disabled = true;
    try { await api("POST", "/scans", { profile: id }); S.wasRunning = true; }
    catch (e) { alert("Scan not started: " + e.message); }
    finally { $("btn-start").disabled = false; }
    await refreshStatus();
    schedulePoll(1000);
  };
  $("btn-abort").onclick = async () => {
    if (!confirm("Abort the running scan? What was found so far is kept.")) return;
    try { await api("POST", "/scans/current/abort"); } catch (e) { alert(e.message); }
    refreshStatus();
  };
  $("btn-last-open").onclick = () => { if (S.status.last) { loadScan(S.status.last.id); showTab("results"); } };
  $("btn-last-pdf").onclick = () => { if (S.status.last) download("/scans/" + S.status.last.id + "/report.pdf", "scan.pdf"); };

  // ---- history ---------------------------------------------------------------------------
  async function loadHistory() {
    let data;
    try { data = await api("GET", "/scans"); } catch (e) { return; }
    S.history = data.scans;
    S.usage = data.usage;
    renderHistory();
    fillScanSelect();
  }

  function renderHistory() {
    const u = S.usage;
    $("hist-usage").textContent = u ? u.count + " of max. " + u.maxScans + " scans · " + fmtBytes(u.bytes) + " of " + fmtBytes(u.maxBytes) : "";
    const rows = S.history.map((h) => {
      const open = el("button", { type: "button", class: "btn btn-small", text: "Open" });
      open.onclick = () => { loadScan(h.id); showTab("results"); };
      const pdf = el("button", { type: "button", class: "btn btn-small", text: "PDF" });
      pdf.onclick = () => download("/scans/" + h.id + "/report.pdf", h.id + ".pdf");
      const del = el("button", { type: "button", class: "btn btn-small btn-ghost btn-danger-text", text: "Delete", disabled: !S.canWrite });
      del.onclick = async () => {
        if (!confirm("Delete scan " + fmtTime(h.startedAt) + " (" + h.profileName + ")?")) return;
        try { await api("DELETE", "/scans/" + h.id); } catch (e) { alert(e.message); }
        if (S.resultId === h.id) { S.result = null; S.resultId = null; }
        await loadHistory();
        refreshStatus();
      };
      return el("tr", { class: h.alarmsAtScan ? "row-bad" : "" },
        el("td", { text: fmtTime(h.startedAt) }), el("td", { text: h.profileName }),
        el("td", { text: (TRIGGER_LABEL[h.trigger] || h.trigger) + (h.user ? " · " + h.user : "") }),
        el("td", { text: fmtDur(h.durationS) }), el("td", { class: "num", text: h.hostsUp }),
        el("td", { class: "num", text: h.openTcp + h.openUdp }),
        el("td", { class: "num" + (h.alarmsAtScan ? " bad" : ""), text: h.alarmsAtScan }),
        el("td", { class: "num", text: h.changes === null || h.changes === undefined ? "–" : h.changes }),
        el("td", { text: h.status }), el("td", { class: "btn-group" }, open, pdf, del));
    });
    $("hist-body").replaceChildren(...(rows.length ? rows : [el("tr", {}, el("td", { colspan: "10", class: "muted", text: "No scans yet." }))]));
    renderChart();
  }

  // Hosts up per scan (oldest left), red dot = forbidden findings at scan time. Bars: <= 24 px, 4 px
  // rounded data end, 2 px gap; one series -> no legend box, the caption names it; tooltip per column.
  function renderChart() {
    const box = $("hist-chart");
    const data = S.history.slice(0, 40).reverse();
    box.replaceChildren();
    box.hidden = data.length < 2;
    if (data.length < 2) return;
    if (box.offsetParent === null) return;   // tab hidden: drawn when the History tab is shown
    const W = Math.max(200, box.clientWidth), H = 70, top = 14, base = H - 14;
    const max = Math.max(1, ...data.map((d) => d.hostsUp));
    const slot = Math.min(W / data.length, 32);   // few scans: bars stay close together (left-aligned)
    const bw = Math.max(2, Math.min(24, slot - 2));
    const s = svg("svg", { viewBox: "0 0 " + W + " " + H, width: W, height: H, role: "img",
      "aria-label": "Hosts up per scan, last " + data.length + " scans" });
    s.append(svg("line", { x1: 0, x2: W, y1: base + 0.5, y2: base + 0.5, stroke: "#e1e4e8", "stroke-width": 1 }));
    const tip = el("div", { class: "chart-tip", hidden: true });
    data.forEach((d, i) => {
      const x = i * slot + (slot - bw) / 2;
      const h = Math.max(1, (base - top) * d.hostsUp / max);
      const y = base - h, r = Math.min(4, bw / 2, h);
      const path = "M" + x + "," + base + " V" + (y + r) + " Q" + x + "," + y + " " + (x + r) + "," + y +
        " H" + (x + bw - r) + " Q" + (x + bw) + "," + y + " " + (x + bw) + "," + (y + r) + " V" + base + " Z";
      s.append(svg("path", { d: path, fill: "#007bc0" }));
      if (d.alarmsAtScan) {
        s.append(svg("circle", { cx: x + bw / 2, cy: Math.max(5, y - 6), r: 4, fill: "#c8102e", stroke: "#fff", "stroke-width": 2 }));
      }
      const hit = svg("rect", { x: i * slot, y: 0, width: slot, height: H, fill: "transparent" });
      hit.addEventListener("mouseenter", () => {
        tip.textContent = fmtTime(d.startedAt) + " · " + d.profileName + ": " + d.hostsUp + " hosts up, " +
          (d.openTcp + d.openUdp) + " open ports" + (d.alarmsAtScan ? ", " + d.alarmsAtScan + " forbidden" : "");
        tip.hidden = false;
        tip.style.left = Math.max(0, Math.min(W - tip.offsetWidth, x + bw / 2 - tip.offsetWidth / 2)) + "px";
      });
      hit.addEventListener("mouseleave", () => { tip.hidden = true; });
      hit.addEventListener("click", () => { loadScan(d.id); showTab("results"); });
      s.append(hit);
    });
    const caption = el("div", { class: "chart-caption muted small" },
      document.createTextNode("Hosts up per scan (max. " + max + ")"),
      el("span", { class: "chart-key" }, el("span", { class: "dot-bad" }), document.createTextNode(" forbidden findings")));
    box.append(caption, s, tip);
  }

  // ---- results ---------------------------------------------------------------------------
  function fillScanSelect() {
    const sel = $("res-scan");
    sel.replaceChildren(...S.history.map((h) => el("option", { value: h.id,
      text: fmtTime(h.startedAt) + " · " + h.profileName + " · " + plural(h.hostsUp, "host") + (h.status !== "completed" ? " · " + h.status : "") })));
    if (S.resultId && S.history.some((h) => h.id === S.resultId)) sel.value = S.resultId;
    sel.disabled = !S.history.length;
    for (const id of ["btn-res-pdf", "btn-res-csv", "btn-res-json"]) $(id).disabled = !S.resultId;
  }
  $("res-scan").onchange = () => loadScan($("res-scan").value);
  $("btn-res-pdf").onclick = () => S.resultId && download("/scans/" + S.resultId + "/report.pdf", "scan.pdf");
  $("btn-res-csv").onclick = () => S.resultId && download("/scans/" + S.resultId + "/export.csv", "scan.csv");
  $("btn-res-json").onclick = () => S.resultId && download("/scans/" + S.resultId + "/export.json", "scan.json");

  async function loadScan(id) {
    if (!id) return;
    document.body.dataset.uiReady = "false";
    try {
      const r = await api("GET", "/scans/" + encodeURIComponent(id));
      if (S.resultId !== id) S.openHosts = new Set();
      S.result = r; S.resultId = id;
      fillScanSelect();
      renderResult();
    } catch (e) {
      $("res-hosts").replaceChildren(el("tr", {}, el("td", { colspan: "5", class: "error", text: "Cannot load the scan: " + e.message })));
    } finally {
      document.body.dataset.uiReady = "true";
    }
  }

  function renderResult() {
    const r = S.result;
    if (!r) return;
    const p = r.profile || {};
    $("res-info").textContent = p.name + " · " + fmtTime(r.startedAt) + " · " + fmtDur(r.durationS) + " · " +
      plural(r.targets.count, "address") + " · TCP " + (p.tcpPorts || "none") + " · UDP " + (p.udpPorts || "none") + " · " +
      (TRIGGER_LABEL[r.trigger] || r.trigger) + (r.user ? " (" + r.user + ")" : "") + " · " + r.status + (r.error ? ": " + r.error : "");
    $("res-tiles").replaceChildren(...summaryTiles(r.summary, r.changes));
    const ch = r.changes;
    const box = $("res-changes");
    box.hidden = !(ch && ch.total);
    if (ch && ch.total) {
      const items = [];
      for (const ip of ch.newHosts) items.push("new host " + ip);
      for (const ip of ch.goneHosts) items.push("host gone: " + ip);
      for (const x of ch.openedPorts) items.push("port opened: " + x.ip + " " + x.proto + "/" + x.port);
      for (const x of ch.closedPorts) items.push("port closed: " + x.ip + " " + x.proto + "/" + x.port);
      for (const x of ch.macChanged) items.push("MAC changed: " + x.ip + " " + x.old + " → " + x.new);
      box.replaceChildren(el("b", { text: ch.total + " change" + (ch.total > 1 ? "s" : "") + " since " + fmtTime(ch.previousFinishedAt) }),
        el("ul", {}, ...items.slice(0, 50).map((t) => el("li", { text: t }))));
    }
    const notes = r.notes || [];
    $("res-notes").hidden = !notes.length;
    $("res-notes").textContent = notes.length ? "Notes: " + notes.join(" · ") : "";
    renderHosts();
  }

  function hostMatches(h) {
    if (S.filter !== "all") {
      const ratings = [h.rating].concat((h.ports || []).map((p) => p.rating));
      if (!ratings.includes(S.filter)) return false;
    }
    if (!S.search) return true;
    const q = S.search.toLowerCase();
    const hay = [h.ip, h.mac, h.vendor, ...hostNames(h), ...(h.ports || []).map((p) => portLabel(p) + " " + (p.service || "") + " " + (p.name || "") + " " + productLine(p))]
      .join(" ").toLowerCase();
    return hay.includes(q);
  }

  function renderHosts() {
    const tbody = $("res-hosts");
    const r = S.result;
    const hosts = (r.hosts || []).filter(hostMatches);
    const rows = [];
    for (const h of hosts) {
      const forbidden = h.rating === "forbidden" || (h.ports || []).some((p) => p.rating === "forbidden");
      const tags = [];
      if (h.self) tags.push(el("span", { class: "badge tag", text: "this device" }));
      if (h.gateway) tags.push(el("span", { class: "badge tag", text: "gateway" }));
      const names = hostNames(h);
      const ports = (h.ports || []).map((p) => el("span", { class: "port " + (p.rating || ""), title: (p.service || p.name || "") + (p.ratingNote ? " · " + p.ratingNote : ""), text: portLabel(p) }));
      const tr = el("tr", { class: "clickable" + (forbidden ? " row-bad" : "") + (S.openHosts.has(h.ip) ? " open" : ""), "data-ip": h.ip },
        el("td", {}, document.createTextNode(h.ip), ...tags.map((t) => [document.createTextNode(" "), t]).flat()),
        el("td", {}, document.createTextNode(names[0] || ""), names.length > 1 ? el("span", { class: "sub", text: names.slice(1).join(", ") }) : null),
        el("td", { class: "mono" }, document.createTextNode(h.mac || ""), h.vendor ? el("span", { class: "sub", text: h.vendor }) : null),
        el("td", {}, ...(ports.length ? ports : [el("span", { class: "muted", text: "–" })])),
        el("td", {}, badge(h.rating)));
      tr.onclick = () => { if (S.openHosts.has(h.ip)) S.openHosts.delete(h.ip); else S.openHosts.add(h.ip); renderHosts(); };
      rows.push(tr);
      if (S.openHosts.has(h.ip)) rows.push(el("tr", { class: "detail-row" }, el("td", { colspan: "5", class: "detail" }, hostDetail(h))));
    }
    if (!rows.length) rows.push(el("tr", {}, el("td", { colspan: "5", class: "muted", text: r.hosts.length ? "No host matches the filter." : "No hosts found." })));
    tbody.replaceChildren(...rows);
  }

  function hostDetail(h) {
    const dl = el("dl", { class: "detail-grid" });
    const add = (k, v) => { if (v) dl.append(el("dt", { text: k }), el("dd", { text: v })); };
    add("MAC", h.mac ? h.mac + (h.vendor ? " · " + h.vendor : "") : "");
    add("Interface", h.interface);
    add("Found by", (h.via || []).join(", "));
    add("Names", (h.names || []).map((n) => n.name + " (" + n.source + ")").join(", "));
    const d = h.discovery || {};
    add("mDNS services", (d.mdns || []).join(", "));
    if (d.ssdp) add("UPnP/SSDP", Object.entries(d.ssdp).map(([k, v]) => k + ": " + v).join(" · "));
    if (d.wsd) add("WS-Discovery", Object.entries(d.wsd).filter(([k]) => k !== "protocol").map(([k, v]) => k + ": " + v).join(" · "));
    if (d.ethernetIp) add("EtherNet/IP", d.ethernetIp.product + " (vendor " + d.ethernetIp.vendorId + ", serial " + d.ethernetIp.serial + ")");
    add("Rating", RATING_LABEL[h.rating] + (h.ratingNote ? " - " + h.ratingNote : ""));
    add("Not open", "TCP closed: " + h.tcpClosed + ", no answer: " + h.tcpFiltered + ((h.udpOpenFiltered || []).length ? " · UDP without answer: " + h.udpOpenFiltered.join(", ") : ""));
    const box = el("div", {}, dl);
    if (S.canWrite) {
      const name = hostNames(h)[0] || h.vendor || "";
      const acts = el("div", { class: "detail-actions" });
      const b = (text, fn, cls) => { const x = el("button", { type: "button", class: "btn btn-small " + (cls || ""), text }); x.onclick = (ev) => { ev.stopPropagation(); fn(); }; return x; };
      acts.append(b("Host expected", () => addToList("whitelist", { hosts: h.ip, ports: "", note: name })));
      if (h.mac) acts.append(b("Host expected (by MAC)", () => addToList("whitelist", { hosts: h.mac, ports: "", note: name })));
      acts.append(b("Host forbidden", () => addToList("blacklist", { hosts: h.ip, ports: "", note: name }), "btn-danger-text"));
      if ((h.ports || []).length) acts.append(b("All open ports expected", () => addToList("whitelist", { hosts: h.ip, ports: h.ports.map(portLabel).join(", "), note: name })));
      box.append(acts);
    }
    const ports = h.ports || [];
    if (ports.length) {
      const rows = ports.map((p) => {
        const act = el("td", { class: "act" });
        if (S.canWrite) {
          const ok = el("button", { type: "button", class: "btn btn-small", text: "Expected", title: "Add " + portLabel(p) + " on " + h.ip + " to the whitelist" });
          ok.onclick = (ev) => { ev.stopPropagation(); addToList("whitelist", { hosts: h.ip, ports: portLabel(p), note: hostNames(h)[0] || "" }); };
          const no = el("button", { type: "button", class: "btn btn-small btn-danger-text", text: "Forbidden", title: "Add " + portLabel(p) + " on " + h.ip + " to the blacklist" });
          no.onclick = (ev) => { ev.stopPropagation(); addToList("blacklist", { hosts: h.ip, ports: portLabel(p), note: hostNames(h)[0] || "" }); };
          act.append(ok, document.createTextNode(" "), no);
        }
        return el("tr", {}, el("td", { class: "mono", text: portLabel(p) }), el("td", { text: p.service || p.name || "" }),
          el("td", { class: "details", text: productLine(p) || "–" }),
          el("td", {}, badge(p.rating), p.ratingNote ? el("span", { class: "sub", text: p.ratingNote }) : null), act);
      });
      box.append(el("table", { class: "ports" },
        el("thead", {}, el("tr", {}, ...["Port", "Service", "Details", "Rating", ""].map((t) => el("th", { text: t })))),
        el("tbody", {}, ...rows)));
    }
    box.onclick = (ev) => ev.stopPropagation();
    return box;
  }

  $("res-search").oninput = () => { S.search = $("res-search").value.trim(); renderHosts(); };
  for (const b of document.querySelectorAll(".seg-btn")) {
    b.onclick = () => {
      S.filter = b.dataset.filter;
      for (const x of document.querySelectorAll(".seg-btn")) x.classList.toggle("active", x === b);
      if (S.result) renderHosts();
    };
  }

  // whitelist/blacklist entries from the results: port entries for the same host are merged
  async function addToList(kind, entry) {
    const next = clone(S.settings);
    const list = next.lists[kind];
    const same = entry.ports && list.find((e) => e.hosts === entry.hosts && e.ports);
    if (same) {
      const have = same.ports.split(/[\s,;]+/).filter(Boolean);
      for (const p of entry.ports.split(/[\s,;]+/).filter(Boolean)) if (!have.includes(p)) have.push(p);
      same.ports = have.join(", ");
    } else {
      list.push({ hosts: entry.hosts, ports: entry.ports, note: (entry.note || "").slice(0, 120) });
    }
    try {
      await saveSettings(next);
    } catch (e) {
      alert("Not added: " + ((e.data && e.data.errors) || [e.message]).join("; "));
      return;
    }
    if (S.resultId) await loadScan(S.resultId);
    refreshStatus();
  }

  // ---- settings (whole document) ------------------------------------------------------------
  async function loadSettings() {
    try { S.settings = await api("GET", "/settings"); } catch (e) { showBanner("Cannot load settings: " + e.message); return; }
    afterSettings();
  }
  async function saveSettings(next) {
    S.settings = await api("PUT", "/settings", next);
    afterSettings();
  }
  function afterSettings() {
    fillRunProfiles();
    renderSchedules();
    renderProfileList();
    if (!S.profileDirty) editProfile(Math.min(S.profileIdx, S.settings.profiles.length - 1), true);
    if (!S.listsDirty) { S.listsDraft = clone(S.settings.lists); renderLists(); }
    if (!S.setDirty) fillSettingsForm();
    applyPermissions();
  }

  function applyPermissions() {
    const ro = !S.canWrite;
    for (const n of document.querySelectorAll("#prof-form input, #prof-form select, #prof-form textarea, #prof-form button, #btn-prof-new, " +
      "#tab-lists input, #tab-lists button, #set-form input, #set-form select, #set-form button, .chip")) n.disabled = ro;
    $("btn-start").disabled = ro;
    $("btn-start").title = ro ? "Needs the permission Manage Network Scanner" : "";
    $("btn-prof-del").disabled = ro || !!S.profileNew || !S.settings || S.settings.profiles.length < 2;
  }

  // ---- profiles ----------------------------------------------------------------------------------
  const WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
  function renderProfileList() {
    const ul = $("prof-list");
    ul.replaceChildren(...S.settings.profiles.map((p, i) => {
      const sched = p.scheduleMode === "off" ? "manual" : p.scheduleMode === "interval" ? "every " + p.scheduleIntervalMinutes + " min" : "at " + p.scheduleTimes;
      const li = el("li", { class: i === S.profileIdx && !S.profileNew ? "sel" : "" }, document.createTextNode(p.name), el("span", { class: "sub", text: p.id + " · " + sched }));
      li.onclick = () => {
        if (S.profileDirty && !confirm("Discard the changes to this profile?")) return;
        S.profileNew = false; editProfile(i);
      };
      return li;
    }));
    if (S.profileNew) ul.append(el("li", { class: "sel" }, document.createTextNode(S.profileDraft.name || "New profile"), el("span", { class: "sub", text: "not saved" })));
  }

  function editProfile(i, keepDirtyFlag) {
    S.profileIdx = Math.max(0, i);
    S.profileDraft = clone(S.settings.profiles[S.profileIdx]);
    S.profileNew = false;
    S.profileDirty = false;
    fillProfileForm();
    renderProfileList();
    if (!keepDirtyFlag) $("prof-state").textContent = "";
  }

  function fillProfileForm() {
    const p = S.profileDraft;
    $("prof-title").textContent = S.profileNew ? "New profile" : p.name;
    for (const n of document.querySelectorAll("#prof-form [data-f]")) {
      const v = p[n.dataset.f];
      if (n.type === "checkbox") n.checked = !!v; else n.value = v ?? "";
      const field = n.closest(".field"); if (field) field.classList.remove("invalid");
    }
    const days = new Set((p.scheduleDays || "").split(/[\s,;]+/).filter(Boolean).map((d) => d.slice(0, 3).toLowerCase()));
    const daysBox = $("p-sdays");
    daysBox.replaceChildren(...WEEKDAYS.map((d) => el("label", { class: "checkbox" },
      el("input", { type: "checkbox", "data-day": d, checked: !days.size || days.has(d) }), document.createTextNode(" " + d))));
    for (const c of daysBox.querySelectorAll("input")) c.onchange = profileChanged;
    showScheduleFields();
    $("prof-errors").hidden = true;
    applyPermissions();
    profileEstimate();
  }

  function readProfileForm() {
    const p = S.profileDraft;
    for (const n of document.querySelectorAll("#prof-form [data-f]")) {
      if (n.type === "checkbox") p[n.dataset.f] = n.checked;
      else if (n.type === "number") p[n.dataset.f] = n.value === "" ? NaN : Number(n.value);
      else p[n.dataset.f] = n.value.trim();
    }
    const days = [...$("p-sdays").querySelectorAll("input:checked")].map((c) => c.dataset.day);
    p.scheduleDays = days.length === 7 ? "" : days.join(",");
    return p;
  }

  function showScheduleFields() {
    const mode = $("p-smode").value;
    for (const n of document.querySelectorAll("#prof-form [data-when]")) n.hidden = n.dataset.when !== mode;
  }

  let estTimer = null;
  function profileChanged() {
    S.profileDirty = true;
    readProfileForm();
    showScheduleFields();
    $("prof-state").textContent = "unsaved changes";
    clearTimeout(estTimer);
    estTimer = setTimeout(profileEstimate, 400);
  }
  for (const n of document.querySelectorAll("#prof-form [data-f]")) { n.oninput = profileChanged; n.onchange = profileChanged; }

  async function profileEstimate() {
    const p = S.profileDraft;
    if (!p) return;
    const e = await estimateFor(p, "profile");
    if (e) $("prof-estimate").textContent = estimateText(p, e);
  }

  function renderTargetChips() {
    const box = $("p-target-chips");
    const chips = [["auto", "auto"]];
    for (const i of (S.network ? S.network.interfaces : [])) {
      if (!i.addresses.length) continue;
      chips.push(["if:" + i.name, "if:" + i.name]);
      for (const n of i.networks) chips.push([n, n]);
    }
    box.replaceChildren(...chips.map(([label, value]) => {
      const c = el("button", { type: "button", class: "chip", text: "+ " + label });
      c.onclick = () => appendToField($("p-targets"), value);
      return c;
    }));
    for (const [proto, presets] of [["tcp", ["common", "industrial", "web", "well-known", "all"]], ["udp", ["common", "industrial"]]]) {
      const pbox = document.querySelector('[data-presets="' + proto + '"]');
      pbox.replaceChildren(...presets.map((name) => {
        const c = el("button", { type: "button", class: "chip", text: "+ " + name });
        c.onclick = () => appendToField($(proto === "tcp" ? "p-tcp" : "p-udp"), name);
        return c;
      }));
    }
    applyPermissions();
  }
  function appendToField(input, value) {
    const items = input.value.split(/[\s,;]+/).filter(Boolean);
    if (!items.includes(value)) items.push(value);
    input.value = items.join(", ");
    profileChanged();
  }

  function profileErrors(errors) {
    const idx = S.profileNew ? S.settings.profiles.length : S.profileIdx;
    const prefix = "profiles[" + idx + "].";
    const mine = errors.filter((m) => m.startsWith(prefix) || !m.startsWith("profiles["));
    const list = $("prof-errors");
    list.replaceChildren(...(mine.length ? mine : errors).map((m) => el("li", { text: m.replace(prefix, "") })));
    list.hidden = false;
    for (const n of document.querySelectorAll("#prof-form [data-f]")) {
      const bad = mine.some((m) => m.startsWith(prefix + n.dataset.f + ":"));
      const field = n.closest(".field"); if (field) field.classList.toggle("invalid", bad);
    }
  }

  async function saveProfile(run) {
    const p = readProfileForm();
    const next = clone(S.settings);
    if (S.profileNew) next.profiles.push(p); else next.profiles[S.profileIdx] = p;
    $("prof-state").textContent = "Saving…";
    try {
      const wasNew = S.profileNew;
      S.profileDirty = false;
      await saveSettings(next);
      const idx = wasNew ? S.settings.profiles.length - 1 : S.profileIdx;
      editProfile(idx);
      $("prof-state").textContent = "Saved " + new Date().toLocaleTimeString();
      if (run) {
        try { await api("POST", "/scans", { profile: p.id }); S.wasRunning = true; showTab("overview"); }
        catch (e) { alert("Scan not started: " + e.message); }
        refreshStatus();
      }
    } catch (e) {
      S.profileDirty = true;
      profileErrors((e.data && e.data.errors) || [e.message]);
      $("prof-state").textContent = "Not saved";
    }
  }
  $("prof-form").onsubmit = (ev) => { ev.preventDefault(); saveProfile(false); };
  $("btn-prof-run").onclick = () => saveProfile(true);
  $("btn-prof-revert").onclick = () => { if (S.profileNew) { S.profileNew = false; } editProfile(S.profileIdx); };
  function newProfile(base) {
    if (S.profileDirty && !confirm("Discard the changes to this profile?")) return;
    const ids = new Set(S.settings.profiles.map((p) => p.id));
    let n = 1, id = "profile-1";
    while (ids.has(id)) id = "profile-" + (++n);
    S.profileDraft = Object.assign(clone(base), { id, name: base.name ? base.name + " (copy)" : "New profile", scheduleMode: "off" });
    S.profileNew = true;
    S.profileDirty = true;
    fillProfileForm();
    renderProfileList();
    $("prof-state").textContent = "not saved yet";
  }
  $("btn-prof-new").onclick = () => newProfile(Object.assign(clone(S.settings.profiles[0]), { name: "", targets: "auto", exclude: "",
    tcpPorts: "common", udpPorts: "", identifyAutomation: false }));
  $("btn-prof-dup").onclick = () => newProfile(readProfileForm());
  $("btn-prof-del").onclick = async () => {
    const p = S.settings.profiles[S.profileIdx];
    if (!confirm("Delete the profile “" + p.name + "”? Its scans stay in the history.")) return;
    const next = clone(S.settings);
    next.profiles.splice(S.profileIdx, 1);
    try { S.profileDirty = false; S.profileIdx = 0; await saveSettings(next); editProfile(0); }
    catch (e) { profileErrors((e.data && e.data.errors) || [e.message]); }
  };

  // ---- lists ----------------------------------------------------------------------------------------
  function renderLists() {
    for (const kind of ["whitelist", "blacklist"]) {
      const rows = S.listsDraft[kind].map((entry, i) => {
        const input = (f, ph) => {
          const n = el("input", { type: "text", value: entry[f], placeholder: ph, "aria-label": f, spellcheck: "false" });
          n.oninput = () => { entry[f] = n.value; S.listsDirty = true; $("lists-state").textContent = "unsaved changes"; };
          return n;
        };
        const del = el("button", { type: "button", class: "btn btn-small btn-ghost btn-danger-text", text: "Remove" });
        del.onclick = () => { S.listsDraft[kind].splice(i, 1); S.listsDirty = true; $("lists-state").textContent = "unsaved changes"; renderLists(); };
        return el("tr", { "data-kind": kind, "data-i": i },
          el("td", {}, input("hosts", "all hosts")), el("td", {}, input("ports", "host itself")),
          el("td", {}, input("note", "")), el("td", {}, del));
      });
      $("list-" + kind).replaceChildren(...(rows.length ? rows : [el("tr", {}, el("td", { colspan: "4", class: "muted", text: "No entries." }))]));
    }
    applyPermissions();
  }
  for (const b of document.querySelectorAll("[data-add]")) {
    b.onclick = () => { S.listsDraft[b.dataset.add].push({ hosts: "", ports: "", note: "" }); S.listsDirty = true; renderLists();
      const rows = $("list-" + b.dataset.add).querySelectorAll("tr"); const last = rows[rows.length - 1]; if (last) last.querySelector("input").focus(); };
  }
  $("btn-lists-save").onclick = async () => {
    const next = clone(S.settings);
    next.lists = clone(S.listsDraft);
    $("lists-state").textContent = "Saving…";
    try {
      S.listsDirty = false;
      await saveSettings(next);
      $("lists-errors").hidden = true;
      $("lists-state").textContent = "Saved " + new Date().toLocaleTimeString() + " - results are rated with the new lists";
      if (S.resultId) loadScan(S.resultId);
      refreshStatus();
    } catch (e) {
      S.listsDirty = true;
      const errors = (e.data && e.data.errors) || [e.message];
      $("lists-errors").replaceChildren(...errors.map((m) => el("li", { text: m })));
      $("lists-errors").hidden = false;
      for (const tr of document.querySelectorAll(".list-table tr[data-kind]")) {
        tr.classList.toggle("invalid", errors.some((m) => m.startsWith("lists." + tr.dataset.kind + "[" + tr.dataset.i + "]")));
      }
      $("lists-state").textContent = "Not saved";
    }
  };
  $("btn-lists-revert").onclick = () => { S.listsDirty = false; S.listsDraft = clone(S.settings.lists); renderLists(); $("lists-state").textContent = ""; $("lists-errors").hidden = true; };

  // ---- settings form ----------------------------------------------------------------------------------
  const getAt = (o, path) => path.split(".").reduce((x, k) => (x == null ? undefined : x[k]), o);
  const setAt = (o, path, v) => { const ks = path.split("."); let x = o; for (const k of ks.slice(0, -1)) x = x[k]; x[ks[ks.length - 1]] = v; };
  function fillSettingsForm() {
    for (const n of document.querySelectorAll("#set-form [data-s]")) {
      n.value = getAt(S.settings, n.dataset.s) ?? "";
      n.closest(".field").classList.remove("invalid");
      n.oninput = () => { S.setDirty = true; $("set-state").textContent = "unsaved changes"; };
    }
  }
  $("set-form").onsubmit = async (ev) => {
    ev.preventDefault();
    const next = clone(S.settings);
    for (const n of document.querySelectorAll("#set-form [data-s]")) setAt(next, n.dataset.s, n.type === "number" ? (n.value === "" ? NaN : Number(n.value)) : n.value);
    try {
      S.setDirty = false;
      await saveSettings(next);
      $("set-errors").hidden = true;
      $("set-state").textContent = "Saved " + new Date().toLocaleTimeString();
      refreshStatus();
    } catch (e) {
      S.setDirty = true;
      const errors = (e.data && e.data.errors) || [e.message];
      $("set-errors").replaceChildren(...errors.map((m) => el("li", { text: m })));
      $("set-errors").hidden = false;
      for (const n of document.querySelectorAll("#set-form [data-s]")) n.closest(".field").classList.toggle("invalid", errors.some((m) => m.startsWith(n.dataset.s + ":")));
      $("set-state").textContent = "Not saved";
    }
  };
  $("btn-set-revert").onclick = () => { S.setDirty = false; fillSettingsForm(); $("set-errors").hidden = true; $("set-state").textContent = ""; };

  async function refreshValues() {
    let values;
    try { values = await api("GET", "/values"); } catch (e) { return; }
    const rows = Object.values(values).map((v) => {
      const full = String(v.value);
      return el("tr", { title: v.description || "" }, el("td", { class: "mono", text: v.address + (v.writable ? "  (writable)" : "") }),
        el("td", { text: v.type }), el("td", { class: "mono", text: full.length > 120 ? full.slice(0, 117) + "…" : full, title: full.length > 120 ? full.slice(0, 2000) : "" }));
    });
    $("values-body").replaceChildren(...rows);
    $("dl-start").textContent = PREFIX.slice(1) + "/scan/start";
  }

  // ---- polling --------------------------------------------------------------------------------------
  let pollTimer = null;
  function schedulePoll(ms) {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(async () => {
      await refreshStatus();
      if (S.tab === "settings") refreshValues();
      const running = S.status && S.status.scan && S.status.scan.running;
      schedulePoll(document.hidden ? 15000 : (running ? 2000 : 5000));
    }, ms);
  }

  (async function init() {
    showTab((location.hash || "#overview").slice(1), false);
    await refreshStatus();
    await loadSettings();
    renderSchedules();
    await loadNetwork();
    await loadHistory();
    S.wasRunning = !!(S.status && S.status.scan && S.status.scan.running);
    if (S.history.length) await loadScan(S.history[0].id);
    if (S.tab === "settings") await refreshValues();
    document.body.dataset.uiReady = "true";  // for tools/ui_check.py (screenshots wait for it)
    schedulePoll(S.wasRunning ? 2000 : 5000);
    window.addEventListener("resize", () => { if (S.tab === "history") renderChart(); });
  })();
})();
