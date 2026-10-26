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
    if (src) src.setData(lastFC); else if (map) map.once("styledata", applyMosques);
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
    var halo = { "text-color": "#064e3b", "text-halo-color": "#fff", "text-halo-width": 1.6 };
    if (c.show_tile_mosques !== false && style.sources.openmaptiles) {
      // Mosques already present in the OSM tiles, drawn live and never stored. The tile schema has no religion
      // field, so this matches on the name (masjid/мечеть/...): an interim, incomplete heuristic.
      var nm = ["downcase", ["coalesce", ["get", "name"], ""]];
      var words = ["masjid", "machit", "mechet", "mosque", "jome", "jame", "мечеть", "масжид", "масджид", "мачит", "жоме", "жаме", "مسجد"];
      style.layers.push({ id: "osm-mosques", type: "symbol", source: "openmaptiles", "source-layer": "poi", minzoom: 12,
        filter: ["all", ["==", ["get", "class"], "place_of_worship"], ["any"].concat(words.map(function (w) { return ["in", w, nm]; }))],
        layout: { "icon-image": "mosque-osm", "icon-size": ["interpolate", ["linear"], ["zoom"], 12, 0.5, 16, 0.9], "icon-allow-overlap": true,
          "text-field": ["step", ["zoom"], "", 14, ["get", "name"]], "text-font": font, "text-size": 11, "text-offset": [0, 1.5], "text-anchor": "top", "text-optional": true },
        paint: halo });
    }
    style.layers.push({ id: "mosques", type: "symbol", source: "mosques",
      layout: { "icon-image": ["match", ["get", "status"], "reported", "mosque-amber", "mosque-green"],
        "icon-size": ["interpolate", ["linear"], ["zoom"], 8, 0.6, 14, 1], "icon-allow-overlap": true,
        "text-field": ["step", ["zoom"], "", 13, ["get", "name"]], "text-font": font, "text-size": 12, "text-offset": [0, 1.6], "text-anchor": "top", "text-optional": true },
      paint: halo });
    return style;
  }

  // ---- language switch: store the choice, reload at the same map view (labels are baked into the style) ----
  function initLang() {
    var sel = $("lang");
    I18N.langs.forEach(function (l) { sel.appendChild(el("option", { value: l[0], text: l[1], title: l[2] })); });
    sel.value = I18N.lang();
    sel.onchange = function () {
      I18N.set(sel.value);
      var u = new URL(location.href);
      u.searchParams.delete("lang");
      if (map) { var c = map.getCenter(); u.searchParams.set("c", c.lat.toFixed(5) + "," + c.lng.toFixed(5) + "," + map.getZoom().toFixed(2)); }
      location.href = u.toString();                // keeps the #tgWebAppData hash inside Telegram
    };
  }

  // ---- daily prayer times strip (computed on the device; region can be switched) ----------------
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
  function prayerPos(startCenter) {
    if (prayerSel !== "me" && REGIONS[+prayerSel]) { var r = REGIONS[+prayerSel]; return { lat: r[0], lon: r[1], name: regionName(+prayerSel) }; }
    var sp = savedPos(), c = map ? map.getCenter() : null;
    var p = sp ? { lat: sp.lat, lon: sp.lon } : c ? { lat: c.lat, lon: c.lng } : { lat: startCenter[1], lon: startCenter[0] };
    return { lat: p.lat, lon: p.lon, name: T(sp ? "prayer.where_me" : "prayer.where_map") + " · " + regionName(nearestRegion(p.lat, p.lon)) };
  }
  function renderPrayer() {
    var now = new Date(), p = prayerPos(start0), o = cfg.prayer || {};
    var tz = inRegion(p.lon, p.lat) ? 5 : -now.getTimezoneOffset() / 60;
    var t = Prayer.times(now.getFullYear(), now.getMonth() + 1, now.getDate(), p.lat, p.lon, tz,
      { fajr: o.fajr_angle, isha: o.isha_angle, asr: o.asr_factor, dhuhrMin: o.dhuhr_offset_min });
    var nowH = (now.getUTCHours() + now.getUTCMinutes() / 60 + tz + 24) % 24;       // current time in that region
    var nextKey = null;
    PNAMES.forEach(function (n) { if (!nextKey && t[n] > nowH && n !== "sunrise") nextKey = n; });
    if (!nextKey) nextKey = "fajr";                                                   // after Isha: tomorrow's Fajr
    var box = $("ptimes"); box.textContent = "";
    PNAMES.forEach(function (n) { box.appendChild(el("span", { class: n === nextKey ? "next" : "", text: T("pn." + n) + " " + Prayer.format(t[n]) })); });
    var diff = t[nextKey] - nowH; if (diff <= 0) diff += 24;
    var mins = Math.round(diff * 60);
    var left = mins >= 60 ? T("dur.hm", { h: Math.floor(mins / 60), m: mins % 60 }) : T("dur.m", { m: mins });
    box.appendChild(el("span", { class: "info", text: "· " + p.name + " · " + T("prayer.approx") + " · " + T("prayer.next") + ": " + left }));
    $("prayer").title = T("prayer.tip");
  }
  var start0 = TASHKENT;
  function initPrayer() {
    var sel = $("region");
    sel.appendChild(el("option", { value: "me", text: T("prayer.me") }));
    REGIONS.forEach(function (r, i) { sel.appendChild(el("option", { value: String(i), text: regionName(i) })); });
    sel.value = prayerSel;
    sel.onchange = function () { prayerSel = sel.value; try { localStorage.setItem("preg", prayerSel); } catch (e) {} renderPrayer(); };
    $("prefresh").onclick = function () {
      var b = $("prefresh"); b.classList.remove("spin"); void b.offsetWidth; b.classList.add("spin");
      if (prayerSel === "me" && navigator.geolocation) {                       // fresh device position, then recompute
        navigator.geolocation.getCurrentPosition(function (p) {
          if (inRegion(p.coords.longitude, p.coords.latitude)) try { localStorage.setItem("lastpos", JSON.stringify({ lat: p.coords.latitude, lon: p.coords.longitude })); } catch (e) {}
          renderPrayer();
        }, renderPrayer, { maximumAge: 0, timeout: 6000 });
      } else renderPrayer();
    };
    setInterval(function () { renderPrayer(); }, 30000);                      // keeps "next prayer" and the date current
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
    var q = !!qm;
    var sp = savedPos();
    var start = q ? { center: [+qm[2], +qm[1]], zoom: +qm[3] }
      : sp ? { center: [sp.lon, sp.lat], zoom: 14 } : { center: TASHKENT, zoom: 12 };
    start0 = start.center;
    initLang();
    configPromise.then(function (c) { cfg = c; initPrayer(); });
    // first mosque query right away, around the start position (~11 km window), in parallel with the style
    var d = 0.1;
    fetchMosques([start.center[0] - d, start.center[1] - d, start.center[0] + d, start.center[1] + d]);

    configPromise.then(function (c) {
      cfg = c;
      var sp2 = c.style_url && c.style_url !== DEFAULT_STYLE ? fetch(c.style_url).then(function (r) { return r.json(); }) : stylePromise;
      return sp2.then(function (style) { return patchStyle(style, c); });
    }).then(function (style) {
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
      map.on("dragstart", function () { userMoved = true; });
      map.on("zoomstart", function (e) { if (e.originalEvent) userMoved = true; });
      map.on("moveend", function () { clearTimeout(moveTimer); moveTimer = setTimeout(ensureMosques, 250); });
      map.on("mouseenter", "mosques", function () { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", "mosques", function () { map.getCanvas().style.cursor = ""; });
      map.on("click", onMapClick);
      var deep = new URLSearchParams(location.search).get("m");          // ?m=<mosque id>: open that mosque (bot links, shares)
      if (deep && /^[0-9a-f-]{36}$/.test(deep)) openMosque(deep);
      if (!q && !deep) locate(start);
      var errs = 0;
      map.on("error", function () {                   // tile/style/sprite request failures: say so instead of a silent blank map
        if (++errs >= 3 && !map.loaded()) mapProblem(T("map.err_tiles"));
      });
      map.on("idle", function () { if (map.loaded()) $("maperr").hidden = true; });
      setTimeout(function () { if (!map.loaded()) mapProblem(T("map.err_slow")); }, 20000);
    }).catch(function () {
      mapProblem(T("map.err_style"));
    });
  }

  // Device location in the background: never blocks rendering. If the user has not touched the map yet and the
  // device is somewhere else than the cached start, jump there (inside Uzbekistan only).
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
    return { top: 0, right: 0, left: 0, bottom: Math.round(innerHeight * 0.56) };   // matches the sheet height
  }
  function revealPoint(lon, lat) { map.easeTo({ center: [lon, lat], duration: 350, padding: mapPadding() }); }

  var resizeTimer = null;
  window.addEventListener("resize", function () {          // rotation, keyboard, browser bars: re-fit instead of reflowing
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () { if (map) { map.resize(); map.easeTo({ padding: mapPadding(), duration: 0 }); } }, 120);
  });

  // ---- panel
  function showPanel(node) { var b = $("panel-body"); b.textContent = ""; b.appendChild(node); $("panel").hidden = false; $("panel").scrollTop = 0; }
  function hidePanel() { $("panel").hidden = true; if (map) map.easeTo({ padding: mapPadding(), duration: 200 }); }
  $("panel-close").onclick = hidePanel;
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

  function openMosque(id, opts) {
    opts = opts || {};
    showPanel(skeleton());
    api("GET", "/api/mosques/" + id).then(function (m) {
      var lb = label(m);
      var node = el("div", {}, [
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
          cfg.bot_username ? el("a", { class: "btn ghost small", target: "_blank", rel: "noopener noreferrer",
            href: "https://t.me/" + cfg.bot_username + "?start=m_" + m.id, text: T("cta.photo") }) : null,
          cfg.bot_username ? el("a", { class: "btn ghost small", target: "_blank", rel: "noopener noreferrer",
            href: "https://t.me/" + cfg.bot_username + "?start=s_" + m.id, text: T("cta.sub") }) : null
        ]),
        el("div", { class: "row" }, yandexLinks(m)),
        el("div", { class: "row" }, [
          btn("", T("btn.edit"), function () { editForm(m); }),
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

  function field(labelText, input) { return el("div", {}, [el("label", { text: labelText }), input]); }

  function timesInputs(values) {
    var grid = el("div", { class: "grid" });
    PRAYERS.forEach(function (p) {
      grid.appendChild(field(T("pn." + p), el("input", { type: "time", "data-p": p, value: values[p] || "" })));
    });
    return grid;
  }
  function readTimes(root) {
    var out = {};
    root.querySelectorAll("input[data-p]").forEach(function (i) { out[i.getAttribute("data-p")] = i.value || null; });
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
        meMarker = new maplibregl.Marker({ element: el("div", { class: "me-dot" }) }).setLngLat([lon, lat]).addTo(map);
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
        ul.appendChild(el("li", { text: m.name, onclick: function () {
          hideResults(); $("q").value = m.name;
          map.flyTo({ center: [m.lon, m.lat], zoom: Math.max(map.getZoom(), 16), duration: 600 });
          openMosque(m.id);
        } }));
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
  function clearPick() { if (pickMarker) { pickMarker.remove(); pickMarker = null; } }
  function setPicked(ll) {
    if (pickDone) { var f = pickDone; pickDone = null; f(ll); return; }
    picked = { lat: +ll.lat.toFixed(6), lng: +ll.lng.toFixed(6) };
    clearPick();
    pickMarker = new maplibregl.Marker({ color: "#059669", draggable: true }).setLngLat([picked.lng, picked.lat]).addTo(map);
    pickMarker.on("dragend", function () { var p = pickMarker.getLngLat(); picked = { lat: +p.lat.toFixed(6), lng: +p.lng.toFixed(6) }; showAddForm(); });
    showAddForm();
    revealPoint(picked.lng, picked.lat);          // keep the chosen point above the sheet
  }
  function startAdd() {
    mode = "pick"; picked = null; pickDone = null; clearPick(); closePopup();
    $("add-btn").hidden = true; $("search").hidden = true; $("nearest").hidden = true; $("add-bar").hidden = false; $("add-hint").textContent = T("add.pick"); hidePanel();
  }
  function endAdd() {
    mode = "browse"; pickDone = null; picked = null; clearPick();
    $("add-btn").hidden = false; $("search").hidden = false; $("nearest").hidden = false; $("add-bar").hidden = true; hidePanel();
  }
  $("add-btn").onclick = startAdd;
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
