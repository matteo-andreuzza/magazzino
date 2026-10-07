/* Cassa: presa degli ordini.
 *
 * L'ordine in corso è salvato nel browser (localStorage): ricaricando la
 * pagina non si perde. Ogni ordine ha un token: se la rete cade e si riprova,
 * il server non lo registra due volte.
 */
(function () {
  "use strict";

  var radice = document.querySelector("[data-pos]");
  if (!radice) return;
  var cfg = JSON.parse(document.getElementById("pos-dati").textContent);
  var csrf = radice.querySelector("input[name=csrfmiddlewaretoken]").value;
  var chiaveBozza = "cassa-bozza-" + cfg.servizio + "-" + cfg.postazione;
  var bloccata = radice.classList.contains("pos-bloccata");

  var $ = function (sel, dentro) { return (dentro || document).querySelector(sel); };
  var $$ = function (sel, dentro) { return Array.prototype.slice.call((dentro || document).querySelectorAll(sel)); };

  var el = {
    cerca: $("[data-cerca]", radice),
    chips: $$("[data-categoria]", radice),
    gruppi: $$("[data-gruppo]", radice),
    articoli: $$("[data-articolo]", radice),
    nessunRisultato: $("[data-nessun-risultato]", radice),
    righe: $("[data-righe]", radice),
    vuoto: $("[data-vuoto]", radice),
    totale: $("[data-totale]", radice),
    pagamenti: $$("[data-pagamento]", radice),
    resto: $("[data-resto]", radice),
    restoValore: $("[data-resto-valore]", radice),
    mancante: $("[data-mancante]", radice),
    conferma: $("[data-conferma]", radice),
    svuota: $("[data-svuota]", radice),
    esito: $("[data-esito]", radice),
    riassunto: $("[data-riassunto]", radice),
    campi: {},
    ultimi: $("[data-ultimi]"),
    iframe: $("[data-iframe-stampa]"),
    avviso: $("[data-avviso]"),
    avvisoStampa: $("[data-avviso-stampa]"),
    avvisoListino: $("[data-avviso-listino]"),
    modoEsauriti: $("[data-modo-esauriti]", radice),
    notaOmaggio: $("[data-nota-omaggio]", radice),
    ordine: $(".pos-ordine", radice),
    avvisoEsauriti: $("[data-avviso-esauriti]", radice),
    asporto: $("[data-asporto]", radice),
    bannerAsporto: $("[data-banner-asporto]", radice),
  };
  $$("[data-campo]", radice).forEach(function (c) { el.campi[c.dataset.campo] = c; });

  // ---------------------------------------------------------------------
  // Utilità
  // ---------------------------------------------------------------------

  function uuid4() {
    // crypto.randomUUID esiste solo in HTTPS: in rete locale (http) si genera a mano.
    if (window.crypto && typeof crypto.randomUUID === "function") return crypto.randomUUID();
    var b = new Uint8Array(16);
    crypto.getRandomValues(b);
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    var h = Array.prototype.map.call(b, function (x) { return ("0" + x.toString(16)).slice(-2); }).join("");
    return h.slice(0, 8) + "-" + h.slice(8, 12) + "-" + h.slice(12, 16) + "-" + h.slice(16, 20) + "-" + h.slice(20);
  }

  function euro(cent) {
    var segno = cent < 0 ? "-" : "";
    cent = Math.abs(Math.round(cent));
    var intero = Math.floor(cent / 100).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ".");
    return segno + intero + "," + ("0" + (cent % 100)).slice(-2) + " €";
  }

  function leggiEuro(testo) {
    var t = (testo || "").replace(/€|\s/g, "");
    if (!t) return null;
    if (t.indexOf(",") >= 0) t = t.replace(/\./g, "").replace(",", ".");
    if (!/^\d+(\.\d{0,2})?$/.test(t)) return NaN;
    return Math.round(parseFloat(t) * 100);
  }

  function normalizza(testo) {
    return (testo || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").trim();
  }

  function crea(tag, classe, testo) {
    var nodo = document.createElement(tag);
    if (classe) nodo.className = classe;
    if (testo !== undefined) nodo.textContent = testo;
    return nodo;
  }

  function articolo(id) {
    return cfg.articoli[String(id)];
  }

  // ---------------------------------------------------------------------
  // Stato dell'ordine in corso
  // ---------------------------------------------------------------------

  function nuovoStato() {
    return {
      righe: [], nome: "", tavolo: "", coperti: "", note: "", ricevuti: "", pagamento: "", token: uuid4(), inviato: false,
      // Da asporto: il tavolo è «ASP» (lo mette il server) e non ci sono coperti.
      asporto: false,
      // Nome e tavolo messi da soli scegliendo «Omaggio»: si tolgono se si cambia pagamento.
      autoNome: false, autoTavolo: false,
    };
  }

  var stato = nuovoStato();
  var tolti = [];
  try {
    var salvato = JSON.parse(localStorage.getItem(chiaveBozza) || "null");
    if (salvato && Array.isArray(salvato.righe)) stato = Object.assign(nuovoStato(), salvato);
  } catch (e) { /* bozza illeggibile: si riparte vuoti */ }

  if (cfg.copia) {
    stato = nuovoStato();
    stato.nome = cfg.copia.nome || "";
    stato.tavolo = cfg.copia.tavolo || "";
    stato.coperti = cfg.copia.coperti ? String(cfg.copia.coperti) : "";
    stato.asporto = !!cfg.copia.asporto;
    stato.note = cfg.copia.note || "";
    stato.righe = cfg.copia.righe.map(function (r) { return { a: r.a, q: r.q, n: r.n || "" }; });
    tolti = cfg.copia.mancanti.slice();
    history.replaceState(null, "", location.pathname);
    mostraAvviso(
      "Copiato l'ordine n. " + cfg.copia.numero + ": correggilo e conferma." +
      (tolti.length ? " Non più in vendita (tolti): " + tolti.join(", ") + "." : "")
    );
    tolti = [];
  }

  stato.righe = stato.righe.filter(function (r) {
    var a = articolo(r.a);
    if (!a && r.t) tolti.push(r.t);
    return a && r.q > 0;
  });
  if (tolti.length) mostraAvviso("Tolti dall'ordine perché non più in vendita: " + tolti.join(", ") + ".");

  function salva() {
    try { localStorage.setItem(chiaveBozza, JSON.stringify(stato)); } catch (e) { /* spazio pieno o bloccato */ }
  }

  function modificato() {
    // Dopo un invio finito male (rete caduta) l'ordine potrebbe essere stato registrato:
    // se lo si cambia diventa un altro ordine, con un token nuovo.
    if (stato.inviato) {
      stato.inviato = false;
      stato.token = uuid4();
      mostraAvviso("Hai cambiato un ordine il cui invio non è andato a buon fine: controlla negli ultimi ordini che non sia già stato registrato.");
    }
    nascondiEsito();
    salva();
    aggiorna();
  }

  function totaleCent() {
    return stato.righe.reduce(function (somma, r) { return somma + articolo(r.a).prezzo * r.q; }, 0);
  }

  function aggiungi(id) {
    var a = articolo(id);
    if (!a || a.esaurito) return;
    var riga = stato.righe.find(function (r) { return String(r.a) === String(id) && !r.n; });
    if (riga) {
      riga.q = Math.min(riga.q + 1, 999);
    } else {
      riga = { a: Number(id), q: 1, n: "", t: a.nome };
      stato.righe.push(riga);
    }
    modificato();
    mostraRiga(stato.righe.indexOf(riga));
  }

  function mostraRiga(indice) {
    // Solo quando l'ordine scorre dentro il suo riquadro (PC): sul telefono la pagina non deve saltare.
    var carrello = el.righe.parentNode;
    var li = el.righe.children[indice];
    if (!li || carrello.scrollHeight <= carrello.clientHeight) return;
    if (li.offsetTop < carrello.scrollTop || li.offsetTop + li.offsetHeight > carrello.scrollTop + carrello.clientHeight) {
      carrello.scrollTop = li.offsetTop - carrello.clientHeight + li.offsetHeight + 8;
    }
    li.classList.add("appena-aggiunta");
    setTimeout(function () { li.classList.remove("appena-aggiunta"); }, 600);
  }

  function unisciUguali() {
    var viste = {};
    stato.righe = stato.righe.filter(function (r) {
      var chiave = r.a + "|" + (r.n || "");
      if (viste[chiave]) {
        viste[chiave].q = Math.min(viste[chiave].q + r.q, 999);
        return false;
      }
      viste[chiave] = r;
      return true;
    });
  }

  // ---------------------------------------------------------------------
  // Disegno
  // ---------------------------------------------------------------------

  function disegnaRighe() {
    el.righe.textContent = "";
    stato.righe.forEach(function (r, indice) {
      var a = articolo(r.a);
      var li = crea("li", "pos-riga");

      var testo = crea("div", "pos-riga-testo");
      testo.appendChild(crea("span", "pos-riga-nome", a.nome));
      if (r.n) testo.appendChild(crea("span", "pos-riga-nota", r.n));
      li.appendChild(testo);

      var quantita = crea("div", "pos-riga-quantita");
      var meno = crea("button", "pos-q", "−");
      meno.type = "button";
      meno.setAttribute("aria-label", "Uno in meno: " + a.nome);
      meno.addEventListener("click", function () {
        r.q -= 1;
        if (r.q <= 0) stato.righe.splice(indice, 1);
        modificato();
      });
      var numero = crea("span", "pos-q-numero", String(r.q));
      var piu = crea("button", "pos-q", "+");
      piu.type = "button";
      piu.setAttribute("aria-label", "Uno in più: " + a.nome);
      piu.addEventListener("click", function () {
        r.q = Math.min(r.q + 1, 999);
        modificato();
      });
      quantita.appendChild(meno);
      quantita.appendChild(numero);
      quantita.appendChild(piu);
      li.appendChild(quantita);

      li.appendChild(crea("span", "pos-riga-importo", euro(a.prezzo * r.q)));

      var notaBtn = crea("button", "pos-riga-nota-btn", r.n ? "Nota ✓" : "Nota");
      notaBtn.type = "button";
      notaBtn.setAttribute("aria-label", "Nota per " + a.nome);
      li.appendChild(notaBtn);

      var notaCampo = crea("input", "pos-riga-nota-campo");
      notaCampo.type = "text";
      notaCampo.maxLength = 60;
      notaCampo.placeholder = "Es. senza cipolla";
      notaCampo.value = r.n || "";
      notaCampo.hidden = true;
      notaCampo.setAttribute("aria-label", "Nota per " + a.nome);
      li.appendChild(notaCampo);

      notaBtn.addEventListener("click", function () {
        notaCampo.hidden = !notaCampo.hidden;
        if (!notaCampo.hidden) notaCampo.focus();
      });
      function salvaNota() {
        var nuova = notaCampo.value.replace(/\s+/g, " ").trim();
        if (nuova === (r.n || "")) {
          notaCampo.hidden = true;
          return;
        }
        // Con una nota diversa, un pezzo solo va su una riga a parte.
        if (r.q > 1 && nuova && !r.n) {
          r.q -= 1;
          stato.righe.splice(indice + 1, 0, { a: r.a, q: 1, n: nuova, t: a.nome });
        } else {
          r.n = nuova;
        }
        unisciUguali();
        modificato();
      }
      notaCampo.addEventListener("change", salvaNota);
      notaCampo.addEventListener("keydown", function (evento) {
        if (evento.key === "Enter") {
          evento.preventDefault();
          salvaNota();
        }
      });

      el.righe.appendChild(li);
    });
    el.vuoto.hidden = stato.righe.length > 0;
  }

  function disegnaConteggi() {
    var conteggi = {};
    stato.righe.forEach(function (r) { conteggi[r.a] = (conteggi[r.a] || 0) + r.q; });
    el.articoli.forEach(function (b) {
      var n = conteggi[b.dataset.articolo] || 0;
      var badge = $("[data-conteggio]", b);
      badge.hidden = n === 0;
      badge.textContent = n ? String(n) : "";
      b.classList.toggle("scelto", n > 0);
      var a = articolo(b.dataset.articolo);
      b.classList.toggle("esaurito", Boolean(a && a.esaurito));
      b.setAttribute("aria-disabled", a && a.esaurito && !modoEsauriti ? "true" : "false");
    });
  }

  function cosaManca() {
    var manca = [];
    if (!stato.righe.length) manca.push("gli articoli");
    var chiServe = cfg.obbligatori && stato.pagamento !== "omaggio";
    if (chiServe && !stato.nome.trim()) manca.push("il nome");
    if (chiServe && !stato.asporto && !stato.tavolo.trim()) manca.push("il tavolo");
    if (!stato.pagamento) manca.push("il pagamento");
    return manca;
  }

  function aggiorna() {
    disegnaRighe();
    disegnaConteggi();
    var totale = totaleCent();
    el.totale.textContent = euro(totale);

    Object.keys(el.campi).forEach(function (nome) {
      if (document.activeElement !== el.campi[nome]) el.campi[nome].value = stato[nome] || "";
    });
    if (el.asporto) {
      var asporto = stato.asporto && cfg.asporto.ammesso;
      el.asporto.setAttribute("aria-pressed", asporto ? "true" : "false");
      el.ordine.classList.toggle("in-asporto", asporto);
      el.bannerAsporto.hidden = !asporto;
      ["tavolo", "coperti"].forEach(function (nome) {
        var campo = el.campi[nome];
        if (!campo) return;
        campo.disabled = asporto;
        if (asporto) campo.value = nome === "tavolo" ? cfg.asporto.tavolo : "";
      });
    }
    el.pagamenti.forEach(function (p) { p.checked = p.value === stato.pagamento; });

    el.resto.hidden = stato.pagamento !== "contanti";
    if (el.notaOmaggio) el.notaOmaggio.hidden = stato.pagamento !== "omaggio";
    var ricevuti = leggiEuro(stato.ricevuti);
    if (ricevuti === null) {
      el.restoValore.textContent = "—";
      el.restoValore.className = "";
    } else if (isNaN(ricevuti)) {
      el.restoValore.textContent = "importo non valido";
      el.restoValore.className = "negativo";
    } else if (ricevuti < totale) {
      el.restoValore.textContent = "mancano " + euro(totale - ricevuti);
      el.restoValore.className = "negativo";
    } else {
      el.restoValore.textContent = euro(ricevuti - totale);
      el.restoValore.className = "";
    }

    var manca = cosaManca();
    el.mancante.textContent = manca.length && stato.righe.length ? "Manca " + manca.join(", ") + "." : "";
    el.conferma.disabled = bloccata || inCorso || manca.length > 0;

    var pezzi = stato.righe.reduce(function (s, r) { return s + r.q; }, 0);
    el.riassunto.hidden = pezzi === 0 || ordineInVista;
    el.riassunto.textContent = pezzi ? "Ordine: " + pezzi + (pezzi === 1 ? " articolo" : " articoli") + " · " + euro(totale) + " ↓" : "";
  }

  // ---------------------------------------------------------------------
  // Filtri e ricerca
  // ---------------------------------------------------------------------

  var categoriaAttiva = "";

  function filtra() {
    var testo = normalizza(el.cerca.value);
    var visibili = 0;
    el.articoli.forEach(function (b) {
      var a = articolo(b.dataset.articolo);
      var mostra = (!testo || normalizza(b.dataset.nome).indexOf(testo) !== -1) &&
        (testo || !categoriaAttiva || String(a.categoria) === categoriaAttiva);
      b.hidden = !mostra;
      if (mostra) visibili++;
    });
    el.gruppi.forEach(function (g) { g.hidden = !$("[data-articolo]:not([hidden])", g); });
    if (el.nessunRisultato) el.nessunRisultato.hidden = visibili > 0 || !el.articoli.length;
  }

  el.cerca.addEventListener("input", filtra);
  el.cerca.addEventListener("keydown", function (evento) {
    if (evento.key === "Enter") {
      evento.preventDefault();
      var primo = el.articoli.find(function (b) { return !b.hidden && !articolo(b.dataset.articolo).esaurito; });
      if (primo && el.cerca.value.trim()) {
        aggiungi(primo.dataset.articolo);
        el.cerca.value = "";
        filtra();
      }
    } else if (evento.key === "Escape") {
      el.cerca.value = "";
      filtra();
    }
  });
  el.chips.forEach(function (chip) {
    chip.addEventListener("click", function () {
      categoriaAttiva = chip.dataset.categoria;
      el.chips.forEach(function (c) { c.classList.toggle("attivo", c === chip); });
      filtra();
    });
  });

  // ---------------------------------------------------------------------
  // Esauriti
  // ---------------------------------------------------------------------

  var modoEsauriti = false;

  function impostaModoEsauriti(attivo) {
    modoEsauriti = attivo;
    el.modoEsauriti.setAttribute("aria-pressed", attivo ? "true" : "false");
    el.avvisoEsauriti.hidden = !attivo;
    radice.classList.toggle("modo-esauriti", attivo);
    disegnaConteggi();
  }

  el.modoEsauriti.addEventListener("click", function () { impostaModoEsauriti(!modoEsauriti); });
  $("[data-modo-esauriti-fine]", radice).addEventListener("click", function () { impostaModoEsauriti(false); });

  function cambiaEsaurito(id) {
    var a = articolo(id);
    var nuovo = !a.esaurito;
    fetch(cfg.url.esaurito.replace("/0/", "/" + id + "/"), {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      body: JSON.stringify({ esaurito: nuovo }),
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) throw new Error();
        a.esaurito = d.esaurito;
        disegnaConteggi();
        aggiornaStato(true);
      })
      .catch(function () { mostraAvviso("Non è stato possibile cambiare lo stato dell'articolo: riprova."); });
  }

  el.articoli.forEach(function (b) {
    b.addEventListener("click", function () {
      if (modoEsauriti) {
        cambiaEsaurito(b.dataset.articolo);
      } else if (!bloccata) {
        aggiungi(b.dataset.articolo);
      }
    });
  });

  // ---------------------------------------------------------------------
  // Campi, pagamento, resto
  // ---------------------------------------------------------------------

  Object.keys(el.campi).forEach(function (nome) {
    var campo = el.campi[nome];
    campo.addEventListener("input", function () {
      if (nome === "coperti") campo.value = campo.value.replace(/\D/g, "");
      stato[nome] = campo.value;
      if (nome === "nome") stato.autoNome = false;
      if (nome === "tavolo") stato.autoTavolo = false;
      if (nome === "ricevuti") {
        salva();
        aggiorna();
      } else {
        modificato();
      }
    });
    campo.addEventListener("keydown", function (evento) {
      if (evento.key !== "Enter") return;
      evento.preventDefault();
      if (nome === "nome" && el.campi.tavolo) el.campi.tavolo.focus();
      else if (nome === "tavolo") el.cerca.focus();
      else if (nome === "ricevuti" && !el.conferma.disabled) invia();
      else campo.blur();
    });
  });

  if (el.asporto) {
    el.asporto.addEventListener("click", function () {
      stato.asporto = !stato.asporto;
      if (stato.asporto) {
        stato.coperti = "";
        // Il tavolo scritto prima (o messo dall'omaggio) non serve più: sulla comanda va «ASP».
        stato.tavolo = "";
        stato.autoTavolo = false;
      }
      modificato();
      if (!stato.asporto && el.campi.tavolo) el.campi.tavolo.focus();
    });
  }

  function omaggio(attivo) {
    // Gli omaggi ai volontari non hanno cliente né tavolo: si riempiono da soli.
    if (!cfg.obbligatori) return;
    if (attivo) {
      if (!stato.nome.trim()) { stato.nome = cfg.omaggio.nome; stato.autoNome = true; }
      if (!stato.tavolo.trim() && !stato.asporto) { stato.tavolo = cfg.omaggio.tavolo; stato.autoTavolo = true; }
    } else {
      if (stato.autoNome && stato.nome === cfg.omaggio.nome) stato.nome = "";
      if (stato.autoTavolo && stato.tavolo === cfg.omaggio.tavolo) stato.tavolo = "";
      stato.autoNome = stato.autoTavolo = false;
    }
  }

  el.pagamenti.forEach(function (p) {
    p.addEventListener("change", function () {
      stato.pagamento = p.value;
      if (p.value !== "contanti") stato.ricevuti = "";
      omaggio(p.value === "omaggio");
      salva();
      aggiorna();
      if (p.value === "contanti" && el.campi.ricevuti) el.campi.ricevuti.focus();
    });
  });

  $$("[data-banconota]", radice).forEach(function (b) {
    b.addEventListener("click", function () {
      var cent = b.dataset.banconota === "esatto" ? totaleCent() : Number(b.dataset.banconota);
      stato.ricevuti = euro(cent).replace(" €", "");
      salva();
      aggiorna();
    });
  });

  var conferma_svuota = null;
  el.svuota.addEventListener("click", function () {
    if (!stato.righe.length && !stato.nome && !stato.tavolo) return;
    if (!conferma_svuota) {
      el.svuota.textContent = "Tocca di nuovo per svuotare";
      conferma_svuota = setTimeout(function () {
        conferma_svuota = null;
        el.svuota.textContent = "Svuota ordine";
      }, 3000);
      return;
    }
    clearTimeout(conferma_svuota);
    conferma_svuota = null;
    el.svuota.textContent = "Svuota ordine";
    stato = nuovoStato();
    salva();
    nascondiEsito();
    aggiorna();
  });

  // ---------------------------------------------------------------------
  // Invio
  // ---------------------------------------------------------------------

  var inCorso = false;

  function mostraAvviso(testo) {
    if (!el.avviso) return;
    el.avviso.textContent = testo;
    el.avviso.hidden = !testo;
  }

  function nascondiEsito() {
    el.esito.hidden = true;
    el.esito.className = "pos-esito";
  }

  function mostraEsito(classe, titolo, dettagli) {
    el.esito.textContent = "";
    el.esito.className = "pos-esito " + classe;
    el.esito.appendChild(crea("strong", "pos-esito-titolo", titolo));
    (dettagli || []).forEach(function (d) {
      if (!d) return;
      var p = crea("p", d.classe || "", d.testo);
      el.esito.appendChild(p);
    });
    el.esito.hidden = false;
  }

  function invia() {
    if (inCorso || bloccata || cosaManca().length) return;
    inCorso = true;
    el.conferma.disabled = true;
    el.conferma.textContent = "Registrazione in corso…";
    nascondiEsito();

    var totale = totaleCent();
    var ricevuti = stato.pagamento === "contanti" ? leggiEuro(stato.ricevuti) : null;
    var corpo = {
      token: stato.token,
      postazione: cfg.postazione,
      nome: stato.nome,
      tavolo: stato.asporto ? "" : stato.tavolo,
      coperti: stato.asporto ? "" : stato.coperti,
      asporto: !!stato.asporto,
      note: stato.note,
      pagamento: stato.pagamento,
      totale: totale,
      righe: stato.righe.map(function (r) { return { articolo: r.a, quantita: r.q, note: r.n || "" }; }),
    };
    var controllo = window.AbortController ? new AbortController() : null;
    var timer = controllo ? setTimeout(function () { controllo.abort(); }, 15000) : null;

    fetch(cfg.url.ordina, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf, Accept: "application/json" },
      body: JSON.stringify(corpo),
      signal: controllo ? controllo.signal : undefined,
    })
      .then(function (r) {
        var json = (r.headers.get("Content-Type") || "").indexOf("application/json") !== -1;
        if (!json) {
          // Pagina di accesso o errore del server: l'ordine non è stato registrato.
          return { dati: { ok: false, errori: [r.status >= 500
            ? "Errore del server: l'ordine non è stato registrato. Riprova tra poco."
            : "Sessione scaduta: ricarica la pagina ed entra di nuovo (l'ordine in corso resta salvato)."] } };
        }
        return r.json().then(function (d) { return { dati: d }; });
      })
      .then(function (risposta) {
        var d = risposta.dati;
        if (d.ok) {
          registrato(d, ricevuti, totale);
        } else {
          stato.inviato = false;
          salva();
          mostraEsito("errore", "Ordine NON registrato", (d.errori || []).map(function (t) { return { testo: t }; }));
          if (d.ricarica) avvisaListinoCambiato(true);
        }
      })
      .catch(function () {
        stato.inviato = true;
        salva();
        mostraEsito("errore", "Connessione assente: l'ordine potrebbe NON essere registrato", [
          { testo: "Premi di nuovo «Conferma»: se era già arrivato non verrà registrato due volte." },
        ]);
      })
      .then(function () {
        if (timer) clearTimeout(timer);
        inCorso = false;
        el.conferma.textContent = "Conferma e stampa";
        aggiorna();
      });
  }

  function registrato(d, ricevuti, totale) {
    var o = d.ordine;
    var dettagli = [];
    var chi = [o.tavolo ? "Tavolo " + o.tavolo : "", o.nome].filter(Boolean).join(" · ");
    dettagli.push({ testo: [chi, o.totaleTesto, o.pagamento].filter(Boolean).join(" · ") });
    if (ricevuti !== null && !isNaN(ricevuti) && ricevuti >= totale) {
      dettagli.push({ testo: "Resto " + euro(ricevuti - totale), classe: "pos-esito-resto" });
    }
    if (!d.nuovo) dettagli.push({ testo: "Era già stato registrato: non è stato duplicato." });
    mostraEsito("ok", "Ordine n. " + o.numero + " registrato", dettagli);

    if (o.daStampare.length) stampaDalBrowser(o.daStampare);
    stato = nuovoStato();
    salva();
    aggiorna();
    aggiornaStato();
    var primo = el.campi.nome || el.cerca;
    if (primo && window.matchMedia("(pointer: fine)").matches) primo.focus();
  }

  el.conferma.addEventListener("click", invia);
  radice.addEventListener("submit", function (evento) { evento.preventDefault(); });

  document.addEventListener("keydown", function (evento) {
    if (evento.key === "F2") {
      evento.preventDefault();
      el.cerca.focus();
      el.cerca.select();
    } else if (evento.key === "F9" || (evento.key === "Enter" && (evento.ctrlKey || evento.metaKey))) {
      evento.preventDefault();
      invia();
    }
  });

  // ---------------------------------------------------------------------
  // Stampa dal browser (stampanti impostate "dal browser della cassa")
  // ---------------------------------------------------------------------

  function stampaDalBrowser(ids) {
    var parametri = ids.map(function (id) { return "id=" + encodeURIComponent(id); }).join("&");
    el.iframe.src = cfg.url.stampeBrowser + "?" + parametri;
  }

  // ---------------------------------------------------------------------
  // Aggiornamento periodico: ultimi ordini, stampa, listino
  // ---------------------------------------------------------------------

  function avvisaListinoCambiato(subito) {
    var ordineVuoto = !stato.righe.length && !stato.nome && !stato.tavolo;
    if (ordineVuoto && !subito) {
      location.reload();
      return;
    }
    el.avvisoListino.hidden = false;
  }
  $("[data-ricarica]").addEventListener("click", function () { location.reload(); });

  function disegnaUltimi(ordini) {
    el.ultimi.textContent = "";
    if (!ordini.length) {
      el.ultimi.appendChild(crea("li", "tenue", "nessun ordine"));
      return;
    }
    ordini.forEach(function (o) {
      var li = crea("li", "pos-ultimo" + (o.annullato ? " annullato" : ""));
      var link = crea("a", "pos-ultimo-link");
      link.href = o.url;
      link.title = [o.ora, o.nome, o.totaleTesto, o.pagamento].filter(Boolean).join(" · ");
      link.appendChild(crea("strong", "", "N. " + o.numero));
      if (o.tavolo) link.appendChild(crea("span", "", "T. " + o.tavolo));
      link.appendChild(crea("span", "num", o.totaleTesto));
      li.appendChild(link);
      if (o.annullato) {
        li.appendChild(crea("span", "etichetta", "annullato"));
      } else if (o.stampa === "errore") {
        li.appendChild(crea("span", "etichetta pericolo", "errore stampa"));
      } else if (o.stampa === "coda") {
        li.appendChild(crea("span", "etichetta avviso", "in stampa"));
      } else if (o.stampa === "ok") {
        var ok = crea("span", "etichetta ok", "✓");
        ok.title = "Stampato";
        ok.setAttribute("aria-label", "stampato");
        li.appendChild(ok);
      }
      if (o.daStampare.length && !o.annullato) {
        var b = crea("button", "pulsante piccolo", "Stampa");
        b.type = "button";
        b.addEventListener("click", function () { stampaDalBrowser(o.daStampare); });
        li.appendChild(b);
      }
      el.ultimi.appendChild(li);
    });
  }

  var fuoriLinea = false;

  function aggiornaStato(adottaVersione) {
    return fetch(cfg.url.stato, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) {
        if ((r.headers.get("Content-Type") || "").indexOf("application/json") === -1) throw new Error("sessione");
        return r.json();
      })
      .then(function (d) {
        fuoriLinea = false;
        if (d.versione !== cfg.versione) {
          if (adottaVersione === true) cfg.versione = d.versione;
          else avvisaListinoCambiato(false);
        }
        // Chiusa o riaperta da un'altra cassa: si ricarica per bloccarla o sbloccarla.
        if (d.chiuso !== bloccata) location.reload();
        var avvisi = [];
        if (d.stampa.avviso) avvisi.push(d.stampa.avviso);
        if (d.stampa.errori) avvisi.push(d.stampa.errori + (d.stampa.errori === 1 ? " stampa è andata" : " stampe sono andate") + " in errore: controlla la stampante (carta, accesa, collegata).");
        el.avvisoStampa.textContent = avvisi.join(" ");
        el.avvisoStampa.hidden = !avvisi.length;
        disegnaUltimi(d.ordini);
      })
      .catch(function () {
        if (!fuoriLinea) {
          fuoriLinea = true;
          el.avvisoStampa.textContent = "Connessione con il server assente: gli ordini non possono essere registrati finché non torna.";
          el.avvisoStampa.hidden = false;
        }
      });
  }

  var ordineInVista = false;
  if (window.IntersectionObserver) {
    new IntersectionObserver(function (voci) {
      ordineInVista = voci[0].isIntersecting;
      aggiorna();
    }, { threshold: 0.15 }).observe(el.ordine);
  }

  // ---------------------------------------------------------------------
  // Avvio
  // ---------------------------------------------------------------------

  salva();
  filtra();
  aggiorna();
  aggiornaStato();
  setInterval(aggiornaStato, 5000);
})();
