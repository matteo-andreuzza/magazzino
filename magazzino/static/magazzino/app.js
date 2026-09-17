(function () {
  "use strict";

  function normalizza(testo) {
    return (testo || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").trim();
  }

  function numero(testo) {
    var t = (testo || "").trim().replace(",", ".");
    if (!/^(\d+(\.\d*)?|\.\d+)$/.test(t)) return null;
    return parseFloat(t);
  }

  // Ogni modulo POST si può inviare una sola volta (doppio tocco sul telefono).
  document.querySelectorAll("form[method=post]").forEach(function (form) {
    form.addEventListener("submit", function (evento) {
      if (form.dataset.inviato === "1") {
        evento.preventDefault();
        return;
      }
      form.dataset.inviato = "1";
      form.querySelectorAll("button[type=submit]").forEach(function (b) {
        b.disabled = true;
        if (b.hasAttribute("data-invio")) b.textContent = "Registrazione in corso…";
      });
    });
  });
  // Tornando indietro col browser la pagina può essere ripristinata "già inviata".
  window.addEventListener("pageshow", function (evento) {
    if (!evento.persisted) return;
    document.querySelectorAll("form[method=post]").forEach(function (form) {
      delete form.dataset.inviato;
      form.querySelectorAll("button[type=submit]").forEach(function (b) {
        b.disabled = false;
        if (b.dataset.testo) b.textContent = b.dataset.testo;
      });
    });
  });
  document.querySelectorAll("button[data-invio]").forEach(function (b) {
    b.dataset.testo = b.textContent;
  });

  // Moduli a elenco: filtri, contatore, Invio che non spedisce il modulo.
  document.querySelectorAll("[data-modulo-righe]").forEach(function (form) {
    var righe = Array.prototype.slice.call(form.querySelectorAll("[data-riga]"));
    var cerca = form.querySelector("[data-cerca]");
    var soloMenu = form.querySelector("[data-solo-menu]");
    var soloPresenti = form.querySelector("[data-solo-presenti]");
    var chips = Array.prototype.slice.call(form.querySelectorAll("[data-chips] .chip"));
    var contatore = form.querySelector("[data-contatore]");
    var visibiliTesto = form.querySelector("[data-conteggio-visibili]");
    var gruppoAttivo = "";

    righe.forEach(function (riga) {
      riga._nome = normalizza(riga.dataset.nome);
      riga._input = riga.querySelector("input[name^='q-']");
    });

    function compilata(riga) {
      return riga._input && riga._input.value.trim() !== "";
    }

    function applicaFiltri() {
      var testo = normalizza(cerca ? cerca.value : "");
      var visibili = 0;
      righe.forEach(function (riga) {
        var mostra = true;
        if (testo) {
          mostra = riga._nome.indexOf(testo) !== -1;
        } else if (soloMenu && soloMenu.checked && riga.dataset.menu !== "1") {
          mostra = false;
        }
        if (mostra && soloPresenti && soloPresenti.checked && riga.dataset.presente !== "1") mostra = false;
        if (mostra && gruppoAttivo && riga.dataset.gruppo !== gruppoAttivo) mostra = false;
        // Le righe già compilate o con errori restano sempre visibili.
        if (compilata(riga) || riga.classList.contains("con-errore")) mostra = true;
        riga.hidden = !mostra;
        if (mostra) visibili++;
      });
      form.querySelectorAll("[data-sezione]").forEach(function (sezione) {
        sezione.hidden = !sezione.querySelector("[data-riga]:not([hidden])");
      });
      if (visibiliTesto) {
        visibiliTesto.textContent = visibili === righe.length
          ? ""
          : "Mostrati " + visibili + " prodotti su " + righe.length + ".";
      }
    }

    function aggiornaRiga(riga) {
      riga.classList.toggle("compilata", compilata(riga));
      var massimo = riga._input.dataset.massimo;
      if (massimo !== undefined) {
        var valore = numero(riga._input.value);
        var limite = numero(massimo);
        riga._input.classList.toggle("oltre", valore !== null && limite !== null && valore > limite + 1e-9);
      }
    }

    function aggiornaContatore() {
      var n = righe.filter(compilata).length;
      if (!contatore) return;
      contatore.textContent = n === 0 ? "Nessuna riga compilata" : n === 1 ? "1 riga compilata" : n + " righe compilate";
    }

    righe.forEach(function (riga) {
      if (!riga._input) return;
      riga._input.addEventListener("input", function () {
        aggiornaRiga(riga);
        aggiornaContatore();
      });
      aggiornaRiga(riga);
    });
    if (cerca) cerca.addEventListener("input", applicaFiltri);
    if (soloMenu) soloMenu.addEventListener("change", applicaFiltri);
    if (soloPresenti) soloPresenti.addEventListener("change", applicaFiltri);
    chips.forEach(function (chip) {
      chip.addEventListener("click", function () {
        gruppoAttivo = chip.dataset.gruppo;
        chips.forEach(function (c) { c.classList.toggle("attivo", c === chip); });
        applicaFiltri();
      });
    });

    form.addEventListener("keydown", function (evento) {
      if (evento.key !== "Enter" || evento.target.tagName !== "INPUT") return;
      evento.preventDefault();
      var campi = Array.prototype.slice.call(form.querySelectorAll("input[name^='q-'], input[name='note']"))
        .filter(function (c) { return c.offsetParent !== null; });
      var i = campi.indexOf(evento.target);
      if (i >= 0 && i + 1 < campi.length) campi[i + 1].focus();
    });

    applicaFiltri();
    aggiornaContatore();
  });

  // Elenchi consultabili (giacenze, prodotti): ricerca veloce.
  document.querySelectorAll("[data-filtro-elenco]").forEach(function (contenitore) {
    var cerca = contenitore.querySelector("[data-cerca]");
    var voci = Array.prototype.slice.call(contenitore.querySelectorAll("[data-voce]"));
    voci.forEach(function (v) { v._nome = normalizza(v.dataset.nome); });
    if (!cerca) return;
    cerca.addEventListener("input", function () {
      var testo = normalizza(cerca.value);
      voci.forEach(function (v) { v.hidden = testo !== "" && v._nome.indexOf(testo) === -1; });
      contenitore.querySelectorAll("[data-sezione]").forEach(function (s) {
        s.hidden = !s.querySelector("[data-voce]:not([hidden])");
      });
    });
  });

  // Caselle dei piatti: seleziona tutto / niente per menu.
  document.querySelectorAll("[data-seleziona]").forEach(function (pulsante) {
    pulsante.addEventListener("click", function () {
      var gruppo = document.getElementById(pulsante.dataset.gruppo);
      if (!gruppo) return;
      var valore = pulsante.dataset.seleziona === "tutti";
      gruppo.querySelectorAll("input[type=checkbox]").forEach(function (c) { c.checked = valore; });
    });
  });

  var errori = document.getElementById("errori");
  if (errori) errori.focus();
})();
