/* Moderator panel. Token lives in sessionStorage only and is sent as a Bearer header (never in a URL).
 * All server text is inserted with textContent. */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var token = null;
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
  function api(method, path, body) {
    return fetch(path, { method: method, headers: { "Content-Type": "application/json", "Authorization": "Bearer " + token },
      body: body ? JSON.stringify(body) : undefined })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) { j.status = r.status; throw j; } return j; }); });
  }
  function fail(e) {
    if (e && e.status === 401) { logout(); toast("Token noto‘g‘ri"); }
    else toast((e && e.message) || "Xatolik");
  }
  var KIND = { incorrect: "Noto‘g‘ri ma’lumot", duplicate: "Takroriy", relocated: "Ko‘chirilgan", closed: "Mavjud emas / yopilgan", other: "Boshqa" };
  var STATUS = { unverified: "Tasdiqlanmagan", reported: "Xabar berilgan", verified: "Tasdiqlangan", archived: "Arxivlangan" };
  function when(i) { return i ? new Date(i).toLocaleString("uz-UZ") : "—"; }
  function mapLink(m, text) { return el("a", { class: "link", href: "/?c=" + m.lat + "," + m.lon + ",17", target: "_blank", rel: "noopener", text: text }); }

  function logout() { token = null; try { sessionStorage.removeItem("adm"); } catch (e) {} render(); }
  $("logout").onclick = logout;
  $("login").onsubmit = function (e) {
    e.preventDefault(); token = $("token").value.trim(); $("token").value = "";
    api("GET", "/api/admin/overview").then(function () { try { sessionStorage.setItem("adm", token); } catch (e) {} render(); }).catch(function (x) { token = null; fail(x); });
  };
  document.querySelectorAll("#tabs button").forEach(function (b) { b.onclick = function () { tab = b.getAttribute("data-tab"); render(); }; });

  function act(id, action, note) {
    return api("POST", "/api/admin/mosques/" + id + "/" + action, { note: note || "" })
      .then(function () { toast("Bajarildi"); render(); }).catch(fail);
  }
  function actionRow(m, actions) {
    var note = el("input", { placeholder: "Izoh / dalil (tasdiqlash uchun majburiy)", maxlength: 500 });
    var btns = actions.map(function (a) {
      return el("button", { class: a.cls || "", text: a.label, onclick: function () {
        if (a.confirm && !window.confirm(a.confirm)) return;
        act(m.id || m.mosque_id, a.action, note.value);
      } });
    });
    return el("div", {}, [note, el("div", { class: "row" }, btns)]);
  }

  function viewReports(v) {
    api("GET", "/api/admin/reports").then(function (r) {
      v.textContent = "";
      if (!r.groups.length) v.appendChild(el("p", { class: "muted", text: "Ochiq xabarlar yo‘q." }));
      r.groups.forEach(function (g) {
        v.appendChild(el("div", { class: "card" }, [
          el("h3", { text: (g.mosque_name || "Nomi yo‘q") + " · " + KIND[g.kind] + " × " + g.count }),
          el("div", { class: "muted" }, [STATUS[g.mosque_status] + " · ", mapLink(g, "xaritada ko‘rish")]),
          el("ul", {}, g.reports.map(function (x) { return el("li", { text: x.reason + "  (" + when(x.created_at) + ")" }); })),
          actionRow({ id: g.mosque_id }, [
            { label: "Arxivlash", action: "archive", cls: "danger", confirm: "Yozuv arxivlansin? Tarix saqlanadi, keyin qaytarish mumkin." },
            { label: "Xabarlarni rad etish", action: "dismiss", cls: "ghost" },
            { label: "Tasdiqlash", action: "verify", cls: "ghost" }
          ])
        ]));
      });
    }).catch(fail);
  }
  function viewList(v, status, actions) {
    api("GET", "/api/admin/mosques?status=" + status).then(function (r) {
      v.textContent = "";
      v.appendChild(el("p", { class: "muted", text: "Jami: " + r.total }));
      r.items.forEach(function (m) {
        v.appendChild(el("div", { class: "card" }, [
          el("h3", { text: m.name || "Nomi ma’lum emas" }),
          el("div", { class: "muted" }, [m.origin + " · v" + m.version + " · " + when(m.updated_at) + " · ", mapLink(m, "xaritada ko‘rish")]),
          m.verification_note ? el("div", { class: "muted", text: "Dalil: " + m.verification_note }) : null,
          actionRow(m, actions)
        ]));
      });
    }).catch(fail);
  }
  function viewActivity(v, ov) {
    v.textContent = "";
    ov.recent.forEach(function (h) {
      v.appendChild(el("div", { class: "hist" }, [
        el("b", { text: (h.name || "Nomi yo‘q") + " · " + h.action + " v" + h.version }),
        el("div", { class: "muted", text: when(h.at) + " · " + (h.actor || "anonim") }),
        h.note ? el("div", { text: h.note }) : null
      ]));
    });
  }

  function render() {
    var on = !!token;
    $("login").hidden = on; $("tabs").hidden = !on; $("logout").hidden = !on; $("stats").hidden = !on;
    var v = $("view"); v.textContent = "";
    if (!on) return;
    document.querySelectorAll("#tabs button").forEach(function (b) { b.classList.toggle("on", b.getAttribute("data-tab") === tab); });
    api("GET", "/api/admin/overview").then(function (ov) {
      var st = $("stats"); st.textContent = "";
      var sum = function (s) { return Object.keys(ov.counts).filter(function (k) { return k.split("/")[1] === s; }).reduce(function (a, k) { return a + ov.counts[k]; }, 0); };
      [["Ochiq xabarlar", ov.open_reports], ["Tasdiqlanmagan", sum("unverified")], ["Xabar berilgan", sum("reported")],
       ["Tasdiqlangan", sum("verified")], ["Arxiv", sum("archived")]].forEach(function (x) {
        st.appendChild(el("div", { class: "stat" }, [el("b", { text: String(x[1]) }), x[0]]));
      });
      if (tab === "reports") viewReports(v);
      else if (tab === "reported") viewList(v, "reported", [
        { label: "Arxivlash", action: "archive", cls: "danger", confirm: "Yozuv arxivlansin?" },
        { label: "Xabarlarni rad etish", action: "dismiss", cls: "ghost" }]);
      else if (tab === "archived") viewList(v, "archived", [{ label: "Qaytarish", action: "restore" }]);
      else viewActivity(v, ov);
    }).catch(fail);
  }
  render();
})();
