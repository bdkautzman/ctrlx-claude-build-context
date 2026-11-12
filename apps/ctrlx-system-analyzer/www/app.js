// System Analyzer web UI - this app's page. Plain JS, no build step, no CDN (devices are often offline).
// Charts: uPlot (vendor/, MIT). Colors: a fixed categorical order (validated for color-vision deficiencies); an entity
// keeps its color while it is charted, it never follows its rank. Axes, grid and event lines take the page's theme
// colors (kit.css) and the charts are rebuilt when the theme or the Styling colors change.
// The shared parts (token + API calls, roles, settings drawer, tabs, help menu + tutorial, About, light/dark) are in
// kit.js - see the list at its top. Roles: view < operate < configure (GET /status says which one the user has).
"use strict";

(function () {
  const { $, el, api, can, showBanner } = Kit;

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
  const UNIT_FMT = { "MB": fmtMb, "%": fmtPct, "": fmtCount };

  // ---- colors: series from a fixed categorical palette; axes, grid, events from the theme (kit.css variables) ----
  const PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
  const probe = el("span", { hidden: true });
  document.body.append(probe);
  function cssColor(name) { probe.style.color = "var(" + name + ")"; return getComputedStyle(probe).color; }   // -> rgb()
  let ink = {};
  function readInk() {
    const before = JSON.stringify(ink);
    ink = { muted: cssColor("--muted"), line: cssColor("--line"), ok: cssColor("--ok"), warn: cssColor("--warn"),
      err: cssColor("--err"), accent: cssColor("--accent") };
    return JSON.stringify(ink) !== before;
  }
  readInk();
  function eventColor(type) {
    return ({ restarted: ink.warn, stopped: ink.err, started: ink.ok, updated: ink.muted, marker: ink.accent,
      analyzer: ink.muted })[type] || ink.muted;
  }
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
    app: null, proc: null, threads: new Set(), sort: { key: "rssMb", dir: -1 }, leakSort: { key: null, dir: -1 },
    leakLevel: "apps", leakMetric: "anon", leakApp: "", leakEntity: null,
    status: null, overview: null, group: null, analysis: null, events: [],
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
      ctx.strokeStyle = ctx.fillStyle = eventColor(e.type);
      ctx.beginPath(); ctx.moveTo(x + 0.5, top); ctx.lineTo(x + 0.5, top + height); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(x - 4 * dpr, top); ctx.lineTo(x + 4 * dpr, top); ctx.lineTo(x, top + 6 * dpr); ctx.fill();
    }
    ctx.restore();
  }

  // spec: {unit, series: [{label, color, width, dash, fade}], events, height, fromZero}
  // uPlot options of a chart; the large view in the dialog uses the same with another size
  function chartOpts(spec, width, height) {
    const fmt = UNIT_FMT[spec.unit] || fmtCount;
    const strokes = [null].concat(spec.series.map((s) => (s.fade ? alpha(s.color, 0.45) : s.color)));
    return {
      width, height,
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
        { stroke: ink.muted, grid: { stroke: ink.line, width: 1 }, ticks: { stroke: ink.line, width: 1 } },
        { stroke: ink.muted, grid: { stroke: ink.line, width: 1 }, ticks: { show: false }, size: 52,
          values: (u, vals) => vals.map((v) => fmt(v)) },
      ],
      series: [{ value: (u, v) => (v === null || v === undefined ? "–" : fmtTime(v)) }].concat(spec.series.map((s, i) => ({
        label: s.label, stroke: strokes[i + 1], width: s.width || 2, dash: s.dash,
        points: { show: false }, spanGaps: false,
        value: (u, v) => (v === null || v === undefined ? "–" : fmt(v) + (spec.unit ? " " + spec.unit : "")),
      }))),
      // legend: a solid circle per line (no border; style.css rounds it); fixed value widths in style.css keep the
      // card's height while the cursor shows values (user's notes 2026-11-11)
      legend: { live: true, markers: { width: 0, fill: (u, i) => strokes[i] } },
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
  }

  // spec: {unit, series: [{label, color, width, dash, fade}], events, height, fromZero, empty}
  function renderChart(id, spec, data) {
    const node = $(id);
    if (!node || node.offsetParent === null) return;
    const sig = spec.series.map((s) => s.label + s.color + (s.dash ? "d" : "")).join("|") + spec.unit + (spec.fromZero !== false)
      + ink.muted + ink.line;   // a new theme: a new chart
    let chart = charts[id];
    if (!spec.series.length) {
      if (chart) { chart.u.destroy(); delete charts[id]; }
      node.replaceChildren(el("p", { class: "muted small empty", text: spec.empty || "No data in this range." }));
      return;
    }
    if (big && big.id === id) showBig(spec, data, sig);   // the large view follows the data
    if (chart && chart.sig === sig && chart.node === node) {
      chart.u._events = spec.events || [];
      chart.u.setData(data);     // rescales: x always spans the selected range (scales.x.range)
      Object.assign(chart, { spec, data });
      return;
    }
    if (chart) chart.u.destroy();
    node.replaceChildren();
    const height = spec.height || 200;
    const u = new uPlot(chartOpts(spec, Math.max(220, node.clientWidth), height), data, node);
    u._events = spec.events || [];
    charts[id] = { u, sig, node, height, spec, data };
    if (!node.dataset.bigView) {                              // a click (no drag-zoom) opens the large view
      node.dataset.bigView = "1";
      node.title = "Click for a large view; drag to zoom into the range";
      node.addEventListener("pointerdown", (ev) => { node._down = [ev.clientX, ev.clientY]; });
      node.addEventListener("click", (ev) => {
        const d = node._down || [ev.clientX, ev.clientY];
        if (Math.hypot(ev.clientX - d[0], ev.clientY - d[1]) > 4 || ev.target.closest(".u-legend")) return;
        openBig(id);
      });
    }
  }

  // ---- large view of a chart: a dialog over the page (user's note 2026-11-11); Esc or a click beside it closes it
  let big = null;   // {id, u, sig}
  const bigDialog = el("dialog", { id: "chart-dialog", class: "dialog chart-dialog", "aria-labelledby": "chart-dialog-title" },
    el("div", { class: "dialog-body" },
      el("div", { class: "card-head dialog-head" },
        el("div", {}, el("h2", { id: "chart-dialog-title" }), el("p", { class: "sub", id: "chart-dialog-sub" })),
        el("button", { type: "button", class: "tool tool-bare", id: "chart-dialog-close", title: "Close", "aria-label": "Close" }, Kit.icon("close"))),
      el("div", { id: "chart-dialog-plot" })));
  document.body.append(bigDialog);
  $("chart-dialog-close").onclick = () => bigDialog.close();
  // a click beside the dialog (on the backdrop) closes it; a zoom drag that ends there does not
  bigDialog.addEventListener("pointerdown", (ev) => { bigDialog._downOutside = ev.target === bigDialog; });
  bigDialog.addEventListener("click", (ev) => { if (ev.target === bigDialog && bigDialog._downOutside) bigDialog.close(); });
  bigDialog.addEventListener("close", () => { if (big && big.u) big.u.destroy(); big = null; $("chart-dialog-plot").replaceChildren(); });
  window.addEventListener("resize", () => {
    if (big && big.u) big.u.setSize({ width: $("chart-dialog-plot").clientWidth, height: bigHeight() });
  });
  const bigHeight = () => Math.max(260, Math.min(640, Math.round(innerHeight * 0.62)));
  function openBig(id) {
    const c = charts[id];
    if (!c) return;
    // title: the card's title (and the chart's own title in a card with several charts, e.g. "nginx · Memory")
    const card = c.node.closest(".card"), box = c.node.parentElement;
    const h2 = card && card.querySelector("h2"), h3 = box !== card && box.querySelector("h3");
    const sub = box.querySelector(".sub") || (card && card.querySelector(".sub, .card-head .muted"));
    $("chart-dialog-title").textContent = [h2, h3].filter(Boolean).map((n) => n.textContent.trim()).join(" · ");
    $("chart-dialog-sub").textContent = sub ? sub.textContent : "";
    big = { id, u: null, sig: "" };
    bigDialog.showModal();
    showBig(c.spec, c.data, c.sig);
  }
  function showBig(spec, data, sig) {
    if (big.u && big.sig === sig) { big.u._events = spec.events || []; big.u.setData(data); return; }
    if (big.u) big.u.destroy();
    const plot = $("chart-dialog-plot");
    plot.replaceChildren();
    big.u = new uPlot(chartOpts(spec, Math.max(320, plot.clientWidth), bigHeight()), data, plot);
    big.u._events = spec.events || [];
    big.sig = sig;
  }

  function resizeCharts() {
    for (const c of Object.values(charts)) {
      if (c.node.offsetParent !== null) c.u.setSize({ width: Math.max(220, c.node.clientWidth), height: c.height });
    }
  }
  window.addEventListener("resize", () => { clearTimeout(resizeCharts.t); resizeCharts.t = setTimeout(resizeCharts, 150); });
  // light/dark or new Styling colors: charts are drawn on a canvas - rebuild them in the new colors
  function onInk() { if (readInk()) { for (const id of Object.keys(charts)) { charts[id].u.destroy(); delete charts[id]; } refresh(false); } }
  document.addEventListener("kit:theme", onInk);
  document.addEventListener("kit:style", onInk);

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
    const asked = Date.now() / 1000;
    const s = await Kit.refreshStatus();   // title, version, Data Layer pill, role pill, [data-role] elements, colors
    if (!s) return;
    if (typeof s.serverTime === "number") state.clockOffset = s.serverTime - (asked + Date.now() / 1000) / 2;
    if (!state.status && s.leakMetric) { state.leakMetric = s.leakMetric; $("leak-metric").value = s.leakMetric; }
    state.status = s;
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
    if (state.tab === "overview") renderStatusList();
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
    const r = s.recording;
    const rec = [["Recording", r.state + (r.error ? " - " + r.error : "")],
      ["Folder", r.path || (r.medium ? "(medium not mounted)" : "–")],
      ["Last write", r.lastWrite ? fmtTime(r.lastWrite) : "–"], ["Written", fmtBytes(r.bytes || 0) + " since the app started"]];
    $("rec-status").replaceChildren(...rec.flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: v })]));
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
      item.addEventListener("click", () => { state.leakLevel = "apps"; state.leakEntity = r.entity; $("leak-level").value = "apps"; location.hash = "#leaks"; });
      return item;
    }));
  }

  function verdictBadge(verdict) {
    const text = { growing: "growing", possible: "possible", stable: "stable", insufficient: "too little data" }[verdict];
    return el("span", { class: "badge badge-" + verdict, text: (verdict === "growing" ? "▲ " : verdict === "possible" ? "△ " : "") + text });
  }

  // ---- apps -----------------------------------------------------------------
  // ---- sortable tables (Apps, Leaks): a click on a column title cycles high → low, low → high, unsorted; text columns
  // (data-text) start with A → Z. The sorted column shows an arrow (↓ high to low, ↑ low to high) and a bold title;
  // rows without a value stay at the bottom (user's note 2026-11-11).
  const VERDICT_RANK = { growing: 3, possible: 2, stable: 1, insufficient: 0 };
  function sortRows(rows, sort, tie) {
    if (!sort.key) return rows;
    const val = (r) => (sort.key === "verdict" ? VERDICT_RANK[r.verdict] : r[sort.key]);
    return rows.slice().sort((x, y) => {
      const a = val(x), b = val(y);
      if (a === b) return tie ? tie(x, y) : 0;
      if (a === null || a === undefined) return 1;
      if (b === null || b === undefined) return -1;
      return (typeof a === "string" ? a.localeCompare(b) : a - b) * sort.dir;
    });
  }
  function setupSortable(tableId, sort, render) {
    for (const th of document.querySelectorAll("#" + tableId + " th[data-sort]")) {
      th.tabIndex = 0;
      const step = () => {
        const first = th.dataset.text ? 1 : -1;
        if (sort.key !== th.dataset.sort) { sort.key = th.dataset.sort; sort.dir = first; }
        else if (sort.dir === first) sort.dir = -first;
        else sort.key = null;
        render();
      };
      th.addEventListener("click", step);
      th.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); step(); } });
    }
  }
  function showSort(tableId, sort) {
    for (const th of document.querySelectorAll("#" + tableId + " th[data-sort]")) {
      const on = th.dataset.sort === sort.key;
      th.classList.toggle("sorted", on);
      th.dataset.dir = on ? (sort.dir > 0 ? "up" : "down") : "";
      th.setAttribute("aria-sort", on ? (sort.dir > 0 ? "ascending" : "descending") : "none");
      th.title = on ? (sort.dir > 0 ? "Sorted low to high (A → Z)" : "Sorted high to low (Z → A)") + " - click to change" : "Click to sort";
    }
  }

  function renderAppsTable() {
    const ov = state.overview;
    if (!ov) return;
    const growth = new Map(((state.analysis && state.analysis.results) || []).map((r) => [r.app, r]));
    const rows = ov.apps.map((a) => Object.assign({}, a, { growth: growth.has(a.key) ? growth.get(a.key).growthMbPerHour : null,
      verdict: growth.has(a.key) ? growth.get(a.key).verdict : null }));
    showSort("apps-table", state.sort);
    const body = $("apps-table").tBodies[0];
    body.replaceChildren(...sortRows(rows, state.sort, (x, y) => x.name.localeCompare(y.name)).map((a) => {
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
    const rows = sortRows(result.results.map((r) => Object.assign({}, r, {
      change: r.startMb === null || r.endMb === null ? null : r.endMb - r.startMb })), state.leakSort);
    showSort("leak-table", state.leakSort);
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
      el("td", {}, el("span", { class: "evdot", style: "background:" + eventColor(e.type) }), document.createTextNode(e.type)),
      el("td", { text: e.appName || e.app || "" }), el("td", { class: "muted", text: e.service || "" }), el("td", { text: e.text }))));
  }

  // ---- settings drawer (kit.js); the medium: a list of the device's media + "Look for media" ----------------------
  async function fillMedia(select, current) {
    select.replaceChildren(el("option", { value: "", text: "Off" }), el("option", { value: current, text: current || "…" }));
    select.value = current;
    let info = { media: [] };
    try { info = await api("GET", "/media"); } catch (e) { /* keep the current value */ }
    const options = [el("option", { value: "", text: "Off" })];
    for (const m of info.media) {
      options.push(el("option", { value: m.name, text: m.name + (m.freeMb !== null ? " · " + fmtMb(m.freeMb) + " MB free" : "") + (m.writable ? "" : " · read-only") }));
    }
    if (current && !info.media.some((m) => m.name === current)) options.push(el("option", { value: current, text: current + " · not mounted" }));
    if (!info.media.length) options.push(el("option", { value: "", text: info.error || "(none mounted - Settings → Storage)", disabled: true }));
    select.replaceChildren(...options);
    select.value = current;
  }
  function mediumField(id) {
    const select = el("select", { id });
    let current = "";
    select.setValue = (v) => { current = v || ""; fillMedia(select, current); };
    select.getValue = () => select.value;
    const look = el("button", { type: "button", class: "btn btn-refresh", id: "btn-media-refresh", title: "Look for media again",
      "aria-label": "Look for media again" }, Kit.icon("refresh"));
    look.onclick = () => fillMedia(select, select.value || current);
    return { node: el("div", { class: "with-button" }, select, look), input: select, wide: true };
  }
  const settings = Kit.setupSettings({
    field: (path, rule, def, id) => (path === "recording.medium" ? mediumField(id) : null),
    onSaved: (saved) => {
      if (saved && saved.leak) { state.leakMetric = saved.leak.metric; $("leak-metric").value = state.leakMetric; }
      refreshStatus();
      refresh(true);
    },
  });

  // ---- pages and refresh ---------------------------------------------------------
  async function renderTab() {
    if (state.tab === "overview") { await renderOverview(); renderStatusList(); }
    else if (state.tab === "apps") await renderApps();
    else if (state.tab === "leaks") await renderLeaks();
    else if (state.tab === "events") renderEvents();
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
      await Kit.download("/export?" + rangeQuery() + "&level=" + level + "&format=" + fmt, "export." + fmt);
    } catch (e) {
      showBanner("Export failed: " + e.message);
    } finally {
      btn.disabled = false;
    }
  }

  // ---- help menu, tutorial ---------------------------------------------------------
  Kit.setupHelp([
    { title: "Welcome",
      text: "This tour shows the System Analyzer: CPU and memory of every app on this device. Step through it with the " +
        "arrows (or the ← → keys); the control of each step blinks. Everything stays usable while the tour is open." },
    { title: "Time range", target: () => document.querySelector(".rangebar"),
      text: "Every chart and table shows this range. Pick a preset, enter from/to, or drag across a chart to zoom in." },
    { title: "Pages", target: () => $("tabs"),
      text: "Overview, the apps with their processes and threads, memory growth (leaks) and events. Each page has its own " +
        "address (…/#apps), so it can be bookmarked." },
    { title: "Overview", page: "overview", target: () => $("tiles"),
      text: "The device now: CPU, memory, pressure, the number of apps and the analyzer's own load." },
    { title: "Analyzer", page: "overview", target: () => $("analyzer-card"),
      text: "The analyzer itself: samples, the RAM history, the device clock and the recording on an external medium." },
    { title: "Apps", page: "apps", target: () => $("apps-table"),
      text: "Every app with CPU and memory over the range. Click an app for its charts, processes and threads." },
    { title: "Leaks", page: "leaks", target: () => $("leak-table"),
      text: "Memory that grows steadily over the range - with the growth per hour and how well it fits a line." },
    { title: "Events", page: "events", target: () => $("events-table"),
      text: () => "Restarts, updates and markers in the range - also shown as lines in the charts." +
        (can("operate") ? " Add a marker before a test to find its start later." : "") },
    { title: "Export", target: () => $("btn-export"), text: "Downloads the range as CSV or JSON: apps, processes, threads or the system." },
    { title: "Settings", when: () => can("configure"), target: () => $("btn-settings"),
      text: "Sampling, history, leak detection and the external medium. Stored in the ctrlX app data, so they are saved " +
        "and loaded with configurations.", act: () => settings.open() },
    { title: "Your permission", when: () => !can("configure"), target: () => $("role"),
      text: () => can("operate") ? "Operator: you may add markers, but not change the settings."
        : "View only: you see everything, but can't add markers or change settings." },
    { title: "Light or dark", target: () => $("btn-theme"),
      text: "Switches the page between light and dark mode. Each browser keeps its own choice." },
    { title: "Help", target: () => $("btn-help"),
      text: "Start this tour again, open the app's page, or see About: version, author, license." },
  ]);

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
  setupSortable("apps-table", state.sort, renderAppsTable);
  setupSortable("leak-table", state.leakSort, () => renderLeaks());
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

  (async function init() {
    await refreshStatus();
    // right after the app started, a 1 h range would squeeze the data into its right edge
    const tiers = state.status ? state.status.history.tiers : [];
    const oldest = Math.min(...tiers.filter((t) => t.oldest).map((t) => t.oldest));
    if (isFinite(oldest) && deviceNow() - oldest < 900) state.preset = 900;
    Kit.setupTabs({ onShow: (page) => { state.tab = page; refresh(true); } });   // refreshes the shown page
    if (location.hash === "#settings") settings.open();
    setInterval(refreshStatus, 10000);
  })();
})();
