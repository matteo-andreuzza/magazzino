/* Riquadro con i dettagli al passaggio del mouse (o con il tasto Tab) sui grafici. */
(function () {
  "use strict";
  var bersagli = document.querySelectorAll("[data-tip]");
  if (!bersagli.length) return;
  var riquadro = document.createElement("div");
  riquadro.className = "g-riquadro";
  riquadro.setAttribute("role", "tooltip");
  riquadro.hidden = true;
  document.body.appendChild(riquadro);

  function mostra(bersaglio, x, y) {
    var dati;
    try { dati = JSON.parse(bersaglio.dataset.tip); } catch (e) { return; }
    riquadro.textContent = "";
    var titolo = document.createElement("div");
    titolo.className = "g-riquadro-titolo";
    titolo.textContent = dati.titolo;
    riquadro.appendChild(titolo);
    dati.righe.forEach(function (r) {
      var riga = document.createElement("div");
      riga.className = "g-riquadro-riga";
      if (r[2]) {
        var chiave = document.createElement("i");
        chiave.style.background = r[2];
        riga.appendChild(chiave);
      }
      var valore = document.createElement("strong");
      valore.textContent = r[0];
      riga.appendChild(valore);
      var etichetta = document.createElement("span");
      etichetta.textContent = r[1];
      riga.appendChild(etichetta);
      riquadro.appendChild(riga);
    });
    riquadro.hidden = false;
    var larghezza = riquadro.offsetWidth;
    var altezza = riquadro.offsetHeight;
    var sinistra = Math.min(x + 14, window.innerWidth - larghezza - 8);
    var alto = y - altezza - 12 < 8 ? y + 16 : y - altezza - 12;
    riquadro.style.left = Math.max(8, sinistra) + window.scrollX + "px";
    riquadro.style.top = alto + window.scrollY + "px";
    bersagli.forEach(function (b) { b.classList.toggle("g-attivo", b === bersaglio); });
  }

  function nascondi() {
    riquadro.hidden = true;
    bersagli.forEach(function (b) { b.classList.remove("g-attivo"); });
  }

  bersagli.forEach(function (b) {
    b.addEventListener("pointermove", function (e) { mostra(b, e.clientX, e.clientY); });
    b.addEventListener("pointerleave", nascondi);
    b.addEventListener("focus", function () {
      var r = b.getBoundingClientRect();
      mostra(b, r.left + r.width / 2, r.top);
    });
    b.addEventListener("blur", nascondi);
  });
})();
