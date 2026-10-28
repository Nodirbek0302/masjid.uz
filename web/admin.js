/* Moderator panel. Token lives in sessionStorage only and is sent as a Bearer header (never in a URL).
 * All server text is inserted with textContent. UI text comes from i18n.js. */
(function () {
  "use strict";
  var T = I18N.t;
  var $ = function (id) { return document.getElementById(id); };
  // Access: Telegram Mini App launch data only (the server checks the signature and the allow-listed Telegram id).
  // A bearer-token form is shown only when the server config says adminTokenLogin is true.
  var inTg = location.hash.indexOf("tgWebAppData") >= 0, tg = null, token = null, devLogin = false;
  try { token = sessionStorage.getItem("adm"); } catch (e) {}
  var tab = "reports";

  function el(tag, props, kids) {
    var n = document.createElement(tag);
    Object.keys(props || {}).forEach(function (k) {
      if (k === "text") n.textContent = props[k];
      else if (k.slice(0, 2) === "on") n.addEventListener(k.slice(2), props[k]);
      else n.setAttribute(k, props[k]);
    });
    (kids || []).forEach(function (c) { if (c) n.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return n;
  }
  function toast(m) { var t = $("toast"); t.textContent = m; t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(function () { t.hidden = true; }, 3500); }
  var API = window.MASJIDUZ_API_BASE || "/masjiduz/v1";
  // Envelope {success, httpStatus, code, message, data}: success returns `data`, failure throws {status, code, message, data}.
  function api(method, path, body) {
    var h = { "Content-Type": "application/json", "X-Client-Platform": "web" };
    if (tg && tg.initData) h["X-Telegram-Init-Data"] = tg.initData; else if (token) h["Authorization"] = "Bearer " + token;
    return fetch(API + path, { method: method, headers: h, body: body ? JSON.stringify(body) : undefined })
      .then(function (r) {
        return r.json().catch(function () { return null; }).then(function (j) {
          if (r.ok && j && j.success !== false) return j.data;
          throw { status: r.status, code: j && j.code, message: (j && j.message) || {}, data: j && j.data };
        });
      });
  }
  function gate(text) { $("gate-text").textContent = text; $("gate").hidden = false; $("login").hidden = true; $("tabs").hidden = true; $("stats").hidden = true; $("view").textContent = ""; }
  function fail(e) {
    if (e && e.status === 401) {
      if (tg) { gate(T("adm.denied")); return; }                // Telegram user not allowed, or launch data too old
      logout(); toast(T("adm.bad_token"));
    } else toast(I18N.errMessage(e));
  }
  function mapLink(m, text) { return el("a", { class: "link", href: "./?c=" + m.lat + "," + m.lon + ",17", target: "_blank", rel: "noopener", text: text }); }
  function skeletons(v) { v.textContent = ""; for (var i = 0; i < 3; i++) v.appendChild(el("div", { class: "sk" })); }

  // language: stored choice, then reload (all text is built from T() at render time)
  I18N.langs.forEach(function (l) { $("lang").appendChild(el("option", { value: l[0], text: l[1], title: l[2] })); });
  $("lang").value = I18N.lang();
  $("lang").onchange = function () { I18N.set($("lang").value); location.reload(); };

  function logout() { token = null; try { sessionStorage.removeItem("adm"); } catch (e) {} render(); }
  $("logout").onclick = logout;
  $("logout").hidden = true;
  $("login").onsubmit = function (e) {
    e.preventDefault(); token = $("token").value.trim(); $("token").value = "";
    api("GET", "/admin/overview").then(function () { try { sessionStorage.setItem("adm", token); } catch (e) {} render(); }).catch(function (x) { token = null; fail(x); });
  };
  document.querySelectorAll("#tabs button").forEach(function (b) { b.onclick = function () { tab = b.getAttribute("data-tab"); render(); }; });

  function act(id, action, note) {
    return api("POST", "/admin/mosques/" + id + "/" + action, { note: note || "" })
      .then(function () { toast(T("adm.done")); render(); }).catch(fail);
  }
  function actionRow(m, actions) {
    var note = el("input", { placeholder: T("adm.note_ph"), maxlength: 500 });
    var btns = actions.map(function (a) {
      return el("button", { class: "btn " + (a.cls || ""), type: "button", text: a.label, onclick: function () {
        if (a.confirm && !window.confirm(a.confirm)) return;
        act(m.id || m.mosqueId, a.action, note.value);
      } });
    });
    return el("div", { class: "actions-wrap" }, [note, el("div", { class: "actions" }, btns)]);
  }
  function statusChip(s) { return el("span", { class: "chip" + (s === "reported" ? " warn" : s === "archived" || s === "unverified" ? " neutral" : ""), text: T("sv." + s) }); }

  function viewReports(v) {
    api("GET", "/admin/reports").then(function (r) {
      v.textContent = "";
      if (!r.groups.length) v.appendChild(el("p", { class: "muted", text: T("adm.none") }));
      r.groups.forEach(function (g) {
        v.appendChild(el("div", { class: "card" }, [
          el("h3", { text: (g.mosqueName || T("adm.no_name")) + " · " + T("rk." + g.kind) + " × " + g.count }),
          el("div", { class: "muted" }, [statusChip(g.mosqueStatus), " ", mapLink(g, T("adm.on_map"))]),
          el("ul", {}, g.reports.map(function (x) { return el("li", { text: x.reason + "  (" + I18N.fmtDate(x.createdAt) + ")" }); })),
          actionRow({ id: g.mosqueId }, [
            { label: T("adm.archive"), action: "archive", cls: "danger", confirm: T("adm.confirm_archive") },
            { label: T("adm.dismiss"), action: "dismiss", cls: "ghost" },
            { label: T("adm.verify"), action: "verify", cls: "ghost" }
          ])
        ]));
      });
    }).catch(fail);
  }
  function viewList(v, status, actions) {
    api("GET", "/admin/mosques?status=" + status).then(function (r) {
      v.textContent = "";
      v.appendChild(el("p", { class: "muted", text: T("adm.total", { n: r.total }) }));
      r.items.forEach(function (m) {
        v.appendChild(el("div", { class: "card" }, [
          el("h3", { text: m.name || T("adm.no_name") }),
          el("div", { class: "muted" }, [m.origin + " · v" + m.version + " · " + I18N.fmtDate(m.updatedAt) + " · ", mapLink(m, T("adm.on_map"))]),
          m.verificationNote ? el("div", { class: "muted", text: T("adm.evidence", { note: m.verificationNote }) }) : null,
          actionRow(m, actions)
        ]));
      });
    }).catch(fail);
  }
  function who(h) {                                   // moderator-only detail: Telegram name / device / app version
    if (h.actor === "admin" || (h.actor || "").indexOf("admin:") === 0) return T("adm.who_moderator");
    if (!h.actor) return T("adm.who_system");
    var parts = [h.actorKind === "telegram" ? T("adm.who_telegram") + (h.tgUsername ? " @" + h.tgUsername : h.tgFirstName ? " " + h.tgFirstName : "")
      : h.actorKind === "mobile" ? T("adm.who_mobile") : T("adm.who_web")];
    if (h.platform && h.platform !== "web") parts.push(h.platform);
    if (h.deviceModel) parts.push(h.deviceModel);
    if (h.appVersion) parts.push("v" + h.appVersion);
    return parts.join(" · ");
  }
  function viewActivity(v, ov) {
    v.textContent = "";
    ov.recent.forEach(function (h) {
      var act = T("act." + h.action); if (act === "act." + h.action) act = h.action;
      v.appendChild(el("div", { class: "card hist" }, [
        el("b", { text: (h.name || T("adm.no_name")) + " · " + act + " v" + h.version }),
        el("div", { class: "muted", text: I18N.fmtDate(h.at) + " · " + who(h) }),
        h.note ? el("div", { text: h.note }) : null
      ]));
    });
  }

  function viewMap(v) {                              // which map everybody gets, and the pool of Yandex keys
    function call(method, path, body) { return api(method, path, body).then(function () { toast(T("adm.done")); render(); }).catch(fail); }
    api("GET", "/admin/map").then(function (m) {
      v.textContent = "";
      var seg = el("div", { class: "seg" }, [["osm", "adm.map_osm"], ["yandex", "adm.map_yandex"]].map(function (o) {
        return el("button", { type: "button", class: "btn ghost" + (m.provider === o[0] ? " on" : ""), text: T(o[1]), onclick: function () {
          if (m.provider !== o[0]) call("POST", "/admin/map/provider", { provider: o[0] });
        } });
      }));
      function stateOf(kind) { var e = m.effective[kind]; return e === "yandex" ? T("adm.map_state_yandex") : m.provider === "yandex" ? T("adm.map_state_fallback") : T("adm.map_state_osm"); }
      var warn = m.provider === "yandex" && (m.effective.web !== "yandex" || m.effective.app !== "yandex");
      var now = T("adm.map_web") + ": " + stateOf("web") + "  ·  " + T("adm.map_app") + ": " + stateOf("app");
      v.appendChild(el("div", { class: "card" }, [
        el("h3", { text: T("adm.map_title") }), el("p", { class: "muted", text: T("adm.map_hint") }), seg,
        el("p", { class: "now" + (warn ? " warn" : ""), text: now })
      ]));
      var list = el("div", { class: "card" }, [el("h3", { text: T("adm.key_title") })]);
      if (!m.keys.length) list.appendChild(el("p", { class: "muted", text: T("adm.key_none") }));
      m.keys.forEach(function (k) {
        var full = k.usersToday >= k.dailyLimit;
        var state = !k.enabled ? [T("adm.key_off"), "neutral"] : k.exhausted ? [T("adm.key_exhausted"), "warn"] : full ? [T("adm.key_full"), "warn"] : [T("adm.key_ok"), ""];
        var pct = Math.min(100, Math.round(100 * k.usersToday / k.dailyLimit));
        list.appendChild(el("div", { class: "keyrow" }, [
          el("div", { class: "keyhead" }, [el("b", { text: k.label || ("#" + k.id) }), el("span", { class: "chip neutral", text: T(k.kind === "app" ? "adm.map_app" : "adm.map_web") }), el("code", { text: k.key }), el("span", { class: "chip " + state[1], text: state[0] })]),
          el("div", { class: "meter" }, [el("i", { style: "width:" + pct + "%" })]),
          el("div", { class: "muted", text: T("adm.key_users", { n: k.usersToday, max: k.dailyLimit }) }),
          el("div", { class: "actions" }, [
            el("button", { class: "btn ghost", type: "button", text: k.enabled ? T("adm.key_pause") : T("adm.key_resume"), onclick: function () { call("PATCH", "/admin/map/keys/" + k.id, { enabled: !k.enabled }); } }),
            k.exhausted ? el("button", { class: "btn ghost", type: "button", text: T("adm.key_reset"), onclick: function () { call("PATCH", "/admin/map/keys/" + k.id, { reset: true }); } }) : null,
            el("button", { class: "btn ghost", type: "button", text: T("adm.key_limit"), onclick: function () {
              var n = window.prompt(T("adm.key_limit_prompt"), String(k.dailyLimit));
              if (n !== null && n.trim()) call("PATCH", "/admin/map/keys/" + k.id, { dailyLimit: +n.trim() });
            } }),
            el("button", { class: "btn danger", type: "button", text: T("adm.key_delete"), onclick: function () {
              if (window.confirm(T("adm.key_confirm_delete"))) call("POST", "/admin/map/keys/" + k.id + "/delete", {});
            } })
          ])
        ]));
      });
      var key = el("input", { type: "password", placeholder: T("adm.key_ph"), autocomplete: "off", spellcheck: "false", maxlength: 200 });
      var label = el("input", { placeholder: T("adm.key_label_ph"), maxlength: 60 });
      var kind = el("select", { "aria-label": T("adm.key_kind") }, [el("option", { value: "web", text: T("adm.kind_web") }), el("option", { value: "app", text: T("adm.kind_app") })]);
      var limit = el("input", { type: "number", min: 1, value: 25000, placeholder: T("adm.key_limit_ph") });
      list.appendChild(el("form", { class: "keyadd", onsubmit: function (e) {
        e.preventDefault();
        if (!key.value.trim()) return;
        call("POST", "/admin/map/keys", { key: key.value.trim(), label: label.value, dailyLimit: +limit.value, kind: kind.value });
      } }, [key, kind, label, limit, el("button", { class: "btn", text: T("adm.key_add") })]));
      v.appendChild(list);
    }).catch(fail);
  }

  function render() {
    var on = !!tg || !!token;
    $("gate").hidden = true;
    $("login").hidden = on || !devLogin; $("tabs").hidden = !on; $("logout").hidden = !on || !!tg; $("stats").hidden = !on;
    if (!on && !devLogin) { gate(T("adm.tg_only")); return; }
    var v = $("view"); v.textContent = "";
    if (!on) return;
    document.querySelectorAll("#tabs button").forEach(function (b) { b.classList.toggle("on", b.getAttribute("data-tab") === tab); });
    skeletons(v);
    api("GET", "/admin/overview").then(function (ov) {
      var st = $("stats"); st.textContent = "";
      var sum = function (s) { return Object.keys(ov.counts).filter(function (k) { return k.split("/")[1] === s; }).reduce(function (a, k) { return a + ov.counts[k]; }, 0); };
      [["adm.stat_open", ov.openReports, ov.openReports > 0], ["adm.stat_unver", sum("unverified")], ["adm.stat_rep", sum("reported"), sum("reported") > 0],
       ["adm.stat_ver", sum("verified")], ["adm.stat_arch", sum("archived")]].forEach(function (x) {
        st.appendChild(el("div", { class: "stat" + (x[2] ? " hot" : "") }, [el("b", { text: String(x[1]) }), T(x[0])]));
      });
      if (tab === "reports") viewReports(v);
      else if (tab === "reported") viewList(v, "reported", [
        { label: T("adm.archive"), action: "archive", cls: "danger", confirm: T("adm.confirm_archive") },
        { label: T("adm.dismiss"), action: "dismiss", cls: "ghost" }]);
      else if (tab === "archived") viewList(v, "archived", [{ label: T("adm.restore"), action: "restore" }]);
      else if (tab === "map") viewMap(v);
      else viewActivity(v, ov);
    }).catch(fail);
  }
  function start() {
    api("GET", "/config").catch(function () { return {}; }).then(function (c) {
      devLogin = !!(c && c.adminTokenLogin);
      render();
    });
  }
  if (inTg) {                                          // load Telegram's script only inside Telegram
    var sc = document.createElement("script");
    sc.src = "https://telegram.org/js/telegram-web-app.js";
    sc.onload = function () { tg = window.Telegram && window.Telegram.WebApp; if (tg) { tg.ready(); tg.expand(); } start(); };
    sc.onerror = function () { start(); };
    document.head.appendChild(sc);
  } else start();
})();
