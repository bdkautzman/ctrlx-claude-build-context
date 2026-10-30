// System Analyzer web UI - plain JS, no build step, no CDN (devices are often offline).
// Charts: uPlot (vendor/, MIT). Colors: a fixed categorical order (validated for color-vision
// deficiencies); an entity keeps its color while it is charted, it never follows its rank.
//
// Auth: the ctrlX menu opens "/<snap>/?token=${bearertoken}". We move the token from the URL
// into sessionStorage (so it is not left in history/bookmarks) and send it as
// "Authorization: Bearer" on every API call. The reverse proxy validates it because
// "/<snap>/api" is listed as restricted in the package manifest.
"use strict";

(function () {
  const PREFIX = "/" + location.pathname.split("/").filter(Boolean)[0]; // "/<snap-name>"
  const API = PREFIX + "/api/v1";
  const TOKEN_KEY = PREFIX.slice(1) + ".token";

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

  async function request(method, path, body) {
    const headers = { "Accept": "application/json" };
    if (token) headers["Authorization"] = "Bearer " + token;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const res = await fetch(API + path, {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin", cache: "no-store",
    });
    if (res.status === 401) showBanner("Session expired or no token - please reopen this app from the ctrlX menu.");
    return res;
  }

  async function api(method, path, body) {
    const res = await request(method, path, body);
    let data = null;
    const text = await res.text();
    if (text) { try { data = JSON.parse(text); } catch (e) { data = { title: text }; } }
    if (!res.ok) { const err = new Error((data && (data.detail || data.title)) || res.statusText); err.status = res.status; err.data = data; throw err; }
    return data;
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
    for (const c of children) if (c !== null && c !== undefined && c !== false) node.append(c);
    return node;
  }
  function showBanner(msg) { const b = $("banner"); b.textContent = msg; b.hidden = !msg; }
  function fmtMb(v) {
    if (v === null || v === undefined) return "–";
    const a = Math.abs(v);
    return a >= 100 ? v.toFixed(0) : a >= 10 ? v.toFixed(1) : v.toFixed(2);
  }
  function fmtPct(v) { return v === null || v === undefined ? "–" : v.toFixed(Math.abs(v) >= 100 ? 0 : 1); }
  function fmtCount(v) { return v === null || v === undefined ? "–" : String(Math.round(v)); }
  function fmtTime(epoch) {
    if (!epoch) return "–";
    const d = new Date(epoch * 1000);
    return d.toLocaleDateString(undefined, { month: "2-digit", day: "2-digit" }) + " " +
      d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }
  function fmtDuration(s) {
    if (s === null || s === undefined) return "–";
    s = Math.round(s);
    const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
    if (d) return d + " d " + h + " h";
    if (h) return h + " h " + m + " min";
    return m ? m + " min" : s + " s";
  }
  function fmtBytes(b) { return b >= 1048576 ? (b / 1048576).toFixed(1) + " MB" : (b / 1024).toFixed(0) + " KB"; }
  function humanize(key) { return key.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/^./, (c) => c.toUpperCase()); }
  const UNIT_FMT = { "MB": fmtMb, "%": fmtPct, "": fmtCount };

  // ---- colors (dataviz reference palette, fixed order, light surface) ---------
  const PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
  const INK2 = "#6a737d", GRID = "#e8eaed";
  const EVENT_COLORS = { restarted: "#c76b00", stopped: "#c8102e", started: "#00884a", updated: "#6a737d",
    marker: "#007bc0", analyzer: "#6a737d" };
  const colorOf = new Map();   // entity key -> palette slot (stable while charted)
  let colorClock = 0;
  const colorUsed = new Map(); // entity key -> last time it was charted
  function colorsFor(keys) {
    // keep each key's slot unless another key of this chart has it; free slots go to new keys,
    // a slot is reused only from keys not charted most recently
    const out = new Map(), taken = new Set();
    colorClock++;
    for (const k of keys) {
      const slot = colorOf.get(k);
      if (slot !== undefined && !taken.has(slot)) { out.set(k, slot); taken.add(slot); }
    }
    for (const k of keys) {
      if (out.has(k)) continue;
      let slot = PALETTE.findIndex((_, i) => !taken.has(i) && ![...colorOf.values()].includes(i));
      if (slot < 0) {
        let oldest = null;
        for (const [other, s] of colorOf) {
          if (taken.has(s) || keys.includes(other)) continue;
          if (oldest === null || (colorUsed.get(other) || 0) < (colorUsed.get(oldest) || 0)) oldest = other;
        }
        slot = oldest !== null ? colorOf.get(oldest) : PALETTE.findIndex((_, i) => !taken.has(i));
        if (oldest !== null) colorOf.delete(oldest);
      }
      colorOf.set(k, slot); out.set(k, slot); taken.add(slot);
    }
    for (const k of keys) colorUsed.set(k, colorClock);
    return new Map([...out].map(([k, s]) => [k, PALETTE[s]]));
  }
  function alpha(hex, a) {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`;
  }

  // ---- state ---------------------------------------------------------------
  const state = {
    preset: 3600, from: 0, to: 0, tab: "overview",
    app: null, proc: null, threads: new Set(), sort: { key: "rssMb", dir: -1 },
    leakLevel: "apps", leakMetric: "anon", leakApp: "", leakEntity: null,
    canWrite: false, status: null, overview: null, group: null, analysis: null, events: [],
    source: null, busy: false, timer: null, clockOffset: 0,
  };

  // "now" of the device: all data is stamped with the device clock, which may be off (no NTP)
  function deviceNow() { return Date.now() / 1000 + state.clockOffset; }
  function clockNote() {
    const off = state.clockOffset;
    if (Math.abs(off) < 120) return "";
    return "device clock " + (off < 0 ? "behind" : "ahead of") + " this computer by " + fmtDuration(Math.abs(off)) + " - times are the device's";
  }
  function updateRange() {
    if (state.preset) {
      state.to = deviceNow();
      state.from = state.to - state.preset;
    }
  }
  function toLocalInput(epoch) {
    const d = new Date(epoch * 1000 - new Date(epoch * 1000).getTimezoneOffset() * 60000);
    return d.toISOString().slice(0, 16);
  }
  function showRangeInputs() {
    $("range-from").value = toLocalInput(state.from);
    $("range-to").value = toLocalInput(state.to);
    for (const b of document.querySelectorAll("[data-preset]")) {
      b.classList.toggle("active", Number(b.dataset.preset) === state.preset);
      b.setAttribute("aria-pressed", Number(b.dataset.preset) === state.preset ? "true" : "false");
    }
  }
  function rangeQuery() { return "from=" + state.from.toFixed(0) + "&to=" + state.to.toFixed(0); }
  function zoomTo(from, to) {
    if (!(to > from)) return;
    state.preset = null;
    state.from = from;
    state.to = to;
    refresh(true);
  }

  // ---- charts --------------------------------------------------------------
  const charts = {};
  function drawEvents(u) {
    const events = u._events;
    if (!events || !events.length) return;
    const ctx = u.ctx, { left, top, width, height } = u.bbox, dpr = devicePixelRatio || 1;
    ctx.save();
    ctx.lineWidth = dpr;
    for (const e of events) {
      const x = Math.round(u.valToPos(e.time, "x", true));
      if (x < left || x > left + width) continue;
      ctx.strokeStyle = ctx.fillStyle = EVENT_COLORS[e.type] || INK2;
      ctx.beginPath(); ctx.moveTo(x + 0.5, top); ctx.lineTo(x + 0.5, top + height); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(x - 4 * dpr, top); ctx.lineTo(x + 4 * dpr, top); ctx.lineTo(x, top + 6 * dpr); ctx.fill();
    }
    ctx.restore();
  }

  // spec: {unit, series: [{label, color, width, dash, fade}], events, height, fromZero}
  function renderChart(id, spec, data) {
    const node = $(id);
    if (!node || node.offsetParent === null) return;
    const fmt = UNIT_FMT[spec.unit] || fmtCount;
    const sig = spec.series.map((s) => s.label + s.color + (s.dash ? "d" : "")).join("|") + spec.unit + (spec.fromZero !== false);
    let chart = charts[id];
    if (!spec.series.length) {
      if (chart) { chart.u.destroy(); delete charts[id]; }
      node.replaceChildren(el("p", { class: "muted small empty", text: spec.empty || "No data in this range." }));
      return;
    }
    if (chart && chart.sig === sig && chart.node === node) {
      chart.u._events = spec.events || [];
      chart.u.setData(data);     // rescales: x always spans the selected range (scales.x.range)
      return;
    }
    if (chart) chart.u.destroy();
    node.replaceChildren();
    const height = spec.height || 200;
    const opts = {
      width: Math.max(220, node.clientWidth), height,
      cursor: { sync: { key: "sa" }, drag: { x: true, y: false, setScale: false }, points: { size: 6 } },
      scales: {
        x: { time: true, range: () => [state.from, state.to] },
        y: { range: (u, min, max) => {
          if (min === null || max === null) return [0, 1];
          if (spec.fromZero === false) { const pad = (max - min) * 0.1 || Math.abs(max) * 0.05 || 1; return [min - pad, max + pad]; }
          return [Math.min(0, min), max > 0 ? max * 1.08 : 1];
        } },
      },
      axes: [
        { stroke: INK2, grid: { stroke: GRID, width: 1 }, ticks: { stroke: GRID, width: 1 } },
        { stroke: INK2, grid: { stroke: GRID, width: 1 }, ticks: { show: false }, size: 52,
          values: (u, vals) => vals.map((v) => fmt(v)) },
      ],
      series: [{ value: (u, v) => (v === null || v === undefined ? "–" : fmtTime(v)) }].concat(spec.series.map((s) => ({
        label: s.label, stroke: s.fade ? alpha(s.color, 0.45) : s.color, width: s.width || 2, dash: s.dash,
        points: { show: false }, spanGaps: false,
        value: (u, v) => (v === null || v === undefined ? "–" : fmt(v) + (spec.unit ? " " + spec.unit : "")),
      }))),
      legend: { live: true },
      hooks: {
        setSelect: [(u) => {
          if (u.select.width > 4) {
            const a = u.posToVal(u.select.left, "x"), b = u.posToVal(u.select.left + u.select.width, "x");
            u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
            zoomTo(a, b);
          }
        }],
        draw: [drawEvents],
      },
    };
    const u = new uPlot(opts, data, node);
    u._events = spec.events || [];
    charts[id] = { u, sig, node, height };
  }
  function resizeCharts() {
    for (const c of Object.values(charts)) {
      if (c.node.offsetParent !== null) c.u.setSize({ width: Math.max(220, c.node.clientWidth), height: c.height });
    }
  }
  window.addEventListener("resize", () => { clearTimeout(resizeCharts.t); resizeCharts.t = setTimeout(resizeCharts, 150); });

  async function fetchSeries(ids, points) {
    const out = { times: [], series: {} };
    for (let i = 0; i < ids.length; i += 200) {   // the API takes at most 200 ids per call
      const part = ids.slice(i, i + 200);
      const r = await api("GET", "/series?ids=" + encodeURIComponent(part.join(",")) + "&" + rangeQuery() + "&points=" + (points || 600));
      if (i === 0) { out.times = r.times; out.tier = r.tier; out.source = r.source; out.step = r.step; out.oldest = r.oldest; }
      Object.assign(out.series, r.series);
    }
    state.source = out;
    return out;
  }
  // cols: [[sid, "avg"|"min"|"max"], ...] -> uPlot data
  function columns(resp, cols) {
    return [resp.times].concat(cols.map(([sid, agg]) => {
      const s = resp.series[sid];
      return s ? s[agg] : resp.times.map(() => null);
    }));
  }
  function hasData(resp, sid) { const s = resp.series[sid]; return !!s && s.avg.some((v) => v !== null); }
  function eventsFor(appKey) {
    return state.events.filter((e) => e.type === "marker" || e.type === "analyzer" || (appKey && e.app === appKey));
  }

  // ---- status / pills --------------------------------------------------------
  async function refreshStatus() {
    try {
      const asked = Date.now() / 1000;
      const s = await api("GET", "/status");
      if (typeof s.serverTime === "number") state.clockOffset = s.serverTime - (asked + Date.now() / 1000) / 2;
      state.status = s;
      state.canWrite = !!s.canWrite;
      document.title = s.title;
      $("app-title").textContent = s.title;
      $("app-version").textContent = /^\d/.test(s.version) ? "v" + s.version : s.version;
      const c = s.collector;
      const own = c.samples ? (c.avgCpuMs / 10 / c.intervalSeconds) : null;
      const pill = $("pill-sampling");
      pill.textContent = "Sampling " + c.intervalSeconds + " s · own CPU " + (own === null ? "…" : own.toFixed(1) + " %");
      pill.className = "pill " + (c.error ? "pill-warn" : "pill-ok");
      pill.title = "Each sample of /proc took " + c.lastCpuMs + " ms CPU (average " + c.avgCpuMs + " ms)" + (c.error ? " - " + c.error : "");
      const r = s.recording, mp = $("pill-medium");
      mp.textContent = r.state === "off" ? "Medium: off" : "Medium: " + r.medium + " · " + r.state;
      mp.className = "pill " + (r.state === "recording" ? "pill-ok" : r.state === "off" ? "pill-unknown" : "pill-warn");
      mp.title = r.error || (r.path || "Long-term recording on an external medium");
      const conn = $("conn");
      if (s.datalayer.connected) { conn.className = "pill pill-ok"; conn.textContent = "Data Layer: connected"; }
      else { conn.className = "pill pill-warn"; conn.textContent = s.datalayer.enabled ? "Data Layer: connecting…" : "Data Layer: off"; }
      if (state.tab === "settings") renderStatusList();
      showBanner("");
    } catch (e) {
      if (e.status !== 401) showBanner("Cannot reach the app: " + e.message);
    }
  }

  function renderStatusList() {
    const s = state.status;
    if (!s) return;
    const c = s.collector, h = s.history;
    const tier = (name) => h.tiers.find((t) => t.name === name) || {};
    const covers = (t) => (t.oldest ? "since " + fmtTime(t.oldest) : "empty") + " (" + fmtDuration(t.capacity * t.step) + " max)";
    const rows = [
      ["Version", s.version], ["Uptime", fmtDuration(s.uptimeSeconds)],
      ["Samples", c.samples + " · every " + c.intervalSeconds + " s"],
      ["CPU per sample", c.lastCpuMs + " ms (average " + c.avgCpuMs + " ms)"],
      ["Processes / threads", (c.processes ?? "–") + " / " + (c.threadCount ?? "–")],
      ["PSS readable", c.pssAvailable === null ? "off" : c.pssAvailable ? "yes" : "no (system-observe plug?)"],
      ["Pressure stall info", c.pressure === "ok" ? "yes" : c.pressure === "missing" ? "not in this device's kernel" : "not readable (system-observe plug?)"],
      ["Device clock", fmtTime(deviceNow()) + (clockNote() ? " · " + clockNote() : "")],
      ["History RAM", fmtBytes(h.bytes) + " of " + fmtBytes(h.maxBytes) + " · " + h.series + " series" + (h.refused ? " · " + h.refused + " refused (budget)" : "")],
      ["Every sample", covers(tier("raw"))], ["1-minute", covers(tier("minute"))], ["10-minute", covers(tier("tenminute"))],
      ["Settings source", s.settings.source + (s.settings.persisted ? "" : " (not saved)")],
      ["Data Layer", s.datalayer.connected ? "connected, " + s.datalayer.registered + " nodes" : (s.datalayer.error || "not connected")],
    ];
    $("status-list").replaceChildren(...rows.flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: String(v) })]));
    const err = s.settings.error || s.datalayer.error || c.error || "";
    $("st-error").textContent = s.datalayer.connected ? (s.settings.error || c.error || "") : err;
    $("st-error").hidden = !$("st-error").textContent;
  }

  // ---- overview -------------------------------------------------------------
  function tile(label, value, sub) {
    return el("div", { class: "tile" }, el("div", { class: "tile-label", text: label }),
      el("div", { class: "tile-value", text: value }), sub ? el("div", { class: "tile-sub", text: sub }) : null);
  }
  function rankBy(resp, sids, fn) {
    return sids.map((sid) => {
      const s = resp.series[sid];
      const vals = s ? s.avg.filter((v) => v !== null) : [];
      return [sid, vals.length ? fn(vals) : null];
    }).filter((x) => x[1] !== null && x[1] > 0).sort((a, b) => b[1] - a[1]);
  }

  async function renderOverview() {
    const ov = state.overview;
    if (!ov || !ov.system) return;
    const sys = ov.system, st = state.status;
    const own = st && st.collector.samples ? st.collector.avgCpuMs / 10 / st.collector.intervalSeconds : null;
    $("tiles").replaceChildren(
      tile("CPU now", fmtPct(sys.cpu) + " %", sys.coreCount + " cores: " + sys.cores.map((c) => fmtPct(c)).join(" / ")),
      tile("Memory used", fmtMb(sys.memUsedMb) + " MB", "of " + fmtMb(sys.memTotalMb) + " MB"),
      tile("Available", fmtMb(sys.memAvailableMb) + " MB", sys.swapTotalMb ? "swap used " + fmtMb(sys.swapUsedMb) + " MB" : "no swap"),
      sys.pressure.memory === null
        ? tile("Memory pressure", "n/a", st && st.collector.pressure === "missing" ? "not in this device's kernel" : "not readable")
        : tile("Memory pressure", fmtPct(sys.pressure.memory) + " %",
          "CPU " + (sys.pressure.cpu === null ? "–" : fmtPct(sys.pressure.cpu)) + " · I/O " + (sys.pressure.io === null ? "–" : fmtPct(sys.pressure.io)) + " %"),
      tile("Apps", String(ov.apps.length), ov.counts.processes + " processes · " + ov.counts.threads + " threads"),
      tile("Analyzer load", own === null ? "–" : fmtPct(own) + " %", "of one core"));

    const metric = state.leakMetric;
    const cores = [...Array(sys.coreCount).keys()].slice(0, 7);
    const apps = ov.apps;
    const memIds = apps.filter((a) => a.kind !== "kernel").map((a) => "app." + a.key + "." + metric);
    const cpuIds = apps.map((a) => "app." + a.key + ".cpu");
    const sysIds = ["sys.cpu", ...cores.map((i) => "sys.core" + i), "sys.memused", "sys.memavail", "sys.psicpu", "sys.psimem", "sys.psiio"];
    const resp = await fetchSeries(sysIds.concat(memIds, cpuIds), 500);
    const markers = eventsFor(null);
    const name = (sid) => { const a = apps.find((x) => sid === "app." + x.key + "." + sid.split(".").pop()); return a ? a.name : sid; };

    renderChart("ch-sys-cpu", { unit: "%", events: markers, series: [{ label: "total", color: PALETTE[0] }]
      .concat(cores.map((i) => ({ label: "core " + i, color: PALETTE[1 + i], width: 1.5 }))) },
    columns(resp, ["sys.cpu", ...cores.map((i) => "sys.core" + i)].map((s) => [s, "avg"])));
    renderChart("ch-sys-mem", { unit: "MB", events: markers, series: [{ label: "used", color: PALETTE[0] }, { label: "available", color: PALETTE[1] }] },
      columns(resp, [["sys.memused", "avg"], ["sys.memavail", "avg"]]));
    const psi = [["sys.psicpu", "CPU"], ["sys.psimem", "memory"], ["sys.psiio", "I/O"]].filter(([s]) => hasData(resp, s));
    renderChart("ch-sys-psi", { unit: "%", events: markers,
      empty: st && st.collector.pressure === "missing" ? "Not available: this device's kernel has no pressure stall information (/proc/pressure)."
        : st && st.collector.pressure === "unreadable" ? "Not readable (system-observe plug)." : "No data in this range.",
      series: psi.map(([, l], i) => ({ label: l, color: PALETTE[i] })) }, columns(resp, psi.map(([s]) => [s, "avg"])));

    $("sub-top-mem").textContent = "MB " + metric + " · the 8 largest in the range";
    const topMem = rankBy(resp, memIds, (v) => Math.max(...v)).slice(0, 8).map((x) => x[0]);
    const memColors = colorsFor(topMem.map((s) => s.split(".")[1]));
    renderChart("ch-top-mem", { unit: "MB", events: markers,
      series: topMem.map((s) => ({ label: name(s), color: memColors.get(s.split(".")[1]) })) }, columns(resp, topMem.map((s) => [s, "avg"])));
    const topCpu = rankBy(resp, cpuIds, (v) => v.reduce((a, b) => a + b, 0) / v.length).slice(0, 8).map((x) => x[0]);
    const cpuColors = colorsFor(topCpu.map((s) => s.split(".")[1]));
    renderChart("ch-top-cpu", { unit: "%", events: markers,
      series: topCpu.map((s) => ({ label: name(s), color: cpuColors.get(s.split(".")[1]) })) }, columns(resp, topCpu.map((s) => [s, "avg"])));

    renderGrowing();
  }

  function renderGrowing() {
    const list = $("growing-list");
    const a = state.analysis;
    if (!a) { list.replaceChildren(el("p", { class: "muted small", text: "…" })); return; }
    $("sub-growing").textContent = "Steady growth of " + a.metric + " over the selected range (≥ " + a.rules.minMbPerHour + " MB/h)";
    const rows = a.results.filter((r) => r.verdict === "growing" || r.verdict === "possible").slice(0, 8);
    if (!rows.length) {
      const enough = a.results.some((r) => r.verdict !== "insufficient");
      list.replaceChildren(el("p", { class: "muted small", text: enough ? "No app grows steadily in this range."
        : "Not enough data yet: the analysis needs at least " + a.rules.minSpanMinutes + " minutes." }));
      return;
    }
    list.replaceChildren(...rows.map((r) => {
      const item = el("button", { type: "button", class: "growing-item" },
        verdictBadge(r.verdict), el("span", { class: "g-name", text: r.name }),
        el("span", { class: "g-val", text: "+" + fmtMb(r.growthMbPerHour) + " MB/h" }),
        el("span", { class: "muted small", text: "fit " + r.fit.toFixed(2) + (r.hoursUntilFull ? " · full in " + fmtDuration(r.hoursUntilFull * 3600) : "") }));
      item.addEventListener("click", () => { state.leakLevel = "apps"; state.leakEntity = r.entity; $("leak-level").value = "apps"; selectTab("leaks"); });
      return item;
    }));
  }

  function verdictBadge(verdict) {
    const text = { growing: "growing", possible: "possible", stable: "stable", insufficient: "too little data" }[verdict];
    return el("span", { class: "badge badge-" + verdict, text: (verdict === "growing" ? "▲ " : verdict === "possible" ? "△ " : "") + text });
  }

  // ---- apps -----------------------------------------------------------------
  function renderAppsTable() {
    const ov = state.overview;
    if (!ov) return;
    const growth = new Map(((state.analysis && state.analysis.results) || []).map((r) => [r.app, r]));
    const rows = ov.apps.map((a) => Object.assign({}, a, { growth: growth.has(a.key) ? growth.get(a.key).growthMbPerHour : null,
      verdict: growth.has(a.key) ? growth.get(a.key).verdict : null }));
    const { key, dir } = state.sort;
    rows.sort((x, y) => {
      const a = x[key], b = y[key];
      if (a === b) return x.name.localeCompare(y.name);
      if (a === null || a === undefined) return 1;
      if (b === null || b === undefined) return -1;
      return (typeof a === "string" ? a.localeCompare(b) : a - b) * dir;
    });
    for (const th of document.querySelectorAll("#apps-table th[data-sort]")) {
      th.classList.toggle("sorted", th.dataset.sort === key);
      th.setAttribute("aria-sort", th.dataset.sort === key ? (dir > 0 ? "ascending" : "descending") : "none");
    }
    const body = $("apps-table").tBodies[0];
    body.replaceChildren(...rows.map((a) => {
      const tr = el("tr", { class: "clickable" + (state.app === a.key ? " selected" : ""), tabindex: "0", "data-key": a.key },
        el("td", {}, el("span", { class: "strong", text: a.name }), a.self ? el("span", { class: "tag", text: "this app" }) : null),
        el("td", { class: "muted", text: a.kind }),
        el("td", { class: "num", text: fmtCount(a.processes) }), el("td", { class: "num", text: fmtCount(a.threads) }),
        el("td", { class: "num", text: fmtPct(a.cpu) }), el("td", { class: "num", text: fmtMb(a.anonMb) }),
        el("td", { class: "num", text: fmtMb(a.rssMb) }), el("td", { class: "num", text: fmtMb(a.pssMb) }),
        el("td", { class: "num", text: fmtMb(a.swapMb) }),
        el("td", { class: "num" }, a.growth === null ? "–" : el("span", { class: a.verdict === "growing" ? "warn-text" : "", text: (a.growth > 0 ? "+" : "") + fmtMb(a.growth) })));
      const open = () => { state.app = a.key; state.proc = null; state.threads.clear(); renderApps(); };
      tr.addEventListener("click", open);
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
      return tr;
    }));
  }

  async function renderApps() {
    renderAppsTable();
    const detail = $("app-detail");
    if (!state.app) { detail.hidden = true; return; }
    detail.hidden = false;
    let group = null;
    try { group = await api("GET", "/group?key=" + encodeURIComponent(state.app)); } catch (e) { group = null; }
    state.group = group;
    const app = (state.overview.apps || []).find((a) => a.key === state.app);
    $("app-detail-title").textContent = group ? group.name : (app ? app.name : state.app);
    $("app-detail-info").textContent = group
      ? [group.services.length ? "services: " + group.services.join(", ") : "", group.revision ? "revision " + group.revision : "",
        group.processes + " processes · " + group.threads + " threads"].filter(Boolean).join(" · ")
      : "no process of this app is running now";
    const k = "app." + state.app + ".";
    const kernel = group ? group.kind === "kernel" : false;
    const mem = kernel ? [] : [["anon", "anon"], ["file", "file"], ["shmem", "shmem"], ["pss", "PSS"], ["swap", "swap"]];
    const resp = await fetchSeries(mem.map(([m]) => k + m).concat([k + "cpu", k + "threads", k + "procs"]), 500);
    const evs = eventsFor(state.app);
    const minmax = $("opt-minmax").checked && resp.tier !== "raw";
    const memShown = mem.filter(([m]) => hasData(resp, k + m));
    const memSeries = memShown.map(([, l], i) => ({ label: l, color: PALETTE[i] }));
    const memCols = memShown.map(([m]) => [k + m, "avg"]);
    if (minmax && hasData(resp, k + "anon")) {
      memSeries.push({ label: "anon min", color: PALETTE[0], width: 1, fade: true }, { label: "anon max", color: PALETTE[0], width: 1, fade: true });
      memCols.push([k + "anon", "min"], [k + "anon", "max"]);
    }
    renderChart("ch-app-mem", { unit: "MB", events: evs, series: memSeries, empty: kernel ? "Kernel threads have no own memory." : undefined }, columns(resp, memCols));
    const cpuSeries = [{ label: "CPU", color: PALETTE[0] }], cpuCols = [[k + "cpu", "avg"]];
    if (minmax) { cpuSeries.push({ label: "CPU max", color: PALETTE[0], width: 1, fade: true }); cpuCols.push([k + "cpu", "max"]); }
    renderChart("ch-app-cpu", { unit: "%", events: evs, series: hasData(resp, k + "cpu") ? cpuSeries : [] }, columns(resp, cpuCols));
    renderChart("ch-app-count", { unit: "", events: evs, series: [{ label: "threads", color: PALETTE[0] }, { label: "processes", color: PALETTE[1] }] },
      columns(resp, [[k + "threads", "avg"], [k + "procs", "avg"]]));
    renderProcTable();
    await renderProcDetail();
  }

  function renderProcTable() {
    const body = $("proc-table").tBodies[0];
    const procs = (state.group && state.group.processList) || [];
    if (!procs.length) { body.replaceChildren(el("tr", {}, el("td", { colspan: "12", class: "muted", text: "No process running now." }))); return; }
    body.replaceChildren(...procs.map((p) => {
      const sched = p.policy === "OTHER" ? (p.nice ? "nice " + p.nice : "normal") : p.policy + " " + p.rtPriority;
      const tr = el("tr", { class: "clickable" + (state.proc === p.key ? " selected" : ""), tabindex: "0", title: p.cmdline || p.name },
        el("td", { class: "num", text: String(p.pid) }),
        el("td", {}, el("span", { class: "strong", text: p.name }), p.self ? el("span", { class: "tag", text: "this app" }) : null),
        el("td", { class: "muted", text: p.service || "" }), el("td", { class: "num", text: fmtPct(p.cpu) }),
        el("td", { class: "num", text: fmtMb(p.anonMb) }), el("td", { class: "num", text: fmtMb(p.rssMb) }),
        el("td", { class: "num", text: fmtMb(p.pssMb) }), el("td", { class: "num", text: fmtMb(p.swapMb) }),
        el("td", { class: "num", text: fmtMb(p.hwmMb) }), el("td", { class: "num", text: String(p.threads) }),
        el("td", { class: p.policy === "FIFO" || p.policy === "RR" ? "rt" : "muted", text: sched }),
        el("td", { class: "muted", text: fmtTime(p.started) }));
      const open = () => { state.proc = p.key; state.threads.clear(); renderProcTable(); renderProcDetail(); };
      tr.addEventListener("click", open);
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
      return tr;
    }));
  }

  async function renderProcDetail() {
    const box = $("proc-detail");
    const p = state.group && (state.group.processList || []).find((x) => x.key === state.proc);
    if (!p) { box.hidden = true; return; }
    box.hidden = false;
    $("proc-detail-title").textContent = p.name + " (PID " + p.pid + ")";
    $("proc-detail-cmd").textContent = p.cmdline;
    const k = "proc." + state.app + "." + p.key + ".";
    const threads = p.threadList || [];
    for (const t of [...state.threads]) if (!threads.some((x) => x.key === t)) state.threads.delete(t);
    if (!state.threads.size) {   // start with the busiest threads
      threads.filter((t) => t.cpu).sort((a, b) => b.cpu - a.cpu).slice(0, 3).forEach((t) => state.threads.add(t.key));
    }
    const thrIds = [...state.threads].map((t) => "thr." + state.app + "." + t + ".cpu");
    const mem = [["anon", "anon"], ["rss", "RSS"], ["pss", "PSS"], ["swap", "swap"]];
    const resp = await fetchSeries(mem.map(([m]) => k + m).concat([k + "cpu"], thrIds), 500);
    const evs = eventsFor(state.app);
    const memShown = mem.filter(([m]) => hasData(resp, k + m));
    renderChart("ch-proc-mem", { unit: "MB", events: evs, series: memShown.map(([, l], i) => ({ label: l, color: PALETTE[i] })),
      empty: p.kernel ? "Kernel threads have no own memory." : "No data yet (a process gets its own charts once it has run for a minute)." },
    columns(resp, memShown.map(([m]) => [k + m, "avg"])));
    renderChart("ch-proc-cpu", { unit: "%", events: evs, series: hasData(resp, k + "cpu") ? [{ label: "CPU", color: PALETTE[0] }] : [] },
      columns(resp, [[k + "cpu", "avg"]]));
    const tkeys = [...state.threads];
    const tColors = colorsFor(tkeys);
    const named = (t) => { const th = threads.find((x) => x.key === t); return th ? th.name + " (" + th.tid + ")" : t; };
    renderChart("ch-thread-cpu", { unit: "%", events: evs, empty: threads.length ? "Tick threads below." : "Single-threaded process (its CPU is on the left).",
      series: tkeys.map((t) => ({ label: named(t), color: tColors.get(t) })) }, columns(resp, thrIds.map((s) => [s, "avg"])));
    const body = $("thread-table").tBodies[0];
    const sorted = threads.slice().sort((a, b) => (b.cpu || 0) - (a.cpu || 0) || a.tid - b.tid);
    body.replaceChildren(...sorted.map((t) => {
      const box = el("input", { type: "checkbox", "aria-label": "Chart thread " + t.name });
      box.checked = state.threads.has(t.key);
      box.disabled = !box.checked && state.threads.size >= 8;
      box.addEventListener("change", () => { if (box.checked) state.threads.add(t.key); else state.threads.delete(t.key); renderProcDetail(); });
      const swatch = state.threads.has(t.key) ? el("span", { class: "swatch", style: "background:" + tColors.get(t.key) }) : null;
      return el("tr", {}, el("td", {}, box, swatch), el("td", { class: "num", text: String(t.tid) }), el("td", { text: t.name }),
        el("td", { class: "num", text: fmtPct(t.cpu) }),
        el("td", { class: t.policy === "FIFO" || t.policy === "RR" ? "rt" : "muted", text: t.policy }),
        el("td", { class: "num", text: t.policy === "OTHER" ? "–" : String(t.rtPriority) }), el("td", { class: "num", text: String(t.nice) }),
        el("td", { class: "num", text: String(t.core) }), el("td", { class: "muted", text: t.state }));
    }));
    if (!sorted.length) body.replaceChildren(el("tr", {}, el("td", { colspan: "9", class: "muted", text: "Single-threaded process." })));
  }

  // ---- leaks ----------------------------------------------------------------
  async function loadAnalysis(level, metric, app) {
    let q = "/analysis?" + rangeQuery() + "&level=" + level + "&metric=" + metric;
    if (app) q += "&app=" + encodeURIComponent(app);
    return api("GET", q);
  }

  async function renderLeaks() {
    const level = state.leakLevel, metric = state.leakMetric;
    $("leak-app-wrap").hidden = level !== "processes";
    const sel = $("leak-app");
    const apps = ((state.overview && state.overview.apps) || []).filter((a) => a.kind !== "kernel").sort((a, b) => a.name.localeCompare(b.name));
    if (sel.options.length - 1 !== apps.length) {
      sel.replaceChildren(el("option", { value: "", text: "all" }), ...apps.map((a) => el("option", { value: a.key, text: a.name })));
      sel.value = state.leakApp;
    }
    const body = $("leak-table").tBodies[0];
    let result;
    try {
      result = level === "apps" && metric === (state.analysis && state.analysis.metric) ? state.analysis
        : await loadAnalysis(level, metric, level === "processes" ? state.leakApp : "");
    } catch (e) {
      body.replaceChildren(el("tr", {}, el("td", { colspan: "11", class: "error", text: e.message })));
      return;
    }
    const rows = result.results;
    const self = ((state.overview && state.overview.apps) || []).find((a) => a.self);
    if (!rows.length) body.replaceChildren(el("tr", {}, el("td", { colspan: "11", class: "muted", text: "No data in this range." })));
    else body.replaceChildren(...rows.map((r) => {
      // the analyzer's own history arrays fill up to the RAM budget (history.maxMemoryMb) - growth by design
      const own = self && r.app === self.key
        ? el("span", { class: "tag", title: "Its RAM history grows until the budget (Settings: RAM budget) is full", text: "this app · history fills its budget" }) : null;
      const tr = el("tr", { class: "clickable" + (state.leakEntity === r.entity ? " selected" : ""), tabindex: "0" },
        el("td", {}, verdictBadge(r.verdict)), el("td", {}, el("span", { class: "strong", text: r.name }), own),
        el("td", { text: r.process ? r.process + " (" + r.pid + ")" + (r.ended ? " · ended" : "") : "" }),
        el("td", { class: "num", text: fmtMb(r.currentMb) }),
        el("td", { class: "num", text: r.startMb === null ? "–" : fmtMb(r.startMb) + " → " + fmtMb(r.endMb) }),
        el("td", { class: "num strong", text: r.growthMbPerHour === null ? "–" : (r.growthMbPerHour > 0 ? "+" : "") + fmtMb(r.growthMbPerHour) }),
        el("td", { class: "num", text: r.fit === null ? "–" : r.fit.toFixed(2) }),
        el("td", { class: "num", text: fmtDuration(r.spanMinutes * 60) }),
        el("td", { class: "num", text: r.hoursUntilFull ? fmtDuration(r.hoursUntilFull * 3600) : "–" }),
        el("td", { class: "num", text: r.threadsPerHour === null ? "–" : (r.threadsPerHour > 0 ? "+" : "") + r.threadsPerHour.toFixed(1) }),
        el("td", { class: "num", text: String(r.restarts) }));
      const open = () => { state.leakEntity = r.entity; renderLeaks(); };
      tr.addEventListener("click", open);
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
      return tr;
    }));
    const r = rows.find((x) => x.entity === state.leakEntity);
    $("leak-detail").hidden = !r;
    if (!r) return;
    $("leak-detail-title").textContent = (r.process ? r.process + " (" + r.pid + ") · " : "") + r.name + " · " + result.metric;
    $("leak-detail-info").textContent = r.growthMbPerHour === null ? "not enough data for a trend"
      : (r.growthMbPerHour > 0 ? "+" : "") + fmtMb(r.growthMbPerHour) + " MB/h · fit " + r.fit.toFixed(2) +
        (r.fittedFrom ? " · fitted after the restart at " + fmtTime(r.fittedFrom) : "");
    const sid = r.entity + "." + result.metric;
    const resp = await fetchSeries([sid], 600);
    const data = columns(resp, [[sid, "min"], [sid, "avg"]]);
    const line = r.line;
    data.push(resp.times.map((t) => (line && t >= line[0][0] && t <= line[1][0])
      ? line[0][1] + (line[1][1] - line[0][1]) * (t - line[0][0]) / ((line[1][0] - line[0][0]) || 1) : null));
    renderChart("ch-leak", { unit: "MB", height: 280, fromZero: false, events: eventsFor(r.app),
      series: [{ label: "lowest (fitted)", color: PALETTE[0] }, { label: "average", color: PALETTE[1], width: 1.5 },
        { label: "trend", color: PALETTE[2], dash: [6, 4] }] }, data);
  }

  // ---- events ---------------------------------------------------------------
  function renderEvents() {
    const body = $("events-table").tBodies[0];
    const evs = state.events.slice().reverse();
    if (!evs.length) { body.replaceChildren(el("tr", {}, el("td", { colspan: "5", class: "muted", text: "No events in this range." }))); return; }
    body.replaceChildren(...evs.map((e) => el("tr", {},
      el("td", { class: "nowrap", text: fmtTime(e.time) }),
      el("td", {}, el("span", { class: "evdot", style: "background:" + (EVENT_COLORS[e.type] || INK2) }), document.createTextNode(e.type)),
      el("td", { text: e.appName || e.app || "" }), el("td", { class: "muted", text: e.service || "" }), el("td", { text: e.text }))));
    $("btn-marker").disabled = !state.canWrite;
    $("marker-text").disabled = !state.canWrite;
  }

  // ---- settings form (generated from defaults + rules) ---------------------
  let meta = null;
  let loaded = null;

  function buildFields(container, defaults, path) {
    for (const [key, def] of Object.entries(defaults)) {
      const full = path ? path + "." + key : key;
      const rule = (meta.rules && meta.rules[full]) || {};
      const label = rule.label || humanize(key);
      if (rule.hidden) continue; // value is kept unchanged (readForm starts from the loaded settings)
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
        for (const opt of rule.enum) input.append(el("option", { value: String(opt), text: String(opt) }));
      } else if (typeof def === "boolean") {
        input = el("input", { id, type: "checkbox" });
      } else if (typeof def === "number") {
        const isInt = (meta.types || {})[full] === "integer";
        input = el("input", { id, type: "number", step: isInt ? "1" : "any", min: rule.min, max: rule.max });
      } else if (Array.isArray(def)) {
        input = el("input", { id, type: "text", placeholder: "JSON array" });
      } else {
        input = el("input", { id, type: "text", maxlength: rule.maxLength });
      }
      input.dataset.path = full;
      input.dataset.kind = Array.isArray(def) ? "array" : typeof def;
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
      if (input.type === "checkbox") input.checked = !!v;
      else if (input.dataset.kind === "array") input.value = JSON.stringify(v ?? []);
      else input.value = v ?? "";
      input.disabled = !state.canWrite;
      input.closest(".field").classList.remove("invalid");
    }
    for (const id of ["btn-save", "btn-revert", "btn-defaults", "btn-rec-save"]) $(id).disabled = !state.canWrite;
    $("rec-threads").checked = !!settings.recording.threads;
    $("rec-threads").disabled = $("rec-medium").disabled = !state.canWrite;
  }

  function readForm() {
    const result = JSON.parse(JSON.stringify(loaded)); // keeps unknown keys
    for (const input of document.querySelectorAll("#settings-fields [data-path]")) {
      let v;
      if (input.type === "checkbox") v = input.checked;
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
      const bad = (errors || []).some((m) => m.startsWith(input.dataset.path + ":"));
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
      state.leakMetric = loaded.leak.metric;
      $("leak-metric").value = state.leakMetric;
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
      fillForm(loaded);
      showErrors([]);
      $("save-state").textContent = "Saved " + new Date().toLocaleTimeString();
    } catch (e) {
      showErrors((e.data && e.data.errors) || [e.message]);
      $("save-state").textContent = "Not saved";
    }
    refreshStatus();
  }

  async function resetDefaults() {
    if (!confirm("Reset all settings to their defaults?")) return;
    try { loaded = await api("POST", "/settings/reset"); fillForm(loaded); showErrors([]); $("save-state").textContent = "Defaults restored"; }
    catch (e) { showErrors([e.message]); }
  }

  // ---- recording card ---------------------------------------------------------
  async function loadMedia() {
    let info;
    try { info = await api("GET", "/media"); } catch (e) { $("rec-state").textContent = e.message; return; }
    const sel = $("rec-medium");
    const current = loaded ? loaded.recording.medium : "";
    const options = [el("option", { value: "", text: "Off" })];
    for (const m of info.media) {
      options.push(el("option", { value: m.name, text: m.name + (m.freeMb !== null ? " · " + fmtMb(m.freeMb) + " MB free" : "") + (m.writable ? "" : " · read-only") }));
    }
    if (current && !info.media.some((m) => m.name === current)) options.push(el("option", { value: current, text: current + " · not mounted" }));
    sel.replaceChildren(...options);
    sel.value = current;
    const r = info.recording;
    const rows = [["State", r.state + (r.error ? " - " + r.error : "")],
      ["Folder", r.path || (r.medium ? "(medium not mounted)" : "–")],
      ["Last write", r.lastWrite ? fmtTime(r.lastWrite) : "–"], ["Written", fmtBytes(r.bytes || 0) + " since the app started"]];
    if (!info.media.length) rows.unshift(["Media", info.error || "none mounted (Settings → Storage)"]);
    $("rec-status").replaceChildren(...rows.flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: v })]));
  }

  async function saveRecording() {
    if (!loaded) return;
    const next = JSON.parse(JSON.stringify(loaded));
    next.recording.medium = $("rec-medium").value;
    next.recording.threads = $("rec-threads").checked;
    $("rec-state").textContent = "Saving…";
    try {
      loaded = await api("PUT", "/settings", next);
      fillForm(loaded);
      $("rec-state").textContent = next.recording.medium ? "Recording to " + next.recording.medium + " from the next minute" : "Recording off";
    } catch (e) {
      $("rec-state").textContent = "Not saved: " + ((e.data && e.data.errors) || [e.message]).join("; ");
    }
    await refreshStatus();
    await loadMedia();
  }

  // ---- tabs and refresh -------------------------------------------------------
  function selectTab(name) {
    state.tab = name;
    for (const t of document.querySelectorAll(".tab")) t.setAttribute("aria-selected", t.dataset.tab === name ? "true" : "false");
    for (const p of document.querySelectorAll(".tabpanel")) p.hidden = p.id !== "tab-" + name;
    try { sessionStorage.setItem(TOKEN_KEY + ".tab", name); } catch (e) { /* storage blocked */ }
    refresh(true);
  }

  async function renderTab() {
    if (state.tab === "overview") await renderOverview();
    else if (state.tab === "apps") await renderApps();
    else if (state.tab === "leaks") await renderLeaks();
    else if (state.tab === "events") renderEvents();
    else if (state.tab === "settings") { renderStatusList(); await loadMedia(); }
    resizeCharts();
  }

  function refreshEveryMs() {
    if (!state.preset) return 0;                       // a fixed range does not move
    const step = state.status ? state.status.collector.intervalSeconds : 5;
    return Math.max(step, state.preset / 360) * 1000;  // 15 min -> 5 s, 24 h -> 4 min
  }

  async function refresh(userAction) {
    if (state.busy) { state.again = true; return; }
    state.busy = true;
    if (userAction) document.body.dataset.uiReady = "false";
    clearTimeout(state.timer);
    try {
      updateRange();
      showRangeInputs();
      const [ov, evs] = await Promise.all([api("GET", "/overview"), api("GET", "/events?" + rangeQuery())]);
      state.overview = ov;
      state.events = evs.events;
      if (state.tab === "overview" || state.tab === "apps" || state.tab === "leaks") {
        try { state.analysis = await loadAnalysis("apps", state.leakMetric, ""); } catch (e) { state.analysis = null; }
      }
      await renderTab();
      const src = state.source;
      $("range-info").textContent = src && src.times
        ? (src.source === "medium" ? "from the medium · 1-minute values" : "RAM · " + (src.tier === "raw" ? "every sample (" + src.step + " s)" : src.tier === "minute" ? "1-minute values" : "10-minute values"))
          + (src.oldest && src.oldest > state.from + 60 ? " · data since " + fmtTime(src.oldest) : "")
          + (clockNote() ? " · " + clockNote() : "")
        : clockNote();
    } catch (e) {
      if (e.status !== 401) showBanner("Cannot load data: " + e.message);
    } finally {
      state.busy = false;
      document.body.dataset.uiReady = "true";  // for tools/ui_check.py (screenshots wait for it)
      const every = refreshEveryMs();
      if (every) state.timer = setTimeout(() => refresh(false), every);
      if (state.again) { state.again = false; refresh(false); }
    }
  }

  async function exportData() {
    const level = $("export-level").value, fmt = $("export-format").value;
    updateRange();
    const btn = $("btn-export");
    btn.disabled = true;
    try {
      const res = await request("GET", "/export?" + rangeQuery() + "&level=" + level + "&format=" + fmt);
      if (!res.ok) { let msg = res.statusText; try { const d = await res.json(); msg = d.detail || d.title || msg; } catch (e) { /* keep */ } throw new Error(msg); }
      const blob = await res.blob();
      const name = ((res.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/) || [])[1] || "export." + fmt;
      const a = el("a", { href: URL.createObjectURL(blob), download: name });
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    } catch (e) {
      showBanner("Export failed: " + e.message);
    } finally {
      btn.disabled = false;
    }
  }

  // ---- start ---------------------------------------------------------------
  for (const b of document.querySelectorAll("[data-preset]")) {
    b.addEventListener("click", () => { state.preset = Number(b.dataset.preset); refresh(true); });
  }
  $("btn-apply").addEventListener("click", () => {
    const from = new Date($("range-from").value).getTime() / 1000, to = new Date($("range-to").value).getTime() / 1000;
    if (!(to > from)) { showBanner("The start of the range must be before its end."); return; }
    showBanner("");
    zoomTo(from, to);
  });
  for (const t of document.querySelectorAll(".tab")) t.addEventListener("click", () => selectTab(t.dataset.tab));
  for (const th of document.querySelectorAll("#apps-table th[data-sort]")) {
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      state.sort = { key, dir: state.sort.key === key ? -state.sort.dir : (key === "name" || key === "kind" ? 1 : -1) };
      renderAppsTable();
    });
  }
  $("opt-minmax").addEventListener("change", () => renderApps());
  $("leak-level").addEventListener("change", (e) => { state.leakLevel = e.target.value; state.leakEntity = null; refresh(true); });
  $("leak-metric").addEventListener("change", (e) => { state.leakMetric = e.target.value; state.leakEntity = null; refresh(true); });
  $("leak-app").addEventListener("change", (e) => { state.leakApp = e.target.value; state.leakEntity = null; refresh(true); });
  $("marker-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const text = $("marker-text").value.trim();
    if (!text) return;
    try {
      await api("POST", "/events", { text });
      $("marker-text").value = "";
      $("marker-state").textContent = "Marker added " + new Date().toLocaleTimeString();
      refresh(true);
    } catch (e) { $("marker-state").textContent = "Not added: " + e.message; }
  });
  $("btn-export").addEventListener("click", exportData);
  $("settings-form").addEventListener("submit", saveSettings);
  $("btn-revert").addEventListener("click", () => { fillForm(loaded); showErrors([]); $("save-state").textContent = ""; });
  $("btn-defaults").addEventListener("click", resetDefaults);
  $("btn-rec-save").addEventListener("click", saveRecording);
  $("btn-rec-refresh").addEventListener("click", loadMedia);

  (async function init() {
    await refreshStatus();
    await loadSettings();
    // right after the app started, a 1 h range would squeeze the data into its right edge
    const tiers = state.status ? state.status.history.tiers : [];
    const oldest = Math.min(...tiers.filter((t) => t.oldest).map((t) => t.oldest));
    if (isFinite(oldest) && deviceNow() - oldest < 900) state.preset = 900;
    let tab = "overview";
    try { tab = sessionStorage.getItem(TOKEN_KEY + ".tab") || "overview"; } catch (e) { /* storage blocked */ }
    if (location.hash === "#settings") tab = "settings";
    if (!document.querySelector('.tab[data-tab="' + tab + '"]')) tab = "overview";
    selectTab(tab);
    setInterval(refreshStatus, 10000);
  })();
})();
