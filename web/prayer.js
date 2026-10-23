/* Daily prayer times from the sun's position (standard astronomical method, same family as PrayTimes.org).
 * Computed in the browser: no API, no key, no request leaves the device. Values are approximate; the owner can
 * tune the angles/Asr factor in the server config (PRAYER_* env) to match the official Uzbek calendar. */
(function (root) {
  "use strict";
  var R = Math.PI / 180;
  function sin(d) { return Math.sin(d * R); }
  function cos(d) { return Math.cos(d * R); }
  function tan(d) { return Math.tan(d * R); }
  function acos(x) { return Math.acos(Math.max(-1, Math.min(1, x))) / R; }
  function atan2(y, x) { return Math.atan2(y, x) / R; }
  function fix(v, n) { v = v - n * Math.floor(v / n); return v < 0 ? v + n : v; }

  function julian(y, m, d) {
    if (m <= 2) { y -= 1; m += 12; }
    var a = Math.floor(y / 100), b = 2 - a + Math.floor(a / 4);
    return Math.floor(365.25 * (y + 4716)) + Math.floor(30.6001 * (m + 1)) + d + b - 1524.5;
  }
  function sunPosition(jd) {
    var D = jd - 2451545.0;
    var g = fix(357.529 + 0.98560028 * D, 360), q = fix(280.459 + 0.98564736 * D, 360);
    var L = fix(q + 1.915 * sin(g) + 0.020 * sin(2 * g), 360);
    var e = 23.439 - 0.00000036 * D;
    var ra = atan2(cos(e) * sin(L), cos(L)) / 15;
    return { decl: Math.asin(sin(e) * sin(L)) / R, eqt: q / 15 - fix(ra, 24) };
  }

  /* year, month(1-12), day, lat, lon (east +), tz (hours from UTC), opts {fajr, isha, asr, dhuhrMin}
   * returns hours (0-24, local) for fajr, sunrise, dhuhr, asr, maghrib, isha */
  function times(y, m, d, lat, lon, tz, opts) {
    opts = opts || {};
    var fajrA = opts.fajr == null ? 15 : opts.fajr, ishaA = opts.isha == null ? 15 : opts.isha, asrF = opts.asr == null ? 2 : opts.asr;
    var sp = sunPosition(julian(y, m, d) + 0.5 - lon / 360);          // sun at local noon of that day
    var noon = fix(12 - sp.eqt, 24);
    function ang(a, ccw) {
      var t = acos((-sin(a) - sin(sp.decl) * sin(lat)) / (cos(sp.decl) * cos(lat))) / 15;
      return noon + (ccw ? -t : t);
    }
    var asrAngle = -Math.atan(1 / (asrF + tan(Math.abs(lat - sp.decl)))) / R;
    var asrT = noon + acos((-sin(asrAngle) - sin(sp.decl) * sin(lat)) / (cos(sp.decl) * cos(lat))) / 15;
    var shift = tz - lon / 15;
    var out = { fajr: ang(fajrA, true), sunrise: ang(0.833, true), dhuhr: noon + (opts.dhuhrMin || 0) / 60, asr: asrT, maghrib: ang(0.833, false), isha: ang(ishaA, false) };
    Object.keys(out).forEach(function (k) { out[k] = fix(out[k] + shift, 24); });
    return out;
  }
  function hm(h) {
    if (!isFinite(h)) return "--:--";
    var t = Math.round(h * 60), hh = Math.floor(t / 60) % 24, mm = t % 60;
    return (hh < 10 ? "0" : "") + hh + ":" + (mm < 10 ? "0" : "") + mm;
  }
  root.Prayer = { times: times, format: hm };
})(typeof window !== "undefined" ? window : this);
