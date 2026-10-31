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

  return { API, LOCAL_ID, token, $, api, el, icon, showBanner, fmtDuration, webUrl, coreTitle };
})();
