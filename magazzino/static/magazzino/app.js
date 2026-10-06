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

  // Modalità scura: il pulsante in alto la accende e spegne e la scelta resta sul telefono.
  // Finché non si sceglie, segue il tema del telefono (anche se cambia a pagina aperta).
  var radice = document.documentElement;
  var sistemaScuro = window.matchMedia("(prefers-color-scheme: dark)");
  var coloreTestata = document.querySelector("meta[name=theme-color]");

  function temaScelto() {
    try { return localStorage.getItem("tema"); } catch (e) { return null; }
  }

  function applicaTema(scuro) {
    radice.dataset.tema = scuro ? "scuro" : "chiaro";
    var testata = document.querySelector(".testata");
    if (coloreTestata && testata) coloreTestata.content = getComputedStyle(testata).backgroundColor;
    document.querySelectorAll("[data-cambia-tema]").forEach(function (b) {
      b.setAttribute("aria-pressed", scuro ? "true" : "false");
      b.title = scuro ? "Modalità chiara" : "Modalità scura";
      b.setAttribute("aria-label", b.title);
    });
  }

  applicaTema(radice.dataset.tema === "scuro");

  // Sul telefono la barra delle pagine scorre di lato: si porta in vista la pagina aperta.
  var paginaAperta = document.querySelector(".nav [aria-current]");
  if (paginaAperta && paginaAperta.scrollIntoView) {
    var barra = paginaAperta.parentElement;
    if (barra.scrollWidth > barra.clientWidth) {
      barra.scrollLeft = paginaAperta.offsetLeft - (barra.clientWidth - paginaAperta.offsetWidth) / 2;
    }
  }
  document.querySelectorAll("[data-cambia-tema]").forEach(function (b) {
    b.addEventListener("click", function () {
      var scuro = radice.dataset.tema !== "scuro";
      try { localStorage.setItem("tema", scuro ? "scuro" : "chiaro"); } catch (e) { /* resta solo per questa pagina */ }
      applicaTema(scuro);
    });
  });
  if (sistemaScuro.addEventListener) {
    sistemaScuro.addEventListener("change", function (evento) {
      if (!temaScelto()) applicaTema(evento.matches);
    });
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

  // Foto dei prodotti: anteprima e, se il browser ci riesce, foto rimpicciolita prima
  // dell'invio (dal telefono arrivano anche 10 MB: col Wi-Fi della sagra ci si mette troppo).
  // Se qualcosa non va si manda la foto originale: la rimpicciolisce comunque il server.
  var LATO_MASSIMO = 1600;
  document.querySelectorAll("input[type=file][data-foto]").forEach(function (campo) {
    var anteprima = campo.closest("form").querySelector("[data-anteprima]");
    var lavoro = 0;

    function mostra(file) {
      if (!anteprima) return;
      var img = document.createElement("img");
      img.alt = "Foto scelta";
      img.src = URL.createObjectURL(file);
      img.onerror = function () { anteprima.textContent = "📷"; };
      anteprima.replaceChildren(img);
    }

    function rimpicciolisci(file) {
      if (!window.createImageBitmap || !window.DataTransfer || !HTMLCanvasElement.prototype.toBlob) {
        return Promise.resolve(null);
      }
      return createImageBitmap(file, { imageOrientation: "from-image" }).then(function (bitmap) {
        var scala = Math.min(1, LATO_MASSIMO / Math.max(bitmap.width, bitmap.height));
        var tela = document.createElement("canvas");
        tela.width = Math.round(bitmap.width * scala);
        tela.height = Math.round(bitmap.height * scala);
        var ctx = tela.getContext("2d");
        ctx.fillStyle = "#fff";
        ctx.fillRect(0, 0, tela.width, tela.height);
        ctx.drawImage(bitmap, 0, 0, tela.width, tela.height);
        if (bitmap.close) bitmap.close();
        return new Promise(function (risolvi) { tela.toBlob(risolvi, "image/jpeg", 0.85); });
      });
    }

    campo.addEventListener("change", function () {
      var file = campo.files && campo.files[0];
      if (!file || file.type.indexOf("image/") !== 0) return;
      var mio = ++lavoro;
      mostra(file);
      if (file.size < 1.5 * 1024 * 1024) return;
      var pulsanti = campo.form.querySelectorAll("button[type=submit]");
      pulsanti.forEach(function (b) { b.disabled = true; });
      rimpicciolisci(file).then(function (blob) {
        if (mio !== lavoro || !blob || blob.size >= file.size) return;
        var nome = file.name.replace(/\.[^.]*$/, "") + ".jpg";
        var scatola = new DataTransfer();
        scatola.items.add(new File([blob], nome, { type: "image/jpeg" }));
        campo.files = scatola.files;
      }).catch(function () { /* resta l'originale */ }).then(function () {
        if (mio === lavoro) pulsanti.forEach(function (b) { b.disabled = false; });
      });
    });
  });

  // Pulsanti «Copia» (chiavi e comandi per collegare Claude).
  document.querySelectorAll("[data-copia]").forEach(function (pulsante) {
    pulsante.addEventListener("click", function () {
      var testo = pulsante.parentNode.querySelector("[data-testo-copia]").textContent;
      var fatto = function () {
        pulsante.textContent = "Copiato";
        setTimeout(function () { pulsante.textContent = "Copia"; }, 2000);
      };
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(testo).then(fatto, function () { seleziona(pulsante); });
      } else {
        seleziona(pulsante);
      }
    });
  });
  // Senza HTTPS il browser non lascia copiare: si seleziona il testo, poi Ctrl+C.
  function seleziona(pulsante) {
    var r = document.createRange();
    r.selectNodeContents(pulsante.parentNode.querySelector("[data-testo-copia]"));
    var sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(r);
    pulsante.textContent = "Selezionato: Ctrl+C";
  }

  var errori = document.getElementById("errori");
  if (errori) errori.focus();
})();
