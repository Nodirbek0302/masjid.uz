/* masjid-uz web client. Runs in browsers, mobile webviews and as a Telegram Mini App.
 * All user-supplied text is inserted with textContent (never innerHTML). UI text comes from i18n.js. */
(function () {
  "use strict";
  var T = I18N.t;
  var PRAYERS = ["fajr", "dhuhr", "asr", "maghrib", "isha"];      // five daily prayers (no separate Friday time)
  var $ = function (id) { return document.getElementById(id); };

  // ---- identity: random client id (per device) + Telegram initData when inside Mini App
  var clientId;
  try { clientId = localStorage.getItem("cid"); } catch (e) {}
  if (!clientId) {
    clientId = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + "-" + Math.random().toString(16).slice(2));
    try { localStorage.setItem("cid", clientId); } catch (e) {}
  }
  var tg = null;
  // Inside Telegram the language chosen in the bot decides: ask the server once per session (the bot's links also carry ?lang=)
  var tgData = "";
  try { tgData = new URLSearchParams(location.hash.slice(1)).get("tgWebAppData") || ""; } catch (e) {}
  try {
    if (tgData && !sessionStorage.getItem("langSync")) {
      sessionStorage.setItem("langSync", "1");
      fetch("/api/me", { headers: { "X-Telegram-Init-Data": tgData } }).then(function (r) { return r.json(); }).then(function (j) {
        if (j && j.lang && j.lang !== I18N.lang()) { I18N.set(j.lang); location.reload(); }
      }).catch(function () {});
    }
  } catch (e) {}
  if (location.hash.indexOf("tgWebAppData") >= 0) {         // load Telegram script only inside Telegram
    var sc = document.createElement("script");
    sc.src = "https://telegram.org/js/telegram-web-app.js";
    sc.onload = function () { tg = window.Telegram && window.Telegram.WebApp; if (tg) { tg.ready(); tg.expand(); } };
    document.head.appendChild(sc);
  }

  function api(method, path, body) {
    var h = { "Content-Type": "application/json", "X-Client-Id": clientId, "X-Client-Platform": "web", "ngrok-skip-browser-warning": "1" };   // header lets API calls pass ngrok's free-plan warning page
    if (tg && tg.initData) h["X-Telegram-Init-Data"] = tg.initData;
    return fetch(path, { method: method, headers: h, body: body ? JSON.stringify(body) : undefined })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) { j.status = r.status; throw j; } return j; }); });
  }

  function el(tag, props, children) {
    var n = document.createElement(tag);
    Object.keys(props || {}).forEach(function (k) {
      if (k === "text") n.textContent = props[k];
      else if (k.slice(0, 2) === "on") n.addEventListener(k.slice(2), props[k]);
      else n.setAttribute(k, props[k]);
    });
    (children || []).forEach(function (c) { if (c) n.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return n;
  }
  function toast(msg) {
    var t = $("toast"); t.textContent = msg; t.hidden = false;
    clearTimeout(toast.t); toast.t = setTimeout(function () { t.hidden = true; }, 3600);
  }
  function errText(e) { return I18N.errMessage(e); }
  function btn(cls, text, onclick) { return el("button", { class: "btn " + cls, type: "button", text: text, onclick: onclick }); }

  // ---- labels: must reflect real status, never imply verification
  function label(m) {
    if (m.status === "verified") return [T("status.verified"), "verified"];
    if (m.status === "reported") return [T("status.reported"), "reported"];
    if (m.origin === "community") return [T("status.community"), ""];
    return null;                                    // plain unverified record: no badge (it never claims to be verified)
  }

  // ---- navigation: opens an external app/site; nothing is requested from or stored by Yandex
  function yandexLinks(m) {
    return [el("a", { class: "btn ghost", target: "_blank", rel: "noopener noreferrer",
      href: "https://yandex.com/maps/?rtext=~" + m.lat + "," + m.lon + "&rtt=auto", text: "🧭 " + T("link.route") })];
  }

  // ---- own mosque icon (drawn here; no third-party artwork): green badge with dome and minaret
  function mosqueIcon(color) {
    var S = 64, c = document.createElement("canvas"); c.width = c.height = S;
    var g = c.getContext("2d");
    g.fillStyle = color; g.strokeStyle = "#fff"; g.lineWidth = 4;
    g.beginPath(); g.arc(32, 32, 28, 0, 7); g.fill(); g.stroke();
    g.fillStyle = "#fff";
    g.beginPath(); g.moveTo(14, 46); g.lineTo(14, 36); g.arc(27, 36, 13, Math.PI, 0); g.lineTo(40, 46); g.closePath(); g.fill();   // dome + base
    g.fillRect(27 - 1, 16, 2, 6);                                                                                                    // finial
    g.fillRect(43, 24, 5, 22);                                                                                                       // minaret
    g.beginPath(); g.moveTo(43, 24); g.lineTo(45.5, 18); g.lineTo(48, 24); g.fill();
    g.fillStyle = color; g.beginPath(); g.moveTo(24, 46); g.lineTo(24, 40); g.arc(27, 40, 3, Math.PI, 0); g.lineTo(30, 46); g.fill();  // door
    return g.getImageData(0, 0, S, S);
  }

  // ---- map
  var map, cfg = { style_url: "https://tiles.openfreemap.org/styles/liberty", duplicate_radius_m: 50 };
  var mode = "browse", picked = null, pickMarker = null, popup = null;

  function emptyFC() { return { type: "FeatureCollection", features: [] }; }
  // ---- fast start ------------------------------------------------------------------------------
  // Everything that can run in parallel does: style JSON, /api/config and the first mosque query start at
  // once (no waiting for the map "load" event). The map opens at the last known position (cached) or the
  // device location, so the first tiles are already the right ones, and only a padded window is fetched;
  // moving the map extends that window on demand.
  var DEFAULT_STYLE = "https://tiles.openfreemap.org/styles/liberty";
  var REGION = [[54, 36], [75.5, 47]];            // Uzbekistan + margin: panning is bounded, so no world-scale tile loading
  var TASHKENT = [69.2797, 41.3111];
  var stylePromise = fetch(DEFAULT_STYLE, { headers: { "ngrok-skip-browser-warning": "1" } }).then(function (r) {
    if (!r.ok) throw new Error("style HTTP " + r.status);
    return r.json();
  });
  stylePromise.catch(function () {});               // handled below; avoids an unhandled-rejection warning
  function mapProblem(text) { $("maperr-text").textContent = text; $("maperr").hidden = false; }
  $("maperr-retry").onclick = function () { location.reload(); };
  var configPromise = api("GET", "/api/config").catch(function () { return cfg; });

  function savedPos() {
    try { var p = JSON.parse(localStorage.getItem("lastpos")); if (p && isFinite(p.lat) && isFinite(p.lon)) return p; } catch (e) {}
    return null;
  }
  function inRegion(lon, lat) { return lon >= REGION[0][0] && lon <= REGION[1][0] && lat >= REGION[0][1] && lat <= REGION[1][1]; }

  var loadedBox = null, mosqueSeq = 0, moveTimer = null, userMoved = false, lastFC = emptyFC();
  function applyMosques() {
    var src = map && map.getSource("mosques");
    if (src) src.setData(lastFC); else if (map && map.engine !== "yandex") map.once("styledata", applyMosques);
  }
  function fetchMosques(box) {                      // box = [west, south, east, north]
    var seq = ++mosqueSeq;
    return api("GET", "/api/mosques?bbox=" + box.join(",")).then(function (r) {
      if (seq !== mosqueSeq) return;                // a newer request superseded this one
      loadedBox = box;
      lastFC = { type: "FeatureCollection", features: r.items.map(function (m) {
        return { type: "Feature", geometry: { type: "Point", coordinates: [m.lon, m.lat] },
          properties: { id: m.id, name: m.name || "", status: m.status } };
      }) };
      applyMosques();
    }).catch(function () {});
  }
  function loadMosques() {                          // force-refresh the current window
    loadedBox = null; ensureMosques();
  }
  function ensureMosques() {
    var b = map.getBounds(), w = b.getWest(), s = b.getSouth(), e = b.getEast(), n = b.getNorth();
    if (loadedBox && w >= loadedBox[0] && s >= loadedBox[1] && e <= loadedBox[2] && n <= loadedBox[3]) return;   // already covered
    var px = (e - w) * 0.5, py = (n - s) * 0.5;      // fetch 50% extra on every side
    fetchMosques([w - px, s - py, e + px, n + py].map(function (v) { return +v.toFixed(5); }));
  }

  // Map labels follow the interface language. The base style prints "latin + native"; we pick one name per language.
  function labelExpr(lang) {
    if (lang === "ru") return ["coalesce", ["get", "name:ru"], ["get", "name:nonlatin"], ["get", "name"]];
    if (lang === "uz-cyrl") return ["coalesce", ["get", "name:nonlatin"], ["get", "name"]];
    if (lang === "uz") return ["coalesce", ["get", "name:latin"], ["get", "name_en"], ["get", "name"]];
    return null;                                    // English: keep the style's own expression
  }

  function patchStyle(style, c) {
    // Only mosques should be visible: drop the base style's other POIs; add our own sources/layers up front
    // so nothing has to wait for the "load" event.
    style.layers = style.layers.filter(function (l) { return l["source-layer"] !== "poi" && l["source-layer"] !== "housenumber"; });
    var expr = labelExpr(I18N.lang());
    if (expr) style.layers.forEach(function (l) {
      var tf = l.layout && l.layout["text-field"];
      if (Array.isArray(tf) && JSON.stringify(tf).indexOf("name:nonlatin") >= 0) l.layout["text-field"] = expr;
    });
    style.sources.mosques = { type: "geojson", data: lastFC };
    var font = ["Noto Sans Regular"];
    var halo = isDark() ? { "text-color": "#d1fae5", "text-halo-color": "#101416", "text-halo-width": 1.6 } : { "text-color": "#064e3b", "text-halo-color": "#fff", "text-halo-width": 1.6 };
    if (c.show_tile_mosques !== false && style.sources.openmaptiles) {
      // Mosques already present in the OSM tiles, drawn live and never stored. The tile schema has no religion
      // field, so this matches on the name (masjid/мечеть/...): an interim, incomplete heuristic.
      var nm = ["downcase", ["coalesce", ["get", "name"], ""]];
      var words = ["masjid", "machit", "mechet", "mosque", "jome", "jame", "мечеть", "масжид", "масджид", "мачит", "жоме", "жаме", "مسجد"];
      style.layers.push({ id: "osm-mosques", type: "symbol", source: "openmaptiles", "source-layer": "poi", minzoom: 12,
        filter: ["all", ["==", ["get", "class"], "place_of_worship"], ["any"].concat(words.map(function (w) { return ["in", w, nm]; }))],
        layout: { "icon-image": "mosque-osm", "icon-size": ["interpolate", ["linear"], ["zoom"], 12, 0.6, 16, 1.1], "icon-allow-overlap": true,
          "text-field": ["step", ["zoom"], "", 14, ["get", "name"]], "text-font": font, "text-size": 11, "text-offset": [0, 1.5], "text-anchor": "top", "text-optional": true },
        paint: halo });
    }
    style.layers.push({ id: "mosques", type: "symbol", source: "mosques",
      layout: { "icon-image": ["match", ["get", "status"], "reported", "mosque-amber", "mosque-green"],
        "icon-size": ["interpolate", ["linear"], ["zoom"], 8, 0.75, 14, 1.25], "icon-allow-overlap": true,
        "text-field": ["step", ["zoom"], "", 13, ["get", "name"]], "text-font": font, "text-size": 12, "text-offset": [0, 1.6], "text-anchor": "top", "text-optional": true },
      paint: halo });
    return style;
  }

  // ---- night mode: the choice is remembered; until the user chooses, the phone's own setting is followed ----
  var theme = null;
  try { theme = localStorage.getItem("theme"); } catch (e) {}
  var darkMQ = window.matchMedia ? matchMedia("(prefers-color-scheme: dark)") : null;
  function isDark() { return theme ? theme === "dark" : !!(darkMQ && darkMQ.matches); }
  function applyTheme() {
    document.documentElement.dataset.theme = isDark() ? "dark" : "light";
    var m = document.querySelector('meta[name="theme-color"]'); if (m) m.content = isDark() ? "#12211e" : "#047857";
    if (map && map.engine === "yandex") map.setDark(isDark());
    else if (map && rawStyle) map.setStyle(buildStyle());                       // the map is recoloured at the same view
  }
  function toggleTheme() { theme = isDark() ? "light" : "dark"; try { localStorage.setItem("theme", theme); } catch (e) {} applyTheme(); }
  if (darkMQ && darkMQ.addEventListener) darkMQ.addEventListener("change", function () { if (!theme) applyTheme(); });

  // The base map is light only: for night its colours are recomputed (the same rules as the mobile app): land, water and
  // buildings become a neutral slate, roads stay subtly visible, text turns light with a dark halo. Real hues are kept, quieter.
  var HEX = /^#([0-9a-fA-F]{3,8})$/, FN = /^(rgba?|hsla?)\(([^)]*)\)$/;
  function parseColor(c) {
    var r, g, b, a = 1, m = HEX.exec(c.trim());
    if (m) {
      var x = m[1]; if (x.length <= 4) x = x.replace(/./g, "$&$&");
      r = parseInt(x.substr(0, 2), 16) / 255; g = parseInt(x.substr(2, 2), 16) / 255; b = parseInt(x.substr(4, 2), 16) / 255;
      if (x.length === 8) a = parseInt(x.substr(6, 2), 16) / 255;
    } else {
      m = FN.exec(c.trim()); if (!m) return null;
      var p = m[2].split(/[,\s/]+/).filter(Boolean); if (p.length < 3) return null;
      var num = function (e, k) { return /%$/.test(e) ? parseFloat(e) / 100 * k : parseFloat(e); };
      if (/^rgb/.test(m[1])) { r = num(p[0], 255) / 255; g = num(p[1], 255) / 255; b = num(p[2], 255) / 255; if (p.length > 3) a = num(p[3], 1); }
      else return [parseFloat(p[0]) % 360, Math.min(1, Math.max(0, num(p[1], 1))), Math.min(1, Math.max(0, num(p[2], 1))), p.length > 3 ? num(p[3], 1) : 1];
    }
    var mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2, d = mx - mn;
    if (d === 0) return [0, 0, l, a];
    var s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
    var h = mx === r ? (g - b) / d + (g < b ? 6 : 0) : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return [h * 60, s, l, a];
  }
  function darkenOne(c, prop) {
    var p = parseColor(c); if (!p) return null;
    var h0 = p[0], s0 = p[1], l = p[2], a = p[3];
    var warm = s0 < 0.12 || h0 <= 75 || h0 >= 345;
    var h = warm ? 210 : h0, nl, ns;
    if (/^(text|icon)/.test(prop)) {
      if (prop.indexOf("halo") >= 0) { nl = 0.06 + 0.1 * l; ns = warm ? 0.12 : s0 * 0.5; }
      else { nl = 0.97 - 0.28 * l; ns = warm ? 0.1 : s0 * 0.35; }
    } else if (/^line/.test(prop)) {
      if (warm && s0 >= 0.55) { nl = 0.26 + 0.18 * l; ns = 0.12; }
      else if (warm) { nl = 0.15 + 0.2 * l; ns = 0.07; }
      else { nl = 0.15 + 0.2 * l; ns = s0 * 0.6; }
    } else { nl = 0.075 + 0.12 * l; ns = warm ? 0.1 : s0 * 0.32; }
    return "hsla(" + h.toFixed(1) + "," + (ns * 100).toFixed(1) + "%," + (nl * 100).toFixed(1) + "%," + a.toFixed(3) + ")";
  }
  function darkenColors(v, prop) {
    if (typeof v === "string") return darkenOne(v, prop) || v;
    if (Array.isArray(v)) return v.map(function (e) { return darkenColors(e, prop); });
    if (v && typeof v === "object") { var o = {}; Object.keys(v).forEach(function (k) { o[k] = darkenColors(v[k], prop); }); return o; }
    return v;
  }
  function darkenStyle(style) {
    style.layers = style.layers.filter(function (l) { return !/^(highway-shield|road_shield)/.test(l.id); });   // their light plates would glare at night
    style.layers.forEach(function (l) {
      if (l.paint) Object.keys(l.paint).forEach(function (k) { if (/-color$/.test(k)) l.paint[k] = darkenColors(l.paint[k], k); });
    });
    return style;
  }
  var rawStyle = null;
  function buildStyle() {
    var st = JSON.parse(JSON.stringify(rawStyle));
    if (isDark()) darkenStyle(st);
    return patchStyle(st, cfg);
  }

  // ---- pages: home | list | map (body[data-page] decides what is visible), pages are built when first opened ----
  var page = "home", mapStart = null, mapQuery = false;
  function whenMap(fn) { if (map) fn(); else pendingMap.push(fn); }
  var pendingMap = [];
  function setPage(p) {
    page = p;
    try { sessionStorage.setItem("page", p); } catch (e) {}
    document.body.dataset.page = p;
    $("nav-home").classList.toggle("on", p === "home");
    $("nav-mosques").classList.toggle("on", p !== "home");
    var fab = $("view-fab");                                                    // one button: the other way of looking
    var toMap = p === "list";
    fab.querySelector("span").textContent = T(toMap ? "view.map" : "view.list");
    fab.querySelector("svg").innerHTML = toMap
      ? '<path fill="currentColor" d="M9 3 3 5.5v15L9 18l6 3 6-2.5v-15L15 6zm0 2.3 4 2v11.4l-4-2zm-4 .4 2-.8v11.4l-2 .8zm12 .8v11.4l-2 .8V7z"/>'
      : '<path fill="currentColor" d="M4 5h3v3H4zm5 0h11v3H9zm-5 5.5h3v3H4zm5 0h11v3H9zM4 16h3v3H4zm5 0h11v3H9z"/>';
    if (p === "map") { createMap(); if (map) setTimeout(function () { map.resize(); map.easeTo({ padding: mapPadding(), duration: 0 }); }, 0); }
    if (p === "list") ensureList();
    if (p === "home") renderPrayer();
  }
  $("nav-home").onclick = function () { setPage("home"); };
  $("nav-mosques").onclick = function () { if (page === "home") setPage(sessionStorage.getItem("view") === "map" ? "map" : "list"); };
  $("view-fab").onclick = function () { var v = page === "list" ? "map" : "list"; try { sessionStorage.setItem("view", v); } catch (e) {} setPage(v); };
  function showOnMap(id, lon, lat, dist) {                                      // a mosque chosen in the list or the search
    try { sessionStorage.setItem("view", "map"); } catch (e) {}
    setPage("map");
    whenMap(function () {
      if (lon != null) map.jumpTo({ center: [lon, lat], zoom: Math.max(map.getZoom(), 16) });
      openMosque(id, dist != null ? { dist: dist } : null);
    });
  }

  // ---- the "⋮" menu: add a mosque, region, refresh, day/night, language (one popup shared by every page) ----
  var ICON = {
    add: '<path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M12 21s7-6.2 7-12a7 7 0 1 0-14 0c0 5.8 7 12 7 12zM12 6.5v5M9.5 9h5"/>',
    region: '<path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M12 21s7-6.2 7-12a7 7 0 1 0-14 0c0 5.8 7 12 7 12z"/><circle cx="12" cy="9" r="2.4" fill="currentColor"/>',
    refresh: '<path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>',
    moon: '<path fill="currentColor" d="M20 14.5A8 8 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z"/>',
    sun: '<circle cx="12" cy="12" r="4.2" fill="currentColor"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9 17 7M7 17l-2.1 2.1" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
    globe: '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18" fill="none" stroke="currentColor" stroke-width="1.8"/>'
  };
  function svg(name) { var s = el("span"); s.innerHTML = '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">' + ICON[name] + "</svg>"; return s.firstChild; }
  function menuBtn(icon, text, value, onclick, on) {
    var b = el("button", { type: "button", role: "menuitem", class: on ? "on" : "", onclick: onclick }, [icon ? svg(icon) : null, el("span", { text: text }), value ? el("span", { class: "v", text: value }) : null]);
    return b;
  }
  function regionLabel() { return prayerSel !== "me" && REGIONS[+prayerSel] ? regionName(+prayerSel) : T("prayer.me").replace(/^\S*\s/, ""); }
  function closeMenu() { $("menu").hidden = true; }
  function openMenu(anchor) {
    var m = $("menu"), r = anchor.getBoundingClientRect();
    m.style.top = Math.round(r.bottom + 6) + "px"; m.style.right = Math.max(12, Math.round(innerWidth - r.right)) + "px"; m.style.left = "auto";
    menuMain(); m.hidden = false;
  }
  function menuMain() {
    var m = $("menu"); m.textContent = "";
    m.appendChild(menuBtn("add", T("btn.add"), "", function () { closeMenu(); setPage("map"); whenMap(startAdd); }));
    m.appendChild(menuBtn("region", T("prayer.region"), regionLabel(), function () { menuPick(T("prayer.region"), [["me", T("prayer.me").replace(/^\S*\s/, "")]].concat(REGIONS.map(function (r, i) { return [String(i), regionName(i)]; })), prayerSel, setRegion); }));
    m.appendChild(menuBtn("refresh", T("btn.refresh"), "", function () { closeMenu(); refreshAll(); }));
    m.appendChild(menuBtn(isDark() ? "sun" : "moon", T(isDark() ? "theme.day" : "theme.night"), "", function () { closeMenu(); toggleTheme(); }));
    m.appendChild(menuBtn("globe", T("lang.label"), I18N.langs.filter(function (l) { return l[0] === I18N.lang(); }).map(function (l) { return l[2]; })[0], function () {
      menuPick(T("lang.label"), I18N.langs.map(function (l) { return [l[0], l[2]]; }), I18N.lang(), changeLang);
    }));
  }
  function menuPick(title, items, cur, onPick) {
    var m = $("menu"); m.textContent = "";
    m.appendChild(el("div", { class: "mt", text: title }));
    items.forEach(function (it) { m.appendChild(menuBtn(null, it[1], "", function () { closeMenu(); onPick(it[0]); }, it[0] === cur)); });
  }
  document.querySelectorAll(".more-btn").forEach(function (b) {
    b.innerHTML = '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><circle cx="12" cy="5" r="2" fill="currentColor"/><circle cx="12" cy="12" r="2" fill="currentColor"/><circle cx="12" cy="19" r="2" fill="currentColor"/></svg>';
    b.onclick = function (e) { e.stopPropagation(); if ($("menu").hidden) openMenu(b); else closeMenu(); };
  });
  document.addEventListener("click", function (e) { if (!$("menu").contains(e.target)) closeMenu(); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeMenu(); });

  function changeLang(code) {                                                   // labels are baked into the map style: reload at the same view
    I18N.set(code);
    if (tgData) {                                                                // keep the bot in the same language
      try { fetch("/api/me/lang", { method: "POST", keepalive: true, headers: { "Content-Type": "application/json", "X-Telegram-Init-Data": tgData }, body: JSON.stringify({ lang: code }) }); } catch (e) {}
    }
    var u = new URL(location.href);
    u.searchParams.delete("lang");
    if (map) { var c = map.getCenter(); u.searchParams.set("c", c.lat.toFixed(5) + "," + c.lng.toFixed(5) + "," + map.getZoom().toFixed(2)); }
    location.href = u.toString();                                                // keeps the #tgWebAppData hash inside Telegram
  }
  function refreshAll() {                                                        // a fresh device position, then everything that depends on it
    if (prayerSel === "me" && navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(function (p) {
        if (inRegion(p.coords.longitude, p.coords.latitude)) try { localStorage.setItem("lastpos", JSON.stringify({ lat: p.coords.latitude, lon: p.coords.longitude })); } catch (e) {}
        renderPrayer(); if (list.built) reloadList();
      }, function () { renderPrayer(); }, { maximumAge: 0, timeout: 6000 });
    } else { renderPrayer(); if (list.built) reloadList(); }
  }

  // ---- daily prayer times: the home page (computed on the device; region can be switched) ----
  var REGIONS = [[41.3111, 69.2797], [41.0167, 69.35], [40.7821, 72.3442], [39.7747, 64.4286], [40.3864, 71.7864], [40.1158, 67.8422], [41.55, 60.6333],
    [41.0011, 71.6683], [40.0844, 65.3792], [38.8606, 65.7891], [42.4531, 59.6103], [39.6542, 66.9597], [40.4897, 68.7842], [37.2242, 67.2783]];
  var PNAMES = ["fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha"];
  var prayerSel = "me";
  try { prayerSel = localStorage.getItem("preg") || "me"; } catch (e) {}
  function regionName(i) { return T("region." + i); }
  function nearestRegion(lat, lon) {
    var best = 0, bd = 1e9;
    REGIONS.forEach(function (r, i) { var d = Math.pow(r[0] - lat, 2) + Math.pow((r[1] - lon) * Math.cos(lat * Math.PI / 180), 2); if (d < bd) { bd = d; best = i; } });
    return best;
  }
  function prayerPos() {
    if (prayerSel !== "me" && REGIONS[+prayerSel]) { var r = REGIONS[+prayerSel]; return { lat: r[0], lon: r[1], name: regionName(+prayerSel), region: regionName(+prayerSel) }; }
    var sp = savedPos(), c = map ? map.getCenter() : null;
    var p = sp ? { lat: sp.lat, lon: sp.lon } : c ? { lat: c.lat, lon: c.lng } : { lat: start0[1], lon: start0[0] };
    var reg = regionName(nearestRegion(p.lat, p.lon));
    return { lat: p.lat, lon: p.lon, name: T(sp ? "prayer.where_me" : "prayer.where_map") + " · " + reg, region: reg };
  }
  function renderPrayer() {
    var now = new Date(), p = prayerPos(), o = cfg.prayer || {};
    var tz = inRegion(p.lon, p.lat) ? 5 : -now.getTimezoneOffset() / 60;
    var t = Prayer.times(now.getFullYear(), now.getMonth() + 1, now.getDate(), p.lat, p.lon, tz,
      { fajr: o.fajr_angle, isha: o.isha_angle, asr: o.asr_factor, dhuhrMin: o.dhuhr_offset_min });
    var nowH = (now.getUTCHours() + now.getUTCMinutes() / 60 + tz + 24) % 24;       // current time in that region
    var nextKey = null, cur = "isha";
    PNAMES.forEach(function (n) { if (!nextKey && t[n] > nowH && n !== "sunrise") nextKey = n; if (t[n] <= nowH) cur = n; });
    if (!nextKey) nextKey = "fajr";                                                   // after Isha: tomorrow's Fajr
    var diff = t[nextKey] - nowH; if (diff <= 0) diff += 24;
    var mins = Math.round(diff * 60);
    var left = mins >= 60 ? T("dur.hm", { h: Math.floor(mins / 60), m: mins % 60 }) : T("dur.m", { m: mins });
    // the home page
    $("home-date").textContent = T("date.fmt", { d: now.getDate(), m: T("date.m" + (now.getMonth() + 1)), w: T("date.d" + (now.getDay() || 7)) });
    $("home-place").textContent = p.region;
    $("hero-name").textContent = T("pn." + nextKey);
    $("hero-time").textContent = Prayer.format(t[nextKey]);
    $("hero-left").textContent = T("home.left", { t: left });
    var rows = $("home-times"); rows.textContent = "";
    PNAMES.forEach(function (n) {
      rows.appendChild(el("div", { class: "trow" + (n === cur ? " cur" : t[n] < nowH ? " past" : "") }, [el("i"), el("span", { text: T("pn." + n) }), el("b", { text: Prayer.format(t[n]) })]));
    });
    $("home-note").textContent = T("prayer.approx") + " · " + p.name;
  }
  var start0 = TASHKENT;
  function setRegion(v) { prayerSel = v; try { localStorage.setItem("preg", v); } catch (e) {} renderPrayer(); }
  function initPrayer() {
    setInterval(function () { renderPrayer(); }, 20000);                      // keeps "next prayer" and the date current
    renderPrayer();
  }

  function mapLocale() {
    return { "NavigationControl.ZoomIn": T("map.zoom_in"), "NavigationControl.ZoomOut": T("map.zoom_out"),
      "GeolocateControl.FindMyLocation": T("map.locate"), "GeolocateControl.LocationNotAvailable": T("map.locate"),
      "AttributionControl.ToggleAttribution": T("map.attrib"), "AttributionControl.MapFeedback": T("map.attrib") };
  }

  function init() {
    var qs = new URLSearchParams(location.search).get("c");                    // optional deep link: ?c=lat,lon,zoom
    var qm = qs && /^(-?[\d.]+),(-?[\d.]+),([\d.]+)$/.exec(qs);
    mapQuery = !!qm;
    var sp = savedPos();
    mapStart = mapQuery ? { center: [+qm[2], +qm[1]], zoom: +qm[3] }
      : sp ? { center: [sp.lon, sp.lat], zoom: 14 } : { center: TASHKENT, zoom: 12 };
    start0 = mapStart.center;
    configPromise.then(function (c) { cfg = c; initPrayer(); });
    var deep = new URLSearchParams(location.search).get("m");                  // ?m=<mosque id>: open that mosque (bot links, shares)
    var first = "home";
    try { first = sessionStorage.getItem("page") || "home"; } catch (e) {}      // after a language change: the same page again
    if (mapQuery || (deep && /^[0-9a-f-]{36}$/.test(deep))) first = "map";
    setPage(first);
    if (first === "map" && deep && /^[0-9a-f-]{36}$/.test(deep)) whenMap(function () { openMosque(deep); });
    if (first === "home" || first === "list") { watchPosition(); }
    applyTheme();
  }

  // The first mosque query and the map itself are only started when the map page is opened (the home page needs neither).
  var mapCreated = false;
  function mosqueDataUrl(color) {                                          // the pin as a picture for the Yandex engine's DOM markers
    var im = mosqueIcon(color), c = document.createElement("canvas"); c.width = c.height = im.width; c.getContext("2d").putImageData(im, 0, 0);
    return c.toDataURL("image/png");
  }
  function afterMap(start) {                                                // what follows once a map of either engine exists
    var q = mapQuery, hasDeep = !!pendingMap.length;
    var fns = pendingMap; pendingMap = []; fns.forEach(function (fn) { fn(); });
    if (!q && !hasDeep) locate(start);
    applyMosques();
    map.on("dragstart", function () { userMoved = true; });
    map.on("zoomstart", function (e) { if (e.originalEvent) userMoved = true; });
    map.on("moveend", function () { clearTimeout(moveTimer); moveTimer = setTimeout(ensureMosques, 250); });
    map.on("click", onMapClick);
    ensureMosques();
  }
  function startYandex(m, start) {                                          // rejects on any trouble: the caller reports the key and falls back
    return YandexMap.create({ key: m.yandex_key, container: "map", center: start.center, zoom: start.zoom, bounds: REGION, dark: isDark(), lang: I18N.lang(), icon: mosqueDataUrl })
      .then(function (ym) { map = ym; afterMap(start); });
  }
  function startOsm(c, start) {
    var sp2 = c.style_url && c.style_url !== DEFAULT_STYLE ? fetch(c.style_url).then(function (r) { return r.json(); }) : stylePromise;
    return sp2.then(function (style) { rawStyle = style; return buildStyle(); }).then(function (style) {
      map = new maplibregl.Map({ container: "map", style: style, center: start.center, zoom: start.zoom, locale: mapLocale(),
        minZoom: 5, maxBounds: REGION, fadeDuration: 0, pixelRatio: Math.min(window.devicePixelRatio || 1, 2),
        attributionControl: { compact: true } });
      map.on("styleimagemissing", function (e) {      // our own icons, created on demand
        var colors = { "mosque-green": "#059669", "mosque-amber": "#b45309", "mosque-osm": "#4aa586" };
        if (colors[e.id] && !map.hasImage(e.id)) map.addImage(e.id, mosqueIcon(colors[e.id]), { pixelRatio: 2 });
      });
      map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
      map.addControl(new maplibregl.GeolocateControl({ trackUserLocation: false }), "top-right");
      map.once("load", function () { map.resize(); });
      map.on("mouseenter", "mosques", function () { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", "mosques", function () { map.getCanvas().style.cursor = ""; });
      afterMap(start);
      var errs = 0;
      map.on("error", function () {                   // tile/style/sprite request failures: say so instead of a silent blank map
        if (++errs >= 3 && !map.loaded()) mapProblem(T("map.err_tiles"));
      });
      map.on("idle", function () { if (map.loaded()) $("maperr").hidden = true; });
      setTimeout(function () { if (!map.loaded()) mapProblem(T("map.err_slow")); }, 20000);
    });
  }
  // Yandex refused the key (or did not load): tell the server (it hands out the next key, or says OpenStreetMap) and carry on without a gap.
  function yandexFailed(m) {
    return api("POST", "/api/map/yandex-failed", { key_id: m.key_id }).then(function (r) { return r.map; }).catch(function () { return { provider: "osm" }; });
  }
  function createMap() {
    if (mapCreated) return;
    mapCreated = true;
    var start = mapStart;
    var d = 0.1;                                                              // first mosque query right away, around the start position (~11 km window)
    fetchMosques([start.center[0] - d, start.center[1] - d, start.center[0] + d, start.center[1] + d]);
    configPromise.then(function (c) {
      cfg = c;
      function osm() { return startOsm(c, start); }
      function yandex(m, tries) {
        return startYandex(m, start).catch(function () {
          document.getElementById("map").textContent = "";
          if (tries >= 2) return yandexFailed(m).then(function () { return osm(); });
          return yandexFailed(m).then(function (next) {
            // the Yandex script cannot take a second key in the same page: the next key needs a fresh page, OpenStreetMap does not
            if (next && next.provider === "yandex" && next.yandex_key && next.key_id !== m.key_id) { try { sessionStorage.setItem("ytry", String(tries + 1)); } catch (e) {} location.reload(); return null; }
            return osm();
          });
        });
      }
      var tries = 0;
      try { tries = +sessionStorage.getItem("ytry") || 0; } catch (e) {}
      if (c.map && c.map.provider === "yandex" && c.map.yandex_key && window.YandexMap) return yandex(c.map, tries);
      return osm();
    }).catch(function () {
      mapProblem(T("map.err_style"));
    });
  }

  // The device position decides the prayer times and the list's distances: asked once at the start, never blocking anything.
  function watchPosition() {
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(function (p) {
      var lon = p.coords.longitude, lat = p.coords.latitude;
      if (!inRegion(lon, lat)) return;
      try { localStorage.setItem("lastpos", JSON.stringify({ lat: lat, lon: lon })); } catch (e) {}
      renderPrayer();
      if (list.built) reloadList();
    }, function () {}, { maximumAge: 600000, timeout: 6000, enableHighAccuracy: false });
  }

  // On the map: if the user has not touched it yet and the device is somewhere else than the cached start, jump there.
  function locate(start) {
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(function (p) {
      var lon = p.coords.longitude, lat = p.coords.latitude;
      if (!inRegion(lon, lat)) return;
      try { localStorage.setItem("lastpos", JSON.stringify({ lat: lat, lon: lon })); } catch (e) {}
      var far = Math.abs(lon - start.center[0]) + Math.abs(lat - start.center[1]) > 0.01;
      if (!userMoved && far && map) { map.jumpTo({ center: [lon, lat], zoom: 14 }); }
      renderPrayer();
    }, function () {}, { maximumAge: 600000, timeout: 6000, enableHighAccuracy: false });
  }

  // ---- the list of mosques: nearest first, 20 at a time while scrolling, a picture when there is one, the address under the name ----
  var list = { items: [], loading: false, done: false, gen: 0, built: false, known: false };
  var LIST_PAGE = 20, LIST_MAX = 200;
  function ensureList() { if (list.built) return; list.built = true; reloadList(); }
  function reloadList() {
    list.gen++; list.items = []; list.done = false; list.loading = false;
    $("list").textContent = ""; $("list-more").textContent = "";
    loadMore();
  }
  function listCard(m) {
    var lead;
    if (m.photo_url && m.photo_url.indexOf("https://") === 0) {
      lead = el("img", { class: "ph", src: m.photo_url, alt: "", loading: "lazy", width: "76", height: "76", referrerpolicy: "no-referrer" });
      lead.addEventListener("error", function () { var i = el("span", { class: "ic" }); i.innerHTML = '<svg viewBox="0 0 24 24" width="24" height="24"><path fill="currentColor" d="M12 2c1 1.6 2.6 2.4 2.6 4.4a2.6 2.6 0 0 1-5.2 0C9.4 4.4 11 3.6 12 2zM5 21V12.5a7 7 0 0 1 14 0V21h-4.5v-4a2.5 2.5 0 0 0-5 0v4z"/></svg>'; lead.replaceWith(i); });
    } else {
      lead = el("span", { class: "ic" }); lead.innerHTML = '<svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true"><path fill="currentColor" d="M12 2c1 1.6 2.6 2.4 2.6 4.4a2.6 2.6 0 0 1-5.2 0C9.4 4.4 11 3.6 12 2zM5 21V12.5a7 7 0 0 1 14 0V21h-4.5v-4a2.5 2.5 0 0 0-5 0v4z"/></svg>';
    }
    var meta = [];
    if (list.known && m.distance_m != null) meta.push(el("span", { text: "↗ " + fmtDist(m.distance_m) }));
    if (m.times_updated_at) meta.push(el("span", { text: "🕒 " + T("list.times") }));
    return el("button", { class: "card", type: "button", onclick: function () { showOnMap(m.id, m.lon, m.lat, list.known ? m.distance_m : null); } }, [lead, el("div", { class: "tx" }, [
      el("div", m.name ? { class: "nm", text: m.name } : { class: "nm missing", text: T("m.name_unknown") }),
      el("div", m.address ? { class: "ad", text: m.address } : { class: "ad missing", text: T("m.addr_unknown") }),
      meta.length ? el("div", { class: "mt" }, meta) : null])]);
  }
  function loadMore() {
    if (list.loading || list.done) return;
    var gen = list.gen, sp = savedPos(), o = sp || { lat: TASHKENT[1], lon: TASHKENT[0] };
    list.known = !!sp; $("list-hint").hidden = list.known; $("list-hint").textContent = T("list.hint");
    list.loading = true;
    var more = $("list-more"); more.textContent = "";
    if (!list.items.length) for (var i = 0; i < 5; i++) more.appendChild(el("div", { class: "sk sk-card" }));
    api("GET", "/api/mosques/nearest?lat=" + (+o.lat).toFixed(5) + "&lon=" + (+o.lon).toFixed(5) + "&limit=" + LIST_PAGE + "&offset=" + list.items.length).then(function (r) {
      if (gen !== list.gen) return;
      more.textContent = ""; list.loading = false;
      r.items.forEach(function (m) { list.items.push(m); $("list").appendChild(listCard(m)); });
      list.done = r.items.length < LIST_PAGE || list.items.length >= LIST_MAX;
      if (list.done && !list.items.length) more.appendChild(el("p", { class: "muted", text: T("list.none") }));
      fillList();
    }).catch(function (e) {
      if (gen !== list.gen) return;
      list.loading = false; more.textContent = "";
      more.appendChild(el("p", { class: "muted", text: errText(e) })); more.appendChild(btn("ghost small", T("btn.retry"), loadMore));
    });
  }
  function fillList() {                                                            // a tall screen: ask for the next page until the end is out of sight
    var p = $("list-page");
    if (page === "list" && !list.done && p.scrollHeight - p.scrollTop - p.clientHeight < 700) loadMore();
  }
  $("list-page").addEventListener("scroll", fillList, { passive: true });

  // Provider POIs (OpenStreetMap data inside the vector tiles). Read transiently from the rendered
  // tiles; nothing from them is stored or copied into our database.
  function poiAt(point) {
    var box = [[point.x - 12, point.y - 12], [point.x + 12, point.y + 12]];
    return map.getLayer("osm-mosques") ? (map.queryRenderedFeatures(box, { layers: ["osm-mosques"] })[0] || null) : null;
  }

  function onMapClick(e) {
    if (mode === "pick") { setPicked(e.lngLat); return; }
    var own = !map.getLayer("mosques") ? [] : map.queryRenderedFeatures(e.point, { layers: ["mosques"] });
    if (own.length) { openMosque(own[0].properties.id); return; }
    var poi = poiAt(e.point);
    if (poi) showProviderPoi(poi, e.lngLat);
  }

  function showProviderPoi(poi, lngLat) {
    var c = poi.geometry.coordinates;
    var body = el("div", { style: "max-width:230px" }, [
      el("b", { text: poi.properties.name || T("poi.name") }),
      el("div", { class: "muted", text: T("poi.note") }),
      el("div", { class: "row" }, [])
    ]);
    var row = body.lastChild;
    api("GET", "/api/mosques/nearby?lat=" + c[1] + "&lon=" + c[0] + "&radius=" + cfg.duplicate_radius_m).then(function (r) {
      if (r.items.length) row.appendChild(btn("small", T("poi.open"), function () { closePopup(); openMosque(r.items[0].id); }));
      else row.appendChild(btn("small", T("poi.add"), function () { closePopup(); startAdd(); setPicked({ lng: c[0], lat: c[1] }); }));
    }).catch(function () {});
    closePopup();
    popup = new maplibregl.Popup({ maxWidth: "260px" }).setLngLat(lngLat).setDOMContent(body).addTo(map);
  }
  function closePopup() { if (popup) { popup.remove(); popup = null; } }

  // ---- layout: where the panel sits decides which part of the map stays visible
  function sideLayout() { return innerWidth >= 700 || (innerWidth > innerHeight && innerHeight <= 520); }
  function mapPadding() {
    var shown = !$("panel").hidden;
    if (!shown) return { top: 0, right: 0, bottom: 0, left: 0 };
    if (sideLayout()) return { top: 0, right: 0, bottom: 0, left: Math.min(402, innerWidth * 0.56 + 12) };
    return { top: 0, right: 0, left: 0, bottom: Math.max(0, Math.round(innerHeight * 0.56 - $("nav").offsetHeight)) };   // matches the sheet height
  }
  function revealPoint(lon, lat) { map.easeTo({ center: [lon, lat], duration: 350, padding: mapPadding() }); }

  var resizeTimer = null;
  window.addEventListener("resize", function () {          // rotation, keyboard, browser bars: re-fit instead of reflowing
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () { if (map) { map.resize(); map.easeTo({ padding: mapPadding(), duration: 0 }); } }, 120);
  });

  // ---- panel
  var closeTimer = null;
  function showPanel(node) {
    clearTimeout(closeTimer); var p = $("panel"); p.classList.remove("closing");
    var b = $("panel-body"); b.textContent = ""; b.appendChild(node); p.hidden = false; p.scrollTop = 0; document.body.classList.add("panel-open");
  }
  function hidePanel() {                                    // slides all the way down (in step with the list/map button), then goes
    var p = $("panel"); document.body.classList.remove("panel-open");
    if (map) map.easeTo({ padding: mapPadding(), duration: 200 });
    if (p.hidden || p.classList.contains("closing")) return;
    p.classList.add("closing");
    clearTimeout(closeTimer);
    closeTimer = setTimeout(function () { p.hidden = true; p.classList.remove("closing"); }, 340);
  }
  $("panel-close").onclick = hidePanel;
  if (window.ResizeObserver) new ResizeObserver(function () {          // the list/map button rides on top of the card
    document.documentElement.style.setProperty("--panel-h", ($("panel").hidden ? 0 : $("panel").offsetHeight) + "px");
  }).observe($("panel"));
  function skeleton() {                                    // instant feedback while the mosque loads
    return el("div", {}, [el("div", { class: "sk", style: "height:26px;width:70%;margin:8px 0" }), el("div", { class: "sk", style: "height:18px;width:45%;margin:8px 0" }),
      el("div", { class: "sk", style: "height:16px;width:85%;margin:14px 0" }), el("div", { class: "sk", style: "height:120px;margin:14px 0" })]);
  }

  function timesGrid(m) {
    var g = el("div", { class: "times" });
    PRAYERS.forEach(function (p) {
      var v = m.prayer_times[p];
      g.appendChild(el("div", { class: "tile" + (v ? "" : " empty") }, [el("small", { text: T("pn." + p) }), el("b", { text: v || "—" })]));
    });
    return g;
  }

  // A picture from Wikimedia Commons (taken from the OpenStreetMap tags) with its credit; nothing at all when the mosque has none.
  function photoBlock(m) {
    if (!m.photo_url || m.photo_url.indexOf("https://") !== 0) return null;
    var img = el("img", { class: "photo", src: m.photo_url, alt: "", loading: "lazy", referrerpolicy: "no-referrer" });
    img.addEventListener("error", function () { wrap.hidden = true; });
    var credit = m.photo_credit && m.photo_credit.indexOf("https://") === 0
      ? el("a", { href: m.photo_credit, target: "_blank", rel: "noopener noreferrer", text: T("m.photo") })
      : el("span", { text: T("m.photo") });
    var wrap = el("div", { class: "photo-wrap" }, [img, el("div", { class: "photo-credit" }, [credit])]);
    return wrap;
  }
  function openMosque(id, opts) {
    opts = opts || {};
    showPanel(skeleton());
    api("GET", "/api/mosques/" + id).then(function (m) {
      var lb = label(m);
      var node = el("div", {}, [
        photoBlock(m),
        el("h2", m.name ? { text: m.name } : { class: "missing", text: T("m.name_unknown") }),
        lb ? el("span", { class: "badge " + lb[1], text: lb[0] }) : null,
        opts.dist != null ? el("div", { class: "dist", text: "📍 " + T("m.distance", { d: fmtDist(opts.dist) }) }) : null,
        el("p", m.address ? { text: m.address } : { class: "missing", text: T("m.addr_unknown") }),
        el("div", { class: "muted stack" }, [
          el("div", { text: m.lat.toFixed(6) + ", " + m.lon.toFixed(6) + " · " + T("m.version", { v: m.version }) }),
          m.verification_note ? el("div", { text: T("m.verif", { note: m.verification_note }) }) : null
        ]),
        el("h3", { text: T("m.times") }), timesGrid(m),
        el("div", { class: "times-meta muted", text: m.times_updated_at ? T("times.updated", { t: I18N.fmtDate(m.times_updated_at) }) : T("times.never") }),
        el("div", { class: "cta" }, [
          el("p", { text: T(m.times_updated_at ? "cta.have" : "cta.empty") }),
          btn(m.times_updated_at ? "ghost small" : "small", T("cta.btn"), function () { editForm(m, { timesOnly: true }); }),
        ]),
        el("div", { class: "row" }, readButtons(m)),
        el("div", { class: "row" }, yandexLinks(m)),
        el("div", { class: "row" }, [
          m.origin === "imported" ? null : btn("", T("btn.edit"), function () { editForm(m); }),     // map-data mosques: only the times are editable (see the button above)
          btn("ghost", T("btn.history"), function () { historyView(m); }),
          btn("danger", T("btn.report"), function () { reportForm(m); })
        ])
      ]);
      showPanel(node);
      if (opts.fit) {                                   // show both "me" and the mosque, clear of the panel
        var pad = mapPadding();
        map.fitBounds(opts.fit, { maxZoom: 17, duration: 600, padding: { top: 110, right: 50, bottom: pad.bottom + 50, left: pad.left + 50 } });
      } else revealPoint(m.lon, m.lat);
    }).catch(function (e) { hidePanel(); toast(errText(e)); });
    revealPoint(map.getCenter().lng, map.getCenter().lat);
  }

  // ---- reading the times from a photo or a voice recording (the same flow as the app): read, check, correct, save ----
  function apiRaw(path, blob, type) {
    var h = { "Content-Type": type, "X-Client-Id": clientId, "X-Client-Platform": "web", "ngrok-skip-browser-warning": "1" };
    if (tg && tg.initData) h["X-Telegram-Init-Data"] = tg.initData;
    return fetch(path, { method: "POST", headers: h, body: blob })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) { j.status = r.status; throw j; } return j; }); });
  }
  function readButtons(m) {                               // photo and voice, always both; when the server cannot, the button opens the bot (or says so)
    var bot = cfg.bot_username;
    function fallback(code) { return bot ? function () { window.open("https://t.me/" + bot + "?start=m_" + m.id, "_blank", "noopener"); } : function () { toast(errText({ error: code })); }; }
    var canVoice = cfg.voice_reading && navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder;
    return [
      btn("ghost small", "📷 " + T("photo.btn"), cfg.photo_reading ? function () { photoSource(m); } : fallback("ocr_unavailable")),
      btn("ghost small", "🎙 " + T("voice.btn"), canVoice ? function () { voiceFlow(m); } : fallback("stt_unavailable")),
      bot ? el("a", { class: "btn ghost small", target: "_blank", rel: "noopener noreferrer", href: "https://t.me/" + bot + "?start=s_" + m.id, text: T("cta.sub") }) : null
    ];
  }
  function readSheet(m, o) {                              // o: title, reading, check, note, other, again(), read() -> Promise, header(result|null) -> node
    var headBox = el("div", {}), body = el("div", {}), cancelled = false, res = null;
    showPanel(el("div", {}, [el("h2", { text: o.title }), headBox, body]));
    function head(r) { headBox.textContent = ""; var h = o.header && o.header(r); if (h) headBox.appendChild(h); }
    function leave() { cancelled = true; openMosque(m.id); }
    function run() {
      head(res); body.textContent = "";
      body.appendChild(el("div", { class: "reading" }, [el("p", { class: "strong", text: o.reading }), el("div", { class: "bar" }, [el("i")]), el("div", { class: "row" }, [btn("ghost", T("btn.cancel"), leave)])]));
      o.read().then(function (r) {
        if (cancelled) return;
        res = r; head(r);
        if (r.alt) choose(r); else review(r.times || {}, r.complete);
      }).catch(function (e) {
        if (cancelled) return;
        body.textContent = "";
        body.appendChild(el("div", { class: "errbox", text: errText(e) }));
        body.appendChild(el("div", { class: "row" }, [btn("", T("btn.retry"), run), btn("ghost", o.other, o.again)]));
      });
    }
    function choose(r) {                                  // a timetable with two columns: which one is the congregation time?
      body.textContent = "";
      body.appendChild(el("p", { class: "strong", text: T("photo.two") }));
      [r.times, r.alt].forEach(function (col, i) {
        var tile = el("button", { type: "button", class: "colpick", onclick: function () { review(col, Object.keys(col).length >= 5); } }, [el("small", { text: T("photo.col", { n: i + 1 }) }), timesGrid({ prayer_times: col })]);
        body.appendChild(tile);
      });
      body.appendChild(el("div", { class: "row" }, [btn("ghost", o.other, o.again)]));
    }
    function review(times, complete) {
      body.textContent = "";
      var save = btn("", T("btn.save"), function () {
        save.disabled = true;
        var now = readTimes(grid), pt = {};
        PRAYERS.forEach(function (p) { if ((now[p] || null) !== (m.prayer_times[p] || null)) pt[p] = now[p]; });
        if (!Object.keys(pt).length) { toast(T("photo.same")); openMosque(m.id); return; }
        api("PATCH", "/api/mosques/" + m.id, { prayer_times: pt, note: o.note }).then(function (u) { toast(T("toast.saved")); loadMosques(); openMosque(u.id); })
          .catch(function (e) { toast(errText(e)); sync(); });
      });
      function sync() { var now = readTimes(grid); save.disabled = !PRAYERS.every(function (p) { return now[p]; }); }
      var grid = timesInputs(times, function () { if (grid) sync(); });
      var missing = !complete || PRAYERS.some(function (p) { return !times[p]; });
      body.appendChild(el("p", { class: "muted", text: o.check }));
      if (missing) body.appendChild(el("div", { class: "warnbox", text: T("photo.incomplete") }));
      body.appendChild(grid);
      body.appendChild(el("div", { class: "row" }, [save, btn("ghost", o.other, o.again)]));
      sync();
    }
    run();
  }

  function photoSource(m) {                               // take a photo or choose one; if the picker is closed we stay here
    function choose(camera) {
      var inp = el("input", { type: "file", accept: "image/*", style: "display:none" });
      if (camera) inp.setAttribute("capture", "environment");
      document.body.appendChild(inp);
      inp.addEventListener("change", function () { var f = inp.files && inp.files[0]; inp.remove(); if (f) photoRead(m, f); });
      inp.click();
    }
    showPanel(el("div", {}, [
      el("h2", { text: T("photo.title") }), el("p", { class: "muted", text: T("photo.src") }),
      el("div", { class: "stack" }, [btn("", "📷 " + T("photo.take"), function () { choose(true); }), btn("ghost", "🖼 " + T("photo.pick"), function () { choose(false); })]),
      el("div", { class: "row" }, [btn("ghost", T("btn.cancel"), function () { openMosque(m.id); })])
    ]));
  }
  function scaleDown(file) {                              // a 12 MP photo becomes a few hundred KB: reading over mobile data stays fast
    if (!window.createImageBitmap) return Promise.resolve(file);
    return createImageBitmap(file).then(function (bm) {
      var k = Math.min(1, 2200 / Math.max(bm.width, bm.height)), c = document.createElement("canvas");
      c.width = Math.round(bm.width * k); c.height = Math.round(bm.height * k);
      c.getContext("2d").drawImage(bm, 0, 0, c.width, c.height);
      return new Promise(function (ok, no) { c.toBlob(function (b) { b ? ok(b) : no(); }, "image/jpeg", 0.85); });
    }).catch(function () { return file; });
  }
  function photoRead(m, file) {
    var url = URL.createObjectURL(file);
    readSheet(m, {
      title: T("photo.title"), reading: T("photo.reading"), check: T("photo.check"), other: T("photo.other"), note: "Veb (rasm)",
      again: function () { URL.revokeObjectURL(url); photoSource(m); },
      header: function () { return el("img", { class: "readpic", src: url, alt: "" }); },
      read: function () { return scaleDown(file).then(function (b) { return apiRaw("/api/timetable/read", b, b.type || "image/jpeg"); }); }
    });
  }

  function voiceFlow(m) {                                 // record up to a minute, then read the five times from what was said
    var stream = null, rec = null, chunks = [], timer = null, t0 = 0, cancelled = false;
    var clock = el("b", { class: "rec-time", text: "0:00" });
    function release() { clearInterval(timer); if (stream) stream.getTracks().forEach(function (t) { t.stop(); }); stream = null; }
    function finish() { if (rec && rec.state !== "inactive") rec.stop(); }
    function cancel() { cancelled = true; if (rec && rec.state !== "inactive") rec.stop(); release(); openMosque(m.id); }
    showPanel(el("div", {}, [
      el("h2", { text: T("voice.title") }), el("p", { class: "muted", text: T("voice.say") }),
      el("div", { class: "rec" }, [el("i", { class: "rec-dot" }), el("span", { text: T("voice.rec") }), clock]),
      el("div", { class: "row" }, [btn("", "■ " + T("voice.stop"), finish), btn("ghost", T("btn.cancel"), cancel)])
    ]));
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (s) {
      if (cancelled) { s.getTracks().forEach(function (t) { t.stop(); }); return; }
      stream = s;
      var type = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus", "audio/webm"].filter(function (t) { return MediaRecorder.isTypeSupported(t); })[0] || "";
      rec = new MediaRecorder(s, type ? { mimeType: type, audioBitsPerSecond: 48000 } : undefined);
      rec.ondataavailable = function (e) { if (e.data && e.data.size) chunks.push(e.data); };
      rec.onstop = function () {
        release(); if (cancelled) return;
        var blob = new Blob(chunks, { type: rec.mimeType || type || "audio/webm" });
        if (!blob.size) { toast(errText({ error: "bad_audio" })); openMosque(m.id); return; }
        voiceRead(m, blob);
      };
      rec.start(); t0 = Date.now();
      timer = setInterval(function () { var sec = Math.floor((Date.now() - t0) / 1000); clock.textContent = Math.floor(sec / 60) + ":" + two(sec % 60); if (sec >= 60) finish(); }, 250);
    }).catch(function () { if (!cancelled) { toast(T("voice.denied")); openMosque(m.id); } });
  }
  function voiceRead(m, blob) {
    readSheet(m, {
      title: T("voice.title"), reading: T("voice.listening"), check: T("voice.check"), other: T("voice.again"), note: "Veb (ovoz)",
      again: function () { voiceFlow(m); },
      header: function (r) { return r && r.heard ? el("p", { class: "heard", text: T("voice.heard", { t: r.heard }) }) : null; },
      read: function () { return apiRaw("/api/timetable/voice", blob, blob.type || "audio/webm"); }
    });
  }

  function field(labelText, input) { return el("div", {}, [el("label", { text: labelText }), input]); }

  // ---- the time wheel (like the iPhone's picker): two scroll wheels over a soft band, a tick for every step ----
  function two(n) { return (n < 10 ? "0" : "") + n; }
  var audioCtx = null;
  function tick() {
    try { if (navigator.vibrate) navigator.vibrate(4); } catch (e) {}
    try {
      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      var o = audioCtx.createOscillator(), g = audioCtx.createGain(), t = audioCtx.currentTime;
      o.type = "sine"; o.frequency.setValueAtTime(1900, t); o.frequency.exponentialRampToValueAtTime(900, t + 0.03);
      g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.14, t + 0.003); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.045);
      o.connect(g); g.connect(audioCtx.destination); o.start(t); o.stop(t + 0.05);
    } catch (e) {}
  }
  function wheel(count, initial, onChange) {              // a looping wheel of 0..count-1: five copies, re-centred when it comes to rest
    var ROW = 42, COPIES = 5, mid = Math.floor(COPIES / 2) * count, sel = initial, box = el("div", { class: "wheel", tabindex: "0" }), items = [], rest;
    for (var c = 0; c < COPIES; c++) for (var i = 0; i < count; i++) { var d = el("div", { class: "wi", text: two(i) }); items.push(d); box.appendChild(d); }
    function mark(n, on) { for (var k = n; k < items.length; k += count) items[k].classList.toggle("sel", on); }
    box.addEventListener("scroll", function () {
      var idx = Math.round(box.scrollTop / ROW), n = ((idx % count) + count) % count;
      if (n !== sel) { mark(sel, false); sel = n; mark(sel, true); tick(); onChange(n); }
      clearTimeout(rest);
      rest = setTimeout(function () { if (idx < count || idx >= (COPIES - 1) * count) box.scrollTop = (mid + sel) * ROW; }, 160);
    });
    box.addEventListener("keydown", function (e) { if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); box.scrollBy({ top: e.key === "ArrowDown" ? ROW : -ROW, behavior: "smooth" }); } });
    return { box: box, init: function () { mark(sel, true); box.scrollTop = (mid + sel) * ROW; } };
  }
  function pickTime(title, cur) {                         // -> Promise of "HH:MM", or null when cancelled
    return new Promise(function (resolve) {
      var m = /^(\d\d):(\d\d)$/.exec(cur || ""), h = m ? Math.min(23, +m[1]) : 12, mi = m ? Math.min(59, +m[2]) : 0;
      var out = el("b", { class: "readout" });
      function upd() { out.textContent = two(h) + ":" + two(mi); }
      var wh = wheel(24, h, function (v) { h = v; upd(); }), wm = wheel(60, mi, function (v) { mi = v; upd(); });
      var back = el("div", { class: "modal-back" });
      function done(v) { document.removeEventListener("keydown", esc); back.classList.add("out"); setTimeout(function () { back.remove(); }, 220); resolve(v); }
      function esc(e) { if (e.key === "Escape") done(null); }
      back.appendChild(el("div", { class: "modal", role: "dialog" }, [
        el("div", { class: "modal-head" }, [el("h2", { text: title }), out]),
        el("div", { class: "wheels" }, [el("div", { class: "band" }), wh.box, el("span", { class: "colon", text: ":" }), wm.box]),
        el("div", { class: "row" }, [btn("", T("btn.save"), function () { done(two(h) + ":" + two(mi)); }), btn("ghost", T("btn.cancel"), function () { done(null); })])
      ]));
      back.addEventListener("click", function (e) { if (e.target === back) done(null); });
      document.body.appendChild(back); document.addEventListener("keydown", esc);
      upd(); wh.init(); wm.init();
      try { if (audioCtx && audioCtx.state === "suspended") audioCtx.resume(); else if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) {}
    });
  }
  function timesInputs(values, onChange) {                // five tiles: tap one to choose its time on the wheel, × clears it
    var grid = el("div", { class: "grid" });
    PRAYERS.forEach(function (p) {
      var val = el("span", { class: "tv" }), b = el("button", { type: "button", class: "tt", "data-p": p }, [val]);
      var x = el("button", { type: "button", class: "tx", "aria-label": T("btn.close"), text: "×" });
      function set(v) { b.dataset.v = v || ""; val.textContent = v || "—"; b.classList.toggle("empty", !v); x.hidden = !v; if (onChange) onChange(); }
      b.onclick = function () { pickTime(T("pn." + p), b.dataset.v).then(function (v) { if (v) set(v); }); };
      x.onclick = function () { set(null); };
      set(values[p] || null);
      grid.appendChild(el("div", { class: "tw" }, [el("label", { text: T("pn." + p) }), b, x]));
    });
    return grid;
  }
  function readTimes(root) {
    var out = {};
    root.querySelectorAll(".tt[data-p]").forEach(function (i) { out[i.getAttribute("data-p")] = i.dataset.v || null; });
    return out;
  }
  function fmtC(lat, lng) { return lat.toFixed(6) + ", " + lng.toFixed(6); }

  function editForm(m, opts) {
    var timesOnly = !!(opts && opts.timesOnly);                  // short form: only the congregation times (the "add the times" button)
    var name = el("input", { value: m.name || "", maxlength: 200, autocomplete: "off" });
    var addr = el("input", { value: m.address || "", maxlength: 300, autocomplete: "off" });
    var grid = timesInputs(m.prayer_times);
    var note = el("input", { placeholder: T("f.note_ph"), maxlength: 300 });
    var coords = el("div", { class: "muted", text: T("edit.loc", { c: fmtC(m.lat, m.lon) }) });
    var newPos = null;
    var node = el("div", {}, [
      el("h2", { text: timesOnly ? T("cta.btn") : T("edit.title") }),
      timesOnly ? el("h3", { text: m.name || T("m.name_unknown") }) : field(T("f.name"), name), timesOnly ? null : field(T("f.addr"), addr),
      timesOnly ? null : el("h3", { text: T("m.times") }), grid, field(T("f.note"), note),
      timesOnly ? null : el("div", { class: "row" }, [coords]),
      timesOnly ? null : el("div", { class: "row" }, [
        btn("ghost", T("edit.pick"), function () {
          mode = "pick"; $("add-bar").hidden = false; $("add-hint").textContent = T("add.pick_new"); hidePanel();
          pickDone = function (ll) { newPos = ll; mode = "browse"; $("add-bar").hidden = true; clearPick(); showPanel(node); coords.textContent = T("edit.newloc", { c: fmtC(ll.lat, ll.lng) }); };
        })
      ]),
      el("div", { class: "row" }, [
        btn("", T("btn.save"), function (ev) {
          var b = ev.currentTarget; b.disabled = true;
          // Send only what this user changed: a form opened a minute ago must never overwrite what others saved since.
          var body = { note: note.value }, now = readTimes(grid), pt = {};
          PRAYERS.forEach(function (p) { if ((now[p] || null) !== (m.prayer_times[p] || null)) pt[p] = now[p]; });
          if (Object.keys(pt).length) body.prayer_times = pt;
          if (!timesOnly) {
            if (name.value.trim() !== (m.name || "")) body.name = name.value;
            if (addr.value.trim() !== (m.address || "")) body.address = addr.value;
            if (newPos) { body.lat = newPos.lat; body.lon = newPos.lng; }
          }
          if (!body.prayer_times && body.name === undefined && body.address === undefined && !newPos) { b.disabled = false; openMosque(m.id); return; }
          api("PATCH", "/api/mosques/" + m.id, body).then(function (u) { toast(T("toast.saved")); loadMosques(); openMosque(u.id); })
            .catch(function (e) {
              b.disabled = false;
              toast(errText(e));
            });
        }),
        btn("ghost", T("btn.cancel"), function () { openMosque(m.id); })
      ])
    ]);
    showPanel(node);
  }
  var pickDone = null;

  function fmtVal(field, v) {
    if (v === null || v === undefined || v === "") return "—";
    if (field === "status") return T("sv." + v);
    if (field === "lat" || field === "lon") return Number(v).toFixed(6);
    return String(v);
  }
  function describeChange(field, pair) {                       // -> list of readable lines for one changed field
    var a = pair[0], b = pair[1];
    if (field === "prayer_times") {
      var keys = PRAYERS.filter(function (p) { return (a && a[p]) !== (b && b[p]); });
      return keys.map(function (p) { return T("pn." + p) + ": " + fmtVal("", a && a[p]) + " → " + fmtVal("", b && b[p]); });
    }
    var fl = T("fld." + field);
    return [(fl === "fld." + field ? field : fl) + ": " + fmtVal(field, a) + " → " + fmtVal(field, b)];
  }

  function historyView(m) {
    var box = el("div", {}, [el("h2", { text: T("hist.title") }), skeleton()]);
    showPanel(box);
    api("GET", "/api/mosques/" + m.id + "/history").then(function (r) {
      box.textContent = "";
      box.appendChild(el("h2", { text: T("hist.title") }));
      r.items.forEach(function (h) {
        var lines = [];
        Object.keys(h.changes).forEach(function (k) { lines = lines.concat(describeChange(k, h.changes[k])); });
        var act = T("act." + h.action); if (act === "act." + h.action) act = h.action;
        box.appendChild(el("div", { class: "hist stack" }, [
          el("b", { text: act + " · v" + h.version }),
          el("div", { class: "muted", text: I18N.fmtDate(h.at) + (h.actor ? " · " + T("role." + h.actor) : "") }),
          lines.length ? el("div", { text: lines.join("; ") }) : null, h.note ? el("div", { class: "muted", text: h.note }) : null
        ]));
      });
      box.appendChild(el("div", { class: "row" }, [btn("ghost", T("btn.back"), function () { openMosque(m.id); })]));
    }).catch(function (e) { toast(errText(e)); });
  }

  function reportForm(m) {
    var kind = el("select", {}, ["incorrect", "duplicate", "relocated", "closed", "other"].map(function (k) { return el("option", { value: k, text: T("rk." + k) }); }));
    var reason = el("textarea", { rows: 3, maxlength: 1000, placeholder: T("rep.reason_ph") });
    showPanel(el("div", {}, [
      el("h2", { text: T("rep.title") }),
      el("p", { class: "muted", text: T("rep.intro") }),
      field(T("rep.type"), kind), field(T("rep.reason"), reason),
      el("div", { class: "row" }, [
        btn("", T("btn.send"), function (ev) {
          var b = ev.currentTarget; b.disabled = true;
          api("POST", "/api/mosques/" + m.id + "/reports", { kind: kind.value, reason: reason.value })
            .then(function () { toast(T("toast.reported")); loadMosques(); openMosque(m.id); })
            .catch(function (e) { b.disabled = false; toast(errText(e)); });
        }),
        btn("ghost", T("btn.cancel"), function () { openMosque(m.id); })
      ])
    ]));
  }

  // ---- nearest mosque to the user (device location; falls back to the map centre) ----------------
  var meMarker = null;
  function fmtDist(m) { return m < 1000 ? T("unit.m", { n: Math.round(m) }) : T("unit.km", { n: (m / 1000).toFixed(m < 10000 ? 1 : 0) }); }
  function findNearest() {
    var b = $("nearest");
    b.disabled = true; toast(T("nearest.locating"));
    function done() { b.disabled = false; }
    function go(lat, lon, fromMap) {
      api("GET", "/api/mosques/nearest?lat=" + lat + "&lon=" + lon + "&limit=1").then(function (r) {
        done();
        if (!r.items.length) { toast(T("nearest.none")); return; }
        var m = r.items[0];
        if (meMarker) meMarker.remove();
        meMarker = newMarker({ element: el("div", { class: "me-dot" }) }).setLngLat([lon, lat]).addTo(map);
        toast(fromMap ? T("nearest.from_map") : T("nearest.btn") + ": " + (m.name || T("m.name_unknown")));
        openMosque(m.id, { dist: m.distance_m, fit: [[Math.min(lon, m.lon), Math.min(lat, m.lat)], [Math.max(lon, m.lon), Math.max(lat, m.lat)]] });
      }).catch(function (e) { done(); toast(errText(e)); });
    }
    function fromMap() { var c = map.getCenter(); go(+c.lat.toFixed(6), +c.lng.toFixed(6), true); }
    if (!navigator.geolocation) { fromMap(); return; }
    navigator.geolocation.getCurrentPosition(function (p) {
      var lat = +p.coords.latitude.toFixed(6), lon = +p.coords.longitude.toFixed(6);
      if (inRegion(lon, lat)) try { localStorage.setItem("lastpos", JSON.stringify({ lat: lat, lon: lon })); } catch (e) {}
      go(lat, lon, false);
    }, fromMap, { timeout: 8000, maximumAge: 60000, enableHighAccuracy: true });
  }
  $("nearest").onclick = findNearest;

  // ---- search by name (our own database only; the server matches any script/apostrophe form)
  var searchTimer = null, searchSeq = 0;
  function hideResults() { $("results").hidden = true; }
  function runSearch() {
    var v = $("q").value.trim(), ul = $("results"), seq = ++searchSeq;
    if (v.length < 2) { hideResults(); return; }
    api("GET", "/api/search?q=" + encodeURIComponent(v)).then(function (r) {
      if (seq !== searchSeq) return;
      ul.textContent = "";
      if (!r.items.length) ul.appendChild(el("li", { class: "muted", text: T("search.none") }));
      r.items.forEach(function (m) {
        ul.appendChild(el("li", { onclick: function () {
          hideResults(); $("q").value = m.name || "";
          showOnMap(m.id, m.lon, m.lat);
        } }, [el("div", { text: m.name || T("m.name_unknown") }), m.address ? el("div", { class: "muted addr", text: m.address }) : null]));
      });
      ul.hidden = false;
    }).catch(function () {});
  }
  $("q").addEventListener("input", function () { clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 220); });
  $("q").addEventListener("keydown", function (e) {
    if (e.key === "Enter") { var f = $("results").querySelector("li:not(.muted)"); if (f) f.click(); }
    if (e.key === "Escape") hideResults();
  });
  document.addEventListener("click", function (e) { if (!$("search").contains(e.target)) hideResults(); });

  // ---- add mosque
  function newMarker(o) { return map.engine === "yandex" ? new map.Marker(o) : new maplibregl.Marker(o); }
  function clearPick() { if (pickMarker) { pickMarker.remove(); pickMarker = null; } }
  function setPicked(ll) {
    if (pickDone) { var f = pickDone; pickDone = null; f(ll); return; }
    picked = { lat: +ll.lat.toFixed(6), lng: +ll.lng.toFixed(6) };
    clearPick();
    pickMarker = newMarker({ color: "#059669", draggable: true }).setLngLat([picked.lng, picked.lat]).addTo(map);
    pickMarker.on("dragend", function () { var p = pickMarker.getLngLat(); picked = { lat: +p.lat.toFixed(6), lng: +p.lng.toFixed(6) }; showAddForm(); });
    showAddForm();
    revealPoint(picked.lng, picked.lat);          // keep the chosen point above the sheet
  }
  function startAdd() {
    mode = "pick"; picked = null; pickDone = null; clearPick(); closePopup();
    document.body.classList.add("picking"); $("add-bar").hidden = false; $("add-hint").textContent = T("add.pick"); hidePanel();
  }
  function endAdd() {
    mode = "browse"; pickDone = null; picked = null; clearPick();
    document.body.classList.remove("picking"); $("add-bar").hidden = true; hidePanel();
  }
  $("add-cancel").onclick = endAdd;

  function showAddForm() {
    var name = el("input", { maxlength: 200, placeholder: T("f.optional"), autocomplete: "off" });
    var addr = el("input", { maxlength: 300, placeholder: T("f.optional"), autocomplete: "off" });
    var grid = timesInputs({});
    var kids = [
      el("h2", { text: T("add.title") }),
      el("div", { class: "muted stack" }, [el("div", { text: T("add.selected", { c: fmtC(picked.lat, picked.lng) }) }), el("div", { text: T("add.notice") })]),
      field(T("f.name"), name), field(T("f.addr"), addr), el("h3", { text: T("m.times_opt") }), grid
    ];
    function submit(force, b) {
      if (b) b.disabled = true;
      var body = { lat: picked.lat, lon: picked.lng, name: name.value, address: addr.value, prayer_times: readTimes(grid), force: !!force };
      api("POST", "/api/mosques", body).then(function (m) { toast(T("toast.added")); endAdd(); loadMosques(); openMosque(m.id); })
        .catch(function (e) {
          if (b) b.disabled = false;
          if (e.error === "possible_duplicate") {
            var box = el("div", {}, [el("h2", { text: T("dup.title") })]);
            e.duplicates.forEach(function (d) {
              box.appendChild(el("div", { class: "dup stack" }, [
                el("b", d.name ? { text: d.name } : { class: "missing", text: T("m.name_unknown") }),
                el("div", { class: "muted", text: T("dup.dist", { d: Math.round(d.distance_m), s: (label(d) || [""])[0] }).replace(/ · $/, "") }),
                btn("small", T("dup.open"), function () { endAdd(); openMosque(d.id); })
              ]));
            });
            box.appendChild(el("div", { class: "row" }, [
              btn("danger", T("dup.force"), function (ev) { submit(true, ev.currentTarget); }),
              btn("ghost", T("btn.back"), function () { showAddForm(); })
            ]));
            showPanel(box);
          } else toast(errText(e));
        });
    }
    kids.push(el("div", { class: "row" }, [btn("", T("btn.save"), function (ev) { submit(false, ev.currentTarget); }), btn("ghost", T("btn.cancel"), endAdd)]));
    showPanel(el("div", {}, kids));
  }

  init();
})();
