"""Biglietti da stampare: comande, ricevute, annullamenti.

Il contenuto di ogni stampa è salvato già pronto come lista di righe (JSON):
la stessa stampa si può mandare a una stampante termica (ESC/POS) oppure
mostrare nel browser, e una ristampa esce identica.

Ogni riga è un dizionario con lo stile "s" e i suoi dati:

    {"s": "titolo", "t": "CUCINA"}                    grande e centrato
    {"s": "evidenza", "t": "ASPORTO"}                 grande, bianco su nero, su tutta la riga
    {"s": "grande", "t": "N. 57  TAV. 12"}            grande, a sinistra
    {"s": "testo", "t": "…", "b": true}               normale (b = grassetto)
    {"s": "centro", "t": "…"}                         centrato
    {"s": "voce", "q": 2, "t": "Grigliata", "p": "25,00", "alta": true}
    {"s": "nota", "t": "senza cipolla"}               sotto una voce
    {"s": "coppia", "t": "Totale", "d": "34,50 €", "b": true}
    {"s": "linea"} / {"s": "vuota"}

Chi manda le stampe alle stampanti:
- le stampanti di rete (normali o termiche) le serve il server stesso, con il
  servizio di stampa che parte insieme all'app (servizio_stampa.py);
- quelle collegate con il cavo a un computer, o tutte se l'app è in cloud, il
  programma di stampa (agente/agente_stampa.py), che chiede le stampe al server.
"""

import base64
import unicodedata
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import formats, timezone

from .formati import euro
from .models import (
    CONNESSIONI_AGENTE,
    AgenteStampa,
    Connessione,
    Documento,
    Postazione,
    Reparto,
    Stampa,
    Stampante,
    StatoStampa,
)

# Una stampa presa da un agente che non risponde torna disponibile dopo un minuto.
TIMEOUT_IN_STAMPA = timedelta(seconds=60)
# Dopo un errore (stampante spenta, carta finita) si riprova da soli per 10 minuti.
ATTESA_RIPROVA = timedelta(seconds=15)
DURATA_RIPROVE = timedelta(minutes=10)

# ---------------------------------------------------------------------------
# Contenuto
# ---------------------------------------------------------------------------


def _intestazione(imp):
    return (imp.intestazione or settings.APP_NOME).strip()


def _quando(momento):
    return formats.date_format(timezone.localtime(momento), "D j/m H:i")


def _numero_tavolo(ordine):
    testo = f"N. {ordine.numero}"
    if ordine.tavolo:
        testo += f"  TAV. {ordine.tavolo}"
    return testo


def _cassiere(ordine):
    """«Cassiere: matteo»: il nome dell'account con cui è stato fatto l'ordine (se c'è)."""
    if ordine.utente_id is None:
        return []
    return [{"s": "testo", "t": f"Cassiere: {ordine.utente.get_username()}"}]


def _voci(righe, *, prezzi=False, alta=False):
    contenuto = []
    for r in righe:
        voce = {"s": "voce", "q": r.quantita, "t": r.descrizione}
        if prezzi:
            voce["p"] = euro(r.prezzo * r.quantita, simbolo=False)
        if alta:
            voce["alta"] = True
        contenuto.append(voce)
        if r.note:
            contenuto.append({"s": "nota", "t": r.note})
    return contenuto


ASPORTO = {"s": "evidenza", "t": "ASPORTO"}


def contenuto_comanda(ordine, reparto, righe, imp, *, copia=False, da_presentare=False):
    """La comanda di un reparto. da_presentare: la porta il cliente (es. comanda del bar stampata in cassa)."""
    titolo = Reparto(reparto).label.upper()
    contenuto = [
        {"s": "centro", "t": _intestazione(imp)},
        {"s": "titolo", "t": f"COPIA {titolo}" if copia else titolo},
    ]
    if da_presentare:
        contenuto.append({"s": "centro", "t": f"Da presentare al {Reparto(reparto).label.lower()}", "b": True})
    # Da asporto: si vede subito, in alto e in fondo (la comanda si piega o si infilza).
    if ordine.asporto:
        contenuto.append(ASPORTO)
    contenuto.append({"s": "grande", "t": _numero_tavolo(ordine)})
    if ordine.nome:
        contenuto.append({"s": "testo", "t": ordine.nome, "b": True})
    dettagli = [_quando(ordine.creato_il)]
    if ordine.postazione == Postazione.BAR:
        dettagli.append("cassa bar")
    if ordine.coperti:
        dettagli.append(f"coperti {ordine.coperti}")
    contenuto += [{"s": "testo", "t": " · ".join(dettagli)}, *_cassiere(ordine), {"s": "linea"}]
    contenuto += _voci(righe, alta=True)
    contenuto.append({"s": "linea"})
    if ordine.note:
        contenuto.append({"s": "testo", "t": f"NOTE: {ordine.note}", "b": True})
    if ordine.asporto:
        contenuto.append({"s": "evidenza", "t": "DA PORTARE VIA"})
    return contenuto


def contenuto_ricevuta(ordine, righe, imp, *, copia=False):
    contenuto = [
        {"s": "titolo", "t": _intestazione(imp)},
        {"s": "centro", "t": "Ricevuta non fiscale" + (" - copia" if copia else "")},
        {"s": "vuota"},
        {"s": "grande", "t": _numero_tavolo(ordine)},
    ]
    if ordine.asporto:
        contenuto.append({"s": "centro", "t": "DA ASPORTO", "b": True})
    if ordine.nome:
        contenuto.append({"s": "testo", "t": ordine.nome, "b": True})
    contenuto += [{"s": "testo", "t": _quando(ordine.creato_il)}, *_cassiere(ordine), {"s": "linea"}]
    contenuto += _voci(righe, prezzi=True)
    contenuto += [
        {"s": "linea"},
        {"s": "coppia", "t": "TOTALE", "d": euro(ordine.totale), "b": True},
        {"s": "testo", "t": f"Pagamento: {ordine.get_pagamento_display()}"},
    ]
    if imp.messaggio_ricevuta:
        contenuto += [{"s": "vuota"}, {"s": "centro", "t": imp.messaggio_ricevuta}]
    return contenuto


def contenuto_annullo(ordine, reparto, righe, imp):
    contenuto = [
        {"s": "centro", "t": _intestazione(imp)},
        {"s": "titolo", "t": "ANNULLATO"},
        *([ASPORTO] if ordine.asporto else []),
        {"s": "grande", "t": _numero_tavolo(ordine)},
    ]
    if ordine.nome:
        contenuto.append({"s": "testo", "t": ordine.nome, "b": True})
    contenuto.append({
        "s": "testo",
        "t": f"Ordine delle {formats.time_format(timezone.localtime(ordine.creato_il), 'H:i')}, "
        f"annullato alle {formats.time_format(timezone.localtime(ordine.annullato_il), 'H:i')}",
    })
    if ordine.motivo_annullamento:
        contenuto.append({"s": "testo", "t": f"Motivo: {ordine.motivo_annullamento}"})
    contenuto.append({"s": "linea"})
    contenuto += _voci(righe, alta=True)
    contenuto += [{"s": "linea"}, {"s": "centro", "t": f"NON PREPARARE ({Reparto(reparto).label.upper()})", "b": True}]
    return contenuto


def contenuto_prova(stampante, imp):
    return [
        {"s": "centro", "t": _intestazione(imp)},
        {"s": "titolo", "t": "PROVA"},
        {"s": "testo", "t": f"Stampante: {stampante.nome}", "b": True},
        {"s": "testo", "t": f"{stampante.colonne} caratteri per riga"
         + (f" · fogli {stampante.carta}" if stampante.connessione == Connessione.IPP else "")
         + f" · {_quando(timezone.now())}"},
        {"s": "testo", "t": "Lettere accentate: à è é ì ò ù € «»"},
        {"s": "linea"},
        {"s": "voce", "q": 2, "t": "Grigliata mista", "alta": True},
        {"s": "voce", "q": 1, "t": "Un piatto con un nome molto lungo che deve andare a capo", "alta": True},
        {"s": "nota", "t": "senza cipolla"},
        {"s": "linea"},
        {"s": "coppia", "t": "TOTALE", "d": euro(12), "b": True},
    ]


# ---------------------------------------------------------------------------
# Accodamento
# ---------------------------------------------------------------------------


def _destinazioni(ordine, imp, *, solo=None):
    """[(documento, reparto, stampante)] per un ordine. `solo`: "cucina", "bar" o "ricevuta"."""
    if ordine.postazione == Postazione.BAR:
        comande = {Reparto.BAR: imp.stampante_cassa_bar if imp.stampa_comande_cassa_bar or solo else None}
        ricevute = imp.stampante_cassa_bar
    else:
        comande = {Reparto.CUCINA: imp.stampante_cucina, Reparto.BAR: imp.stampante_bar}
        ricevute = imp.stampante_ricevute
    destinazioni = [(Documento.COMANDA, reparto, stampante) for reparto, stampante in comande.items()]
    if imp.ricevuta_cliente or solo == "ricevuta":
        destinazioni.append((Documento.RICEVUTA, "", ricevute))
    if solo == "ricevuta":
        destinazioni = [d for d in destinazioni if d[0] == Documento.RICEVUTA]
    elif solo:
        destinazioni = [d for d in destinazioni if d[0] == Documento.COMANDA and d[1] == solo]
    return destinazioni


def stampanti_ordine(ordine, imp, solo):
    """Per la ristampa: la stampante che riceverebbe `solo`, oppure None."""
    for _, _, stampante in _destinazioni(ordine, imp, solo=solo):
        if stampante is not None and stampante.attiva:
            return stampante
    return None


def porta_il_cliente(ordine, imp):
    """La comanda del bar esce in cassa, sulla stampante delle ricevute: la porta il cliente al bar."""
    return (
        ordine.postazione == Postazione.INGRESSO
        and imp.stampante_bar_id is not None
        and imp.stampante_bar_id == imp.stampante_ricevute_id
    )


def stampa_qui():
    """A casa non si stampa: le stampanti sono in sagra, e una stampa fatta da casa uscirebbe
    in sagra solo alla prossima accensione (vedi sistema/sincronia.py)."""
    return settings.SERVER_NOME != "casa"


DA_CASA = "Dal server di casa non si stampa: le stampanti sono in sagra. Ristampa dal programma della sagra."


def accoda_ordine(ordine, righe, imp, *, solo=None, copia=False):
    """Crea le stampe di un ordine (da chiamare dentro la transazione che lo registra)."""
    if not stampa_qui():
        return []
    stampe = []
    adesso = timezone.now()
    al_cliente = porta_il_cliente(ordine, imp)
    for documento, reparto, stampante in _destinazioni(ordine, imp, solo=solo):
        if stampante is None or not stampante.attiva:
            continue
        if documento == Documento.COMANDA:
            del_reparto = [r for r in righe if r.reparto == reparto]
            if not del_reparto:
                continue
            contenuto = contenuto_comanda(
                ordine, reparto, del_reparto, imp, copia=copia, da_presentare=al_cliente and reparto == Reparto.BAR
            )
        else:
            contenuto = contenuto_ricevuta(ordine, righe, imp, copia=copia)
        stampe.append(Stampa(
            stampante=stampante, ordine=ordine, documento=documento, reparto=reparto,
            copia=copia, contenuto=contenuto, creata_il=adesso,
        ))
    return Stampa.objects.bulk_create(stampe)


def accoda_annullo(ordine, righe, imp):
    """Dopo l'annullamento: le comande non ancora stampate non escono più,
    per quelle già stampate (o forse stampate) esce un biglietto «ANNULLATO»."""
    stampe = list(ordine.stampe.select_related("stampante").exclude(stato=StatoStampa.ANNULLATA))
    da_fermare = [s.pk for s in stampe if s.documento != Documento.ANNULLO and s.stato == StatoStampa.IN_CODA]
    Stampa.objects.filter(pk__in=da_fermare, stato=StatoStampa.IN_CODA).update(stato=StatoStampa.ANNULLATA)
    uscite = {}
    for s in stampe:
        if s.documento == Documento.COMANDA and s.pk not in da_fermare:
            uscite.setdefault(s.reparto, s.stampante)
    adesso = timezone.now()
    if not stampa_qui():
        return []
    nuove = [
        Stampa(
            stampante=stampante, ordine=ordine, documento=Documento.ANNULLO, reparto=reparto, creata_il=adesso,
            contenuto=contenuto_annullo(ordine, reparto, [r for r in righe if r.reparto == reparto], imp),
        )
        for reparto, stampante in uscite.items()
        if stampante.attiva
    ]
    return Stampa.objects.bulk_create(nuove)


def accoda_prova(stampante, imp):
    return Stampa.objects.create(
        stampante=stampante, documento=Documento.PROVA, contenuto=contenuto_prova(stampante, imp)
    )


# ---------------------------------------------------------------------------
# Impaginazione (comune a stampante termica e browser)
# ---------------------------------------------------------------------------

SOSTITUZIONI = {
    "’": "'", "‘": "'", "“": '"', "”": '"', "„": '"', "–": "-", "—": "-", "…": "...", "\t": " ",
}


def _ascii(testo):
    testo = testo.replace("€", "EUR").replace("«", '"').replace("»", '"').replace("·", "-").replace("×", "x")
    testo = "".join(c for c in unicodedata.normalize("NFKD", testo) if not unicodedata.combining(c))
    return testo.encode("ascii", "replace").decode("ascii")


def prepara_testo(testo, accenti=True):
    testo = "".join(SOSTITUZIONI.get(c, c) for c in str(testo))
    testo = "".join(c for c in testo if c.isprintable())
    return testo if accenti else _ascii(testo)


def _spezza(testo, larghezza):
    """Va a capo tra le parole; le parole troppo lunghe si spezzano."""
    larghezza = max(larghezza, 1)
    if len(testo) <= larghezza:
        return [testo]  # così restano gli spazi voluti, es. "N. 57  TAV. 12"
    righe, attuale = [], ""
    for parola in testo.split():
        while len(parola) > larghezza:
            if attuale:
                righe.append(attuale)
                attuale = ""
            righe.append(parola[:larghezza])
            parola = parola[larghezza:]
        if not parola:
            continue
        if not attuale:
            attuale = parola
        elif len(attuale) + 1 + len(parola) <= larghezza:
            attuale += " " + parola
        else:
            righe.append(attuale)
            attuale = parola
    if attuale or not righe:
        righe.append(attuale)
    return righe


def impagina(contenuto, colonne=42, accenti=True):
    """[(stile, allineamento, testo)] riga per riga.

    stile: insieme di "b" (grassetto), "alto" (altezza doppia), "largo" (larghezza doppia),
    "inverso" (bianco su nero).
    allineamento: "sx" o "centro". Il testo è già tagliato alla larghezza della riga.
    """
    righe = []
    for voce in contenuto:
        s = voce.get("s")
        t = prepara_testo(voce.get("t", ""), accenti)
        grassetto = {"b"} if voce.get("b") else set()
        if s == "titolo":
            righe += [({"b", "alto", "largo"}, "centro", r) for r in _spezza(t, colonne // 2)]
        elif s == "grande":
            righe += [({"b", "alto", "largo"}, "sx", r) for r in _spezza(t, colonne // 2)]
        elif s == "evidenza":
            # La fascia nera va da un bordo all'altro: il testo è centrato con gli spazi.
            righe += [({"b", "alto", "largo", "inverso"}, "sx", r.center(colonne // 2)) for r in _spezza(t, colonne // 2)]
        elif s == "centro":
            righe += [(grassetto, "centro", r) for r in _spezza(t, colonne)]
        elif s == "testo":
            righe += [(grassetto, "sx", r) for r in _spezza(t, colonne)]
        elif s == "voce":
            stile = {"b", "alto"} if voce.get("alta") else set()
            prefisso = f"{voce.get('q', 1)} x "
            prezzo = prepara_testo(voce.get("p", ""), accenti)
            spazio_prezzo = len(prezzo) + 1 if prezzo else 0
            pezzi = _spezza(t, colonne - len(prefisso) - spazio_prezzo)
            for i, pezzo in enumerate(pezzi):
                inizio = (prefisso if i == 0 else " " * len(prefisso)) + pezzo
                if i == 0 and prezzo:
                    inizio = inizio.ljust(colonne - len(prezzo)) + prezzo
                righe.append((stile, "sx", inizio))
        elif s == "nota":
            rientro = "    > "
            for i, pezzo in enumerate(_spezza(t, colonne - len(rientro))):
                righe.append(({"b"}, "sx", (rientro if i == 0 else " " * len(rientro)) + pezzo))
        elif s == "coppia":
            destra = prepara_testo(voce.get("d", ""), accenti)
            pezzi = _spezza(t, colonne - len(destra) - 1)
            for pezzo in pezzi[:-1]:
                righe.append((grassetto, "sx", pezzo))
            righe.append((grassetto, "sx", pezzi[-1].ljust(colonne - len(destra)) + destra))
        elif s == "linea":
            righe.append((set(), "sx", "-" * colonne))
        elif s == "vuota":
            righe.append((set(), "sx", ""))
    return righe


ESC = b"\x1b"
GS = b"\x1d"
# ESC t 19: tabella caratteri PC858 (europea, con il simbolo dell'euro).
TABELLA_PC858 = 19


def escpos(contenuto, *, colonne=42, accenti=True):
    """Comandi ESC/POS pronti da mandare alla stampante termica."""
    dati = bytearray(ESC + b"@")
    if accenti:
        dati += ESC + b"t" + bytes([TABELLA_PC858])
    for stile, allineamento, testo in impagina(contenuto, colonne, accenti):
        modo = (0x08 if "b" in stile else 0) | (0x10 if "alto" in stile else 0) | (0x20 if "largo" in stile else 0)
        dati += ESC + b"a" + bytes([1 if allineamento == "centro" else 0])
        dati += ESC + b"!" + bytes([modo])
        # GS B: stampa in negativo (bianco su nero).
        dati += GS + b"B" + bytes([1 if "inverso" in stile else 0])
        dati += testo.encode("cp858" if accenti else "ascii", "replace") + b"\n"
    dati += ESC + b"!\x00" + ESC + b"a\x00" + GS + b"B\x00"
    # Avanza la carta oltre la taglierina e taglia.
    dati += ESC + b"d\x04" + GS + b"V" + bytes([66, 0])
    return bytes(dati)


# ---------------------------------------------------------------------------
# Coda: servizio di stampa del server e programma di stampa (agente)
# ---------------------------------------------------------------------------

# Nome con cui il servizio di stampa del server compare tra chi stampa.
NOME_SERVER = "(server)"


def segna_contatto(agente, *, ip=None, versione=""):
    """Chi stampa si fa vivo: serve all'avviso in cassa quando nessuno manda le stampe."""
    campi = {"ultimo_contatto": timezone.now()}
    if ip:
        campi["indirizzo_ip"] = ip
    if versione:
        campi["versione"] = versione[:20]
    if not AgenteStampa.objects.filter(nome=agente).update(**campi):
        AgenteStampa.objects.get_or_create(nome=agente, defaults=campi)


def _da_stampare(agente, connessioni, adesso):
    disponibili = (
        Q(stato=StatoStampa.IN_CODA)
        | Q(stato=StatoStampa.IN_STAMPA, presa_il__lt=adesso - TIMEOUT_IN_STAMPA)
        | Q(stato=StatoStampa.ERRORE, presa_il__lt=adesso - ATTESA_RIPROVA, creata_il__gte=adesso - DURATA_RIPROVE)
    )
    return (
        Stampa.objects.filter(disponibili, stampante__attiva=True, stampante__connessione__in=connessioni)
        .filter(Q(stampante__agente="") | Q(stampante__agente__iexact=agente))
    )


def stampanti_da_servire(*, agente, connessioni):
    """Le stampanti (pk) che hanno qualcosa da stampare per questo agente."""
    return set(_da_stampare(agente, connessioni, timezone.now()).values_list("stampante_id", flat=True).distinct())


def assegna(*, agente, connessioni, massimo=5, stampante=None):
    """Le prossime stampe, segnate come prese da `agente` (nessun altro le riceve per un minuto)."""
    adesso = timezone.now()
    with transaction.atomic():
        coda = _da_stampare(agente, connessioni, adesso)
        if stampante is not None:
            coda = coda.filter(stampante_id=stampante)
        lavori = list(
            coda.select_for_update(skip_locked=True, of=("self",))
            .select_related("stampante")
            .order_by("creata_il", "pk")[:massimo]
        )
        for s in lavori:
            s.stato = StatoStampa.IN_STAMPA
            s.presa_il = adesso
            s.agente = agente
            s.tentativi += 1
        Stampa.objects.bulk_update(lavori, ["stato", "presa_il", "agente", "tentativi"])
    return lavori


def prendi_lavori(*, agente, ip=None, versione="", massimo=5):
    """Per il programma di stampa: le prossime stampe pronte da inviare (ESC/POS in base64)."""
    with transaction.atomic():
        segna_contatto(agente, ip=ip, versione=versione)
        lavori = assegna(agente=agente, connessioni=CONNESSIONI_AGENTE, massimo=massimo)
    return [
        {
            "id": s.pk,
            "descrizione": str(s),
            "connessione": s.stampante.connessione,
            "indirizzo": s.stampante.indirizzo,
            "dati": base64.b64encode(
                escpos(s.contenuto, colonne=s.stampante.colonne, accenti=s.stampante.accenti)
            ).decode("ascii"),
        }
        for s in lavori
    ]


def registra_esito(*, agente, stampa_id, ok, errore=""):
    """Esito comunicato dall'agente. False se la stampa non era (più) sua."""
    with transaction.atomic():
        s = Stampa.objects.select_for_update().filter(pk=stampa_id).first()
        if s is None or s.stato != StatoStampa.IN_STAMPA or s.agente != agente:
            return False
        if ok:
            s.stato, s.stampata_il, s.errore = StatoStampa.STAMPATA, timezone.now(), ""
        else:
            s.stato, s.errore = StatoStampa.ERRORE, (str(errore or "") or "Errore sconosciuto")[:300]
        s.save(update_fields=["stato", "stampata_il", "errore"])
    return True


def riprova(stampa):
    """Rimette in coda una stampa andata in errore."""
    return Stampa.objects.filter(pk=stampa.pk, stato=StatoStampa.ERRORE).update(
        stato=StatoStampa.IN_CODA, errore=""
    )


def segna_stampate_dal_browser(ids):
    return Stampa.objects.filter(
        pk__in=ids, stampante__connessione=Connessione.BROWSER,
        stato__in=[StatoStampa.IN_CODA, StatoStampa.ERRORE],
    ).update(stato=StatoStampa.STAMPATA, stampata_il=timezone.now())


def avviso_stampanti():
    """Stampanti di rete che all'ultimo controllo (ogni minuto) non rispondevano o non potevano stampare."""
    from sistema.models import Battito

    from .ipp import TESTI_BLOCCANTI

    stampanti = {f"stampante-{s.pk}": s for s in Stampante.objects.filter(attiva=True, connessione__in=[Connessione.IPP, Connessione.RETE])}
    controlli = Battito.objects.filter(nome__in=stampanti, ultimo_il__gte=timezone.now() - timedelta(minutes=3)).order_by("nome")
    problemi = []
    for controllo in controlli:
        nome = stampanti[controllo.nome].nome
        if controllo.stato == "errore":
            problemi.append(f"la stampante «{nome}» non risponde (è accesa e collegata?)")
        else:
            bloccanti = [t for t in TESTI_BLOCCANTI if t in controllo.messaggio]
            if bloccanti:
                problemi.append(f"«{nome}»: {', '.join(sorted(bloccanti))}")
    if not problemi:
        return ""
    testo = "; ".join(problemi)
    return testo[0].upper() + testo[1:] + ". Le comande restano in coda ed escono quando torna a posto."


def stato_stampa():
    """Per l'avviso in cassa: c'è chi manda le stampe alle stampanti attive?

    avviso: testo da mostrare (vuoto se va tutto bene); server: il servizio di stampa
    del server è acceso; errori: stampe andate male nelle ultime ore.
    """
    connessioni = set(Stampante.objects.filter(attiva=True).values_list("connessione", flat=True))
    collegati = [a.nome for a in AgenteStampa.objects.all() if a.collegato]
    server = NOME_SERVER in collegati
    agenti = any(nome != NOME_SERVER for nome in collegati)
    avviso = ""
    if (Connessione.IPP in connessioni and not server) or (Connessione.RETE in connessioni and not (server or agenti)):
        avviso = "Il servizio di stampa non è attivo: le comande restano in coda. Avvisa chi gestisce il computer del programma."
    elif Connessione.COMPUTER in connessioni and not agenti:
        avviso = "Il programma di stampa non è collegato: le comande restano in coda. Avvisa chi gestisce il computer delle stampanti."
    elif server:
        avviso = avviso_stampanti()
    return {
        "avviso": avviso,
        "server": server,
        "errori": Stampa.objects.filter(
            stato=StatoStampa.ERRORE, creata_il__gte=timezone.now() - timedelta(hours=6)
        ).count(),
    }
