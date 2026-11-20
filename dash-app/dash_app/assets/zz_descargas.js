/* Aviso de descarga en curso: al pulsar un botón de Excel/CSV/PDF/PPT se deshabilita,
   muestra "Generando…" y se libera cuando terminan los callbacks pendientes (o a los 120 s). */
(function () {
  var RE_ID = /(download|descarg|pdf|csv|excel|xlsx|pptx|ppt|export)/i;
  var pendientes = 0, esperando = [];

  var style = document.createElement("style");
  style.textContent =
    ".vt-busy{pointer-events:none!important;opacity:.6;cursor:progress!important}" +
    ".vt-busy::after{content:'';display:inline-block;width:11px;height:11px;margin-left:8px;" +
    "border:2px solid currentColor;border-right-color:transparent;border-radius:50%;" +
    "vertical-align:-1px;animation:vt-spin .7s linear infinite}" +
    "@keyframes vt-spin{to{transform:rotate(360deg)}}";
  document.head.appendChild(style);

  var f0 = window.fetch;
  window.fetch = function () {
    var url = arguments[0] && (arguments[0].url || arguments[0]);
    var es = typeof url === "string" && url.indexOf("_dash-update-component") >= 0;
    if (es) pendientes++;
    var p = f0.apply(this, arguments);
    if (es) {
      var fin = function () { pendientes = Math.max(0, pendientes - 1); revisar(); };
      p.then(fin, fin);
    }
    return p;
  };

  function liberar(e) {
    e.btn.classList.remove("vt-busy");
    e.btn.removeAttribute("aria-busy");
    if (e.titulo !== null) e.btn.title = e.titulo;
    clearTimeout(e.tope);
  }
  function revisar() {
    var ahora = Date.now();
    esperando = esperando.filter(function (e) {
      if (pendientes === 0 && ahora - e.t0 > 1500) { liberar(e); return false; }
      return true;
    });
  }

  document.addEventListener("click", function (ev) {
    var b = ev.target.closest && ev.target.closest("button");
    if (!b) return;
    if (b.classList.contains("vt-busy")) { ev.stopPropagation(); ev.preventDefault(); return; }
    if (!(b.closest(".vt-dl-btns") || RE_ID.test(b.id || ""))) return;
    var e = { btn: b, t0: Date.now(), titulo: b.getAttribute("title") };
    b.classList.add("vt-busy");
    b.setAttribute("aria-busy", "true");
    b.title = "Generando el archivo, espera a que termine la descarga…";
    e.tope = setTimeout(function () { liberar(e); esperando = esperando.filter(function (x) { return x !== e; }); }, 1800000);
    esperando.push(e);
    setTimeout(revisar, 1600);
  }, true);
})();
