/* Sito pubblico: interruttore del tema, tabelle ordinabili, filtri degli avvisi, crocino dei grafici.
   Niente librerie. Il tema scelto si ricorda in localStorage (sempre in try/catch: può mancare). */
(function () {
  "use strict";
  var root = document.documentElement;

  // ---------------------------------------------------------------- tema
  function currentTheme() {
    var t = root.getAttribute("data-theme");
    if (t) return t;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  var btn = document.querySelector("button.theme");
  function label() {
    if (!btn) return;
    var dark = currentTheme() === "dark";
    btn.setAttribute("aria-label", dark ? "Passa al tema chiaro" : "Passa al tema scuro");
    btn.setAttribute("title", dark ? "Tema chiaro" : "Tema scuro");
  }
  if (btn) {
    btn.addEventListener("click", function () {
      var next = currentTheme() === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("tema", next); } catch (e) { /* archivio non disponibile */ }
      label();
    });
    label();
  }

  // sul telefono il menu scorre: la voce della pagina aperta resta visibile
  var cur = document.querySelector(".nav [aria-current]");
  if (cur && cur.parentNode.scrollWidth > cur.parentNode.clientWidth) {
    cur.parentNode.scrollLeft = cur.offsetLeft - (cur.parentNode.clientWidth - cur.offsetWidth) / 2;
  }

  // ---------------------------------------------------------------- tabelle ordinabili
  function cellValue(td) {
    if (!td) return "";
    var v = td.getAttribute("data-v");
    if (v === null) return td.textContent.trim().toLowerCase();
    if (v === "") return null;
    var n = Number(v);
    return isNaN(n) ? v.toLowerCase() : n;
  }
  document.querySelectorAll("table.sortable").forEach(function (table) {
    var heads = table.querySelectorAll("thead th");
    heads.forEach(function (th, col) {
      if (th.hasAttribute("data-nosort")) return;
      var b = document.createElement("button");
      b.type = "button";
      b.className = "sort";
      while (th.firstChild) b.appendChild(th.firstChild);
      th.appendChild(b);
      b.addEventListener("click", function () {
        var asc = th.getAttribute("aria-sort") !== "ascending";
        if (!th.hasAttribute("aria-sort") && th.classList.contains("n")) asc = false; // numeri: prima i più grandi
        heads.forEach(function (h) { h.removeAttribute("aria-sort"); });
        th.setAttribute("aria-sort", asc ? "ascending" : "descending");
        var body = table.tBodies[0];
        var rows = Array.prototype.slice.call(body.rows);
        rows.sort(function (a, b) {
          var x = cellValue(a.cells[col]), y = cellValue(b.cells[col]);
          if (x === null && y === null) return 0;
          if (x === null) return 1;   // i mancanti sempre in fondo
          if (y === null) return -1;
          var r = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), "it");
          return asc ? r : -r;
        });
        rows.forEach(function (r) { body.appendChild(r); });
      });
    });
  });

  // ---------------------------------------------------------------- filtri degli avvisi
  document.querySelectorAll("[data-filter-for]").forEach(function (bar) {
    var list = document.getElementById(bar.getAttribute("data-filter-for"));
    if (!list) return;
    bar.addEventListener("click", function (ev) {
      var b = ev.target.closest("button[data-kind]");
      if (!b) return;
      bar.querySelectorAll("button").forEach(function (x) { x.setAttribute("aria-pressed", x === b ? "true" : "false"); });
      var k = b.getAttribute("data-kind");
      list.querySelectorAll("li[data-kind]").forEach(function (li) {
        li.hidden = !(k === "tutti" || li.getAttribute("data-kind") === k);
      });
    });
  });

  // ---------------------------------------------------------------- crocino e riquadro sui grafici
  document.querySelectorAll(".chart-box[data-points]").forEach(function (box) {
    var pts;
    try { pts = JSON.parse(box.getAttribute("data-points")); } catch (e) { return; }
    var svg = box.querySelector("svg"), cross = svg && svg.querySelector(".cross");
    if (!svg || !cross || !pts.dates || pts.dates.length < 2) return;
    var tip = document.createElement("div");
    tip.className = "tip";
    tip.hidden = true;
    tip.setAttribute("role", "status");
    box.appendChild(tip);
    var vb = svg.viewBox.baseVal;
    function show(clientX) {
      var r = svg.getBoundingClientRect();
      var x = (clientX - r.left) / r.width * vb.width;
      var i = Math.round((x - pts.x0) / pts.w * (pts.dates.length - 1));
      i = Math.max(0, Math.min(pts.dates.length - 1, i));
      var cx = pts.x0 + pts.w * i / (pts.dates.length - 1);
      cross.setAttribute("x1", cx); cross.setAttribute("x2", cx); cross.setAttribute("visibility", "visible");
      tip.textContent = "";
      var head = document.createElement("b");
      head.textContent = pts.dates[i];
      tip.appendChild(head);
      pts.series.forEach(function (s) {
        var row = document.createElement("div"), sw = document.createElement("i");
        sw.style.background = "var(--" + String(s.cls).replace(/[^a-z0-9]/g, "") + ")";
        row.appendChild(sw);
        row.appendChild(document.createTextNode(s.label + ": " + s.values[i]));
        tip.appendChild(row);
      });
      tip.hidden = false;
      var b = box.getBoundingClientRect();
      var px = r.left - b.left + cx / vb.width * r.width;
      var left = px + 12;
      if (left + tip.offsetWidth > b.width) left = px - tip.offsetWidth - 12;
      tip.style.left = Math.max(0, left) + "px";
      tip.style.top = "8px";
    }
    function hide() { tip.hidden = true; cross.setAttribute("visibility", "hidden"); }
    svg.addEventListener("pointermove", function (e) { show(e.clientX); });
    svg.addEventListener("pointerleave", hide);
    svg.addEventListener("pointerdown", function (e) { show(e.clientX); });
  });
})();
