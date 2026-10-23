/* masjid-uz web client. Runs in browsers, mobile webviews and as a Telegram Mini App.
 * All user-supplied text is inserted with textContent (never innerHTML). */
(function () {
  "use strict";
  var PRAYERS = [["fajr", "Bomdod"], ["dhuhr", "Peshin"], ["asr", "Asr"], ["maghrib", "Shom"], ["isha", "Xufton"], ["jumuah", "Juma"]];
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
    clearTimeout(toast.t); toast.t = setTimeout(function () { t.hidden = true; }, 3500);
  }
  function errText(e) { return (e && e.message) || "Xatolik yuz berdi"; }

  // ---- labels: must reflect real status, never imply verification
  function label(m) {
    if (m.status === "verified") return ["Tasdiqlangan", ""];
    if (m.status === "reported") return ["Xabar berilgan (tekshirilmoqda)", "reported"];
    if (m.origin === "community") return ["Foydalanuvchi qo‘shgan masjid", ""];
    if (m.source === "openstreetmap") return ["OpenStreetMap ma’lumoti · tasdiqlanmagan", ""];
    return ["Tasdiqlanmagan", ""];
  }
  function fmtTime(iso) { return iso ? new Date(iso).toLocaleString("uz-UZ") : "—"; }

  // ---- navigation: opens an external app/site; nothing is requested from or stored by Yandex
  function yandexLinks(m) {
    var ll = m.lon + "," + m.lat;
    return [
      el("a", { class: "btn", target: "_blank", rel: "noopener noreferrer",
        href: "https://yandex.com/maps/?pt=" + ll + "&z=17&l=map", text: "Yandex Maps‘da ochish" }),
      el("a", { class: "btn", target: "_blank", rel: "noopener noreferrer",
        href: "https://yandex.com/maps/?rtext=~" + m.lat + "," + m.lon + "&rtt=auto", text: "Yo‘nalish" })
    ];
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
  function loadMosques() {                          // compatibility: force-refresh the current window
    loadedBox = null; ensureMosques();
  }
  function ensureMosques() {
    var b = map.getBounds(), w = b.getWest(), s = b.getSouth(), e = b.getEast(), n = b.getNorth();
    if (loadedBox && w >= loadedBox[0] && s >= loadedBox[1] && e <= loadedBox[2] && n <= loadedBox[3]) return;   // already covered
    var px = (e - w) * 0.5, py = (n - s) * 0.5;      // fetch 50% extra on every side
    fetchMosques([w - px, s - py, e + px, n + py].map(function (v) { return +v.toFixed(5); }));
  }

  function patchStyle(style, c) {
    // Only mosques should be visible: drop the base style's other POIs; add our own sources/layers up front
    // so nothing has to wait for the "load" event.
    style.layers = style.layers.filter(function (l) { return l["source-layer"] !== "poi" && l["source-layer"] !== "housenumber"; });
    style.sources.mosques = { type: "geojson", data: lastFC };
    var font = ["Noto Sans Regular"];
    var halo = { "text-color": "#0b4f3b", "text-halo-color": "#fff", "text-halo-width": 1.5 };
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

  // ---- daily prayer times strip (computed on the device; region can be switched) ----------------
  var REGIONS = [["Toshkent shahri", 41.3111, 69.2797], ["Toshkent viloyati", 41.0167, 69.35], ["Andijon", 40.7821, 72.3442],
    ["Buxoro", 39.7747, 64.4286], ["Farg‘ona", 40.3864, 71.7864], ["Jizzax", 40.1158, 67.8422], ["Xorazm", 41.55, 60.6333],
    ["Namangan", 41.0011, 71.6683], ["Navoiy", 40.0844, 65.3792], ["Qashqadaryo", 38.8606, 65.7891],
    ["Qoraqalpog‘iston", 42.4531, 59.6103], ["Samarqand", 39.6542, 66.9597], ["Sirdaryo", 40.4897, 68.7842], ["Surxondaryo", 37.2242, 67.2783]];
  var PNAMES = [["fajr", "Bomdod"], ["sunrise", "Quyosh"], ["dhuhr", "Peshin"], ["asr", "Asr"], ["maghrib", "Shom"], ["isha", "Xufton"]];
  var prayerSel = "me", prayerDay = "";
  try { prayerSel = localStorage.getItem("preg") || "me"; } catch (e) {}
  function nearestRegion(lat, lon) {
    var best = 0, bd = 1e9;
    REGIONS.forEach(function (r, i) { var d = Math.pow(r[1] - lat, 2) + Math.pow((r[2] - lon) * Math.cos(lat * Math.PI / 180), 2); if (d < bd) { bd = d; best = i; } });
    return best;
  }
  function prayerPos(startCenter) {
    if (prayerSel !== "me" && REGIONS[+prayerSel]) { var r = REGIONS[+prayerSel]; return { lat: r[1], lon: r[2], name: r[0] }; }
    var sp = savedPos(), c = map ? map.getCenter() : null;
    var p = sp ? { lat: sp.lat, lon: sp.lon } : c ? { lat: c.lat, lon: c.lng } : { lat: startCenter[1], lon: startCenter[0] };
    return { lat: p.lat, lon: p.lon, name: (sp ? "Mening joyim" : "Xarita markazi") + " · " + REGIONS[nearestRegion(p.lat, p.lon)][0] };
  }
  function renderPrayer() {
    var now = new Date(), p = prayerPos(start0), o = cfg.prayer || {};
    var tz = inRegion(p.lon, p.lat) ? 5 : -now.getTimezoneOffset() / 60;
    var t = Prayer.times(now.getFullYear(), now.getMonth() + 1, now.getDate(), p.lat, p.lon, tz,
      { fajr: o.fajr_angle, isha: o.isha_angle, asr: o.asr_factor, dhuhrMin: o.dhuhr_offset_min });
    var nowH = (now.getUTCHours() + now.getUTCMinutes() / 60 + tz + 24) % 24;       // current time in that region
    var nextKey = null;
    PNAMES.forEach(function (n) { if (!nextKey && t[n[0]] > nowH && n[0] !== "sunrise") nextKey = n[0]; });
    if (!nextKey) nextKey = "fajr";                                                   // after Xufton: tomorrow's Bomdod
    var box = $("ptimes"); box.textContent = "";
    PNAMES.forEach(function (n) { box.appendChild(el("span", { class: n[0] === nextKey ? "next" : "", text: n[1] + " " + Prayer.format(t[n[0]]) })); });
    var diff = t[nextKey] - nowH; if (diff <= 0) diff += 24;
    var mins = Math.round(diff * 60);
    box.appendChild(el("span", { text: "· " + p.name + " · taxminiy · keyingi: " + (mins >= 60 ? Math.floor(mins / 60) + " soat " : "") + (mins % 60) + " daq" }));
    prayerDay = now.toDateString();
    $("prayer").title = "Quyosh holatidan hisoblangan taxminiy vaqt. Masjid jamoat vaqti boshqacha bo‘lishi mumkin.";
  }
  var start0 = TASHKENT;
  function initPrayer() {
    var sel = $("region");
    sel.appendChild(el("option", { value: "me", text: "📍 Mening joyim" }));
    REGIONS.forEach(function (r, i) { sel.appendChild(el("option", { value: String(i), text: r[0] })); });
    sel.value = prayerSel;
    sel.onchange = function () { prayerSel = sel.value; try { localStorage.setItem("preg", prayerSel); } catch (e) {} renderPrayer(); };
    $("prefresh").onclick = function () {
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

  function init() {
    var q = /[?&]c=([-\d.]+),([-\d.]+),([\d.]+)/.exec(location.search);   // optional deep link: ?c=lat,lon,zoom
    var sp = savedPos();
    var start = q ? { center: [+q[2], +q[1]], zoom: +q[3] }
      : sp ? { center: [sp.lon, sp.lat], zoom: 14 } : { center: TASHKENT, zoom: 12 };
    start0 = start.center;
    configPromise.then(function (c) { cfg = c; initPrayer(); });
    // first mosque query right away, around the start position (~11 km window), in parallel with the style
    var d = 0.1;
    fetchMosques([start.center[0] - d, start.center[1] - d, start.center[0] + d, start.center[1] + d]);

    configPromise.then(function (c) {
      cfg = c;
      var sp2 = c.style_url && c.style_url !== DEFAULT_STYLE ? fetch(c.style_url).then(function (r) { return r.json(); }) : stylePromise;
      return sp2.then(function (style) { return patchStyle(style, c); });
    }).then(function (style) {
      map = new maplibregl.Map({ container: "map", style: style, center: start.center, zoom: start.zoom,
        minZoom: 5, maxBounds: REGION, fadeDuration: 0, pixelRatio: Math.min(window.devicePixelRatio || 1, 2),
        attributionControl: { compact: true } });
      map.on("styleimagemissing", function (e) {      // our own icons, created on demand
        var colors = { "mosque-green": "#0b7a5b", "mosque-amber": "#b45309", "mosque-osm": "#4aa586" };
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
      if (!q) locate(start);
      var errs = 0;
      map.on("error", function (e) {                  // tile/style/sprite request failures: say so instead of a silent blank map
        if (++errs >= 3 && !map.loaded()) mapProblem("Xarita ma’lumotlari yuklanmayapti (tiles.openfreemap.org). Internet, VPN yoki tarmoq filtrini tekshiring.");
      });
      map.on("idle", function () { if (map.loaded()) $("maperr").hidden = true; });
      setTimeout(function () { if (!map.loaded()) mapProblem("Xarita uzoq yuklanmoqda. Ulanish sekin bo‘lishi mumkin."); }, 20000);
    }).catch(function () {
      mapProblem("Xarita uslubini yuklab bo‘lmadi (tiles.openfreemap.org). Internet, VPN yoki tarmoq filtrini tekshiring.");
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
    var body = el("div", { style: "max-width:230px;color:#1b1f23" }, [
      el("b", { text: poi.properties.name || "Masjid" }),
      el("div", { class: "muted", text: "Xarita ma’lumoti (© OpenStreetMap). Hali bizning bazamizda yo‘q." }),
      el("div", { class: "row" }, [])
    ]);
    var row = body.lastChild;
    api("GET", "/api/mosques/nearby?lat=" + c[1] + "&lon=" + c[0] + "&radius=" + cfg.duplicate_radius_m).then(function (r) {
      if (r.items.length) row.appendChild(el("button", { text: "Bizning sahifani ochish", onclick: function () { closePopup(); openMosque(r.items[0].id); } }));
      else row.appendChild(el("button", { text: "Bazaga qo‘shish", onclick: function () { closePopup(); startAdd(); setPicked({ lng: c[0], lat: c[1] }); } }));
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
    if (sideLayout()) return { top: 0, right: 0, bottom: 0, left: Math.min(392, innerWidth * 0.55 + 12) };
    return { top: 0, right: 0, left: 0, bottom: Math.round(innerHeight * 0.52) };   // matches the sheet height
  }
  function revealPoint(lon, lat) { map.easeTo({ center: [lon, lat], duration: 350, padding: mapPadding() }); }

  var resizeTimer = null;
  window.addEventListener("resize", function () {          // rotation, keyboard, browser bars: re-fit instead of reflowing
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () { if (map) { map.resize(); map.easeTo({ padding: mapPadding(), duration: 0 }); } }, 120);
  });

  // ---- panel
  function showPanel(node) { var b = $("panel-body"); b.textContent = ""; b.appendChild(node); $("panel").hidden = false; }
  function hidePanel() { $("panel").hidden = true; if (map) map.easeTo({ padding: mapPadding(), duration: 200 }); }
  $("panel-close").onclick = hidePanel;

  function timesTable(m) {
    var t = el("table");
    PRAYERS.forEach(function (p) {
      var v = m.prayer_times[p[0]];
      t.appendChild(el("tr", {}, [el("td", { text: p[1] }), el("td", v ? { text: v } : { class: "missing", text: "ma’lum emas" })]));
    });
    return t;
  }

  function openMosque(id) {
    api("GET", "/api/mosques/" + id).then(function (m) {
      var lb = label(m);
      var node = el("div", {}, [
        el("h2", m.name ? { text: m.name } : { class: "missing", text: "Nomi ma’lum emas" }),
        el("span", { class: "badge " + lb[1], text: lb[0] }),
        el("p", m.address ? { text: m.address } : { class: "missing", text: "Manzil ma’lum emas" }),
        el("div", { class: "muted", text: m.lat.toFixed(6) + ", " + m.lon.toFixed(6) + " · versiya " + m.version }),
        el("div", { class: "muted", text: "Oxirgi o‘zgarish: " + fmtTime(m.updated_at) }),
        m.source === "openstreetmap" ? el("div", { class: "muted", text: "Joylashuv va nom: © OpenStreetMap contributors (ODbL). Namoz vaqtlari jamoa tomonidan kiritiladi." }) : null,
        m.verification_note ? el("div", { class: "muted", text: "Tasdiq asosi: " + m.verification_note }) : null,
        el("h3", { text: "Jamoat namozi vaqtlari" }), timesTable(m),
        el("div", { class: "row" }, yandexLinks(m)),
        el("div", { class: "row" }, [
          el("button", { text: "Tahrirlash", onclick: function () { editForm(m); } }),
          el("button", { class: "ghost", text: "Tarix", onclick: function () { historyView(m); } }),
          el("button", { class: "danger", text: "Xato haqida xabar", onclick: function () { reportForm(m); } })
        ])
      ]);
      showPanel(node);
      revealPoint(m.lon, m.lat);
    }).catch(function (e) { toast(errText(e)); });
  }

  function field(labelText, input) { return el("div", {}, [el("label", { text: labelText }), input]); }

  function timesInputs(values) {
    var grid = el("div", { class: "grid" });
    PRAYERS.forEach(function (p) {
      grid.appendChild(field(p[1], el("input", { type: "time", "data-p": p[0], value: values[p[0]] || "" })));
    });
    return grid;
  }
  function readTimes(root) {
    var out = {};
    root.querySelectorAll("input[data-p]").forEach(function (i) { out[i.getAttribute("data-p")] = i.value || null; });
    return out;
  }

  function editForm(m) {
    var name = el("input", { value: m.name || "", maxlength: 200 });
    var addr = el("input", { value: m.address || "", maxlength: 300 });
    var grid = timesInputs(m.prayer_times);
    var note = el("input", { placeholder: "Qisqa izoh (ixtiyoriy)", maxlength: 300 });
    var coords = el("div", { class: "muted", text: "Joylashuv: " + m.lat.toFixed(6) + ", " + m.lon.toFixed(6) });
    var newPos = null;
    showPanel(el("div", {}, [
      el("h2", { text: "Tahrirlash" }), field("Nomi", name), field("Manzil", addr),
      el("h3", { text: "Jamoat namozi vaqtlari" }), grid, field("Izoh", note),
      coords,
      el("div", { class: "row" }, [
        el("button", { class: "ghost", text: "Joyni xaritadan o‘zgartirish", onclick: function () {
          mode = "pick"; $("add-bar").hidden = false; $("add-hint").textContent = "Yangi joyni bosing"; hidePanel();
          pickDone = function (ll) { newPos = ll; mode = "browse"; $("add-bar").hidden = true; clearPick(); showPanel(back); coords.textContent = "Yangi joy: " + ll.lat.toFixed(6) + ", " + ll.lng.toFixed(6); };
        } })
      ]),
      el("div", { class: "row" }, [
        el("button", { text: "Saqlash", onclick: function () {
          var body = { expected_version: m.version, name: name.value, address: addr.value, prayer_times: readTimes(grid), note: note.value };
          if (newPos) { body.lat = newPos.lat; body.lon = newPos.lng; }
          api("PATCH", "/api/mosques/" + m.id, body).then(function (u) { toast("Saqlandi"); loadMosques(); openMosque(u.id); })
            .catch(function (e) {
              if (e.error === "version_conflict") { toast("Boshqa kishi o‘zgartirgan. Yangilandi, qayta urining."); openMosque(m.id); }
              else toast(errText(e));
            });
        } }),
        el("button", { class: "ghost", text: "Bekor", onclick: function () { openMosque(m.id); } })
      ])
    ]));
    var back = $("panel-body").firstChild;
  }
  var pickDone = null;

  function historyView(m) {
    var box = el("div", {}, [el("h2", { text: "O‘zgarishlar tarixi" }), el("div", { class: "muted", text: "Yuklanmoqda…" })]);
    showPanel(box);
    api("GET", "/api/mosques/" + m.id + "/history").then(function (r) {
      box.textContent = "";
      box.appendChild(el("h2", { text: "O‘zgarishlar tarixi" }));
      r.items.forEach(function (h) {
        var lines = Object.keys(h.changes).map(function (k) {
          return k + ": " + JSON.stringify(h.changes[k][0]) + " → " + JSON.stringify(h.changes[k][1]);
        });
        box.appendChild(el("div", { class: "hist" }, [
          el("b", { text: h.action + " · v" + h.version }),
          el("div", { class: "muted", text: fmtTime(h.at) + (h.actor ? " · " + ({ moderator: "moderator", osm: "OpenStreetMap", user: "foydalanuvchi" }[h.actor] || h.actor) : "") }),
          el("div", { text: lines.join("; ") }), h.note ? el("div", { class: "muted", text: h.note }) : null
        ]));
      });
      box.appendChild(el("button", { class: "ghost", text: "Orqaga", onclick: function () { openMosque(m.id); } }));
    }).catch(function (e) { toast(errText(e)); });
  }

  function reportForm(m) {
    var kind = el("select", {}, [["incorrect", "Noto‘g‘ri ma’lumot"], ["duplicate", "Takroriy yozuv"], ["relocated", "Ko‘chirilgan"], ["closed", "Mavjud emas / yopilgan"], ["other", "Boshqa"]]
      .map(function (k) { return el("option", { value: k[0], text: k[1] }); }));
    var reason = el("textarea", { rows: 3, maxlength: 1000, placeholder: "Sababini yozing (majburiy)" });
    showPanel(el("div", {}, [
      el("h2", { text: "Xato haqida xabar" }),
      el("p", { class: "muted", text: "Yozuv darhol o‘chirilmaydi: xabarlar guruhlanib moderator tomonidan ko‘rib chiqiladi. Tarix saqlanadi." }),
      field("Turi", kind), field("Sabab", reason),
      el("div", { class: "row" }, [
        el("button", { text: "Yuborish", onclick: function () {
          api("POST", "/api/mosques/" + m.id + "/reports", { kind: kind.value, reason: reason.value })
            .then(function () { toast("Xabaringiz qabul qilindi"); loadMosques(); openMosque(m.id); })
            .catch(function (e) { toast(errText(e)); });
        } }),
        el("button", { class: "ghost", text: "Bekor", onclick: function () { openMosque(m.id); } })
      ])
    ]));
  }

  // ---- search by name (our own database only)
  var searchTimer = null;
  function hideResults() { $("results").hidden = true; }
  function runSearch() {
    var v = $("q").value.trim(), ul = $("results");
    if (v.length < 2) { hideResults(); return; }
    api("GET", "/api/search?q=" + encodeURIComponent(v)).then(function (r) {
      if ($("q").value.trim() !== v) return;
      ul.textContent = "";
      if (!r.items.length) ul.appendChild(el("li", { class: "muted", text: "Hech narsa topilmadi" }));
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
  $("q").addEventListener("input", function () { clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 250); });
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
    pickMarker = new maplibregl.Marker({ color: "#0b7a5b", draggable: true }).setLngLat([picked.lng, picked.lat]).addTo(map);
    pickMarker.on("dragend", function () { var p = pickMarker.getLngLat(); picked = { lat: +p.lat.toFixed(6), lng: +p.lng.toFixed(6) }; showAddForm(); });
    showAddForm();
    revealPoint(picked.lng, picked.lat);          // keep the chosen point above the sheet
  }
  function startAdd() {
    mode = "pick"; picked = null; pickDone = null; clearPick(); closePopup();
    $("add-btn").hidden = true; $("search").hidden = true; $("add-bar").hidden = false; $("add-hint").textContent = "Xaritada masjid joyini bosing"; hidePanel();
  }
  function endAdd() {
    mode = "browse"; pickDone = null; picked = null; clearPick();
    $("add-btn").hidden = false; $("search").hidden = false; $("add-bar").hidden = true; hidePanel();
  }
  $("add-btn").onclick = startAdd;
  $("add-cancel").onclick = endAdd;

  function showAddForm(dups) {
    var name = el("input", { maxlength: 200, placeholder: "Ixtiyoriy" });
    var addr = el("input", { maxlength: 300, placeholder: "Ixtiyoriy" });
    var grid = timesInputs({});
    var kids = [
      el("h2", { text: "Yangi masjid" }),
      el("div", { class: "muted", text: "Tanlangan joy: " + picked.lat.toFixed(6) + ", " + picked.lng.toFixed(6) + " (markerni sudrab to‘g‘rilash mumkin)" }),
      el("div", { class: "muted", text: "Bu yozuv “Foydalanuvchi qo‘shgan masjid” deb belgilanadi va tasdiqlangan hisoblanmaydi." }),
      field("Nomi", name), field("Manzil", addr), el("h3", { text: "Jamoat namozi vaqtlari (ixtiyoriy)" }), grid
    ];
    function submit(force) {
      var body = { lat: picked.lat, lon: picked.lng, name: name.value, address: addr.value, prayer_times: readTimes(grid), force: !!force };
      api("POST", "/api/mosques", body).then(function (m) { toast("Qo‘shildi"); endAdd(); loadMosques(); openMosque(m.id); })
        .catch(function (e) {
          if (e.error === "possible_duplicate") {
            var box = el("div", {}, [el("h2", { text: "Yaqin atrofda masjid bor" })]);
            e.duplicates.forEach(function (d) {
              box.appendChild(el("div", { class: "dup" }, [
                el("b", d.name ? { text: d.name } : { class: "missing", text: "Nomi ma’lum emas" }),
                el("div", { class: "muted", text: Math.round(d.distance_m) + " m uzoqlikda · " + label(d)[0] }),
                el("button", { text: "Shuni ochish", onclick: function () { endAdd(); openMosque(d.id); } })
              ]));
            });
            box.appendChild(el("div", { class: "row" }, [
              el("button", { class: "danger", text: "Baribir yangisini qo‘shish", onclick: function () { submit(true); } }),
              el("button", { class: "ghost", text: "Orqaga", onclick: function () { showAddForm(); } })
            ]));
            showPanel(box);
          } else toast(errText(e));
        });
    }
    kids.push(el("div", { class: "row" }, [el("button", { text: "Saqlash", onclick: function () { submit(false); } }), el("button", { class: "ghost", text: "Bekor", onclick: endAdd })]));
    showPanel(el("div", {}, kids));
  }

  init();
})();
