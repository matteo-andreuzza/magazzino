/* Monitor della cucina: comande da fare, in preparazione e pronte. Si aggiorna ogni 3 secondi. */
(function () {
  "use strict";
  var datiEl = document.getElementById("monitor-dati");
  if (!datiEl) return;
  var cfg = JSON.parse(datiEl.textContent);
  var csrf = document.querySelector("input[name=csrfmiddlewaretoken]").value;
  var colonne = {};
  document.querySelectorAll("[data-colonna]").forEach(function (c) { colonne[c.dataset.colonna] = c; });
  var conte = {};
  document.querySelectorAll("[data-conta]").forEach(function (c) { conte[c.dataset.conta] = c; });
  var totaliEl = document.querySelector("[data-totali]");
  var connessione = document.querySelector("[data-connessione]");
  var pulsanteSuono = document.querySelector("[data-suono]");

  var differenzaOrologio = 0; // ora del server - ora del tablet
  var ultimi = null;
  var firma = "";
  var visti = null;
  var suono = false;
  var audio = null;

  function crea(tag, classe, testo) {
    var nodo = document.createElement(tag);
    if (classe) nodo.className = classe;
    if (testo !== undefined) nodo.textContent = testo;
    return nodo;
  }

  function minutiDa(iso) {
    return Math.max(0, Math.floor((Date.now() + differenzaOrologio - new Date(iso).getTime()) / 60000));
  }

  function beep() {
    if (!suono) return;
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      [0, 0.25].forEach(function (ritardo) {
        var osc = audio.createOscillator();
        var guadagno = audio.createGain();
        osc.frequency.value = 880;
        guadagno.gain.value = 0.25;
        osc.connect(guadagno);
        guadagno.connect(audio.destination);
        osc.start(audio.currentTime + ritardo);
        osc.stop(audio.currentTime + ritardo + 0.15);
      });
    } catch (e) { /* audio non disponibile */ }
  }

  pulsanteSuono.addEventListener("click", function () {
    suono = !suono;
    pulsanteSuono.setAttribute("aria-pressed", suono ? "true" : "false");
    pulsanteSuono.textContent = suono ? "Suono attivo" : "Attiva suono";
    if (suono) beep();
  });
  var schermo = document.querySelector("[data-schermo-intero]");
  schermo.addEventListener("click", function () {
    if (document.fullscreenElement) document.exitFullscreen();
    else if (document.documentElement.requestFullscreen) document.documentElement.requestFullscreen();
  });

  function azione(id, nome) {
    return fetch(cfg.url.azione.replace("/0/", "/" + id + "/").replace("AZIONE", nome), {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRFToken": csrf, Accept: "application/json" },
    })
      .then(function (r) { return r.json(); })
      .catch(function () { connessione.textContent = "Azione non riuscita: controlla la connessione"; connessione.className = "monitor-connessione errore"; })
      .then(aggiorna);
  }

  function pulsante(testo, classe, id, nome) {
    var b = crea("button", "pulsante " + classe, testo);
    b.type = "button";
    b.addEventListener("click", function () {
      b.disabled = true;
      azione(id, nome);
    });
    return b;
  }

  function scheda(c) {
    var riferimento = c.stato === "pronta" && c.pronta ? c.pronta : c.creato;
    var minuti = minutiDa(c.creato);
    var classe = "monitor-comanda";
    if (c.annullato) classe += " annullata";
    else if (c.stato !== "pronta") classe += minuti >= 20 ? " ritardo" : minuti >= 10 ? " attesa" : "";
    var box = crea("article", classe);

    var testa = crea("header", "monitor-testa");
    testa.appendChild(crea("span", "monitor-numero", "N. " + c.numero));
    if (c.tavolo) testa.appendChild(crea("span", "monitor-tavolo", "Tav. " + c.tavolo));
    var tempo = crea("span", "monitor-tempo", c.stato === "pronta" ? "pronta da " + minutiDa(riferimento) + "'" : minuti + "'");
    testa.appendChild(tempo);
    box.appendChild(testa);

    if (c.nome) box.appendChild(crea("div", "monitor-nome", c.nome + (c.coperti ? " · " + c.coperti + " coperti" : "")));
    if (c.annullato) box.appendChild(crea("div", "monitor-annullato", "ANNULLATO: non preparare"));

    var elenco = crea("ul", "monitor-righe");
    c.righe.forEach(function (r) {
      var li = crea("li");
      li.appendChild(crea("strong", "monitor-q", r.q + "×"));
      li.appendChild(crea("span", "", r.t));
      if (r.n) li.appendChild(crea("span", "monitor-nota", r.n));
      elenco.appendChild(li);
    });
    box.appendChild(elenco);
    if (c.note) box.appendChild(crea("p", "monitor-note", "Note: " + c.note));

    var azioni = crea("div", "monitor-azioni");
    if (c.annullato) {
      azioni.appendChild(pulsante("Tolgo", "", c.id, "ritira"));
    } else if (c.stato === "nuova") {
      azioni.appendChild(pulsante("Inizio", "", c.id, "inizia"));
      azioni.appendChild(pulsante("Pronta", "secondario", c.id, "pronta"));
    } else if (c.stato === "preparazione") {
      azioni.appendChild(pulsante("Pronta", "", c.id, "pronta"));
      azioni.appendChild(pulsante("↩", "secondario indietro", c.id, "indietro"));
    } else if (c.stato === "pronta") {
      azioni.appendChild(pulsante("Ritirata", "", c.id, "ritira"));
      azioni.appendChild(pulsante("↩", "secondario indietro", c.id, "indietro"));
    }
    box.appendChild(azioni);
    return box;
  }

  function disegna(d) {
    var ora = new Date(d.ora).getTime();
    differenzaOrologio = ora - Date.now();
    var perColonna = { nuova: [], preparazione: [], pronta: [] };
    d.comande.forEach(function (c) {
      var colonna = c.stato === "ritirata" ? "pronta" : c.stato;
      if (perColonna[colonna]) perColonna[colonna].push(c);
    });
    Object.keys(perColonna).forEach(function (nome) {
      var contenitore = colonne[nome];
      contenitore.textContent = "";
      perColonna[nome].forEach(function (c) { contenitore.appendChild(scheda(c)); });
      if (!perColonna[nome].length) contenitore.appendChild(crea("p", "monitor-vuota", "Nessuna comanda"));
      conte[nome].textContent = String(perColonna[nome].filter(function (c) { return !c.annullato; }).length);
    });
    totaliEl.textContent = "";
    d.daPreparare.forEach(function (t) {
      var li = crea("li");
      li.appendChild(crea("strong", "monitor-q", t.q + "×"));
      li.appendChild(crea("span", "", t.t));
      totaliEl.appendChild(li);
    });
    if (!d.daPreparare.length) totaliEl.appendChild(crea("li", "tenue", "Niente"));

    var nuove = d.comande.filter(function (c) { return c.stato === "nuova" && !c.annullato; }).map(function (c) { return c.id; });
    if (visti !== null && nuove.some(function (id) { return visti.indexOf(id) === -1; })) beep();
    visti = (visti || []).concat(nuove);
  }

  function aggiorna() {
    return fetch(cfg.url.dati, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) {
        if ((r.headers.get("Content-Type") || "").indexOf("application/json") === -1) throw new Error("sessione");
        return r.json();
      })
      .then(function (d) {
        if (!d.ok) throw new Error("spento");
        ultimi = d;
        // Si ridisegna solo se qualcosa è cambiato, per non perdere un tocco a metà.
        var nuova = JSON.stringify([d.comande, d.daPreparare]);
        if (nuova !== firma) {
          firma = nuova;
          disegna(d);
        }
        connessione.textContent = "Aggiornato alle " + new Date().toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
        connessione.className = "monitor-connessione";
      })
      .catch(function () {
        connessione.textContent = "Connessione assente: le comande potrebbero non essere aggiornate";
        connessione.className = "monitor-connessione errore";
      });
  }

  aggiorna();
  setInterval(aggiorna, 3000);
  // Aggiorna i minuti trascorsi anche senza risposte nuove.
  setInterval(function () { if (ultimi) disegna(ultimi); }, 30000);
})();
