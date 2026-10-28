/* Yandex Maps JavaScript API v3 behind the small part of the MapLibre interface that app.js uses, so the rest of the page does not care which
 * engine draws the map. Zoom is exchanged in the MapLibre scale (Yandex's own is one more). Loaded only when the server hands out a Yandex key
 * (admin panel -> Map); any failure rejects, and app.js falls back to OpenStreetMap and reports the key. */
(function (root) {
  "use strict";
  var TILE_ZOOM_SHIFT = 1;

  function loadApi(key, lang) {
    return new Promise(function (resolve, reject) {
      if (root.ymaps3) { root.ymaps3.ready.then(resolve, reject); return; }
      var s = document.createElement("script");
      s.src = "https://api-maps.yandex.ru/v3/?apikey=" + encodeURIComponent(key) + "&lang=" + (lang === "en" ? "en_US" : "ru_RU");
      s.async = true;
      var t = setTimeout(function () { reject(new Error("yandex timeout")); }, 15000);
      s.onload = function () { if (!root.ymaps3) { clearTimeout(t); reject(new Error("yandex missing")); return; } root.ymaps3.ready.then(function () { clearTimeout(t); resolve(); }, function (e) { clearTimeout(t); reject(e); }); };
      s.onerror = function () { clearTimeout(t); reject(new Error("yandex script")); };
      document.head.appendChild(s);
    });
  }

  function mercY(lat) { var s = Math.sin(lat * Math.PI / 180); s = Math.max(-0.9999, Math.min(0.9999, s)); return 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI); }
  function shiftPx(lon, lat, dx, dy, zoom) {                // move a point by screen pixels (MapLibre zoom scale: 512 px tiles)
    var world = 512 * Math.pow(2, zoom), x = (lon + 180) / 360 * world + dx, y = mercY(lat) * world + dy;
    var n = Math.PI - 2 * Math.PI * y / world;
    return [x / world * 360 - 180, 180 / Math.PI * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n)))];
  }

  function create(opts) {                                   // opts: container id, center [lon,lat], zoom, bounds [[w,s],[e,n]], dark, lang, onError
    return loadApi(opts.key, opts.lang).then(function () {
      var Y = root.ymaps3;
      var el = document.getElementById(opts.container);
      var state = { z: opts.zoom + TILE_ZOOM_SHIFT, c: opts.center, programmatic: 0 };
      var handlers = {}, markers = {}, labels = false, ready = false;
      function on(ev, fn) { (handlers[ev] = handlers[ev] || []).push(fn); }
      function emit(ev, data) { (handlers[ev] || []).slice().forEach(function (f) { f(data); }); }
      var map = new Y.YMap(el, {
        location: { center: opts.center, zoom: state.z },
        theme: opts.dark ? "dark" : "light",
        behaviors: ["drag", "scrollZoom", "dblClick", "pinchZoom"],
        restrictMapArea: opts.bounds ? [[opts.bounds[0][0], opts.bounds[1][1]], [opts.bounds[1][0], opts.bounds[0][1]]] : undefined,
        zoomRange: { min: 6, max: 20 }
      }, [new Y.YMapDefaultSchemeLayer({ theme: opts.dark ? "dark" : "light" }), new Y.YMapDefaultFeaturesLayer({})]);
      var markerLayer = el;                                  // DOM markers carry the CSS class that shows names from zoom 13
      function userCaused() { return Date.now() > state.programmatic; }
      var listener = new Y.YMapListener({
        layer: "any",
        onActionStart: function () { if (userCaused()) { emit("dragstart", {}); emit("zoomstart", { originalEvent: true }); } },
        onUpdate: function (u) {
          state.c = u.location.center; state.z = u.location.zoom;
          var show = state.z - TILE_ZOOM_SHIFT >= 13;
          if (show !== labels) { labels = show; markerLayer.classList.toggle("ym-labels", show); }
        },
        onActionEnd: function () { emit("moveend", {}); },
        onClick: function (obj, ev) {
          if (obj) return;                                   // a marker handles its own click
          var c = ev.coordinates, sc = ev.screenCoordinates || [0, 0];
          emit("click", { lngLat: { lng: c[0], lat: c[1] }, point: { x: sc[0], y: sc[1] } });
        }
      });
      map.addChild(listener);
      ready = true;

      // zoom buttons (the Yandex control package), best effort
      Y.import("@yandex/ymaps3-controls@0.0.1").then(function (ctl) {
        var controls = new Y.YMapControls({ position: "right" });
        controls.addChild(new ctl.YMapZoomControl({}));
        map.addChild(controls);
      }).catch(function () {});

      var facade = {
        engine: "yandex",
        on: function (ev, a, b) { if (typeof a === "function") on(ev, a); },           // layer-specific events (mouseenter on "mosques") are not needed
        once: function (ev, fn) { var w = function (d) { handlers[ev] = (handlers[ev] || []).filter(function (f) { return f !== w; }); fn(d); }; on(ev, w); if (ev === "load" || ev === "styledata") setTimeout(w, 0); },
        loaded: function () { return ready; },
        resize: function () { map.update({}); },
        getZoom: function () { return state.z - TILE_ZOOM_SHIFT; },
        getCenter: function () { return { lng: state.c[0], lat: state.c[1] }; },
        getBounds: function () {
          var b = map.bounds;                                // [[west, north], [east, south]]
          return { getWest: function () { return b[0][0]; }, getNorth: function () { return b[0][1]; }, getEast: function () { return b[1][0]; }, getSouth: function () { return b[1][1]; } };
        },
        getCanvas: function () { return el; },
        getLayer: function (id) { return id === "mosques"; },
        queryRenderedFeatures: function () { return facade._hit ? [facade._hit] : []; },
        setStyle: function () { /* recoloured by setDark */ },
        setDark: function (dark) { map.update({ theme: dark ? "dark" : "light" }); },
        _go: function (center, zoomMl, ms) {
          state.programmatic = Date.now() + (ms || 0) + 300;
          var loc = { center: center, zoom: zoomMl + TILE_ZOOM_SHIFT };
          if (ms) loc.duration = ms;
          map.setLocation(loc);
          state.c = center; state.z = loc.zoom;
        },
        jumpTo: function (o) { facade._go(o.center, o.zoom != null ? o.zoom : facade.getZoom(), 0); },
        easeTo: function (o) {
          if (o.padding) map.setMargin([o.padding.top || 0, o.padding.right || 0, o.padding.bottom || 0, o.padding.left || 0]);
          if (o.center) facade._go(o.center, facade.getZoom(), o.duration || 0);
        },
        fitBounds: function (b, o) {                         // b = [[w,s],[e,n]]: fit inside the free part, never closer than o.maxZoom
          var p = o.padding || {}, w = el.clientWidth, h = el.clientHeight, l = p.left || 0, t = p.top || 0, r = p.right || 0, bt = p.bottom || 0;
          var availW = Math.max(60, w - l - r), availH = Math.max(60, h - t - bt);
          var lonSpan = Math.max(1e-9, Math.abs(b[1][0] - b[0][0]) / 360), latSpan = Math.max(1e-9, Math.abs(mercY(b[1][1]) - mercY(b[0][1])));
          var z = Math.min(Math.log(availW / (512 * lonSpan)) / Math.LN2, Math.log(availH / (512 * latSpan)) / Math.LN2, o.maxZoom || 18);
          z = Math.max(5, z);
          var cx = (b[0][0] + b[1][0]) / 2, cy = (b[0][1] + b[1][1]) / 2;
          var c = shiftPx(cx, cy, w / 2 - (l + availW / 2), h / 2 - (t + availH / 2), z);
          facade._go(c, z, o.duration || 0);
        },
        // mosques: setData(FeatureCollection) like a MapLibre geojson source
        getSource: function (id) {
          if (id !== "mosques") return null;
          return { setData: function (fc) {
            var seen = {};
            fc.features.forEach(function (f) {
              var id = f.properties.id; seen[id] = 1;
              var m = markers[id];
              if (m && m.status === f.properties.status && m.name === f.properties.name) return;
              if (m) map.removeChild(m.obj);
              markers[id] = makeMarker(f);
            });
            Object.keys(markers).forEach(function (id) { if (!seen[id]) { map.removeChild(markers[id].obj); delete markers[id]; } });
          } };
        },
        destroy: function () { map.destroy(); }
      };
      function makeMarker(f) {
        var p = f.properties, c = f.geometry.coordinates;
        var wrap = document.createElement("div"); wrap.className = "ym-pin";
        var img = document.createElement("img"); img.src = opts.icon(p.status === "reported" ? "#b45309" : "#059669"); img.alt = ""; img.draggable = false;
        var nm = document.createElement("span"); nm.className = "ym-name"; nm.textContent = p.name || "";
        wrap.appendChild(img); wrap.appendChild(nm);
        wrap.addEventListener("click", function (e) {
          e.stopPropagation();
          facade._hit = f;
          emit("click", { lngLat: { lng: c[0], lat: c[1] }, point: { x: 0, y: 0 } });
          facade._hit = null;
        });
        var obj = new Y.YMapMarker({ coordinates: c }, wrap);
        map.addChild(obj);
        return { obj: obj, status: p.status, name: p.name };
      }
      // free-standing markers (the "me" dot, the draggable pin when a spot is chosen)
      facade.Marker = function (o) {
        var self = this, coords = [0, 0], obj = null, ownEl = o.element || (function () { var d = document.createElement("div"); d.className = "ym-pick"; return d; })(), drag = null;
        function build() {
          var props = { coordinates: coords };
          if (o.draggable) { props.draggable = true; props.onDragEnd = function (c) { coords = c; if (drag) drag(); }; }
          obj = new Y.YMapMarker(props, ownEl);
          map.addChild(obj);
        }
        self.setLngLat = function (c) { coords = c; return self; };
        self.getLngLat = function () { return { lng: coords[0], lat: coords[1] }; };
        self.addTo = function () { build(); return self; };
        self.remove = function () { if (obj) { map.removeChild(obj); obj = null; } };
        self.on = function (ev, fn) { if (ev === "dragend") drag = fn; return self; };
      };
      return facade;
    });
  }
  root.YandexMap = { create: create };
})(window);
