(function (root) {
  "use strict";
  var D = /*DICT*/{};
  var LANGS = /*LANGS*/[];
  var HTML_LANG = { uz: "uz", "uz-cyrl": "uz-Cyrl", ru: "ru", en: "en" };

  function norm(tag) {                       // BCP-47-ish tag -> one of our codes, or null
    tag = String(tag || "").toLowerCase().replace(/_/g, "-");
    if (!tag) return null;
    if (/^uz(-(cyrl|cyrillic)|-uz-cyrl)/.test(tag)) return "uz-cyrl";
    if (tag.indexOf("uz") === 0) return "uz";
    if (tag.indexOf("ru") === 0 || /^(kk|ky|tg|tk|be|uk)/.test(tag)) return "ru";   // Russian is the lingua franca around Uzbekistan
    if (tag.indexOf("en") === 0) return "en";
    return null;
  }
  function telegramLang() {                  // user language from the Mini App launch parameters (no script needed)
    try {
      var m = /[#&]tgWebAppData=([^&]+)/.exec(location.hash);
      if (!m) return null;
      var u = new URLSearchParams(decodeURIComponent(m[1])).get("user");
      return u ? JSON.parse(u).language_code : null;
    } catch (e) { return null; }
  }
  function detect() {
    var q = /[?&]lang=([\w-]+)/.exec(location.search);
    var saved = null;
    try { saved = localStorage.getItem("lang"); } catch (e) {}
    var c = norm(q && q[1]) || (D[saved] ? saved : null) || norm(telegramLang());
    if (c) return c;
    var l = navigator.languages || [navigator.language];
    for (var i = 0; i < l.length; i++) { c = norm(l[i]); if (c) return c; }
    return "uz";
  }

  var lang = detect();
  function t(key, params) {
    var s = D[lang] && D[lang][key];
    if (s == null) s = D.uz[key];
    if (s == null) return key;
    return params ? s.replace(/\{(\w+)\}/g, function (_, k) { return params[k] != null ? params[k] : "{" + k + "}"; }) : s;
  }
  function apply(scope) {
    scope = scope || document;
    scope.querySelectorAll("[data-i18n]").forEach(function (n) { n.textContent = t(n.getAttribute("data-i18n")); });
    scope.querySelectorAll("[data-i18n-ph]").forEach(function (n) { n.setAttribute("placeholder", t(n.getAttribute("data-i18n-ph"))); });
    scope.querySelectorAll("[data-i18n-aria]").forEach(function (n) { n.setAttribute("aria-label", t(n.getAttribute("data-i18n-aria"))); n.setAttribute("title", t(n.getAttribute("data-i18n-aria"))); });
  }
  function init() {
    document.documentElement.lang = HTML_LANG[lang] || "uz";
    var title = document.querySelector("title[data-i18n]");
    if (title) document.title = t(title.getAttribute("data-i18n"));
    apply(document);
  }
  function set(code) {
    if (!D[code]) return;
    try { localStorage.setItem("lang", code); } catch (e) {}
    lang = code;
  }
  function fmtDate(iso) {                    // locale-independent, so every language renders identically
    if (!iso) return "—";
    var d = new Date(iso), p = function (n) { return (n < 10 ? "0" : "") + n; };
    return p(d.getDate()) + "." + p(d.getMonth() + 1) + "." + d.getFullYear() + " " + p(d.getHours()) + ":" + p(d.getMinutes());
  }
  function errMessage(e) {                   // API error object or network failure -> localized text
    if (!e) return t("err.generic");
    if (e instanceof TypeError || e.name === "TypeError") return t("err.network");
    var code = e.error || "";
    if (D.uz["err." + code]) return t("err." + code);
    if (/^invalid_/.test(code)) return t("err.invalid");
    return t("err.generic");
  }
  root.I18N = { t: t, apply: apply, init: init, set: set, lang: function () { return lang; }, langs: LANGS, fmtDate: fmtDate, errMessage: errMessage };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})(window);
