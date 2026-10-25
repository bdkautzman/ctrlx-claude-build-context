// AMR Fleet View web UI - plain JS, no build step, no CDN (devices are often offline).
//
// Auth: the ctrlX menu opens "/<snap>/?token=${bearertoken}". We move the token from the URL
// into sessionStorage (so it is not left in history/bookmarks) and send it as
// "Authorization: Bearer" on every API call. The reverse proxy validates it because
// "/<snap>/api" is listed as restricted in the package manifest.
//
// Coordinates: "world" = map units, x right, y up - the units of the map file and of the AMR
// JSON. The view is {cx, cy, zoom (CSS px per map unit), rot (rad)}: a world point is moved by
// -center, flipped (y up), scaled by zoom, rotated by rot and put in the middle of the canvas.
// The map arrives as PNG tiles per level (level 0 = one pixel per map cell, each level halves;
// src/app/occupancy_map.py), so only the visible part of a large map is loaded.
// AMR positions are polled (GET /fleet?since=<revision>) and interpolated between updates.
// Paths: the app keeps each AMR's recent path; a switched-on path is fetched once and then extended
// from the fleet updates. Help menu: the app's GitHub page and a tutorial (a card that steps through
// the controls and blinks a ring over each).
"use strict";

(function () {
  const PREFIX = "/" + location.pathname.split("/").filter(Boolean)[0]; // "/<snap-name>"
  const API = PREFIX + "/api/v1";
  const TOKEN_KEY = PREFIX.slice(1) + ".token";
  const PREF_KEY = PREFIX.slice(1) + ".prefs";

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

  function authHeaders(extra) {
    const headers = Object.assign({}, extra);
    if (token) headers["Authorization"] = "Bearer " + token;
    return headers;
  }

  async function api(method, path, body) {
    const headers = authHeaders({ "Accept": "application/json" });
    if (body !== undefined) headers["Content-Type"] = "application/json";
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

  async function apiImage(path) {
    const res = await fetch(API + path, { headers: authHeaders(), credentials: "same-origin" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    return createImageBitmap(await res.blob());
  }

  // ---- helpers -----------------------------------------------------------------
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
  function icon(name) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "ic");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "#i-" + name);
    svg.append(use);
    return svg;
  }
  function showBanner(msg) { const b = $("banner"); b.textContent = msg; b.hidden = !msg; }
  function setText(node, text) { if (node.textContent !== text) node.textContent = text; }  // no needless restyle
  function humanize(key) {   // "toGoal" -> "To goal"
    return key.replace(/([a-z0-9])([A-Z])/g, (m, a, b) => a + " " + b.toLowerCase()).replace(/[_-]+/g, " ")
      .replace(/^./, (c) => c.toUpperCase());
  }
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  function wrapDeg(d) { return ((d % 360) + 540) % 360 - 180; }    // -> [-180, 180)
  function niceStep(v) {                                           // 1, 2, 5 x 10^n <= v
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    const m = v / p;
    return (m >= 5 ? 5 : m >= 2 ? 2 : 1) * p;
  }
  function fmtMeters(m) {
    if (m >= 1000) return (m / 1000) + " km";
    if (m >= 1) return m + " m";
    if (m >= 0.01) return Math.round(m * 100) + " cm";
    return Math.round(m * 1000) + " mm";
  }
  function fmtNum(v) { return Math.abs(v) >= 100 ? Math.round(v).toString() : v.toFixed(2); }

  const prefs = (function () {
    let p = {};
    try { p = JSON.parse(localStorage.getItem(PREF_KEY) || "{}") || {}; } catch (e) { p = {}; }
    return {
      get: (k, d) => (k in p ? p[k] : d),
      set: (k, v) => { p[k] = v; try { localStorage.setItem(PREF_KEY, JSON.stringify(p)); } catch (e) { /* ignore */ } },
    };
  })();

  // ---- state -------------------------------------------------------------------
  const canvas = $("map");
  const ctx = canvas.getContext("2d");
  // The map (background, floor, grid, tiles) is drawn into this layer only when the view, the colors or the
  // loaded tiles change; every frame copies it and draws the AMRs on top. Redrawing the whole map each
  // frame while AMRs move was most of the browser's work (performance test 2026-10-23).
  const layer = document.createElement("canvas");
  const lctx = layer.getContext("2d");
  let layerKey = "";       // what the layer shows
  let lastFrameKey = "";   // what the previous frame showed - a different one = the view is moving
  let tileVersion = 0;     // +1 whenever a tile finished loading (or failed): the layer must be redrawn
  const mini = $("minimap");
  const mctx = mini.getContext("2d");
  const colors = {};
  function readColors() {
    for (const k of ["map-bg", "map-floor", "map-wall", "map-grid", "amr-stale", "accent", "text", "card"]) colors[k] = cssVar("--" + k);
  }
  readColors();
  let amrPalette = ["#1f6feb", "#d1242f", "#1a7f37", "#bf8700", "#8250df", "#0a8a9a", "#cf2a93", "#57801c", "#d4570a", "#3e4bd1"];
  let viewConfigJson = "";   // last applied GET /view-config (style + view settings)

  const cfg = { unitsPerMeter: 1000, amrLength: 1000, amrWidth: 700, refreshMs: 250, pathMinutes: 0, pathStepM: 0.25 };
  let mapInfo = null;      // GET /map, only when a map is loaded
  let mapError = "";
  let mapNote = "";        // e.g. "Loading map ..." (not an error)
  const view = { cx: 0, cy: 0, zoom: 0.05, rot: 0 };
  let viewReady = false;   // first fit done
  const size = { w: 1, h: 1, dpr: 1 };
  const amrs = new Map();  // id -> {data, from, to, t0, dur, color}
  let fleetRevision = -1;
  let fleetStatus = null;
  let lastChangeAt = 0;
  let updateInterval = 250;
  let follow = null;       // id of the AMR the view is locked to
  let focusPulse = null;   // {id, t0}: the crosshair was pressed - the AMR's outline pulses (drawFocusPulse)
  let selected = null;
  let headingUp = prefs.get("headingUp", true);
  let showLabels = prefs.get("labels", true);
  let showMinimap = prefs.get("minimap", true);
  let dirty = true;
  let firstFleetAnswer = false;  // for data-ui-ready
  let canWrite = false;
  const MAX_DETAIL_INFO = 16;
  const paths = new Map();                          // id -> {pts: [[x, y, performance.now() ms], ...], loading}
  const pathOn = new Set(prefs.get("paths", []));   // AMRs whose path this viewer switched on

  // ---- view transforms -----------------------------------------------------------
  function worldToScreen(x, y) {
    const c = Math.cos(view.rot), s = Math.sin(view.rot);
    const dx = (x - view.cx) * view.zoom, dy = -(y - view.cy) * view.zoom;
    return [size.w / 2 + dx * c - dy * s, size.h / 2 + dx * s + dy * c];
  }
  function screenToWorld(sx, sy) {
    const c = Math.cos(view.rot), s = Math.sin(view.rot);
    const ddx = sx - size.w / 2, ddy = sy - size.h / 2;
    const dx = ddx * c + ddy * s, dy = -ddx * s + ddy * c;
    return [view.cx + dx / view.zoom, view.cy - dy / view.zoom];
  }
  function visibleWorldBox() {
    const pts = [[0, 0], [size.w, 0], [0, size.h], [size.w, size.h]].map(([x, y]) => screenToWorld(x, y));
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    return { x0: Math.min(...xs), x1: Math.max(...xs), y0: Math.min(...ys), y1: Math.max(...ys) };
  }
  function zoomLimits() {
    const fit = fitZoom();
    const maxZ = mapInfo ? 32 / mapInfo.resolution : 4000 / cfg.unitsPerMeter;
    return [Math.min(fit / 8, maxZ), maxZ];
  }
  function setZoom(z) { const [lo, hi] = zoomLimits(); view.zoom = clamp(z, lo, hi); dirty = true; }
  function zoomAt(sx, sy, factor) {
    if (follow) { setZoom(view.zoom * factor); return; }  // locked: zoom around the AMR
    const [wx, wy] = screenToWorld(sx, sy);
    setZoom(view.zoom * factor);
    const [wx2, wy2] = screenToWorld(sx, sy);
    view.cx += wx - wx2;
    view.cy += wy - wy2;
  }
  function panBy(dxScreen, dyScreen) {
    const [ax, ay] = screenToWorld(0, 0), [bx, by] = screenToWorld(dxScreen, dyScreen);
    view.cx -= bx - ax;
    view.cy -= by - ay;
    dirty = true;
  }
  function worldExtent() {
    if (mapInfo) return { x0: mapInfo.left, x1: mapInfo.right, y0: mapInfo.bottom, y1: mapInfo.top };
    if (amrs.size) {
      const pad = 2 * cfg.unitsPerMeter;
      const xs = [...amrs.values()].map((a) => a.to.x), ys = [...amrs.values()].map((a) => a.to.y);
      return { x0: Math.min(...xs) - pad, x1: Math.max(...xs) + pad, y0: Math.min(...ys) - pad, y1: Math.max(...ys) + pad };
    }
    const u = 10 * cfg.unitsPerMeter;
    return { x0: -u, x1: u, y0: -u, y1: u };
  }
  function mapInView() {
    const b = visibleWorldBox();
    return b.x0 < mapInfo.right && b.x1 > mapInfo.left && b.y0 < mapInfo.top && b.y1 > mapInfo.bottom;
  }
  function fitZoom() {
    const e = worldExtent();
    return Math.min(size.w / ((e.x1 - e.x0) * 1.06), size.h / ((e.y1 - e.y0) * 1.06));
  }
  function fitView() {
    setFollow(null);
    const e = worldExtent();
    view.rot = 0;
    view.cx = (e.x0 + e.x1) / 2;
    view.cy = (e.y0 + e.y1) / 2;
    view.zoom = fitZoom();
    viewReady = true;
    dirty = true;
  }

  // ---- map tiles -------------------------------------------------------------------
  // tiles: key -> {state: queued|loading|ok|error, img, frame, prio, at}; Map order = LRU
  const tiles = new Map();
  const TILE_CACHE = 160, MAX_INFLIGHT = 6;
  let inflight = 0, frameNo = 0;
  let overview = null;     // the single tile of the top level: fallback and minimap
  let wallHex = "333d4a";

  function tileKey(z, x, y) { return mapInfo.id + "/" + z + "/" + x + "/" + y; }
  function tilePath(z, x, y) { return "/map/tiles/" + mapInfo.id + "/" + z + "/" + x + "/" + y + ".png?color=" + wallHex; }
  function tileCount(z) {
    const w = ((mapInfo.width - 1) >> z) + 1, h = ((mapInfo.height - 1) >> z) + 1;
    return [Math.ceil(w / mapInfo.tileSize), Math.ceil(h / mapInfo.tileSize)];
  }
  function loadedTile(z, x, y) {
    const t = tiles.get(tileKey(z, x, y));
    return t && t.state === "ok" ? t : null;
  }
  function wantTile(z, x, y, prio) {
    const key = tileKey(z, x, y);
    let t = tiles.get(key);
    if (t && t.state === "error" && performance.now() - t.at > 5000) { tiles.delete(key); t = null; }
    if (!t) { t = { state: "queued" }; tiles.set(key, t); }
    else { tiles.delete(key); tiles.set(key, t); }  // LRU touch
    t.frame = frameNo;
    t.prio = prio;
    t.z = z; t.x = x; t.y = y;
    return t.state === "ok" ? t : null;
  }
  function pumpTiles() {
    const queued = [];
    for (const [key, t] of tiles) {
      if (t.state !== "queued") continue;
      if (t.frame !== frameNo) tiles.delete(key);  // not visible any more
      else queued.push(t);
    }
    queued.sort((a, b) => a.prio - b.prio);
    const id = mapInfo && mapInfo.id;
    for (const t of queued) {
      if (inflight >= MAX_INFLIGHT) break;
      t.state = "loading";
      inflight++;
      apiImage(tilePath(t.z, t.x, t.y))
        .then((img) => { t.img = img; t.state = "ok"; })
        .catch(() => {
          t.state = "error";
          t.at = performance.now();
          setTimeout(() => { tileVersion++; dirty = true; }, 5100);   // redraw then retries it
        })
        .finally(() => { inflight--; if (mapInfo && mapInfo.id === id) { tileVersion++; dirty = true; } });
    }
    let excess = tiles.size - TILE_CACHE;
    for (const [key, t] of tiles) {
      if (excess <= 0) break;
      if (t.state === "ok" || t.state === "error") {
        if (t.img && t.img.close) t.img.close();
        tiles.delete(key);
        excess--;
      }
    }
  }
  function resetTiles() {
    for (const t of tiles.values()) if (t.img && t.img.close) t.img.close();
    tiles.clear();
    tileVersion++;
    if (overview && overview.close) overview.close();
    overview = null;
  }

  function loadOverview() {
    const top = mapInfo.levels - 1, id = mapInfo.id, color = wallHex;
    apiImage(tilePath(top, 0, 0)).then((img) => {
      if (mapInfo && mapInfo.id === id && wallHex === color) { overview = img; tileVersion++; dirty = true; }
    }).catch(() => {});
  }

  let mapLoading = false;
  async function loadMap() {
    if (mapLoading) return;
    mapLoading = true;
    try {
      const info = await api("GET", "/map");
      cfg.unitsPerMeter = info.unitsPerMeter || cfg.unitsPerMeter;
      const changed = !mapInfo || !info.loaded || info.id !== mapInfo.id;
      if (changed) resetTiles();
      mapInfo = info.loaded ? info : null;
      mapError = info.error || "";
      mapNote = info.loading ? "Loading map " + info.file + " …" : "";
      if (info.loading) setTimeout(loadMap, 1000);   // until it is ready (its id then arrives here)
      if (mapInfo && changed) loadOverview();
      // a changed map (file updated while the app runs) keeps the view, unless it is out of sight
      if (mapInfo && changed && (!viewReady || (!follow && !mapInView()))) fitView();
    } catch (e) {
      mapError = "Cannot load the map: " + e.message;
    } finally {
      mapLoading = false;
      const msg = $("map-msg");
      msg.textContent = mapError ? "Map: " + mapError : mapNote;
      msg.classList.toggle("info", !mapError && !!mapNote);
      msg.hidden = !msg.textContent;
      layoutMinimap();
      dirty = true;
    }
  }

  // ---- AMR state -----------------------------------------------------------------
  function hashId(id) {   // FNV-1a; the widget uses the same, so an AMR has one color everywhere
    let h = 2166136261;
    for (let i = 0; i < id.length; i++) { h ^= id.charCodeAt(i); h = Math.imul(h, 16777619); }
    return h >>> 0;
  }
  function amrColor(a) { return amrPalette[a.hash % amrPalette.length]; }
  function poseAt(a, now) {
    const t = a.dur > 0 ? clamp((now - a.t0) / a.dur, 0, 1) : 1;
    return {
      x: a.from.x + (a.to.x - a.from.x) * t,
      y: a.from.y + (a.to.y - a.from.y) * t,
      r: a.from.r + wrapDeg(a.to.r - a.from.r) * t,
    };
  }
  function isStale() { return !fleetStatus || fleetStatus.stale; }

  function applyFleet(d) {
    const now = performance.now();
    fleetStatus = d;
    fleetRevision = d.revision;
    if (d.unchanged) return false;
    if (lastChangeAt) updateInterval = clamp(0.7 * updateInterval + 0.3 * (now - lastChangeAt), 50, 2000);
    lastChangeAt = now;
    const seen = new Set();
    let membership = false;
    for (const item of d.amrs) {
      seen.add(item.id);
      const target = { x: item.x, y: item.y, r: item.rotation };
      let a = amrs.get(item.id);
      if (!a) {
        a = { from: target, to: target, t0: now, dur: 0, hash: hashId(item.id) };
        amrs.set(item.id, a);
        membership = true;
        if (pathOn.has(item.id) && cfg.pathMinutes) fetchPath(item.id);
      } else {
        const cur = poseAt(a, now);
        const jump = Math.hypot(target.x - cur.x, target.y - cur.y) > 5 * cfg.unitsPerMeter;
        a.from = jump ? target : cur;
        a.to = target;
        a.t0 = now;
        a.dur = jump ? 0 : updateInterval;
        if (pathOn.has(item.id) && cfg.pathMinutes) {
          if (jump) fetchPath(item.id);     // relocalized? the app decides whether the path goes on
          else recordPath(item.id, target, now);
        }
      }
      a.data = item;
    }
    for (const id of [...amrs.keys()]) if (!seen.has(id)) { amrs.delete(id); paths.delete(id); membership = true; }
    if (selected && !amrs.has(selected)) selected = null;
    dirty = true;
    return membership;
  }

  // ---- driven paths ----------------------------------------------------------------------
  // The app keeps every AMR's path of the last minutes (setting fleet.pathHistoryMinutes). Switching a
  // path on fetches it once (GET /fleet/path); then the positions of the fleet updates are appended
  // here with the app's spacing (pathStepM) and the oldest dropped.
  function shade(hex, f) {   // towards black by f (0..1): the path is a few shades darker than its AMR
    if (!HEX.test(hex || "")) return hex;
    return "#" + [1, 3, 5].map((i) => Math.round(parseInt(hex.slice(i, i + 2), 16) * (1 - f))
      .toString(16).padStart(2, "0")).join("");
  }
  function pathColor(a, stale) { return shade(stale ? colors["amr-stale"] : amrColor(a), 0.35); }
  async function fetchPath(id) {
    const path = { pts: [], loading: true };
    paths.set(id, path);
    try {
      const d = await api("GET", "/fleet/path?id=" + encodeURIComponent(id));
      const now = performance.now();
      if (paths.get(id) === path) path.pts = d.points.map(([x, y, age]) => [x, y, now - age * 1000]);
    } catch (e) {
      if (paths.get(id) === path) paths.delete(id);
      // not in the fleet any more (404) or no connection: try again while it is switched on
      setTimeout(() => { if (pathOn.has(id) && amrs.has(id) && !paths.has(id) && cfg.pathMinutes) fetchPath(id); }, 5000);
    }
    path.loading = false;
    dirty = true;
  }
  function recordPath(id, p, now) {
    const path = paths.get(id);
    if (!path || path.loading) return;
    const last = path.pts[path.pts.length - 1];
    if (!last || Math.hypot(p.x - last[0], p.y - last[1]) >= cfg.pathStepM * cfg.unitsPerMeter) path.pts.push([p.x, p.y, now]);
    const cutoff = now - cfg.pathMinutes * 60000;
    let old = 0;
    while (old < path.pts.length - 1 && path.pts[old][2] < cutoff) old++;
    if (old) path.pts.splice(0, old);
  }
  function togglePath(id) {
    if (pathOn.has(id)) { pathOn.delete(id); paths.delete(id); }
    else { pathOn.add(id); if (amrs.has(id) && cfg.pathMinutes) fetchPath(id); }
    prefs.set("paths", [...pathOn].slice(-50));
    updateList();
    dirty = true;
  }

  // ---- follow ----------------------------------------------------------------------
  function setFollow(id) {
    follow = id;
    if (id && amrs.has(id)) {
      const p = poseAt(amrs.get(id), performance.now());
      view.cx = p.x; view.cy = p.y;
      const minZoom = 40 / cfg.amrLength;  // make the AMR at least ~40 px long
      if (view.zoom < minZoom) setZoom(minZoom);
    }
    $("followbar").hidden = !follow;
    $("follow-name").textContent = follow || "";
    $("heading-up").checked = headingUp;
    updateList();
    dirty = true;
  }
  function applyFollow(now) {
    if (!follow) return;
    const a = amrs.get(follow);
    $("follow-note").textContent = a ? "" : "(not in the fleet data)";
    if (!a) return;
    const p = poseAt(a, now);
    view.cx = p.x;
    view.cy = p.y;
    if (headingUp) view.rot = (p.r * Math.PI) / 180 - Math.PI / 2;
  }

  // ---- rendering ----------------------------------------------------------------------
  function resize() {
    const r = canvas.getBoundingClientRect();
    size.dpr = window.devicePixelRatio || 1;
    size.w = Math.max(1, r.width);
    size.h = Math.max(1, r.height);
    canvas.width = layer.width = Math.round(size.w * size.dpr);
    canvas.height = layer.height = Math.round(size.h * size.dpr);
    if (!viewReady && (mapInfo || amrs.size)) fitView();
    dirty = true;
  }

  function setMapPixelTransform(g) {
    // level-0 map pixel (p, q) -> device pixels: p right = +x, q down = -y
    const res = mapInfo.resolution, c = Math.cos(view.rot), s = Math.sin(view.rot), z = view.zoom, d = size.dpr;
    const lx = mapInfo.left - view.cx, ty = mapInfo.top - view.cy;
    g.setTransform(d * z * c * res, d * z * s * res, -d * z * s * res, d * z * c * res,
      d * (size.w / 2 + z * (c * lx + s * ty)), d * (size.h / 2 + z * (s * lx - c * ty)));
  }

  function drawMap(g) {
    setMapPixelTransform(g);
    g.fillStyle = colors["map-floor"];
    g.fillRect(0, 0, mapInfo.width, mapInfo.height);
    drawGrid(g);
    setMapPixelTransform(g);
    const T = mapInfo.tileSize, top = mapInfo.levels - 1;
    const cellPx = view.zoom * mapInfo.resolution * size.dpr;   // device px per map cell
    const z = clamp(Math.round(Math.log2(1 / cellPx)), 0, top);
    g.imageSmoothingEnabled = cellPx * (1 << z) < 1;
    const span = T * (1 << z);                                   // level-0 px per tile
    const b = visibleWorldBox(), res = mapInfo.resolution;
    const p0 = (b.x0 - mapInfo.left) / res, p1 = (b.x1 - mapInfo.left) / res;
    const q0 = (mapInfo.top - b.y1) / res, q1 = (mapInfo.top - b.y0) / res;
    const [ntx, nty] = tileCount(z);
    const tx0 = clamp(Math.floor(p0 / span), 0, ntx - 1), tx1 = clamp(Math.floor(p1 / span), 0, ntx - 1);
    const ty0 = clamp(Math.floor(q0 / span), 0, nty - 1), ty1 = clamp(Math.floor(q1 / span), 0, nty - 1);
    const pc = (p0 + p1) / 2, qc = (q0 + q1) / 2;
    for (let ty = ty0; ty <= ty1; ty++) {
      for (let tx = tx0; tx <= tx1; tx++) {
        const prio = Math.hypot((tx + 0.5) * span - pc, (ty + 0.5) * span - qc);
        const t = wantTile(z, tx, ty, prio);
        if (t) { g.drawImage(t.img, tx * span, ty * span, span, span); continue; }
        // not loaded yet: draw the matching part of a coarser level that is
        for (let up = z + 1; up <= top; up++) {
          const k = up - z, ax = tx >> k, ay = ty >> k;
          const img = up === top && overview ? overview : (loadedTile(up, ax, ay) || {}).img;
          if (!img) continue;
          const sub = T >> k, sx = (tx - (ax << k)) * sub, sy = (ty - (ay << k)) * sub;
          g.drawImage(img, sx, sy, Math.max(1, sub), Math.max(1, sub), tx * span, ty * span, span, span);
          break;
        }
      }
    }
    pumpTiles();
  }

  function drawGrid(g) {
    const step = niceStep(200 / (view.zoom * cfg.unitsPerMeter)) * cfg.unitsPerMeter;  // 80..200 px
    const b = visibleWorldBox();
    const x0 = Math.max(b.x0, mapInfo.left), x1 = Math.min(b.x1, mapInfo.right);
    const y0 = Math.max(b.y0, mapInfo.bottom), y1 = Math.min(b.y1, mapInfo.top);
    if (x0 >= x1 || y0 >= y1 || (x1 - x0) / step > 400 || (y1 - y0) / step > 400) return;
    g.setTransform(size.dpr, 0, 0, size.dpr, 0, 0);
    g.strokeStyle = colors["map-grid"];
    g.lineWidth = 1;
    g.beginPath();
    for (let x = Math.ceil(x0 / step) * step; x <= x1; x += step) {
      const [ax, ay] = worldToScreen(x, y0), [bx, by] = worldToScreen(x, y1);
      g.moveTo(ax, ay); g.lineTo(bx, by);
    }
    for (let y = Math.ceil(y0 / step) * step; y <= y1; y += step) {
      const [ax, ay] = worldToScreen(x0, y), [bx, by] = worldToScreen(x1, y);
      g.moveTo(ax, ay); g.lineTo(bx, by);
    }
    g.stroke();
  }

  // Zoomed far out (big maps) AMRs keep their shape - body with the heading arrow - at a minimum size.
  // 0.2.3-0.3.0 drew a small arrowhead below 14 px instead: hard to see (user, 2026-10-24). 16 px keeps
  // the arrow inside readable; 0.2.2 had 18 px (on a 100 m map ~4 m: 200 AMRs were a crowd).
  const MIN_AMR_PX = 16;
  function amrScreenSize() {
    let L = cfg.amrLength * view.zoom, W = cfg.amrWidth * view.zoom;
    if (L < MIN_AMR_PX) { W *= MIN_AMR_PX / L; L = MIN_AMR_PX; }
    return [L, Math.max(W, 8)];
  }

  // Crosshair pressed: the lock's blue outline pulses around the AMR three times and goes. Zoomed out
  // (small AMRs) it is drawn bigger, at least FOCUS_MIN_PX long, so the AMR is easy to find on a big map.
  const FOCUS_PULSE_MS = 2000, FOCUS_MIN_PX = 60;
  function drawFocusPulse(L, W, now) {    // in the AMR's rotated frame (ctx at its centre)
    const k = (now - focusPulse.t0) / FOCUS_PULSE_MS;
    if (k >= 1) { focusPulse = null; return; }
    const beat = reduceMotion ? 1 : 0.5 - 0.5 * Math.cos(k * 6 * Math.PI);   // 0 -> 1 -> 0, three times
    const grow = Math.max(1, FOCUS_MIN_PX / (L + 8));
    const w = (L + 8) * grow + 14 * beat, h = (W + 8) * grow + 14 * beat;
    ctx.strokeStyle = colors.accent;
    ctx.globalAlpha = k < 0.8 ? 1 : (1 - k) / 0.2;                        // fades out at the end
    ctx.lineWidth = 3;
    roundRect(-w / 2, -h / 2, w, h, 6 * grow);
    ctx.stroke();
    ctx.globalAlpha = 1;
  }

  // A line from the oldest kept point to the AMR's current (interpolated) position, on a halo in the floor
  // color so it stays visible over walls and grid. Points closer than 1 px on screen are skipped.
  function drawPaths(now) {
    if (!paths.size) return;
    const c = Math.cos(view.rot), s = Math.sin(view.rot), z = view.zoom, w2 = size.w / 2, h2 = size.h / 2;
    const stale = isStale();
    ctx.setTransform(size.dpr, 0, 0, size.dpr, 0, 0);
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    for (const [id, path] of paths) {
      const a = amrs.get(id);
      if (!a || !path.pts.length) continue;
      const pose = poseAt(a, now);
      let n = 0, lx = 0, ly = 0;
      ctx.beginPath();
      const add = (x, y, force) => {
        const dx = (x - view.cx) * z, dy = -(y - view.cy) * z;
        const sx = w2 + dx * c - dy * s, sy = h2 + dx * s + dy * c;
        if (n && !force && Math.abs(sx - lx) < 1 && Math.abs(sy - ly) < 1) return;
        if (n++) ctx.lineTo(sx, sy); else ctx.moveTo(sx, sy);
        lx = sx; ly = sy;
      };
      for (const q of path.pts) add(q[0], q[1], false);
      add(pose.x, pose.y, true);
      if (n < 2) continue;
      ctx.strokeStyle = colors["map-floor"];
      ctx.globalAlpha = 0.7;
      ctx.lineWidth = 6;
      ctx.stroke();
      ctx.strokeStyle = pathColor(a, stale);
      ctx.globalAlpha = 1;
      ctx.lineWidth = 3;
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const hitList = [];      // [{id, x, y, r}] in CSS px, for clicks
  function drawAmrs(now) {
    hitList.length = 0;
    const [L, W] = amrScreenSize();
    const stale = isStale();
    const labels = [];
    for (const [id, a] of amrs) {
      const p = poseAt(a, now);
      const [sx, sy] = worldToScreen(p.x, p.y);
      const radius = Math.hypot(L, W) / 2;
      const margin = focusPulse && focusPulse.id === id ? FOCUS_MIN_PX : 0;   // its pulse may reach into view
      if (sx < -radius - 80 - margin || sy < -radius - 40 - margin || sx > size.w + radius + 80 + margin
          || sy > size.h + radius + 40 + margin) continue;
      hitList.push({ id, x: sx, y: sy, r: radius + 4 });
      const phi = view.rot - (p.r * Math.PI) / 180;
      ctx.setTransform(size.dpr, 0, 0, size.dpr, 0, 0);
      if (id === tourPulse) {          // the tutorial's example AMR pulses
        const k = reduceMotion ? 1 : 0.5 + 0.5 * Math.sin(now / 160);
        ctx.strokeStyle = colors.accent;
        ctx.globalAlpha = 0.35 + 0.65 * k;
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.arc(sx, sy, radius + 8 + 6 * k, 0, 2 * Math.PI);
        ctx.stroke();
        ctx.globalAlpha = 1;
      }
      ctx.translate(sx, sy);
      ctx.rotate(phi);
      if (focusPulse && focusPulse.id === id) drawFocusPulse(L, W, now);
      if (id === selected || id === follow) {
        ctx.strokeStyle = colors.accent;
        ctx.lineWidth = 3;
        roundRect(-L / 2 - 4, -W / 2 - 4, L + 8, W + 8, 6);
        ctx.stroke();
      }
      ctx.fillStyle = stale ? colors["amr-stale"] : amrColor(a);
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 1.5;
      roundRect(-L / 2, -W / 2, L, W, Math.min(4, W / 4));
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = "#ffffff";             // heading: arrow towards the front (+x of the AMR)
      ctx.beginPath();
      ctx.moveTo(L / 2 - Math.max(3, L * 0.1), 0);
      ctx.lineTo(L / 2 - Math.max(9, L * 0.38), -W * 0.3);
      ctx.lineTo(L / 2 - Math.max(7, L * 0.3), 0);
      ctx.lineTo(L / 2 - Math.max(9, L * 0.38), W * 0.3);
      ctx.closePath();
      ctx.fill();
      if (showLabels || id === selected || id === follow) {
        labels.push({ id, a, x: sx, y: sy, r: radius, body: hitList.length - 1,
          prio: id === follow ? 2 : id === selected ? 1 : 0 });
      }
    }
    drawLabels(labels);
  }

  // Labels go below their AMR, else above / right / left - wherever they cover neither another label
  // nor another AMR. A label without a free spot is left out (the list still shows the AMR); the
  // selected and the locked AMR always get theirs (placed first).
  function drawLabels(labels) {
    const labelInk = inkFor(colors["map-floor"] || "#ffffff");
    ctx.setTransform(size.dpr, 0, 0, size.dpr, 0, 0);
    ctx.font = "600 12px system-ui, -apple-system, 'Segoe UI', Roboto, Arial, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.lineJoin = "round";
    const overlaps = (a, b) => a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;
    const bodies = hitList.map((h) => ({ x0: h.x - h.r + 6, y0: h.y - h.r + 6, x1: h.x + h.r - 6, y1: h.y + h.r - 6 }));
    const placed = [];
    labels.sort((a, b) => b.prio - a.prio);
    for (const lab of labels) {
      const text = lab.a.data && lab.a.data.name ? lab.id + " · " + lab.a.data.name : lab.id;
      const w = ctx.measureText(text).width + 6, h = 15, gap = lab.r - 2;
      const spots = [[lab.x - w / 2, lab.y + gap], [lab.x - w / 2, lab.y - gap - h],
        [lab.x + gap, lab.y - h / 2], [lab.x - gap - w, lab.y - h / 2]]
        .map(([x, y]) => ({ x0: x, y0: y, x1: x + w, y1: y + h }));
      let spot = spots.find((box) => !placed.some((b) => overlaps(b, box))
        && !bodies.some((b, i) => i !== lab.body && overlaps(b, box)));
      if (!spot && lab.prio > 0) spot = spots[0];
      if (!spot) continue;
      placed.push(spot);
      ctx.strokeStyle = colors["map-floor"];        // labels sit on the map: colors from the floor
      ctx.lineWidth = 4;
      ctx.strokeText(text, (spot.x0 + spot.x1) / 2, spot.y0 + 1);
      ctx.fillStyle = labelInk;
      ctx.fillText(text, (spot.x0 + spot.x1) / 2, spot.y0 + 1);
    }
  }
  function roundRect(x, y, w, h, r) {
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(x, y, w, h, r);
    else ctx.rect(x, y, w, h);
  }

  let lastScale = "", lastRot = null;
  function updateHud() {
    const pxPerMeter = view.zoom * cfg.unitsPerMeter;
    const meters = niceStep(110 / pxPerMeter);
    const label = fmtMeters(meters);
    if (label !== lastScale) {
      lastScale = label;
      $("scale-label").textContent = label;
    }
    $("scale-bar").style.width = Math.round(meters * pxPerMeter) + "px";
    if (lastRot !== view.rot) {
      lastRot = view.rot;
      $("compass").style.transform = "rotate(" + view.rot + "rad)";
    }
  }

  // minimap: whole map north-up, AMRs, the visible area
  const miniGeo = { s: 1, w: 0, h: 0 };
  function layoutMinimap() {
    const show = showMinimap && !!mapInfo;
    mini.hidden = !show;
    $("btn-minimap").setAttribute("aria-pressed", String(showMinimap));
    if (!show) return;
    const narrow = size.w < 600;   // phone / small HMI: keep most of the map visible
    const maxW = narrow ? Math.min(110, size.w * 0.28) : Math.min(220, size.w * 0.3);
    const maxH = narrow ? Math.min(130, size.h * 0.25) : Math.min(220, size.h * 0.35);
    miniGeo.s = Math.min(maxW / mapInfo.width, maxH / mapInfo.height);  // minimap px per map cell
    miniGeo.w = Math.max(40, Math.round(mapInfo.width * miniGeo.s));
    miniGeo.h = Math.max(40, Math.round(mapInfo.height * miniGeo.s));
    mini.style.width = miniGeo.w + "px";
    mini.style.height = miniGeo.h + "px";
    mini.width = Math.round(miniGeo.w * size.dpr);
    mini.height = Math.round(miniGeo.h * size.dpr);
  }
  function miniPoint(x, y) {
    return [(x - mapInfo.left) / mapInfo.resolution * miniGeo.s, (mapInfo.top - y) / mapInfo.resolution * miniGeo.s];
  }
  function drawMinimap(now) {
    if (mini.hidden || !mapInfo) return;
    mctx.setTransform(size.dpr, 0, 0, size.dpr, 0, 0);
    mctx.fillStyle = colors["map-floor"];
    mctx.fillRect(0, 0, miniGeo.w, miniGeo.h);
    if (overview) {
      const span = mapInfo.tileSize * (1 << (mapInfo.levels - 1));    // level-0 px covered by the tile
      mctx.imageSmoothingEnabled = true;
      mctx.drawImage(overview, 0, 0, span * miniGeo.s, span * miniGeo.s);
    }
    const dot = amrs.size > 50 ? 1.5 : amrs.size > 15 ? 2 : 3;   // many AMRs: smaller dots
    for (const a of amrs.values()) {
      const p = poseAt(a, now);
      const [x, y] = miniPoint(p.x, p.y);
      mctx.fillStyle = isStale() ? colors["amr-stale"] : amrColor(a);
      mctx.beginPath();
      mctx.arc(x, y, dot, 0, 2 * Math.PI);
      mctx.fill();
    }
    const corners = [[0, 0], [size.w, 0], [size.w, size.h], [0, size.h]].map(([sx, sy]) => miniPoint(...screenToWorld(sx, sy)));
    mctx.strokeStyle = colors.accent;
    mctx.fillStyle = colors.accent;
    mctx.lineWidth = 1.5;
    mctx.beginPath();
    corners.forEach(([x, y], i) => (i ? mctx.lineTo(x, y) : mctx.moveTo(x, y)));
    mctx.closePath();
    mctx.globalAlpha = 0.12;
    mctx.fill();
    mctx.globalAlpha = 1;
    mctx.stroke();
  }

  function anyAnimating(now) {
    for (const a of amrs.values()) if (a.dur > 0 && now < a.t0 + a.dur) return true;
    return false;
  }

  function frame() {
    requestAnimationFrame(frame);
    const now = performance.now();
    const animating = anyAnimating(now);
    if (focusPulse && now - focusPulse.t0 >= FOCUS_PULSE_MS) { focusPulse = null; dirty = true; }  // also off-screen
    if (!dirty && !animating && !tourPulse && !focusPulse) return;
    dirty = false;
    frameNo++;
    applyFollow(now);
    const key = [view.cx, view.cy, view.zoom, view.rot, size.w, size.h, size.dpr, mapInfo ? mapInfo.id : "",
      wallHex, colors["map-bg"], colors["map-floor"], colors["map-grid"], tileVersion].join("|");
    const moving = key !== lastFrameKey;
    lastFrameKey = key;
    if (key !== layerKey && moving) {
      // the view changes from frame to frame (locked onto an AMR, panning, zooming): caching can't help,
      // draw the map straight onto the canvas (copying a fresh layer each frame only costs extra)
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.fillStyle = colors["map-bg"];
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      if (mapInfo) drawMap(ctx);
    } else {
      if (key !== layerKey) {         // the view has settled: draw the map layer once, then reuse it
        layerKey = key;
        lctx.setTransform(1, 0, 0, 1, 0, 0);
        lctx.fillStyle = colors["map-bg"];
        lctx.fillRect(0, 0, layer.width, layer.height);
        if (mapInfo) drawMap(lctx);
      }
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.drawImage(layer, 0, 0);
    }
    drawPaths(now);
    drawAmrs(now);
    drawMinimap(now);
    updateHud();
    updateReady();
  }

  // <body data-ui-ready>: "true" when the first fleet answer is in and no visible map tile is loading
  // (tools/ui_check.py waits for it before screenshots)
  function updateReady() {
    let busy = !firstFleetAnswer || mapLoading;
    if (!busy) for (const t of tiles.values()) if (t.state === "queued" || t.state === "loading") { busy = true; break; }
    const value = busy ? "false" : "true";
    if (document.body.dataset.uiReady !== value) document.body.dataset.uiReady = value;
  }

  // ---- AMR list ------------------------------------------------------------------------
  const rows = new Map();  // id -> {li, ...parts}
  let listIds = "";
  function filterText() { return $("amr-filter").value.trim().toLowerCase(); }
  function matches(id, a, f) {
    if (!f) return true;
    const d = a.data || {};
    return [id, d.name, d.state].some((v) => v && String(v).toLowerCase().includes(f));
  }
  // A tile per AMR: click (or Enter) on its head expands/collapses the details - only one is open,
  // and it is the "selected" AMR (ring on the map). Only the crosshair moves the map, the lock follows.
  function toggleSelected(id) {
    selected = selected === id ? null : id;
    updateList();
    dirty = true;
  }
  function rebuildList() {
    const list = $("amr-list");
    const f = filterText();
    const ids = [...amrs.keys()].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
    rows.clear();
    list.replaceChildren();
    for (const id of ids) {
      const a = amrs.get(id);
      if (!matches(id, a, f)) continue;
      const followBtn = el("button", { type: "button", class: "btn-icon", title: "Lock the view to this AMR", "aria-label": "Follow " + id }, icon("lock"));
      const centerBtn = el("button", { type: "button", class: "btn-icon", title: "Show this AMR on the map", "aria-label": "Center on " + id }, icon("target"));
      const pathBtn = el("button", { type: "button", class: "btn-icon btn-path", "aria-label": "Path of " + id }, icon("path"));
      const parts = {
        swatch: el("span", { class: "swatch" }),
        title: el("div", { class: "amr-title" }),
        sub: el("div", { class: "amr-sub" }),
        battery: el("div", { class: "battery", title: "Battery" }, el("span")),
        details: el("dl", { class: "amr-details", hidden: true }),
        followBtn,
        centerBtn,
        pathBtn,
        shown: {},          // what the tile shows now (updateList writes only changes)
      };
      parts.head = el("div", { class: "amr-head", role: "button", tabindex: "0", "aria-expanded": "false",
        title: "Show / hide details" }, parts.swatch, el("div", { class: "amr-main" }, parts.title, parts.sub),
        parts.battery, el("div", { class: "amr-actions" }, centerBtn, followBtn, pathBtn));
      const li = el("li", { class: "amr", "data-id": id }, parts.head, parts.details);
      parts.head.addEventListener("click", () => toggleSelected(id));
      parts.head.addEventListener("keydown", (ev) => {
        if (ev.target === parts.head && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); toggleSelected(id); }
      });
      centerBtn.addEventListener("click", (ev) => { ev.stopPropagation(); centerOn(id); });
      followBtn.addEventListener("click", (ev) => { ev.stopPropagation(); setFollow(follow === id ? null : id); });
      pathBtn.addEventListener("click", (ev) => { ev.stopPropagation(); togglePath(id); });
      parts.li = li;
      rows.set(id, parts);
      list.append(li);
    }
    listIds = ids.join("\n") + "|" + f;
    updateList();
  }
  function fmtValue(v) {
    if (typeof v === "boolean") return v ? "yes" : "no";
    if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(Math.round(v * 1000) / 1000);
    return String(v);
  }
  function fillDetails(dl, id, d) {
    const items = [];
    if (d.name) items.push(["Name", d.name]);
    items.push(["State", d.state || "–"]);
    if (typeof d.battery === "number") items.push(["Battery", Math.round(d.battery) + " %"]);
    items.push(["Position", "x " + fmtNum(d.x) + "   y " + fmtNum(d.y)]);
    items.push(["Heading", (Math.round(d.rotation * 10) / 10) + "°"]);
    if (typeof d.speed === "number") items.push(["Speed", (d.speed / cfg.unitsPerMeter).toFixed(2) + " m/s"]);
    if (fleetStatus && fleetStatus.ageMs != null) items.push(["Updated", (fleetStatus.ageMs / 1000).toFixed(1) + " s ago"]);
    for (const [k, v] of Object.entries(d.info || {}).slice(0, MAX_DETAIL_INFO)) items.push([humanize(k), fmtValue(v)]);
    dl.replaceChildren(...items.flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: v })]));
  }
  // Runs with every fleet update (5 per second). Every tile remembers what it shows (r.shown) and only
  // changed values are written: the browser restyles and lays out whatever is written, even an unchanged
  // value - rewriting 200 tiles per update cost more frames than drawing the map (A/B test 2026-10-24).
  // Tiles outside the visible part of the list are not laid out or painted at all (content-visibility).
  function updateList() {
    const f = filterText();
    const ids = [...amrs.keys()].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
    if (ids.join("\n") + "|" + f !== listIds) { rebuildList(); return; }
    const stale = isStale();
    for (const [id, r] of rows) {
      const a = amrs.get(id), s = r.shown;
      const d = a.data || {};
      const battery = typeof d.battery === "number" ? Math.round(clamp(d.battery, 0, 100)) : -1;
      const color = amrColor(a);
      if (s.color !== color) { s.color = color; r.swatch.style.background = color; }
      const name = d.name || "";
      if (s.name !== name) {
        s.name = name;
        r.title.replaceChildren(document.createTextNode(id), name ? el("span", { class: "muted", text: " · " + name }) : "");
      }
      const sub = (d.state || "–") + (battery >= 0 ? " · " + battery + " %" : "");
      if (s.sub !== sub) { s.sub = sub; r.sub.textContent = sub; }
      if (s.battery !== battery) {
        s.battery = battery;
        r.battery.hidden = battery < 0;
        r.battery.firstChild.style.width = Math.max(0, battery) + "%";
        r.battery.className = "battery" + (battery < 20 ? " low" : battery < 40 ? " mid" : "");
        r.battery.title = "Battery " + battery + " %";
      }
      const open = id === selected;
      if (s.open !== open) {
        s.open = open;
        r.li.classList.toggle("selected", open);
        r.head.setAttribute("aria-expanded", String(open));
        r.details.hidden = !open;
      }
      if (open) fillDetails(r.details, id, d);           // one tile: position, speed etc. change all the time
      if (s.stale !== stale) { s.stale = stale; r.li.classList.toggle("stale", stale); }
      const locked = id === follow;
      if (s.locked !== locked) {
        s.locked = locked;
        r.followBtn.classList.toggle("on", locked);
        r.followBtn.setAttribute("aria-pressed", String(locked));
      }
      const pathShown = cfg.pathMinutes > 0 && pathOn.has(id);
      const pc = pathShown ? pathColor(a, stale) : "";
      const pathKey = cfg.pathMinutes + "|" + pc;
      if (s.path !== pathKey) {
        s.path = pathKey;
        r.pathBtn.hidden = !cfg.pathMinutes;
        r.pathBtn.classList.toggle("on", pathShown);
        r.pathBtn.setAttribute("aria-pressed", String(pathShown));
        r.pathBtn.title = pathShown ? "Hide the path" : "Show the path of the last " + cfg.pathMinutes + " min";
        if (pathShown) {
          r.pathBtn.style.setProperty("--path", pc);
          r.pathBtn.style.setProperty("--path-ink", inkFor(pc));
        }
      }
    }
    setText($("amr-count"), amrs.size ? "(" + amrs.size + ")" : "");
    const empty = $("amr-empty");
    if (!amrs.size) {
      empty.replaceChildren(document.createTextNode("No AMRs. Write the fleet JSON to the Data Layer node "),
        el("code", { text: PREFIX.slice(1) + "/fleet/amrs" }),
        document.createTextNode(canWrite ? " or switch on the simulation in the settings." : "."));
    } else if (!rows.size) {
      empty.textContent = "No AMR matches the filter.";
    }
    const noEmpty = amrs.size > 0 && rows.size > 0;
    if (empty.hidden !== noEmpty) empty.hidden = noEmpty;
  }
  function centerOn(id) {
    const a = amrs.get(id);
    if (!a) return;
    if (follow && follow !== id) setFollow(null);
    const p = poseAt(a, performance.now());
    view.cx = p.x; view.cy = p.y;
    focusPulse = { id, t0: performance.now() };
    dirty = true;
  }
  function scrollRowIntoView(id) {
    const r = rows.get(id);
    if (r) r.li.scrollIntoView({ block: "nearest" });
  }

  // ---- fleet polling + status -------------------------------------------------------------
  function updateFleetPill(error) {   // every poll: write only what changed
    const s = fleetStatus;
    let cls, text, title;
    if (error) { cls = "pill pill-err"; text = "Fleet: no connection"; title = error; }
    else if (!s || !s.revision) { cls = "pill pill-unknown"; text = "Fleet: no data yet"; title = "Nothing written to " + PREFIX.slice(1) + "/fleet/amrs since the app started"; }
    else if (s.stale) { cls = "pill pill-warn"; text = s.count + " AMR" + (s.count === 1 ? "" : "s") + " · no update for " + Math.round(s.ageMs / 1000) + " s"; title = "Last update " + new Date(s.receivedAt * 1000).toLocaleTimeString(); }
    else { cls = "pill pill-ok"; text = s.count + " AMR" + (s.count === 1 ? "" : "s") + " · live"; title = "Updates: " + s.revision; }
    const pill = $("fleet-pill");
    if (pill.className !== cls) pill.className = cls;
    setText(pill, text);
    if (pill.title !== title) pill.title = title;
    if (error) return;
    const sim = !!s && s.source === "simulation" && !s.stale;
    if ($("sim-badge").hidden === sim) $("sim-badge").hidden = !sim;
    const err = $("fleet-error"), msg = s && s.lastError ? "Last fleet update rejected: " + s.lastError : "";
    setText(err, msg);
    if (err.hidden !== !msg) err.hidden = !msg;
  }

  async function pollFleet() {
    let delay = cfg.refreshMs;
    try {
      const d = await api("GET", "/fleet?since=" + fleetRevision);
      const wasStale = isStale();
      const membership = applyFleet(d);
      if ((d.mapId || null) !== (mapInfo ? mapInfo.id : null) && !mapLoading) loadMap();
      if (!viewReady && !mapInfo && amrs.size) fitView();
      if (membership || !d.unchanged || wasStale !== isStale()) { updateList(); dirty = true; }
      updateFleetPill("");
    } catch (e) {
      updateFleetPill(e.message);
      delay = e.status === 401 ? 15000 : 2000;
    }
    if (!firstFleetAnswer) { firstFleetAnswer = true; dirty = true; }
    if (document.hidden) delay = Math.max(delay, 2000);
    setTimeout(pollFleet, delay);
  }

  async function refreshStatus() {
    try {
      const s = await api("GET", "/status");
      canWrite = !!s.canWrite;
      $("btn-settings").hidden = !canWrite;          // read-only users get no settings at all
      if (!canWrite && !$("drawer").hidden) closeSettings();
      document.title = s.title;
      $("app-title").textContent = s.title;
      $("app-version").textContent = /^\d/.test(s.version) ? "v" + s.version : s.version;
      const conn = $("conn");
      if (s.datalayer.connected) { conn.className = "pill pill-ok"; conn.textContent = "Data Layer: connected"; }
      else { conn.className = "pill pill-warn"; conn.textContent = s.datalayer.enabled ? "Data Layer: connecting…" : "Data Layer: off"; }
      conn.title = s.datalayer.error || "Data Layer connection";
      showBanner("");
    } catch (e) {
      if (e.status !== 401) showBanner("Cannot reach the app: " + e.message);
    }
  }

  // What the view needs (GET /view-config, also for read-only users): units, AMR size, colors.
  function applyViewConfig(v) {
    cfg.unitsPerMeter = v.unitsPerMeter;
    cfg.amrLength = v.amrLengthM * v.unitsPerMeter;
    cfg.amrWidth = v.amrWidthM * v.unitsPerMeter;
    cfg.refreshMs = v.refreshIntervalMs;
    cfg.pathStepM = v.pathStepM || cfg.pathStepM;
    const minutes = v.pathHistoryMinutes || 0;
    if (minutes !== cfg.pathMinutes) {          // fetch again: the app keeps more or less now
      cfg.pathMinutes = minutes;
      paths.clear();
      if (minutes) for (const id of pathOn) if (amrs.has(id)) fetchPath(id);
    }
    applyStyle(v.style);
    dirty = true;
  }
  function viewConfigFrom(s) {   // the same from full settings (after saving them)
    return { style: s.style, unitsPerMeter: s.map.unitsPerMeter, amrLengthM: s.fleet.amrLengthM,
      amrWidthM: s.fleet.amrWidthM, staleAfterMs: s.fleet.staleAfterMs, refreshIntervalMs: s.display.refreshIntervalMs,
      pathHistoryMinutes: s.fleet.pathHistoryMinutes, pathStepM: cfg.pathStepM };
  }
  async function loadViewConfig() {
    try {
      const v = await api("GET", "/view-config");
      const json = JSON.stringify(v);
      if (json !== viewConfigJson && $("drawer").hidden) {   // don't disturb a preview in the settings
        viewConfigJson = json;
        applyViewConfig(v);
      }
    } catch (e) { /* keep the current one; refreshStatus reports connection problems */ }
  }

  // Styling (settings "style"): the three base colors set the page chrome - background, lines and
  // secondary text are derived in style.css with color-mix(); the map colors go to the canvas.
  const HEX = /^#[0-9a-fA-F]{6}$/;
  function inkFor(hex) {   // readable text on a colored button: dark on light colors, white on dark
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
      .map((c) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)));
    return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.4 ? "#1f2328" : "#ffffff";
  }
  function applyStyle(style) {
    if (!style) return;
    const root = document.documentElement.style;
    const set = (name, value) => { if (HEX.test(value || "")) { root.setProperty("--" + name, value); colors[name] = value; } };
    set("accent", style.accent);
    set("card", style.panel);
    set("text", style.text);
    set("map-bg", style.mapBackground);
    set("map-floor", style.mapFloor);
    set("map-grid", style.mapGrid);
    set("amr-stale", style.staleAmr);
    if (HEX.test(style.accent || "")) root.setProperty("--accent-ink", inkFor(style.accent));
    const palette = (style.amrColors || []).filter((c) => HEX.test(c));
    if (palette.length) amrPalette = palette;
    if (HEX.test(style.mapWalls || "")) {
      root.setProperty("--map-wall", style.mapWalls);
      colors["map-wall"] = style.mapWalls;
      const hex = style.mapWalls.slice(1).toLowerCase();
      if (hex !== wallHex) {            // tiles are drawn by the server in the wall color: fetch again
        wallHex = hex;
        if (mapInfo) { resetTiles(); loadOverview(); }
      }
    }
    updateList();
    dirty = true;
  }

  // ---- input: mouse, touch, keyboard ---------------------------------------------------------
  const pointers = new Map();   // pointerId -> {x, y}
  let drag = null;              // {moved, startX, startY, hit}
  let pinch = null;             // {dist, mx, my}
  function localPoint(ev) {
    const r = canvas.getBoundingClientRect();
    return [ev.clientX - r.left, ev.clientY - r.top];
  }
  function hitTest(x, y) {
    let best = null, bestD = Infinity;
    for (const h of hitList) {
      const d = Math.hypot(h.x - x, h.y - y);
      if (d <= h.r && d < bestD) { best = h.id; bestD = d; }
    }
    return best;
  }
  function pinchState() {
    const [a, b] = [...pointers.values()];
    return { dist: Math.hypot(a.x - b.x, a.y - b.y), mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2 };
  }

  canvas.addEventListener("pointerdown", (ev) => {
    canvas.focus({ preventScroll: true });
    const [x, y] = localPoint(ev);
    pointers.set(ev.pointerId, { x, y });
    canvas.setPointerCapture(ev.pointerId);
    if (pointers.size === 1) drag = { moved: false, startX: x, startY: y, hit: hitTest(x, y) };
    if (pointers.size === 2) { pinch = pinchState(); if (drag) drag.moved = true; }
  });
  canvas.addEventListener("pointermove", (ev) => {
    const [x, y] = localPoint(ev);
    if (ev.pointerType === "mouse") {
      const [wx, wy] = screenToWorld(x, y);
      $("cursor-pos").textContent = "x " + fmtNum(wx) + "   y " + fmtNum(wy);
      if (!pointers.size) canvas.classList.toggle("over-amr", !!hitTest(x, y));
    }
    const p = pointers.get(ev.pointerId);
    if (!p) return;
    const dx = x - p.x, dy = y - p.y;
    p.x = x; p.y = y;
    if (pointers.size === 2 && pinch) {
      const now = pinchState();
      zoomAt(now.mx, now.my, now.dist / Math.max(1, pinch.dist));
      if (!follow) panBy(now.mx - pinch.mx, now.my - pinch.my);
      pinch = now;
      return;
    }
    if (pointers.size !== 1 || !drag) return;
    if (!drag.moved && Math.hypot(x - drag.startX, y - drag.startY) < 5) return;
    if (!drag.moved) {
      drag.moved = true;
      canvas.classList.add("dragging");
      if (follow) setFollow(null);   // dragging the map ends the lock
    }
    panBy(dx, dy);
  });
  function endPointer(ev) {
    if (!pointers.has(ev.pointerId)) return;
    pointers.delete(ev.pointerId);
    if (pointers.size < 2) pinch = null;
    if (pointers.size === 0) {
      canvas.classList.remove("dragging");
      if (drag && !drag.moved && ev.type === "pointerup") {
        selected = drag.hit;
        if (drag.hit) scrollRowIntoView(drag.hit);
        updateList();
        dirty = true;
      }
      drag = null;
    }
  }
  canvas.addEventListener("pointerup", endPointer);
  canvas.addEventListener("pointercancel", endPointer);
  canvas.addEventListener("pointerleave", (ev) => { if (ev.pointerType === "mouse") $("cursor-pos").textContent = ""; });
  canvas.addEventListener("dblclick", (ev) => {
    const [x, y] = localPoint(ev);
    const hit = hitTest(x, y);
    if (hit) setFollow(follow === hit ? null : hit);   // double-click on the locked AMR unlocks
    else zoomAt(x, y, 2);
  });
  canvas.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    const [x, y] = localPoint(ev);
    const delta = ev.deltaY * (ev.deltaMode === 1 ? 16 : ev.deltaMode === 2 ? 400 : 1);
    zoomAt(x, y, Math.exp(-delta * 0.0015));
  }, { passive: false });

  window.addEventListener("keydown", (ev) => {
    const tag = (ev.target.tagName || "").toLowerCase();
    if (!$("drawer").hidden) { if (ev.key === "Escape") closeSettings(); return; }
    if (!helpMenu.hidden && ev.key === "Escape") { setHelpMenu(false); helpBtn.focus(); return; }
    if (tag === "input" || tag === "select" || tag === "textarea" || ev.ctrlKey || ev.metaKey || ev.altKey) return;
    if (tour && (ev.key === "ArrowLeft" || ev.key === "ArrowRight" || ev.key === "Escape")) {   // the tutorial has the arrows
      ev.preventDefault();
      if (ev.key === "Escape") endTour(); else stepTour(ev.key === "ArrowRight" ? 1 : -1);
      return;
    }
    const step = Math.min(size.w, size.h) * 0.15;
    const keys = {
      "+": () => zoomAt(size.w / 2, size.h / 2, 1.5), "=": () => zoomAt(size.w / 2, size.h / 2, 1.5),
      "-": () => zoomAt(size.w / 2, size.h / 2, 1 / 1.5), "_": () => zoomAt(size.w / 2, size.h / 2, 1 / 1.5),
      "0": fitView, "f": fitView, "F": fitView, "n": northUp, "N": northUp,
      "Escape": () => setFollow(null),
      "ArrowLeft": () => { setFollow(null); panBy(step, 0); }, "ArrowRight": () => { setFollow(null); panBy(-step, 0); },
      "ArrowUp": () => { setFollow(null); panBy(0, step); }, "ArrowDown": () => { setFollow(null); panBy(0, -step); },
    };
    if (keys[ev.key]) { keys[ev.key](); ev.preventDefault(); }
  });

  function miniToWorld(ev) {
    const r = mini.getBoundingClientRect();
    const px = (ev.clientX - r.left) / miniGeo.s, py = (ev.clientY - r.top) / miniGeo.s;
    return [mapInfo.left + px * mapInfo.resolution, mapInfo.top - py * mapInfo.resolution];
  }
  let miniDrag = false;
  mini.addEventListener("pointerdown", (ev) => {
    if (!mapInfo) return;
    miniDrag = true;
    mini.setPointerCapture(ev.pointerId);
    setFollow(null);
    [view.cx, view.cy] = miniToWorld(ev);
    dirty = true;
  });
  mini.addEventListener("pointermove", (ev) => { if (miniDrag && mapInfo) { [view.cx, view.cy] = miniToWorld(ev); dirty = true; } });
  mini.addEventListener("pointerup", () => { miniDrag = false; });
  mini.addEventListener("pointercancel", () => { miniDrag = false; });

  function northUp() {
    if (follow && headingUp) { headingUp = false; prefs.set("headingUp", false); $("heading-up").checked = false; }
    view.rot = 0;
    dirty = true;
  }

  // ---- toolbar ---------------------------------------------------------------------------------
  $("btn-zoom-in").addEventListener("click", () => zoomAt(size.w / 2, size.h / 2, 1.5));
  $("btn-zoom-out").addEventListener("click", () => zoomAt(size.w / 2, size.h / 2, 1 / 1.5));
  $("btn-fit").addEventListener("click", fitView);
  $("btn-north").addEventListener("click", northUp);
  $("btn-unfollow").addEventListener("click", () => setFollow(null));
  $("heading-up").addEventListener("change", (ev) => {
    headingUp = ev.target.checked;
    prefs.set("headingUp", headingUp);
    if (!headingUp) view.rot = 0;
    dirty = true;
  });
  function setLabels(on) {
    showLabels = on;
    prefs.set("labels", on);
    $("btn-labels").setAttribute("aria-pressed", String(on));
    dirty = true;
  }
  $("btn-labels").addEventListener("click", () => setLabels(!showLabels));
  $("btn-minimap").addEventListener("click", () => { showMinimap = !showMinimap; prefs.set("minimap", showMinimap); layoutMinimap(); dirty = true; });
  function setPanel(open, remember = true) {
    $("panel").hidden = !open;
    if (remember) prefs.set("panel", open);
    requestAnimationFrame(resize);
  }
  $("btn-panel").addEventListener("click", () => setPanel($("panel").hidden));
  $("amr-filter").addEventListener("input", updateList);

  // ---- settings drawer (form generated from defaults + rules) ------------------------------
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
      if (rule.format === "color") {
        container.append(colorField(id, full, label, rule, Array.isArray(def)));
        continue;
      }
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

  // A color: picker + hex text field kept in step; the text field carries data-path (it is the value).
  function colorInput(value, onChange) {
    const picker = el("input", { type: "color", class: "color-picker", "aria-label": "Pick a color" });
    const text = el("input", { type: "text", class: "color-hex", maxlength: "7", spellcheck: "false", placeholder: "#rrggbb" });
    const sync = (v) => { text.value = v; if (HEX.test(v)) picker.value = v.toLowerCase(); };
    picker.addEventListener("input", () => { text.value = picker.value; onChange(); });
    text.addEventListener("input", () => { if (HEX.test(text.value)) picker.value = text.value.toLowerCase(); onChange(); });
    sync(value || "#000000");
    return { node: el("span", { class: "color-input" }, picker, text), picker, text, sync };
  }
  function colorField(id, path, label, rule, isList) {
    const field = el("div", { class: "field" + (isList ? " field-wide" : "") });
    if (!isList) {
      const c = colorInput("#000000", previewStyle);
      c.text.id = id;
      c.text.dataset.path = path;
      c.text.dataset.kind = "string";
      c.text.syncColor = c.sync;
      field.append(el("label", { for: id, text: label }), c.node);
    } else {
      const box = el("div", { class: "color-list", id });
      box.dataset.path = path;
      box.dataset.kind = "colors";
      box.dataset.max = rule.maxItems || 16;
      box.dataset.min = rule.minItems || 1;
      field.append(el("label", { text: label }), box);
    }
    if (rule.help) field.append(el("div", { class: "help", text: rule.help }));
    return field;
  }
  function fillColorList(box, values) {
    const items = values.map((v) => {
      const c = colorInput(v, previewStyle);
      const remove = el("button", { type: "button", class: "btn-icon btn-remove", title: "Remove this color", "aria-label": "Remove color" }, icon("close"));
      remove.addEventListener("click", () => {
        if (box.querySelectorAll(".color-item").length > Number(box.dataset.min)) { item.remove(); previewStyle(); updateColorListButtons(box); }
      });
      const item = el("span", { class: "color-item" }, c.node, remove);
      return item;
    });
    const add = el("button", { type: "button", class: "btn btn-small btn-add", text: "+ Color" });
    add.addEventListener("click", () => {
      const current = readColorList(box);
      const defaults = (meta && meta.defaults.style.amrColors) || ["#888888"];
      fillColorList(box, current.concat([defaults[current.length % defaults.length]]));
      previewStyle();
    });
    box.replaceChildren(...items, add);
    updateColorListButtons(box);
  }
  function updateColorListButtons(box) {
    const n = box.querySelectorAll(".color-item").length;
    for (const b of box.querySelectorAll(".btn-remove")) b.disabled = !canWrite || n <= Number(box.dataset.min);
    const add = box.querySelector(".btn-add");
    if (add) add.disabled = !canWrite || n >= Number(box.dataset.max);
  }
  function readColorList(box) { return [...box.querySelectorAll(".color-hex")].map((t) => t.value.trim()); }
  function previewStyle() {   // live preview while editing; Revert / closing restores the saved one
    if (!loaded) return;
    applyStyle(readForm().style);
  }
  function defaultColors() {
    const style = meta.defaults.style;
    const current = readForm();
    current.style = JSON.parse(JSON.stringify(style));
    fillForm(current, true);
    previewStyle();
  }

  function getAt(obj, path) { return path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj); }
  function setAt(obj, path, value) {
    const parts = path.split("."); let o = obj;
    for (const p of parts.slice(0, -1)) { if (typeof o[p] !== "object" || o[p] === null) o[p] = {}; o = o[p]; }
    o[parts[parts.length - 1]] = value;
  }

  function fillForm(settings, keepErrors) {
    for (const input of document.querySelectorAll("#settings-fields [data-path]")) {
      const v = getAt(settings, input.dataset.path);
      if (input.dataset.kind === "colors") fillColorList(input, Array.isArray(v) ? v : []);
      else if (input.type === "checkbox") input.checked = !!v;
      else if (input.dataset.kind === "array") input.value = JSON.stringify(v ?? []);
      else if (input.syncColor) input.syncColor(v ?? "");
      else input.value = v ?? "";
      input.disabled = !canWrite;
      if (!keepErrors) input.closest(".field").classList.remove("invalid");
    }
    for (const b of document.querySelectorAll("#settings-fields .color-picker, #settings-fields .color-hex")) b.disabled = !canWrite;
    for (const id of ["btn-save", "btn-revert", "btn-defaults"]) $(id).disabled = !canWrite;
  }

  function readForm() {
    const result = JSON.parse(JSON.stringify(loaded)); // keeps unknown keys
    for (const input of document.querySelectorAll("#settings-fields [data-path]")) {
      let v;
      if (input.type === "checkbox") v = input.checked;
      else if (input.dataset.kind === "number") v = input.value === "" ? NaN : Number(input.value);
      else if (input.dataset.kind === "array") { try { v = JSON.parse(input.value || "[]"); } catch (e) { v = input.value; } }
      else if (input.dataset.kind === "colors") v = readColorList(input);
      else v = input.value.trim();
      setAt(result, input.dataset.path, v);
    }
    return result;
  }

  function showErrors(errors) {
    const list = $("settings-errors");
    list.replaceChildren(...(errors || []).map((m) => el("li", { text: m })));
    list.hidden = !errors || !errors.length;
    for (const input of document.querySelectorAll("#settings-fields [data-path]")) {
      const bad = (errors || []).some((m) => m.startsWith(input.dataset.path + ":") || m.startsWith(input.dataset.path + "["));
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
        const styleSet = $("f-style-accent") && $("f-style-accent").closest("fieldset");
        if (styleSet) {
          const btn = el("button", { type: "button", class: "btn btn-small", id: "btn-default-colors", text: "Default colors" });
          btn.addEventListener("click", defaultColors);
          styleSet.querySelector("legend").after(el("div", { class: "fieldset-tools" }, btn,
            el("span", { class: "muted", text: "Changes show right away; Save keeps them for everyone." })));
        }
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
      viewConfigJson = "";
      applyViewConfig(viewConfigFrom(loaded));
      fillForm(loaded);
      showErrors([]);
      $("save-state").textContent = "Saved " + new Date().toLocaleTimeString();
      loadMap();
    } catch (e) {
      showErrors((e.data && e.data.errors) || [e.message]);
      $("save-state").textContent = "Not saved";
    }
  }

  async function resetDefaults() {
    if (!confirm("Reset all settings to their defaults?")) return;
    try {
      loaded = await api("POST", "/settings/reset");
      viewConfigJson = "";
      applyViewConfig(viewConfigFrom(loaded)); fillForm(loaded); showErrors([]);
      $("save-state").textContent = "Defaults restored";
      loadMap();
    } catch (e) { showErrors([e.message]); }
  }

  function openSettings() {
    if (!canWrite) return;   // read-only users: no settings (the API answers 403 anyway)
    $("drawer").hidden = false;
    $("save-state").textContent = "";
    loadSettings();
  }
  function closeSettings() {
    $("drawer").hidden = true;
    if (loaded) applyStyle(loaded.style);   // drop an unsaved preview
    canvas.focus({ preventScroll: true });
  }

  $("settings-form").addEventListener("submit", saveSettings);
  $("btn-revert").addEventListener("click", () => { fillForm(loaded); applyStyle(loaded.style); showErrors([]); $("save-state").textContent = ""; });
  $("btn-defaults").addEventListener("click", resetDefaults);
  $("btn-settings").addEventListener("click", openSettings);
  $("btn-close-settings").addEventListener("click", closeSettings);

  // ---- help menu ---------------------------------------------------------------------------------
  const helpBtn = $("btn-help"), helpMenu = $("help-menu");
  function setHelpMenu(open) {
    helpMenu.hidden = !open;
    helpBtn.setAttribute("aria-expanded", String(open));
    if (open) helpMenu.querySelector("[role=menuitem]").focus();
  }
  helpBtn.addEventListener("click", () => setHelpMenu(helpMenu.hidden));
  helpMenu.addEventListener("click", () => setHelpMenu(false));     // an item was chosen
  helpMenu.addEventListener("keydown", (ev) => {
    if (ev.key !== "ArrowDown" && ev.key !== "ArrowUp") return;
    ev.preventDefault();
    ev.stopPropagation();
    const items = [...helpMenu.querySelectorAll("[role=menuitem]")];
    const i = items.indexOf(document.activeElement);
    items[(i + (ev.key === "ArrowDown" ? 1 : items.length - 1)) % items.length].focus();
  });
  document.addEventListener("pointerdown", (ev) => {
    if (!helpMenu.hidden && !ev.target.closest(".menu-wrap")) setHelpMenu(false);
  });
  $("btn-tutorial").addEventListener("click", startTour);

  // ---- tutorial ----------------------------------------------------------------------------------
  // A card at the bottom of the map steps through the controls; the control of the step blinks - a ring
  // laid over it (position: fixed), so nothing clips it. Everything stays usable while the card is open;
  // the AMR steps use an example AMR (it pulses on the map) and "Show me" does the step's action.
  let tour = null;          // {i, amr, step}
  let tourPulse = null;     // id of the AMR that pulses on the map
  const ring = $("tour-ring"), tourCard = $("tour");
  const tourRow = () => (tour && tour.amr ? rows.get(tour.amr) || {} : {});
  const TOUR = [
    { title: "Welcome",
      text: "This tour shows the controls of the AMR Fleet View. Step through it with the arrows (or the ← → " +
        "keys); the control of each step blinks. Everything stays usable - try the controls while the tour is open." },
    { title: "Map", panel: false, target: () => canvas,
      text: "The floor plan with the AMRs, live; the point of an AMR shows where it is heading. Drag to move the " +
        "map, use the mouse wheel or pinch to zoom. Click an AMR to select it, double-click it to lock onto it." },
    { title: "AMR list", panel: true, target: () => $("panel"),
      text: "Every AMR with its state and battery. The filter finds AMRs by id, name or state; the button at the " +
        "top left shows and hides the list." },
    { title: "Details", panel: true, amr: true, target: () => tourRow().head,
      act: () => { if (selected !== tour.amr) toggleSelected(tour.amr); },
      text: "Click the tile of an AMR to open its details: position, heading, speed, last update and the extra " +
        "information of the fleet data. Click it again to close them." },
    { title: "Focus", panel: true, amr: true, showsMap: true, target: () => tourRow().centerBtn, act: () => centerOn(tour.amr),
      text: "The crosshair moves the map to this AMR (it pulses on the map now). The map stays there - it does " +
        "not follow the AMR." },
    { title: "Lock", panel: true, amr: true, showsMap: true, target: () => tourRow().followBtn,
      act: () => { if (follow !== tour.amr) setFollow(tour.amr); },
      text: "The lock follows the AMR: it stays in the middle and the map moves around it; with “Rotate map " +
        "with AMR” it also keeps facing up. Unlock in the bar at the top, or drag the map, to end it." },
    { title: "Path", panel: true, amr: true, showsMap: true, when: () => cfg.pathMinutes > 0, target: () => tourRow().pathBtn,
      act: () => { if (!pathOn.has(tour.amr)) togglePath(tour.amr); },
      text: () => "Shows where this AMR drove in the last " + cfg.pathMinutes + " minutes, in a darker shade of " +
        "its color. Switch on several paths to compare them; click again to hide one." },
    { title: "Zoom and view", panel: false, target: () => document.querySelector(".tools .tool-group"),
      text: "Zoom in and out, show the whole map, turn the map back to north up. Keys: + and − zoom, F shows " +
        "the whole map, N turns it north up." },
    { title: "Names and overview", panel: false, target: () => document.querySelectorAll(".tools .tool-group")[1],
      text: "Show or hide the names of the AMRs on the map, and the overview map." },
    { title: "Overview map", panel: false, when: () => !!mapInfo, target: () => (mini.hidden ? $("btn-minimap") : mini),
      text: () => "The whole map with all AMRs; the frame is the part you see. Click or drag in it to move the " +
        "view." + (mini.hidden ? " It is switched off at the moment - the blinking button shows it." : "") },
    { title: "Status", target: () => ["sim-badge", "fleet-pill", "conn"].map($),
      text: "Whether the AMR data is current (number of AMRs, “live” or the time since the last update), " +
        "“Simulation” while the AMRs come from the built-in simulation, and the Data Layer connection." },
    { title: "Settings", when: () => canWrite, target: () => $("btn-settings"),
      text: "Map file and units, the fleet data, colors, the path history and the simulation. Only users who " +
        "may change settings see this button." },
    { title: "Help", target: () => helpBtn, text: "Start this tour again, or open the app's page on GitHub." },
  ];
  const tourSteps = () => TOUR.filter((st) => !st.when || st.when());
  function pickTourAmr() {
    return [selected, follow].find((id) => id && rows.has(id)) || rows.keys().next().value || null;
  }
  const narrowLayout = () => window.matchMedia("(max-width: 760px)").matches;   // the list floats over the map
  function renderTour() {
    const steps = tourSteps();
    tour.i = clamp(tour.i, 0, steps.length - 1);
    const st = tour.step = steps[tour.i];
    if (st.amr && !rows.has(tour.amr)) tour.amr = pickTourAmr();
    if (st.panel && $("panel").hidden) setPanel(true, false);
    else if (st.panel === false && narrowLayout() && !$("panel").hidden) setPanel(false, false);
    $("tour-count").textContent = "Step " + (tour.i + 1) + " of " + steps.length;
    $("tour-title").textContent = st.title;
    $("tour-text").textContent = (typeof st.text === "function" ? st.text() : st.text)
      + (st.amr && !tour.amr ? " (There is no AMR in the list at the moment.)" : "");
    $("tour-prev").disabled = tour.i === 0;
    const last = tour.i === steps.length - 1;
    tourCard.classList.toggle("last", last);
    $("tour-next").setAttribute("aria-label", last ? "End the tutorial" : "Next step");
    $("tour-next").title = last ? "End the tutorial" : "Next step";
    $("tour-try").hidden = !(st.act && tour.amr);
    tourPulse = st.amr ? tour.amr : null;
    if (st.amr && tour.amr) scrollRowIntoView(tour.amr);
    ring.classList.remove("blink");
    void ring.offsetWidth;                  // restart the blinking
    ring.classList.add("blink");
    placeTour();
    dirty = true;
  }
  // every frame while open: the ring follows its control (list rebuilt, scrolled, layout changed)
  function placeTour() {
    if (!tour) return;
    const t = tour.step.target ? tour.step.target() : null;
    const els = (Array.isArray(t) ? t : [t]).filter((e) => e && e.getClientRects().length);
    if (!els.length) { ring.hidden = true; tourCard.classList.remove("at-top"); return; }
    const rs = els.map((e) => e.getBoundingClientRect());
    const x0 = Math.max(2, Math.min(...rs.map((r) => r.left)) - 4), y0 = Math.max(2, Math.min(...rs.map((r) => r.top)) - 4);
    const x1 = Math.min(innerWidth - 2, Math.max(...rs.map((r) => r.right)) + 4);
    const y1 = Math.min(innerHeight - 2, Math.max(...rs.map((r) => r.bottom)) + 4);
    Object.assign(ring.style, { left: x0 + "px", top: y0 + "px", width: (x1 - x0) + "px", height: (y1 - y0) + "px" });
    ring.hidden = false;
    // the card goes to the top of the map when it would cover the control (not for the whole map)
    const m = $("mapwrap").getBoundingClientRect(), cw = tourCard.offsetWidth, ch = tourCard.offsetHeight;
    const cx0 = m.left + (m.width - cw) / 2, cy1 = m.bottom - 8, cy0 = cy1 - ch - 8;
    const covers = x0 < cx0 + cw && x1 > cx0 && y0 < cy1 && y1 > cy0;
    tourCard.classList.toggle("at-top", covers && (x1 - x0) * (y1 - y0) < 0.5 * m.width * m.height);
  }
  function tourLoop() { if (tour) { placeTour(); requestAnimationFrame(tourLoop); } }
  function startTour() {
    if (!$("drawer").hidden) closeSettings();
    const running = !!tour;
    tour = { i: 0, amr: pickTourAmr(), step: null };
    tourCard.hidden = false;
    renderTour();
    if (!running) requestAnimationFrame(tourLoop);
    $("tour-next").focus();
  }
  function endTour() {
    tour = null;
    tourPulse = null;
    tourCard.hidden = true;
    ring.hidden = true;
    dirty = true;
    canvas.focus({ preventScroll: true });
  }
  function stepTour(delta) {
    if (tour.i + delta >= tourSteps().length) { endTour(); return; }
    tour.i = Math.max(0, tour.i + delta);
    renderTour();
  }
  $("tour-prev").addEventListener("click", () => stepTour(-1));
  $("tour-next").addEventListener("click", () => stepTour(1));
  $("tour-close").addEventListener("click", endTour);
  $("tour-try").addEventListener("click", () => {
    if (!tour || !tour.step.act || !amrs.has(tour.amr)) return;
    tour.step.act();
    if (tour.step.showsMap && narrowLayout()) setPanel(false, false);   // the result is on the map
  });

  // ---- start ---------------------------------------------------------------------------------
  (async function init() {
    wallHex = (colors["map-wall"] || "#333d4a").replace("#", "").slice(0, 6);
    setLabels(showLabels);
    $("heading-up").checked = headingUp;
    const narrow = window.matchMedia("(max-width: 760px)").matches;
    $("panel").hidden = !prefs.get("panel", !narrow);
    new ResizeObserver(() => { resize(); layoutMinimap(); }).observe($("mapwrap"));
    resize();
    await refreshStatus();
    await loadViewConfig();
    await loadMap();
    updateList();
    requestAnimationFrame(frame);
    pollFleet();
    setInterval(() => { refreshStatus(); loadViewConfig(); }, 5000);   // picks up style changes of others
    if (location.hash === "#settings") openSettings();
  })();
})();
