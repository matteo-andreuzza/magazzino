/* Pannello del sistema: due schemi animati (chi parla con chi, dentro il programma) e le
   tabelle di servizi, stampanti, dispositivi, attività, errori e versione. Si aggiorna da
   solo ogni 5 secondi da /sistema/stato.json (vedi sistema/monitor.py).

   Gli schemi si ricostruiscono solo quando cambia la loro forma (un dispositivo che arriva,
   uno stato che cambia): altrimenti si aggiornano solo le scritte e le animazioni non ripartono. */
(function () {
  "use strict";

  var NS = "http://www.w3.org/2000/svg";
  var OGNI = 5000;
  var radice = document.querySelector("[data-pannello]");
  if (!radice) return;
  var indirizzo = radice.dataset.indirizzoStato;
  var movimento = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var stato = JSON.parse(document.getElementById("stato-iniziale").textContent);
  var scelto = "server";
  var sceltoProgramma = null;
  var inPausa = false;
  var firme = {};
  var ultimoArrivo = new Date();
  var ultimoErrore = null;

  var NOMI_STATO = { ok: "a posto", avviso: "da guardare", errore: "guasto", spento: "spento", sconosciuto: "non si sa" };
  var PESO = { ok: 0, spento: 1, sconosciuto: 2, avviso: 3, errore: 4 };

  // -------------------------------------------------------------------------
  // Utilità
  // -------------------------------------------------------------------------

  function svg(nome, attributi, padre) {
    var e = document.createElementNS(NS, nome);
    Object.keys(attributi || {}).forEach(function (k) {
      if (attributi[k] !== null && attributi[k] !== undefined) e.setAttribute(k, attributi[k]);
    });
    if (padre) padre.appendChild(e);
    return e;
  }

  function el(tag, classe, testo, padre) {
    var e = document.createElement(tag);
    if (classe) e.className = classe;
    if (testo !== undefined && testo !== null) e.textContent = testo;
    if (padre) padre.appendChild(e);
    return e;
  }

  function taglia(t, n) {
    t = t === null || t === undefined ? "" : String(t);
    return t.length > n ? t.slice(0, n - 1) + "…" : t;
  }

  function due(n) { return (n < 10 ? "0" : "") + n; }

  function ora(d) { return due(d.getHours()) + ":" + due(d.getMinutes()); }

  function quando(iso) {
    if (!iso) return "mai";
    var d = new Date(iso);
    if (isNaN(d)) return iso;
    var oggi = new Date();
    var ieri = new Date(oggi.getTime() - 86400000);
    if (d.toDateString() === oggi.toDateString()) return "oggi " + ora(d);
    if (d.toDateString() === ieri.toDateString()) return "ieri " + ora(d);
    return due(d.getDate()) + "/" + due(d.getMonth() + 1) + " " + ora(d);
  }

  function fa(iso) {
    if (!iso) return "mai";
    var s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (isNaN(s)) return iso;
    if (s < 0) s = 0;
    if (s < 10) return "adesso";
    if (s < 60) return Math.round(s) + " s fa";
    if (s < 3600) return Math.round(s / 60) + " min fa";
    if (s < 86400) return Math.floor(s / 3600) + " h " + Math.round((s % 3600) / 60) + " min fa";
    return quando(iso);
  }

  function durata(secondi) {
    secondi = Math.max(0, Math.round(secondi));
    var g = Math.floor(secondi / 86400), h = Math.floor((secondi % 86400) / 3600), m = Math.floor((secondi % 3600) / 60);
    if (g) return g + " g " + h + " h";
    if (h) return h + " h " + m + " min";
    if (m) return m + " min";
    return secondi + " s";
  }

  function accesoDa(iso) { return iso ? durata((Date.now() - new Date(iso).getTime()) / 1000) : null; }

  function byte(n) {
    if (n === null || n === undefined) return "";
    var u = ["B", "KB", "MB", "GB", "TB"], i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return n.toFixed(i ? 1 : 0).replace(".", ",") + " " + u[i];
  }

  function peggiore() {
    var risultato = "ok";
    Array.prototype.forEach.call(arguments, function (s) { if ((PESO[s] || 0) > PESO[risultato]) risultato = s; });
    return risultato;
  }

  /* Lo stato di un filo: guasto se uno dei due capi è guasto, spento se uno è spento o non si sa. */
  function statoFilo(a, b) {
    if (a === "errore" || b === "errore") return "errore";
    if (a === "spento" || b === "spento" || a === "sconosciuto" || b === "sconosciuto") return "spento";
    if (a === "avviso" || b === "avviso") return "avviso";
    return "ok";
  }

  function perId(elenco) {
    var mappa = {};
    (elenco || []).forEach(function (x) { mappa[x.id] = x; });
    return mappa;
  }

  // -------------------------------------------------------------------------
  // Icone (24x24, solo contorno)
  // -------------------------------------------------------------------------

  var ICONE = {
    telefono: "M8 2h8a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1zM11 18.5h2",
    tablet: "M5 3h14a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zM11 18h2",
    computer: "M3 4h18v12H3zM8 20h8M12 16v4",
    claude: "M12 3l2.2 6.8H21l-5.5 4 2.1 6.7L12 16.4 6.4 20.5l2.1-6.7L3 9.8h6.8z",
    stampante: "M6 9V3h12v6M6 18H4a1 1 0 0 1-1-1v-6a1 1 0 0 1 1-1h16a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-2M6 14h12v7H6z",
    rete: "M3 8h18v8H3zM6.5 12h.01M9.5 12h.01M12.5 12h.01M15.5 12h.01M5 8V5M19 8V5M12 4a6 6 0 0 1 5 2.5M12 4a6 6 0 0 0-5 2.5",
    internet: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3c3.2 3.2 3.2 14.8 0 18M12 3c-3.2 3.2-3.2 14.8 0 18",
    database: "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
    nuvola: "M7 18h10a4 4 0 0 0 .6-8 6 6 0 0 0-11.6 1.6A3.3 3.3 0 0 0 7 18z",
    casa: "M3 11l9-7 9 7M5 10v10h14V10M10 20v-6h4v6",
    web: "M4 4h16v16H4zM4 9h16M7 6.5h.01M10 6.5h.01",
    backup: "M4 7h16v13H4zM4 7l2-3h12l2 3M9 12h6",
    copia: "M8 8h12v12H8zM4 16V4h12",
    ingranaggio: "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1",
    codice: "M8 7l-5 5 5 5M16 7l5 5-5 5M14 4l-4 16",
    coda: "M4 6h16M4 12h16M4 18h10",
  };

  function icona(padre, nome, x, y, scala) {
    if (!ICONE[nome]) return;
    svg("path", { d: ICONE[nome], class: "nodo-icona", transform: "translate(" + x + " " + y + ") scale(" + (scala || 1) + ")" }, padre);
  }

  // -------------------------------------------------------------------------
  // Disegno comune ai due schemi
  // -------------------------------------------------------------------------

  function preparaTela(tela) {
    while (tela.firstChild) tela.removeChild(tela.firstChild);
    var defs = svg("defs", {}, tela);
    var filtro = svg("filter", { id: "bagliore", x: "-200%", y: "-200%", width: "500%", height: "500%" }, defs);
    svg("feGaussianBlur", { stdDeviation: "2.4", result: "sfocato" }, filtro);
    var unione = svg("feMerge", {}, filtro);
    svg("feMergeNode", { in: "sfocato" }, unione);
    svg("feMergeNode", { in: "SourceGraphic" }, unione);
    return { fili: svg("g", { class: "strato-fili" }, tela), nodi: svg("g", { class: "strato-nodi" }, tela), puntini: svg("g", { class: "strato-puntini" }, tela) };
  }

  /* Un filo con i puntini che scorrono. tipo: richieste (andata e ritorno), dati, stampe. */
  function filo(strati, f, prefisso) {
    var id = prefisso + "-" + f.id;
    svg("path", { id: id, d: f.d, class: "filo filo-" + f.stato }, strati.fili);
    if (f.etichetta) {
      svg("text", { class: "etichetta-filo", x: f.etichetta[1], y: f.etichetta[2], "text-anchor": f.etichetta[3] || "middle" }, strati.fili)
        .textContent = f.etichetta[0];
    }
    if (!f.attivo || f.stato === "spento" || f.stato === "errore" || !movimento) return;
    var tempo = f.veloce ? 1.3 : (f.tipo === "richieste" ? 2.2 : 3.2);
    var quanti = f.quanti || (f.veloce ? 4 : 3);
    var andata = f.tipo === "richieste" ? "richieste" : f.tipo;
    for (var i = 0; i < quanti; i++) puntino(strati.puntini, id, andata, tempo, -i * tempo / quanti, f.inverso);
    if (f.tipo === "richieste") {
      for (var j = 0; j < 2; j++) puntino(strati.puntini, id, "risposte", tempo * 1.15, -j * tempo / 2 - tempo / 4, !f.inverso);
    }
  }

  function puntino(padre, id, tipo, tempo, inizio, inverso) {
    var c = svg("circle", { r: tipo === "risposte" ? 2.4 : 3.1, class: "puntino puntino-" + tipo }, padre);
    var a = svg("animateMotion", {
      dur: tempo.toFixed(2) + "s", repeatCount: "indefinite", begin: inizio.toFixed(2) + "s",
      keyPoints: inverso ? "1;0" : "0;1", keyTimes: "0;1", calcMode: "linear",
    }, c);
    svg("mpath", { href: "#" + id }, a);
  }

  /* Un riquadro cliccabile. n: {id, x, y, w, h, icona, titolo, sotto, stato, numero}. */
  function nodo(padre, n, scegli) {
    var g = svg("g", { class: "nodo stato-" + n.stato + (n.id === scegli.attuale() ? " scelto" : ""), tabindex: "0", role: "button", "data-id": n.id }, padre);
    svg("title", {}, g).textContent = n.titolo + ": " + (NOMI_STATO[n.stato] || n.stato);
    svg("rect", { class: "nodo-corpo", x: n.x, y: n.y, width: n.w, height: n.h, rx: 10 }, g);
    svg("circle", { class: "luce-nodo", cx: n.x + n.w - 13, cy: n.y + 13, r: 5 }, g);
    var testoX = n.x + 12;
    if (n.icona) {
      icona(g, n.icona, n.x + 10, n.y + (n.h > 64 ? 12 : n.h / 2 - 11), 0.92);
      testoX = n.x + 42;
    }
    var spazio = Math.floor((n.w - (testoX - n.x) - 24) / 6.6);
    svg("text", { class: "nodo-titolo", x: testoX, y: n.y + (n.h > 64 ? 28 : n.h / 2 - 3) }, g).textContent = taglia(n.titolo, spazio);
    var sotto = svg("text", { class: "nodo-sotto", x: testoX, y: n.y + (n.h > 64 ? 46 : n.h / 2 + 14), "data-sotto": n.id }, g);
    sotto.textContent = taglia(n.sotto, Math.floor((n.w - (testoX - n.x) - 10) / 6));
    if (n.altro) {
      svg("text", { class: "nodo-sotto", x: testoX, y: n.y + 62, "data-altro": n.id }, g).textContent = taglia(n.altro, Math.floor((n.w - 50) / 6));
    }
    function scegliQuesto() { scegli.fai(n.id); }
    g.addEventListener("click", scegliQuesto);
    g.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); scegliQuesto(); } });
    return g;
  }

  // -------------------------------------------------------------------------
  // Schema 1: chi parla con chi
  // -------------------------------------------------------------------------

  var DX = { x: 960, w: 222 };
  var SX = { x: 18, w: 222 };

  function modelloRete(s) {
    var servizi = perId(s.servizi);
    var copia = s.modalita === "copia";
    var nodi = [], fili = [];
    var attivi = s.dispositivi.filter(function (d) { return d.attivo; });

    // Dispositivi (a sinistra)
    var dispositivi = s.dispositivi.slice(0, 9);
    if (!dispositivi.length) {
      nodi.push({ id: "nessuno", x: SX.x, y: 300, w: SX.w, h: 56, icona: "computer", titolo: "Nessun dispositivo", sotto: "nessuno ha usato l'app nelle ultime 12 ore", stato: "spento", tipo: "vuoto" });
    }
    dispositivi.forEach(function (d, i) {
      var y = 30 + i * 66;
      var icone = { telefono: "telefono", tablet: "tablet", claude: "claude", agente: "stampante" };
      nodi.push({
        id: "disp-" + d.id, x: SX.x, y: y, w: SX.w, h: 56, icona: icone[d.tipo] || "computer",
        titolo: d.ruolo || d.tipo, sotto: (d.utente ? d.utente + " · " : "") + (d.attivo ? "attivo" : fa(d.ultimo_il)),
        stato: d.attivo ? "ok" : "spento", tipo: "dispositivo", dati: d,
      });
      var yc = y + 28;
      fili.push({
        id: "disp-" + d.id, tipo: "richieste", attivo: d.attivo, stato: d.attivo ? "ok" : "spento",
        d: "M240 " + yc + " C268 " + yc + " 262 345 290 345",
      });
    });

    // Rete della sagra
    nodi.push({ id: "rete", x: 290, y: 298, w: 124, h: 94, icona: "rete", titolo: "Rete", sotto: "switch · Wi-Fi", altro: attivi.length + " attivi", stato: attivi.length ? "ok" : "spento", tipo: "rete" });

    // Server e container
    var web = servizi.web || {}, db = servizi.db || {}, stampa = servizi.stampa || {};
    var backup = servizi.backup || {}, drive = servizi.drive || {}, servizioCopia = servizi.copia || {};
    fili.push({ id: "rete-web", tipo: "richieste", attivo: attivi.length > 0, stato: statoFilo(web.stato, attivi.length ? "ok" : "spento"),
      d: "M414 345 C446 345 444 170 478 170", etichetta: ["HTTP :8000", 446, 250] });
    nodi.push({ id: "web", x: 478, y: 70, w: 414, h: 200, icona: "web", titolo: "web · l'app Django", sotto: web.testo, stato: web.stato || "sconosciuto", tipo: "servizio", chip: true });
    nodi.push({ id: "stampa", x: 620, y: 300, w: 272, h: 70, icona: "stampante", titolo: "stampa · servizio di stampa", sotto: stampa.testo, stato: stampa.stato || "sconosciuto", tipo: "servizio" });
    nodi.push({ id: "db", x: 478, y: 400, w: 414, h: 92, icona: "database", titolo: "db · PostgreSQL", sotto: db.testo, stato: db.stato || "sconosciuto", tipo: "servizio" });
    nodi.push({ id: "backup", x: 478, y: 532, w: 130, h: 92, icona: "backup", titolo: "backup", sotto: breve(backup), stato: backup.stato || "sconosciuto", tipo: "servizio" });
    nodi.push({ id: "drive", x: 620, y: 532, w: 130, h: 92, icona: "nuvola", titolo: "drive", sotto: breve(drive), stato: drive.stato || "sconosciuto", tipo: "servizio" });
    nodi.push({ id: "copia", x: 762, y: 532, w: 130, h: 92, icona: "copia", titolo: "copia", sotto: breve(servizioCopia), stato: servizioCopia.stato || "sconosciuto", tipo: "servizio" });

    fili.push({ id: "web-db", tipo: "dati", attivo: true, stato: statoFilo(web.stato, db.stato), d: "M540 270 L540 400", etichetta: ["SQL", 548, 340, "start"] });
    var coda = (s.componenti.coda || {}).in_coda || 0;
    fili.push({ id: "stampa-db", tipo: "dati", attivo: stampa.stato === "ok" || stampa.stato === "avviso", stato: statoFilo(stampa.stato, db.stato),
      d: "M756 370 L756 400", veloce: coda > 0, etichetta: ["coda", 764, 390, "start"] });
    fili.push({ id: "db-backup", tipo: "dati", attivo: backup.stato === "ok", stato: statoFilo(backup.stato, db.stato), d: "M543 492 L543 532", etichetta: ["pg_dump", 551, 516, "start"] });
    fili.push({ id: "backup-drive", tipo: "dati", attivo: drive.stato === "ok", stato: statoFilo(backup.stato, drive.stato), d: "M608 578 L620 578" });
    fili.push({ id: "copia-db", tipo: "dati", attivo: servizioCopia.stato === "ok", stato: statoFilo(servizioCopia.stato, db.stato), d: "M827 492 L827 532" });

    // Stampanti (a destra in alto)
    var stampanti = s.stampanti.slice(0, 5);
    if (!stampanti.length && !copia) {
      nodi.push({ id: "nessuna-stampante", x: DX.x, y: 160, w: DX.w, h: 56, icona: "stampante", titolo: "Nessuna stampante", sotto: "aggiungile da Cassa → Stampanti", stato: "spento", tipo: "vuoto" });
    }
    stampanti.forEach(function (p, i) {
      var y = 30 + i * 66;
      nodi.push({ id: "stamp-" + p.id, x: DX.x, y: y, w: DX.w, h: 56, icona: "stampante", titolo: p.nome,
        sotto: p.indirizzo ? p.indirizzo + " · " + (NOMI_STATO[p.stato] || "") : p.tipo, stato: p.stato, tipo: "stampante", dati: p });
      var yc = y + 28;
      var serve = p.connessione === "ipp" || p.connessione === "rete";
      fili.push({
        id: "stamp-" + p.id, tipo: "stampe", attivo: serve && !copia && (stampa.stato === "ok" || stampa.stato === "avviso"),
        stato: serve && !copia ? statoFilo(stampa.stato, p.stato) : "spento", veloce: p.in_coda > 0,
        d: "M892 335 C930 335 925 " + yc + " 960 " + yc,
        etichetta: i === 0 ? ["IPP · 9100", 928, 322] : null,
      });
    });

    // Internet, Google Drive e l'altro server
    var internet = peggiore(drive.stato === "ok" || servizioCopia.stato === "ok" ? "ok" : "spento");
    nodi.push({ id: "internet", x: DX.x, y: 410, w: DX.w, h: 60, icona: "internet", titolo: "Internet", sotto: "solo per Drive e copia remota", stato: internet, tipo: "internet" });
    nodi.push({ id: "gdrive", x: DX.x, y: 500, w: DX.w, h: 58, icona: "nuvola", titolo: "Google Drive", sotto: drive.testo, stato: drive.stato || "sconosciuto", tipo: "servizio-esterno", servizio: "drive" });
    nodi.push({ id: "altro", x: DX.x, y: 588, w: DX.w, h: 64, icona: copia ? "web" : "casa",
      titolo: copia ? "Server della sagra" : "Server di casa (copia)", sotto: copia ? s.copia_di : servizioCopia.testo,
      stato: servizioCopia.stato || "sconosciuto", tipo: "servizio-esterno", servizio: "copia" });
    fili.push({ id: "drive-internet", tipo: "dati", attivo: drive.stato === "ok", stato: drive.stato === "ok" ? "ok" : "spento",
      d: "M685 624 L685 646 Q685 666 705 666 L918 666 Q936 666 936 648 L936 456 Q936 440 952 440 L960 440", etichetta: ["rclone", 800, 680] });
    fili.push({ id: "copia-internet", tipo: "dati", attivo: servizioCopia.stato === "ok", stato: servizioCopia.stato === "ok" ? "ok" : "spento",
      d: "M892 578 C924 578 922 455 960 455", inverso: copia, etichetta: ["Tailscale", 902, 520, "start"] });
    fili.push({ id: "internet-gdrive", tipo: "dati", attivo: drive.stato === "ok", stato: drive.stato === "ok" ? "ok" : "spento", d: "M1010 470 L1010 500" });
    fili.push({ id: "internet-altro", tipo: "dati", attivo: servizioCopia.stato === "ok", stato: servizioCopia.stato === "ok" ? "ok" : "spento",
      d: "M1182 440 C1199 440 1199 620 1182 620", inverso: copia });

    return { nodi: nodi, fili: fili };
  }

  function disegnaRete() {
    var tela = document.querySelector("[data-schema-rete]");
    var m = modelloRete(stato);
    var firma = JSON.stringify([m.nodi.map(function (n) { return [n.id, n.stato]; }), m.fili.map(function (f) { return [f.id, f.stato, f.attivo, !!f.veloce]; })]);
    if (firma !== firme.rete) {
      firme.rete = firma;
      var strati = preparaTela(tela);
      var cornice = svg("g", { class: "nodo stato-ok", tabindex: "0", role: "button", "data-id": "server", "aria-label": "Server" }, strati.fili);
      svg("rect", { class: "cornice", x: 452, y: 14, width: 466, height: 674, rx: 16 }, cornice);
      svg("text", { class: "cornice-titolo", x: 470, y: 40 }, cornice).textContent = stato.modalita === "copia" ? "SERVER DI CASA (copia)" : "SERVER DELLA SAGRA";
      svg("text", { class: "cornice-sotto", x: 470, y: 58, "data-server": "1" }, cornice);
      cornice.addEventListener("click", function () { sceltaRete.fai("server"); });
      cornice.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sceltaRete.fai("server"); } });
      m.fili.forEach(function (f) { filo(strati, f, "fr"); });
      m.nodi.forEach(function (n) {
        var g = nodo(strati.nodi, n, sceltaRete);
        if (n.chip) chipWeb(g);
      });
    }
    aggiornaTestiRete(tela, m);
    mostraDettaglioRete();
  }

  function chipWeb(g) {
    var c = stato.componenti || {};
    var voci = [
      ["magazzino", "Magazzino"], ["bar", "Bar"], ["cassa", "Cassa"],
      ["assistente", "Claude (MCP)"], ["api", "API stampa"], ["sistema", "Sistema"],
    ];
    voci.forEach(function (v, i) {
      var x = 492 + (i % 3) * 134, y = 128 + Math.floor(i / 3) * 64;
      svg("rect", { class: "chip-corpo", x: x, y: y, width: 124, height: 52, rx: 8 }, g);
      svg("text", { class: "chip-testo", x: x + 10, y: y + 21 }, g).textContent = v[1];
      svg("text", { class: "chip-numero", x: x + 10, y: y + 40, "data-chip": v[0] }, g).textContent = testoChip(v[0], c);
    });
  }

  function testoChip(nome, c) {
    if (nome === "magazzino") return (c.magazzino || {}).operazioni_oggi + " operazioni oggi";
    if (nome === "bar") return (c.bar || {}).fusti_attaccati + " fusti attaccati";
    if (nome === "cassa") return (c.cassa || {}).ordini_oggi + " ordini oggi";
    if (nome === "assistente") return "usato " + fa((c.assistente || {}).ultimo_uso);
    if (nome === "api") return (c.coda || {}).in_coda + " stampe in coda";
    if (nome === "sistema") return (c.sistema || {}).errori_da_vedere + " errori da vedere";
    return "";
  }

  function aggiornaTestiRete(tela, m) {
    var server = stato.server || {};
    var parti = [];
    if (server.acceso_da) parti.push("acceso da " + durata(server.acceso_da));
    if (server.carico) parti.push("carico " + server.carico[0].toFixed(2));
    if (server.temperatura) parti.push(server.temperatura.toFixed(0) + " °C");
    if (server.disco_backup) parti.push("disco " + byte(server.disco_backup.libero) + " liberi");
    var riga = tela.querySelector("[data-server]");
    if (riga) riga.textContent = parti.join(" · ") || stato.indirizzo;
    m.nodi.forEach(function (n) {
      var t = tela.querySelector('[data-sotto="' + n.id + '"]');
      if (t) t.textContent = taglia(n.sotto, Math.floor((n.w - (n.icona ? 52 : 22)) / 6));
    });
    Array.prototype.forEach.call(tela.querySelectorAll("[data-chip]"), function (t) { t.textContent = testoChip(t.dataset.chip, stato.componenti || {}); });
  }

  var sceltaRete = {
    attuale: function () { return scelto; },
    fai: function (id) {
      scelto = id;
      Array.prototype.forEach.call(document.querySelectorAll("[data-schema-rete] .nodo"), function (g) {
        g.classList.toggle("scelto", g.dataset.id === id);
      });
      mostraDettaglioRete();
    },
  };

  /* Il riquadro a destra dello schema: cosa è, come sta, cosa fare. */
  function dettaglio(contenitore, d) {
    contenitore.textContent = "";
    var testa = el("div", "sezione-titolo", null, contenitore);
    el("h3", null, d.titolo, testa);
    if (d.stato) el("span", "pillola-stato stato-" + d.stato, NOMI_STATO[d.stato] || d.stato, testa);
    if (d.testo) el("p", null, d.testo, contenitore);
    if (d.cosa) el("p", "tenue piccolo", d.cosa, contenitore);
    var righe = (d.righe || []).filter(function (r) { return r[1] !== null && r[1] !== undefined && r[1] !== ""; });
    if (righe.length) {
      var dl = el("dl", null, null, contenitore);
      righe.forEach(function (r) { el("dt", null, r[0], dl); el("dd", null, r[1], dl); });
    }
    if (d.aiuto && d.aiuto.length) {
      el("strong", null, "Se non va", contenitore);
      var ul = el("ul", null, null, contenitore);
      d.aiuto.forEach(function (a) {
        var li = el("li", null, null, ul);
        // Le parti che sono comandi si scrivono come codice.
        a.split(/(docker compose [^,;.]+|scripts\/[\w.-]+(?: [^,;.]+)?|python3? [^,;.]+)/).forEach(function (pezzo, i) {
          if (!pezzo) return;
          if (i % 2) el("code", null, pezzo, li); else li.appendChild(document.createTextNode(pezzo));
        });
      });
    }
  }

  /* Per i riquadri piccoli: «a posto · 21:30». */
  function breve(s) {
    if (!s) return "";
    return (NOMI_STATO[s.stato] || s.stato) + (s.ultimo_il ? " · " + quando(s.ultimo_il).replace("oggi ", "") : "");
  }

  function righeServizio(s) {
    return [["Acceso da", accesoDa(s.avviato_il)], ["Ultimo segnale", s.ultimo_il ? fa(s.ultimo_il) : null]].concat(s.dettagli || []);
  }

  function mostraDettaglioRete() {
    var contenitore = document.querySelector("[data-dettaglio]");
    var servizi = perId(stato.servizi);
    var id = scelto;
    var server = stato.server || {};
    if (id === "server") {
      dettaglio(contenitore, {
        titolo: stato.modalita === "copia" ? "Server di casa" : "Server della sagra", stato: peggiore.apply(null, stato.servizi.map(function (s) { return s.stato === "sconosciuto" ? "ok" : s.stato; })),
        testo: "Il computer su cui girano i container del programma (Docker Compose).",
        righe: [
          ["Indirizzo dell'app", stato.indirizzo], ["Acceso da", server.acceso_da ? durata(server.acceso_da) : null],
          ["Carico (1, 5, 15 min)", server.carico ? server.carico.join(" · ") : null], ["Processori", server.processori],
          ["Memoria libera", server.memoria ? byte(server.memoria.libera) + " su " + byte(server.memoria.totale) : null],
          ["Temperatura", server.temperatura ? server.temperatura.toFixed(1) + " °C" : null],
          ["Disco dei backup", server.disco_backup ? byte(server.disco_backup.libero) + " liberi su " + byte(server.disco_backup.totale) : null],
          ["Disco dei dati", server.disco_dati ? byte(server.disco_dati.libero) + " liberi su " + byte(server.disco_dati.totale) : null],
          ["Architettura", server.architettura],
        ],
        aiuto: ["Stato dei container: docker compose ps (tutti «Up», web e stampa «healthy»).",
          "Tutto si riaccende da solo all'avvio del computer (restart: unless-stopped).",
          "Disco quasi pieno: docker system prune toglie le immagini vecchie; i backup vecchi si cancellano da soli."],
      });
      return;
    }
    if (servizi[id]) {
      var s = servizi[id];
      dettaglio(contenitore, { titolo: s.nome, stato: s.stato, testo: s.testo, cosa: s.cosa, righe: righeServizio(s), aiuto: s.aiuto });
      return;
    }
    var m = modelloRete(stato);
    var n = m.nodi.filter(function (x) { return x.id === id; })[0];
    if (!n) { contenitore.innerHTML = '<p class="tenue">Tocca un riquadro dello schema.</p>'; return; }
    if (n.servizio && servizi[n.servizio]) {
      var e = servizi[n.servizio];
      dettaglio(contenitore, { titolo: n.titolo, stato: e.stato, testo: e.testo, cosa: e.cosa, righe: righeServizio(e), aiuto: e.aiuto });
    } else if (n.tipo === "dispositivo") {
      var d = n.dati;
      dettaglio(contenitore, {
        titolo: d.ruolo || d.tipo, stato: n.stato, testo: d.attivo ? "Attivo: sta usando l'app." : "Non si fa vivo da " + fa(d.ultimo_il).replace(" fa", "") + ".",
        righe: [["Utente", d.utente], ["Tipo", d.tipo], ["Indirizzo IP", d.ip], ["Browser", d.browser], ["Ultima pagina", d.pagina], ["Ultima volta", quando(d.ultimo_il)]],
        aiuto: d.tipo === "agente"
          ? ["Il programma di stampa deve restare acceso su quel computer (python3 agente_stampa.py).", "Serve solo per stampanti collegate a un computer o con l'app in cloud."]
          : ["Se non si collega: deve essere sul Wi-Fi della sagra e aprire http://" + stato.indirizzo + "/.",
            "Se una cassa resta bianca o lenta: ricarica la pagina (l'ordine in corso non si perde)."],
      });
    } else if (n.tipo === "stampante") {
      var p = n.dati;
      dettaglio(contenitore, {
        titolo: p.nome, stato: p.stato, testo: p.testo, cosa: p.tipo,
        righe: [["Indirizzo", p.indirizzo], ["Modello", p.modello], ["Controllata", fa(p.controllata_il)], ["Ultima stampa", quando(p.ultima_stampa)],
          ["In coda", p.in_coda], ["In errore (6 ore)", p.errori]],
        aiuto: ["Accesa? Carta e toner? Cavo di rete nello switch?", "Cassa → Stampanti → Controlla dice cosa risponde; poi Stampa di prova.",
          "Indirizzo IP fisso: se cambia, la stampante non si trova più.", "Le comande non perse: restano in coda e si ritentano da sole per 10 minuti."],
      });
    } else if (n.tipo === "rete") {
      dettaglio(contenitore, {
        titolo: "Rete della sagra", stato: n.stato, testo: "Lo switch con il server, le stampanti e il punto di accesso Wi-Fi dei telefoni e dei tablet.",
        righe: [["Indirizzo dell'app", "http://" + stato.indirizzo + "/"], ["Dispositivi attivi", stato.dispositivi.filter(function (x) { return x.attivo; }).length]],
        aiuto: ["Cavi nello switch e switch acceso.", "Senza router nessuno dà gli indirizzi: server, stampanti e Wi-Fi vanno impostati a mano.",
          "Indirizzo del server fisso: i tablet lo hanno tra i preferiti."],
      });
    } else if (n.tipo === "internet") {
      dettaglio(contenitore, {
        titolo: "Internet", stato: n.stato, testo: "Alla sagra non serve: cassa, stampa, magazzino e bar funzionano senza. Serve solo per Google Drive e per la copia remota.",
        aiuto: ["Senza internet i backup su Drive e la copia remota aspettano e recuperano da soli."],
      });
    } else {
      dettaglio(contenitore, { titolo: n.titolo, stato: n.stato, testo: n.sotto });
    }
  }

  // -------------------------------------------------------------------------
  // Schema 2: dentro il programma
  // -------------------------------------------------------------------------

  var PEZZI = [
    { id: "p-cassa-browser", x: 20, y: 30, w: 190, h: 64, icona: "tablet", titolo: "Cassa (browser)", file: "cassa/static/cassa/pos.js",
      cosa: "La cassa nel browser: listino, ordine in corso (salvato nel browser), conferma. Ogni pochi secondi chiede lo stato (listino cambiato, stampe in errore)." },
    { id: "p-pagine", x: 20, y: 130, w: 190, h: 64, icona: "computer", titolo: "Pagine", file: "*/templates, magazzino/static/app.js",
      cosa: "Le pagine di magazzino, bar, cassa e amministrazione (HTML fatto dal server, poco JavaScript)." },
    { id: "p-claude", x: 20, y: 230, w: 190, h: 64, icona: "claude", titolo: "Claude (MCP)", file: "assistente/views.py · /mcp/",
      cosa: "Claude parla con l'app in MCP (JSON-RPC su HTTP) con una chiave personale: legge e, se la chiave lo permette, registra." },
    { id: "p-agente", x: 20, y: 330, w: 190, h: 64, icona: "stampante", titolo: "Programma di stampa", file: "cassa/agente/agente_stampa.py",
      cosa: "Facoltativo: su un computer con stampanti collegate o con l'app in cloud chiede le stampe al server (API con chiave)." },
    { id: "p-pannello", x: 20, y: 430, w: 190, h: 64, icona: "ingranaggio", titolo: "Pannello sistema", file: "sistema/static/sistema/pannello.js",
      cosa: "Questa pagina: legge /sistema/stato.json ogni 5 secondi." },
    { id: "p-django", x: 250, y: 30, w: 150, h: 464, icona: "codice", titolo: "Django", file: "config/urls.py, config/settings.py",
      cosa: "Riceve ogni richiesta: login obbligatorio, middleware (sistema: dispositivi, errori, sola lettura sulla copia), poi la vista giusta." },
    { id: "p-cassa", x: 440, y: 30, w: 210, h: 64, icona: "codice", titolo: "cassa.services", file: "cassa/services.py, cassa/views/",
      cosa: "Ordini (crea, annulla, ristampa), chiusura cassa, listino. Nella stessa transazione dell'ordine mette in coda le stampe." },
    { id: "p-magazzino", x: 440, y: 120, w: 210, h: 64, icona: "codice", titolo: "magazzino.services", file: "magazzino/services.py",
      cosa: "Carichi, spostamenti, prelievi per la cucina, rientri, rettifiche, serate. Le giacenze sono la somma dei movimenti." },
    { id: "p-bar", x: 440, y: 210, w: 210, h: 64, icona: "codice", titolo: "bar.services", file: "bar/services.py",
      cosa: "Bevande, carichi, fusti e spine, pesate. Le bevande vendute in cassa escono da sole dal magazzino del bar (calcolate dalle righe degli ordini)." },
    { id: "p-strumenti", x: 440, y: 300, w: 210, h: 64, icona: "codice", titolo: "assistente.strumenti", file: "assistente/strumenti*.py",
      cosa: "Gli strumenti MCP: chiamano gli stessi servizi delle pagine, quindi gli stessi controlli." },
    { id: "p-api", x: 440, y: 390, w: 210, h: 50, icona: "codice", titolo: "API stampa", file: "cassa/views/stampe.py",
      cosa: "/cassa/api/agente/: il programma di stampa prende i lavori e dice com'è andata." },
    { id: "p-monitor", x: 440, y: 450, w: 210, h: 50, icona: "codice", titolo: "sistema.monitor", file: "sistema/monitor.py, sistema/views.py",
      cosa: "Raccoglie lo stato di tutto per questo pannello; API della copia remota." },
    { id: "p-coda", x: 700, y: 30, w: 190, h: 64, icona: "coda", titolo: "Coda delle stampe", file: "cassa/stampa.py · tabella Stampa",
      cosa: "Ogni comanda o ricevuta è una riga con il contenuto già pronto: in coda, in stampa, stampata o in errore. Si ritenta da sola." },
    { id: "p-db", x: 700, y: 150, w: 190, h: 250, icona: "database", titolo: "PostgreSQL", file: "container db",
      cosa: "Tutti i dati. Le tabelle sistema_* e le sessioni sono solo di questo server: non vanno nelle copie." },
    { id: "p-copia", x: 700, y: 430, w: 190, h: 64, icona: "copia", titolo: "Copie dei dati", file: "sistema/copia.py, carica.py, copia_remota.py",
      cosa: "Copia per la chiavetta, copia remota per il server di casa, caricamento con controllo della versione." },
    { id: "p-servizio", x: 950, y: 30, w: 230, h: 64, icona: "stampante", titolo: "servizio_stampa", file: "cassa/servizio_stampa.py",
      cosa: "Il container «stampa»: un thread per stampante, stampa in ordine, controlla le stampanti ogni minuto." },
    { id: "p-fogli", x: 950, y: 130, w: 230, h: 64, icona: "codice", titolo: "fogli.py → ipp.py", file: "cassa/fogli.py, cassa/ipp.py",
      cosa: "Disegna comande e ricevute su A4/A5 e le manda in IPP (PWG raster, URF, PDF o JPEG). Le termiche ricevono ESC/POS." },
    { id: "p-stampanti", x: 950, y: 230, w: 230, h: 64, icona: "stampante", titolo: "Stampanti", file: "Cassa → Stampanti",
      cosa: "Laser e getto d'inchiostro (IPP, porta 631), termiche (porta 9100)." },
    { id: "p-backup", x: 950, y: 330, w: 230, h: 64, icona: "backup", titolo: "backup · drive", file: "docker-compose.yml",
      cosa: "Container shell: pg_dump ogni 30 minuti nella cartella backup/, rclone verso Google Drive." },
    { id: "p-casa", x: 950, y: 430, w: 230, h: 64, icona: "casa", titolo: "Server di casa", file: "COPIA_DI, COPIA_CHIAVE",
      cosa: "La copia di sola lettura (es. Raspberry su Tailscale): prende la copia dalla sagra ogni pochi minuti." },
  ];

  var COLLEGAMENTI = [
    ["p-cassa-browser", "p-django", "richieste"], ["p-pagine", "p-django", "richieste"], ["p-claude", "p-django", "richieste"],
    ["p-agente", "p-django", "richieste"], ["p-pannello", "p-django", "richieste"],
    ["p-django", "p-cassa", "richieste"], ["p-django", "p-magazzino", "richieste"], ["p-django", "p-bar", "richieste"],
    ["p-django", "p-strumenti", "richieste"], ["p-django", "p-api", "richieste"], ["p-django", "p-monitor", "richieste"],
    ["p-cassa", "p-coda", "stampe"], ["p-api", "p-coda", "stampe"], ["p-coda", "p-db", "dati"],
    ["p-cassa", "p-db", "dati"], ["p-magazzino", "p-db", "dati"], ["p-bar", "p-db", "dati"], ["p-strumenti", "p-db", "dati"],
    ["p-monitor", "p-db", "dati"], ["p-db", "p-copia", "dati"],
    ["p-coda", "p-servizio", "stampe"], ["p-servizio", "p-fogli", "stampe"], ["p-fogli", "p-stampanti", "stampe"],
    ["p-db", "p-backup", "dati"], ["p-copia", "p-casa", "dati"],
  ];

  function centro(p, lato) {
    if (lato === "destra") return [p.x + p.w, p.y + Math.min(p.h / 2, 32)];
    if (lato === "sinistra") return [p.x, p.y + Math.min(p.h / 2, 32)];
    if (lato === "sotto") return [p.x + p.w / 2, p.y + p.h];
    return [p.x + p.w / 2, p.y];
  }

  function percorso(a, b) {
    var da, a2;
    if (b.y >= a.y + a.h && Math.abs(a.x - b.x) < 30) { // uno sotto l'altro
      da = centro(a, "sotto"); a2 = centro(b, "sopra");
      return "M" + da[0] + " " + da[1] + " L" + a2[0] + " " + a2[1];
    }
    da = centro(a, "destra"); a2 = centro(b, "sinistra");
    if (b.h > 200) a2 = [b.x, Math.max(b.y + 20, Math.min(b.y + b.h - 20, da[1]))];
    if (a.h > 200) da = [a.x + a.w, Math.max(a.y + 20, Math.min(a.y + a.h - 20, a2[1]))];
    var meta = (da[0] + a2[0]) / 2;
    return "M" + da[0] + " " + da[1] + " C" + meta + " " + da[1] + " " + meta + " " + a2[1] + " " + a2[0] + " " + a2[1];
  }

  function statiPezzi() {
    var servizi = perId(stato.servizi);
    var c = stato.componenti || {};
    var dispositivi = stato.dispositivi || [];
    function attivo(ruoli) { return dispositivi.some(function (d) { return d.attivo && ruoli.indexOf(d.ruolo) >= 0; }); }
    var stampanti = stato.stampanti || [];
    var statoStampanti = stampanti.length ? peggiore.apply(null, stampanti.map(function (p) { return p.stato === "sconosciuto" ? "ok" : p.stato; })) : "spento";
    var copia = servizi.copia || {};
    return {
      "p-cassa-browser": attivo(["Cassa ingresso", "Cassa bar", "Cassa"]) ? "ok" : "spento",
      "p-pagine": attivo(["Magazzino", "Bar", "Amministrazione", "Monitor cucina"]) ? "ok" : "spento",
      "p-claude": attivo(["Claude (MCP)"]) ? "ok" : "spento",
      "p-agente": dispositivi.some(function (d) { return d.tipo === "agente" && d.attivo; }) ? "ok" : "spento",
      "p-pannello": "ok",
      "p-django": (servizi.web || {}).stato || "ok",
      "p-cassa": "ok", "p-magazzino": "ok", "p-bar": "ok", "p-strumenti": "ok", "p-api": "ok",
      "p-monitor": (c.sistema || {}).errori_da_vedere ? "avviso" : "ok",
      "p-coda": (c.coda || {}).errori ? "avviso" : "ok",
      "p-db": (servizi.db || {}).stato || "sconosciuto",
      "p-copia": copia.stato === "spento" ? "ok" : (copia.stato || "ok"),
      "p-servizio": (servizi.stampa || {}).stato || "sconosciuto",
      "p-fogli": (servizi.stampa || {}).stato === "spento" ? "spento" : "ok",
      "p-stampanti": stato.modalita === "copia" ? "spento" : statoStampanti,
      "p-backup": peggiore((servizi.backup || {}).stato === "sconosciuto" ? "ok" : (servizi.backup || {}).stato, (servizi.drive || {}).stato === "avviso" ? "avviso" : "ok"),
      "p-casa": copia.stato || "spento",
    };
  }

  function numeriPezzo(id) {
    var c = stato.componenti || {};
    var mappa = {
      "p-cassa": (c.cassa || {}).ordini_oggi + " ordini oggi",
      "p-magazzino": (c.magazzino || {}).operazioni_oggi + " operazioni oggi · " + (c.magazzino || {}).prodotti + " prodotti",
      "p-bar": (c.bar || {}).operazioni_oggi + " operazioni oggi · " + (c.bar || {}).fusti_attaccati + " fusti attaccati",
      "p-strumenti": (c.assistente || {}).chiavi + " chiavi · usato " + fa((c.assistente || {}).ultimo_uso),
      "p-coda": (c.coda || {}).in_coda + " in coda · " + (c.coda || {}).errori + " in errore",
      "p-monitor": (c.sistema || {}).errori_da_vedere + " errori da vedere",
      "p-db": ((perId(stato.servizi).db || {}).testo || ""),
      "p-servizio": (perId(stato.servizi).stampa || {}).testo || "",
      "p-stampanti": (stato.stampanti || []).length + " stampanti",
      "p-casa": (perId(stato.servizi).copia || {}).testo || "",
    };
    return mappa[id] || "";
  }

  var sceltaProgramma = {
    attuale: function () { return sceltoProgramma; },
    fai: function (id) {
      sceltoProgramma = id;
      Array.prototype.forEach.call(document.querySelectorAll("[data-schema-programma] .nodo"), function (g) {
        g.classList.toggle("scelto", g.dataset.id === id);
      });
      mostraDettaglioProgramma();
    },
  };

  function disegnaProgramma() {
    var tela = document.querySelector("[data-schema-programma]");
    var stati = statiPezzi();
    var perPezzo = perId(PEZZI);
    var fili = COLLEGAMENTI.map(function (c, i) {
      var a = perPezzo[c[0]], b = perPezzo[c[1]];
      var statoFiloPezzi = statoFilo(stati[c[0]], stati[c[1]]);
      return { id: "c" + i, tipo: c[2], d: percorso(a, b), stato: statoFiloPezzi, attivo: statoFiloPezzi !== "spento",
        veloce: c[2] === "stampe" && ((stato.componenti || {}).coda || {}).in_coda > 0, quanti: 2 };
    });
    var firma = JSON.stringify([stati, fili.map(function (f) { return [f.stato, f.veloce]; })]);
    if (firma !== firme.programma) {
      firme.programma = firma;
      var strati = preparaTela(tela);
      fili.forEach(function (f) { filo(strati, f, "fp"); });
      PEZZI.forEach(function (p) {
        nodo(strati.nodi, { id: p.id, x: p.x, y: p.y, w: p.w, h: p.h, icona: p.icona, titolo: p.titolo, sotto: numeriPezzo(p.id) || p.file, stato: stati[p.id] }, sceltaProgramma);
      });
    }
    PEZZI.forEach(function (p) {
      var t = tela.querySelector('[data-sotto="' + p.id + '"]');
      if (t) t.textContent = taglia(numeriPezzo(p.id) || p.file, Math.floor((p.w - 52) / 6));
    });
    mostraDettaglioProgramma();
  }

  function mostraDettaglioProgramma() {
    var contenitore = document.querySelector("[data-dettaglio-programma]");
    var p = perId(PEZZI)[sceltoProgramma];
    if (!p) return;
    var stati = statiPezzi();
    dettaglio(contenitore, { titolo: p.titolo, stato: stati[p.id], testo: p.cosa, righe: [["Dove", p.file], ["Adesso", numeriPezzo(p.id)]] });
  }

  // -------------------------------------------------------------------------
  // Tabelle e schede
  // -------------------------------------------------------------------------

  function pillola(s, testo) { var e = el("span", "pillola-stato stato-" + s, testo || NOMI_STATO[s] || s); return e; }

  function tabella(contenitore, intestazioni, righe, vuoto) {
    contenitore.textContent = "";
    if (!righe.length) { el("p", "tenue", vuoto, contenitore); return; }
    var t = el("table", "tabella-pannello", null, contenitore);
    var tr = el("tr", null, null, el("thead", null, null, t));
    intestazioni.forEach(function (h) { el("th", null, h, tr); });
    var corpo = el("tbody", null, null, t);
    righe.forEach(function (r) {
      var riga = el("tr", null, null, corpo);
      r.forEach(function (c) {
        var td = el("td", null, null, riga);
        if (c instanceof Node) td.appendChild(c); else td.textContent = c === null || c === undefined ? "" : c;
      });
    });
  }

  function disegnaServizi() {
    var server = stato.server || {};
    var computer = document.querySelector("[data-computer]");
    computer.textContent = "";
    [["Computer acceso da", server.acceso_da ? durata(server.acceso_da) : null], ["Carico", server.carico ? server.carico.join(" · ") : null],
      ["Memoria libera", server.memoria ? byte(server.memoria.libera) : null], ["Temperatura", server.temperatura ? server.temperatura.toFixed(1) + " °C" : null],
      ["Disco backup libero", server.disco_backup ? byte(server.disco_backup.libero) : null], ["Indirizzo", stato.indirizzo]]
      .forEach(function (v) { if (v[1] !== null && v[1] !== undefined) { var s = el("span", "tenue", v[0] + ":", computer); el("strong", null, v[1], s); } });

    var contenitore = document.querySelector("[data-servizi]");
    contenitore.textContent = "";
    stato.servizi.forEach(function (s) {
      var scheda = el("article", "scheda-servizio stato-" + s.stato, null, contenitore);
      var h = el("h3", null, s.nome, scheda);
      h.appendChild(pillola(s.stato));
      el("p", null, s.testo, scheda);
      el("p", "tenue piccolo", s.cosa, scheda);
      var dl = el("dl", null, null, scheda);
      righeServizio(s).forEach(function (r) { if (r[1]) { el("dt", null, r[0], dl); el("dd", null, r[1], dl); } });
      if (s.aiuto && s.aiuto.length) {
        var det = el("details", null, null, scheda);
        el("summary", null, "Se non va", det);
        var ul = el("ul", null, null, det);
        s.aiuto.forEach(function (a) { el("li", null, a, ul); });
      }
    });
  }

  function disegnaStampanti() {
    tabella(document.querySelector("[data-stampanti]"), ["Stampante", "Stato", "Tipo", "Indirizzo", "Ultima stampa", "In coda", "Errori 6 h", "Controllata"],
      stato.stampanti.map(function (p) {
        var nome = el("span", null, p.nome);
        if (p.modello) { nome.appendChild(el("br")); nome.appendChild(el("span", "tenue piccolo", p.modello)); }
        var s = el("span");
        s.appendChild(pillola(p.stato));
        s.appendChild(el("div", "tenue piccolo", p.testo));
        return [nome, s, p.tipo, p.indirizzo, quando(p.ultima_stampa), p.in_coda, p.errori, fa(p.controllata_il)];
      }), "Nessuna stampante: si aggiungono da Cassa → Stampanti.");
    tabella(document.querySelector("[data-stampe-errore]"), ["Quando", "Cosa", "Stampante", "Ordine", "Errore"],
      stato.stampe_in_errore.map(function (s) { return [quando(s.quando), s.cosa, s.stampante, s.ordine ? "n. " + s.ordine : "", s.errore]; }),
      "Nessuna stampa in errore.");
  }

  function disegnaDispositivi() {
    tabella(document.querySelector("[data-dispositivi]"), ["Cosa fa", "Stato", "Utente", "Tipo", "Indirizzo IP", "Browser", "Ultima pagina", "Ultima volta"],
      stato.dispositivi.map(function (d) {
        return [d.ruolo, pillola(d.attivo ? "ok" : "spento", d.attivo ? "attivo" : "inattivo"), d.utente, d.tipo, d.ip, d.browser, d.pagina, fa(d.ultimo_il)];
      }), "Nessun dispositivo nelle ultime 12 ore.");
  }

  function disegnaAttivita() {
    var dl = document.querySelector("[data-attivita]");
    dl.textContent = "";
    stato.attivita.forEach(function (a) {
      var d = el("div", null, null, dl);
      el("dt", null, a.cosa, d);
      var dd = el("dd", null, quando(a.quando), d);
      if (a.quando) dd.title = fa(a.quando);
    });
  }

  function disegnaErrori() {
    var contenitore = document.querySelector("[data-errori]");
    var daVedere = stato.errori.filter(function (e) { return !e.visto; }).length;
    var conta = document.querySelector("[data-conta-errori]");
    conta.textContent = daVedere;
    conta.hidden = !daVedere;
    // Le tracce aperte restano aperte dopo l'aggiornamento.
    var aperti = {};
    Array.prototype.forEach.call(contenitore.querySelectorAll("details[open]"), function (d) { aperti[d.dataset.id] = true; });
    contenitore.textContent = "";
    if (!stato.errori.length) { el("p", "tenue", "Nessun errore registrato. 👍", contenitore); return; }
    stato.errori.forEach(function (e) {
      var voce = el("div", "errore-voce" + (e.visto ? " visto" : ""), null, contenitore);
      var testa = el("div", "errore-testa", null, voce);
      el("span", "errore-tipo", e.tipo, testa);
      el("span", "distintivo", e.origine, testa);
      if (e.volte > 1) el("span", "distintivo", e.volte + " volte", testa);
      el("span", "tenue piccolo", "ultima " + quando(e.ultimo_il) + (e.volte > 1 ? " · prima " + quando(e.primo_il) : ""), testa);
      el("div", null, e.messaggio, voce);
      el("div", "tenue piccolo", e.dove + (e.utente ? " · " + e.utente : ""), voce);
      var det = el("details", null, null, voce);
      det.dataset.id = e.id;
      if (aperti[e.id]) det.open = true;
      el("summary", "piccolo", "Traccia completa", det);
      el("pre", null, e.traccia, det);
    });
  }

  function disegnaVersione() {
    var v = stato.versione;
    var contenitore = document.querySelector("[data-versione]");
    contenitore.textContent = "";
    el("h3", null, "Programma", contenitore);
    var dl = el("dl", "attivita", null, contenitore);
    [["Impronta del codice", v.codice], ["Modalità", v.modalita === "copia" ? "copia di sola lettura" : "sagra"], ["Python", v.python], ["Django", v.django],
      ["DEBUG", v.debug ? "acceso (solo per prove!)" : "spento"]].forEach(function (r) {
      var d = el("div", null, null, dl); el("dt", null, r[0], d); el("dd", null, r[1], d);
    });
    if (v.da_applicare.length) {
      var avviso = el("div", "box-errori", null, contenitore);
      el("p", null, "Migrazioni non ancora applicate: " + v.da_applicare.join(", ") + ". Riavvia l'app (le applica all'avvio) o usa scripts/aggiorna.sh.", avviso);
    }
    el("h3", null, "Ultima migrazione per app", contenitore);
    tabella(el("div", "tabella-scorre", null, contenitore), ["App", "Migrazione"], v.migrazioni, "");
    var b = document.querySelector("[data-backup]");
    b.textContent = "";
    el("h3", null, "Ultimi backup", b);
    tabella(el("div", "tabella-scorre", null, b), ["File", "Quando", "Dimensione"],
      stato.backup.map(function (f) { return [f.nome, quando(f.quando), byte(f.dimensione)]; }),
      "Nessun backup visibile da qui (la cartella backup/ non è collegata a questo container).");
  }

  function disegnaSintesi() {
    var box = document.querySelector("[data-sintesi]");
    var testo = box.querySelector(".sintesi-testo");
    box.querySelector("[data-ora]").textContent = "aggiornato " + fa(ultimoArrivo.toISOString()) + (inPausa ? " · in pausa" : "");
    if (ultimoErrore) {
      box.dataset.stato = "errore";
      testo.textContent = ultimoErrore;
      return;
    }
    var problemi = [];
    stato.servizi.forEach(function (s) { if (s.stato === "errore" || s.stato === "avviso") problemi.push([s.stato, s.nome]); });
    stato.stampanti.forEach(function (p) { if (p.stato === "errore" || p.stato === "avviso") problemi.push([p.stato, p.nome]); });
    var errori = stato.errori.filter(function (e) { return !e.visto; }).length;
    if (errori) problemi.push(["avviso", errori + (errori === 1 ? " errore da vedere" : " errori da vedere")]);
    var peggio = peggiore.apply(null, problemi.map(function (p) { return p[0]; }).concat(["ok"]));
    box.dataset.stato = peggio;
    testo.textContent = problemi.length
      ? (peggio === "errore" ? "Guasto: " : "Da guardare: ") + problemi.map(function (p) { return p[1]; }).join(", ")
      : "Tutto a posto";
  }

  function disegnaTutto() {
    try {
      disegnaSintesi();
      disegnaRete();
      disegnaProgramma();
      disegnaServizi();
      disegnaStampanti();
      disegnaDispositivi();
      disegnaAttivita();
      disegnaErrori();
      disegnaVersione();
    } catch (e) {
      ultimoErrore = "Errore nel disegnare il pannello: " + e.message;
      disegnaSintesi();
      if (window.console) console.error(e);
    }
  }

  // -------------------------------------------------------------------------
  // Aggiornamento
  // -------------------------------------------------------------------------

  function aggiorna() {
    if (inPausa || document.hidden) return;
    fetch(indirizzo, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) {
        if (!r.ok) throw new Error("il server ha risposto " + r.status);
        var tipo = r.headers.get("Content-Type") || "";
        if (tipo.indexOf("json") < 0) throw new Error("sessione scaduta: ricarica la pagina ed entra di nuovo");
        return r.json();
      })
      .then(function (dati) {
        stato = dati;
        ultimoArrivo = new Date();
        ultimoErrore = null;
        disegnaTutto();
      })
      .catch(function (e) { ultimoErrore = "Server non raggiungibile: " + e.message; disegnaSintesi(); });
  }

  document.querySelector("[data-pausa]").addEventListener("click", function () {
    inPausa = !inPausa;
    this.setAttribute("aria-pressed", inPausa ? "true" : "false");
    this.textContent = inPausa ? "Riprendi" : "Pausa";
    if (!inPausa) aggiorna();
    disegnaSintesi();
  });
  document.addEventListener("visibilitychange", function () { if (!document.hidden) aggiorna(); });

  disegnaTutto();
  setInterval(aggiorna, OGNI);
  setInterval(disegnaSintesi, 1000);  // «aggiornato 3 s fa»
})();
