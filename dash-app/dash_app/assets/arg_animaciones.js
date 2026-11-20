/* Contador animado en las tarjetas KPI: al cambiar el texto de .vt-kpi-value, la cifra sube
   desde 0 hasta su valor en ~0.8 s. Respeta prefers-reduced-motion. No toca el texto final. */
(function () {
  "use strict";
  if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  var enCurso = new WeakSet();

  function formato(v, dec) {
    return v.toLocaleString("en-US", { minimumFractionDigits: dec, maximumFractionDigits: dec });
  }

  function animar(el) {
    if (enCurso.has(el)) return;
    var txt = el.textContent.trim();
    if (el.dataset.final === txt) return;
    var m = txt.match(/\d[\d,]*\.?\d*/);
    if (!m) { el.dataset.final = txt; return; }
    var num = parseFloat(m[0].replace(/,/g, ""));
    var dec = (m[0].split(".")[1] || "").length;
    el.dataset.final = txt;
    enCurso.add(el);
    var t0 = performance.now(), dur = 800;
    function paso(t) {
      var p = Math.min(1, (t - t0) / dur);
      var e = 1 - Math.pow(1 - p, 3);
      el.textContent = p < 1 ? txt.replace(m[0], formato(num * e, dec)) : txt;
      if (p < 1) { requestAnimationFrame(paso); } else { enCurso.delete(el); }
    }
    requestAnimationFrame(paso);
  }

  function revisar(nodo) {
    if (!nodo || nodo.nodeType !== 1) { nodo = nodo && nodo.parentElement; }
    if (!nodo) return;
    var kpi = nodo.closest ? nodo.closest(".vt-kpi-value") : null;
    if (kpi) { animar(kpi); return; }
    if (nodo.querySelectorAll) { nodo.querySelectorAll(".vt-kpi-value").forEach(animar); }
  }

  new MutationObserver(function (muts) {
    muts.forEach(function (mu) {
      if (mu.type === "characterData") { revisar(mu.target.parentElement); return; }
      mu.addedNodes.forEach(revisar);
    });
  }).observe(document.documentElement, { childList: true, subtree: true, characterData: true });
})();
