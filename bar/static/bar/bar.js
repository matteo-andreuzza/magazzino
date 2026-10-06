/* Bar: la pagina dei fusti.
 *
 * Ogni operazione è un modulo normale nella pagina (si apre toccando un
 * fusto). Qui si aggiunge:
 *  - il trascinamento (mouse e dita; col dito si tiene premuto un attimo,
 *    così si può ancora scorrere la pagina): fusto nuovo su una spina =
 *    attacca, fusto attaccato tra i finiti = finito. Un fusto va solo sulle
 *    spine della sua bevanda (niente rossa sulla spina della bionda);
 *  - l'invio senza ricaricare la pagina (se non riesce, si invia normalmente);
 *  - i litri calcolati al volo mentre si scrive il peso;
 *  - l'aggiornamento quando si torna sulla pagina (un altro telefono può
 *    aver cambiato i fusti).
 */
(function () {
  "use strict";

  if (!document.querySelector("[data-fusti-pagina]")) return;

  var principale = document.querySelector("main.contenuto");

  function numero(testo) {
    var t = (testo || "").trim().replace(",", ".");
    if (!/^(\d+(\.\d*)?|\.\d+)$/.test(t)) return null;
    return parseFloat(t);
  }

  function litri(n) {
    return n.toFixed(1).replace(".", ",") + " l";
  }

  function pagina() {
    return document.querySelector("[data-fusti-pagina]");
  }

  // ---------------------------------------------------------------- avvisi

  var timerAvviso = null;
  function avvisoVolante(testo, errore) {
    var vecchio = document.querySelector(".avviso-volante");
    if (vecchio) vecchio.remove();
    var avviso = document.createElement("div");
    avviso.className = "avviso-volante" + (errore ? " errore" : "");
    avviso.setAttribute("role", "status");
    avviso.textContent = testo;
    document.body.appendChild(avviso);
    clearTimeout(timerAvviso);
    timerAvviso = setTimeout(function () { avviso.remove(); }, 5000);
  }

  // ------------------------------------------------- aggiornare la pagina

  var occupato = false;

  function sostituisci(html) {
    var nuovo = new DOMParser().parseFromString(html, "text/html");
    var contenuto = nuovo.querySelector("main.contenuto");
    if (!contenuto || !nuovo.querySelector("[data-fusti-pagina]")) return false;
    document.querySelectorAll("dialog[open]").forEach(function (d) { d.close(); });
    principale.innerHTML = contenuto.innerHTML;
    document.title = nuovo.title;
    var messaggi = principale.querySelectorAll(".messaggio");
    if (messaggi.length) {
      var ultimo = messaggi[messaggi.length - 1];
      var sopra = ultimo.getBoundingClientRect().top;
      if (sopra < 0 || sopra > window.innerHeight - 40) {
        avvisoVolante(ultimo.textContent.trim(), ultimo.classList.contains("error"));
      }
    }
    return true;
  }

  function invia(form) {
    if (occupato) return;
    occupato = true;
    document.body.classList.add("fusti-in-corso");
    form.querySelectorAll("button[type=submit]").forEach(function (b) { b.disabled = true; });
    fetch(form.action, {
      method: "POST",
      body: new FormData(form),
      credentials: "same-origin",
      headers: { "X-Requested-With": "fetch" },
    })
      .then(function (risposta) {
        if (!risposta.ok) throw new Error("risposta " + risposta.status);
        return risposta.text();
      })
      .then(function (html) {
        if (!sostituisci(html)) throw new Error("pagina inattesa");
        occupato = false;
        document.body.classList.remove("fusti-in-corso");
      })
      .catch(function () {
        // Senza rete o con un errore: si manda il modulo come una volta (la pagina si ricarica).
        HTMLFormElement.prototype.submit.call(form);
      });
  }

  function ricarica() {
    if (occupato || trascina || document.querySelector("dialog[open]")) return;
    fetch(window.location.href, { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
      .then(function (r) { return r.ok ? r.text() : null; })
      .then(function (html) {
        if (html && !occupato && !trascina && !document.querySelector("dialog[open]")) sostituisci(html);
      })
      .catch(function () { /* si riprova più tardi */ });
  }

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") ricarica();
  });
  setInterval(function () {
    if (document.visibilityState === "visible") ricarica();
  }, 60000);

  document.addEventListener("submit", function (evento) {
    var form = evento.target;
    if (!form.matches || !form.matches("form[data-azione-fusti]")) return;
    evento.preventDefault();
    invia(form);
  });

  // -------------------------------------------------------------- finestre

  var appenaTrascinato = false;

  document.addEventListener("click", function (evento) {
    var stesso = evento.target.closest("[data-stesso-peso]");
    if (stesso) {
      var campo = stesso.closest("[data-pesata]").querySelector("[data-peso]");
      campo.value = stesso.dataset.stessoPeso;
      anteprimaPeso(campo);
      return;
    }
    var apri = evento.target.closest("[data-apri]");
    if (!apri) return;
    if (appenaTrascinato) {
      appenaTrascinato = false;
      return;
    }
    var finestra = document.getElementById(apri.dataset.apri);
    if (finestra && finestra.showModal) {
      finestra.showModal();
      finestra.querySelectorAll("[data-peso]").forEach(anteprimaPeso);
    }
  });

  // Toccando fuori dalla finestra si chiude.
  document.addEventListener("click", function (evento) {
    var d = evento.target;
    if (d.tagName !== "DIALOG" || !d.open) return;
    var r = d.getBoundingClientRect();
    if (evento.clientX < r.left || evento.clientX > r.right || evento.clientY < r.top || evento.clientY > r.bottom) d.close();
  });

  // Come i controlli del server (services.controlla_peso): con litri e tara provvisori
  // quello che dipende da loro è solo un avviso.
  function anteprimaPeso(campo) {
    var riga = campo.closest("[data-pesata]");
    var testo = riga.querySelector("[data-anteprima-peso]");
    var peso = numero(campo.value);
    var prima = numero(riga.dataset.prima);
    var tara = numero(riga.dataset.tara);
    var densita = numero(riga.dataset.densita) || 1;
    var capienza = numero(riga.dataset.capienza);
    var daPieno = riga.dataset.daPieno === "1";
    var provvisori = riga.dataset.provvisori === "1";
    testo.classList.remove("attenzione");
    if (peso === null) {
      testo.textContent = campo.value.trim() ? "Scrivi il peso in chili, per esempio 18,4." : "";
      return;
    }
    if (prima !== null && !daPieno && peso > prima) {
      testo.textContent = "Pesa più di prima: controlla il peso (se è stato cambiato, prima sposta tra i finiti quello finito).";
      testo.classList.add("attenzione");
      return;
    }
    if (tara !== null && peso < tara && !provvisori) {
      testo.textContent = "Pesa meno di un fusto vuoto: se è finito spostalo tra i finiti.";
      testo.classList.add("attenzione");
      return;
    }
    if (prima !== null && daPieno && peso > prima + 1.5 && !provvisori) {
      testo.textContent = "Pesa più di un fusto pieno: controlla il peso.";
      testo.classList.add("attenzione");
      return;
    }
    var parti = [];
    if (tara !== null) {
      var rimasti = Math.max(0, (peso - tara) / densita);
      if (capienza !== null) rimasti = Math.min(rimasti, capienza);
      parti.push("restano ≈ " + litri(rimasti));
    }
    if (prima !== null) parti.push("usciti stasera ≈ " + litri(Math.max(0, (prima - peso) / densita)));
    if (provvisori) parti.push("(litri e tara provvisori)");
    testo.textContent = parti.join(" · ");
  }

  document.addEventListener("input", function (evento) {
    if (evento.target.matches && evento.target.matches("[data-peso]")) anteprimaPeso(evento.target);
  });

  // Nella pesata di tutti i fusti, Invio passa al peso dopo (e dall'ultimo salva).
  document.addEventListener("keydown", function (evento) {
    if (evento.key !== "Enter" || !evento.target.matches || !evento.target.matches(".pesata-tutti [data-peso]")) return;
    var campi = Array.prototype.slice.call(evento.target.form.querySelectorAll("[data-peso]"));
    var dopo = campi[campi.indexOf(evento.target) + 1];
    if (dopo) {
      evento.preventDefault();
      dopo.focus();
    }
  });

  // ----------------------------------------------------------- conferme

  function esegui(campi) {
    var form = document.querySelector("[data-modulo-trascina]");
    if (!form) return;
    ["azione", "spina", "tipo", "fusto", "attuale"].forEach(function (nome) {
      form.elements[nome].value = campi[nome] || "";
    });
    invia(form);
  }

  function conferma(titolo, testo, si, campi) {
    var finestra = document.getElementById("d-conferma");
    if (!finestra) return;
    finestra.querySelector("[data-conferma-titolo]").textContent = titolo;
    finestra.querySelector("[data-conferma-testo]").textContent = testo;
    var pulsante = finestra.querySelector("[data-conferma-si]");
    pulsante.textContent = si;
    pulsante.onclick = function () {
      finestra.close();
      esegui(campi);
    };
    finestra.querySelector("[data-conferma-no]").onclick = function () { finestra.close(); };
    finestra.showModal();
  }

  // ---------------------------------------------------------- trascinare

  var trascina = null;

  // Una spina che dà una bevanda accetta solo i fusti di quella bevanda.
  function spineCompatibili(scena, origine, soloLibere) {
    var selettore = ".attacco[data-zona=spina]" + (soloLibere ? ":not([data-attaccato])" : "");
    var solo = origine.dataset.soloSpina; // un fusto di una serata prima torna solo alla sua spina
    return Array.prototype.filter.call(scena.querySelectorAll(selettore), function (spina) {
      var sua = spina.dataset.bevandaSpina;
      if (solo && spina.dataset.spina !== solo) return false;
      return !sua || sua === origine.dataset.bevanda;
    });
  }

  function bersagliPer(origine) {
    var scena = pagina().querySelector("[data-scena]");
    var tipo = origine.dataset.trascina;
    if (tipo === "pieno") return spineCompatibili(scena, origine, false);
    if (tipo === "consumato") return spineCompatibili(scena, origine, true);
    if (tipo === "attaccato") {
      var zone = [scena.querySelector("[data-zona=consumati]")];
      if (origine.hasAttribute("data-staccabile")) zone.push(scena.querySelector("[data-zona=pieni]"));
      return zone.filter(Boolean);
    }
    return [];
  }

  function inizia(evento) {
    var t = trascina;
    var disegno = t.el.querySelector(".fusto-svg");
    var r = (disegno || t.el).getBoundingClientRect();
    var fantasma = document.querySelector("[data-trascinato]");
    fantasma.replaceChildren(disegno ? disegno.cloneNode(true) : document.createTextNode(""));
    fantasma.style.width = Math.max(r.width, 48) + "px";
    fantasma.hidden = false;
    t.fantasma = fantasma;
    t.mezzo = { x: Math.max(r.width, 48) / 2, y: r.height * 0.55 };
    t.attivo = true;
    t.bersagli = bersagliPer(t.el);
    t.bersagli.forEach(function (b) { b.classList.add("bersaglio"); });
    t.el.classList.add("origine");
    t.el.classList.remove("pronto");
    document.body.classList.add("trascinando");
    if (t.el.setPointerCapture) {
      try { t.el.setPointerCapture(t.id); } catch (e) { /* va bene lo stesso */ }
    }
    scorriBordi();
  }

  function muovi(evento) {
    var t = trascina;
    t.x = evento.clientX;
    t.y = evento.clientY;
    t.fantasma.style.transform = "translate(" + (t.x - t.mezzo.x) + "px," + (t.y - t.mezzo.y) + "px) rotate(-4deg)";
    var sotto = document.elementFromPoint(t.x, t.y);
    var zona = sotto && sotto.closest("[data-zona]");
    if (zona && t.bersagli.indexOf(zona) === -1) zona = null;
    if (zona !== t.sopra) {
      if (t.sopra) t.sopra.classList.remove("sopra");
      if (zona) zona.classList.add("sopra");
      t.sopra = zona;
    }
  }

  // Vicino al bordo dello schermo la pagina scorre da sola (sul telefono le zone stanno una sotto l'altra).
  function scorriBordi() {
    if (!trascina || !trascina.attivo) return;
    var y = trascina.y;
    if (typeof y === "number") {
      if (y < 70) window.scrollBy(0, -Math.ceil((70 - y) / 5));
      else if (y > window.innerHeight - 70) window.scrollBy(0, Math.ceil((y - window.innerHeight + 70) / 5));
    }
    window.requestAnimationFrame(scorriBordi);
  }

  function lascia() {
    var t = trascina;
    var zona = t.sopra;
    var origine = t.el;
    var nome = origine.dataset.nome || "fusto";
    if (!zona) return;
    var tipo = origine.dataset.trascina;
    if (tipo === "pieno") {
      if (zona.dataset.attaccato) {
        conferma(
          "Cambio fusto a «" + zona.dataset.nomeSpina + "»",
          "Il " + (zona.dataset.nomeAttaccato || "fusto").toLowerCase() + " attaccato è finito? Va tra i finiti e al suo posto si attacca un " + nome.toLowerCase() + " nuovo.",
          "Sì, cambia il fusto",
          { azione: "cambia", spina: zona.dataset.spina, tipo: origine.dataset.tipo, attuale: zona.dataset.attaccato }
        );
      } else {
        esegui({ azione: "attacca", spina: zona.dataset.spina, tipo: origine.dataset.tipo });
      }
    } else if (tipo === "attaccato") {
      if (zona.dataset.zona === "consumati") {
        esegui({ azione: "finito", fusto: origine.dataset.fusto });
      } else if (zona.dataset.zona === "pieni") {
        conferma(
          "Rimettere tra i nuovi?",
          "Solo se è stato attaccato per sbaglio: il fusto torna in magazzino pieno, come se non fosse mai stato attaccato.",
          "Sì, rimettilo tra i nuovi",
          { azione: "stacca", fusto: origine.dataset.fusto }
        );
      }
    } else if (tipo === "consumato") {
      esegui({ azione: "riattacca", fusto: origine.dataset.fusto, spina: zona.dataset.spina });
    }
  }

  function fine() {
    var t = trascina;
    if (!t) return;
    clearTimeout(t.timer);
    t.el.classList.remove("pronto", "origine");
    if (t.attivo) {
      t.fantasma.hidden = true;
      t.fantasma.replaceChildren();
      t.fantasma.style.transform = "";
      t.bersagli.forEach(function (b) { b.classList.remove("bersaglio", "sopra"); });
      document.body.classList.remove("trascinando");
      // Il clic che arriva subito dopo il rilascio non deve aprire la finestra.
      appenaTrascinato = true;
      setTimeout(function () { appenaTrascinato = false; }, 350);
    }
    trascina = null;
  }

  document.addEventListener("pointerdown", function (evento) {
    var el = evento.target.closest("[data-trascina]");
    if (!el || occupato || (evento.pointerType === "mouse" && evento.button !== 0)) return;
    trascina = {
      el: el, id: evento.pointerId, x0: evento.clientX, y0: evento.clientY,
      attivo: false, pronto: evento.pointerType !== "touch",
    };
    if (!trascina.pronto) {
      // Col dito: tenendo premuto un attimo si prende il fusto; muovendosi subito si scorre la pagina.
      trascina.timer = setTimeout(function () {
        if (!trascina || trascina.el !== el) return;
        trascina.pronto = true;
        el.classList.add("pronto");
        if (navigator.vibrate) navigator.vibrate(12);
      }, 230);
    }
  });

  document.addEventListener("pointermove", function (evento) {
    if (!trascina || evento.pointerId !== trascina.id) return;
    if (!trascina.attivo) {
      var dx = evento.clientX - trascina.x0, dy = evento.clientY - trascina.y0;
      if (Math.sqrt(dx * dx + dy * dy) < 8) return;
      if (!trascina.pronto) {
        fine();
        return;
      }
      inizia(evento);
    }
    evento.preventDefault();
    muovi(evento);
  });

  // Una volta preso il fusto col dito, la pagina non deve scorrere sotto.
  document.addEventListener("touchmove", function (evento) {
    if (trascina && trascina.pronto) evento.preventDefault();
  }, { passive: false });

  document.addEventListener("pointerup", function (evento) {
    if (!trascina || evento.pointerId !== trascina.id) return;
    if (trascina.attivo) lascia();
    fine();
  });
  document.addEventListener("pointercancel", function (evento) {
    if (trascina && evento.pointerId === trascina.id) fine();
  });
  document.addEventListener("keydown", function (evento) {
    if (evento.key === "Escape" && trascina) fine();
  });
  document.addEventListener("contextmenu", function (evento) {
    if (evento.target.closest && evento.target.closest("[data-trascina]")) evento.preventDefault();
  });
  document.addEventListener("dragstart", function (evento) {
    if (evento.target.closest && evento.target.closest("[data-trascina]")) evento.preventDefault();
  });
})();
