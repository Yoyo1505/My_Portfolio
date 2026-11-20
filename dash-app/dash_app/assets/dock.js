// Dock de pestañas: al seleccionar una pestaña se encoge (aunque el mouse siga encima) y vuelve a poder
// expandirse cuando el mouse sale y entra de nuevo. El CSS (zzz_tema.css) hace el resto con :hover.
(function () {
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest(".nav-tabs-custom .nav-link");
    if (a) a.closest(".nav-tabs-custom").classList.add("sel");
  }, true);
  document.addEventListener("mouseout", function (e) {
    var w = e.target.closest && e.target.closest(".nav-tabs-custom");
    if (w && w.classList.contains("sel") && !(e.relatedTarget && w.contains(e.relatedTarget))) w.classList.remove("sel");
  });
})();

// Dock de filtros: un clic en un ícono abre solo su panel; clic fuera o Esc lo cierra.
// Los menús desplegables se montan en un popper de Radix o dentro del panel: no cuentan como "fuera".
(function () {
  function cerrar(salvo) {
    document.querySelectorAll(".tfb-celda.abierta").forEach(function (c) { if (c !== salvo) c.classList.remove("abierta"); });
  }
  document.addEventListener("click", function (e) {
    var t = e.target;
    var b = t.closest && t.closest(".tfb-burbuja");
    if (b) {
      var c = b.closest(".tfb-celda");
      cerrar(c);
      c.classList.toggle("abierta");
      return;
    }
    if (t.closest && (t.closest(".tfb-panel") || t.closest(".dash-dropdown-content") || t.closest("[data-radix-popper-content-wrapper]"))) return;
    cerrar(null);
  }, true);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") cerrar(null); });
})();