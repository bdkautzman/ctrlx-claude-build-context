// Core Connector - shared by its pages (index.html, browse.html). The kit's shared UI parts (token + API calls,
// roles, settings drawer, help menu + tutorial, About, light/dark) are in kit.js, loaded before this file; CC keeps
// the names the pages use and adds this app's own helpers.
"use strict";

window.CC = (function () {
  const LOCAL_ID = "_local";
  // web UI of a core: configured ones from the server, this device = the page's own origin
  function webUrl(core) { return core.local ? location.origin + "/" : core.webUrl; }
  function coreTitle(core) { return core.local ? "This device" : core.name; }
  return {
    API: Kit.API, LOCAL_ID, token: Kit.token, $: Kit.$, api: Kit.api, el: Kit.el, icon: Kit.icon, showBanner: Kit.showBanner,
    fmtDuration: Kit.fmtDuration, can: Kit.can, webUrl, coreTitle, setupHelp: Kit.setupHelp,
  };
})();
