/* Pagina di stampa dei biglietti: stampa da sola e segna come stampate le stampe "dal browser". */
(function () {
  "use strict";
  var corpo = document.body;
  var ids = (corpo.dataset.daSegnare || "").split(",").filter(Boolean).map(Number);
  var segnate = false;

  function segna() {
    if (segnate || !ids.length) return;
    segnate = true;
    var csrf = document.querySelector("input[name=csrfmiddlewaretoken]");
    fetch(corpo.dataset.urlFatto, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf ? csrf.value : "" },
      body: JSON.stringify({ ids: ids }),
      keepalive: true,
    }).catch(function () { segnate = false; });
  }

  window.addEventListener("afterprint", segna);
  var pulsante = document.querySelector("[data-stampa]");
  if (pulsante) pulsante.addEventListener("click", function () { window.print(); });
  if (corpo.dataset.automatica === "1" && document.querySelector(".foglio")) {
    window.addEventListener("load", function () {
      setTimeout(function () {
        window.print();
        // Alcuni browser non segnalano la fine della stampa: si segna comunque.
        setTimeout(segna, 1500);
      }, 150);
    });
  }
})();
