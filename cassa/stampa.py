"""Biglietti da stampare: comande, ricevute, annullamenti.

Il contenuto di ogni stampa è salvato già pronto come lista di righe (JSON):
la stessa stampa si può mandare a una stampante termica (ESC/POS) oppure
mostrare nel browser, e una ristampa esce identica.

Ogni riga è un dizionario con lo stile "s" e i suoi dati:

    {"s": "titolo", "t": "CUCINA"}                    grande e centrato
    {"s": "grande", "t": "N. 57  TAV. 12"}            grande, a sinistra
    {"s": "testo", "t": "…", "b": true}               normale (b = grassetto)
    {"s": "centro", "t": "…"}                         centrato
    {"s": "voce", "q": 2, "t": "Grigliata", "p": "25,00", "alta": true}
    {"s": "nota", "t": "senza cipolla"}               sotto una voce
    {"s": "coppia", "t": "Totale", "d": "34,50 €", "b": true}
    {"s": "linea"} / {"s": "vuota"}

Le stampanti di rete o collegate a un computer ricevono le stampe dal
programma di stampa (agente/agente_stampa.py), che le chiede al server:
così funziona anche con l'app in cloud, senza aprire porte nella rete della sagra.
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


def contenuto_comanda(ordine, reparto, righe, imp, *, copia=False):
    titolo = Reparto(reparto).label.upper()
    contenuto = [
        {"s": "centro", "t": _intestazione(imp)},
        {"s": "titolo", "t": f"COPIA {titolo}" if copia else titolo},
        {"s": "grande", "t": _numero_tavolo(ordine)},
    ]
    if ordine.nome:
        contenuto.append({"s": "testo", "t": ordine.nome, "b": True})
    dettagli = [_quando(ordine.creato_il)]
    if ordine.postazione == Postazione.BAR:
        dettagli.append("cassa bar")
    if ordine.coperti:
        dettagli.append(f"coperti {ordine.coperti}")
    contenuto += [{"s": "testo", "t": " · ".join(dettagli)}, {"s": "linea"}]
    contenuto += _voci(righe, alta=True)
    contenuto.append({"s": "linea"})
    if ordine.note:
        contenuto.append({"s": "testo", "t": f"NOTE: {ordine.note}", "b": True})
    return contenuto


def contenuto_ricevuta(ordine, righe, imp, *, copia=False):
    contenuto = [
        {"s": "titolo", "t": _intestazione(imp)},
        {"s": "centro", "t": "Ricevuta non fiscale" + (" - copia" if copia else "")},
        {"s": "vuota"},
        {"s": "grande", "t": _numero_tavolo(ordine)},
    ]
    if ordine.nome:
        contenuto.append({"s": "testo", "t": ordine.nome, "b": True})
    contenuto += [{"s": "testo", "t": _quando(ordine.creato_il)}, {"s": "linea"}]
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
        {"s": "testo", "t": f"{stampante.colonne} caratteri per riga · {_quando(timezone.now())}"},
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


def accoda_ordine(ordine, righe, imp, *, solo=None, copia=False):
    """Crea le stampe di un ordine (da chiamare dentro la transazione che lo registra)."""
    stampe = []
    adesso = timezone.now()
    for documento, reparto, stampante in _destinazioni(ordine, imp, solo=solo):
        if stampante is None or not stampante.attiva:
            continue
        if documento == Documento.COMANDA:
            del_reparto = [r for r in righe if r.reparto == reparto]
            if not del_reparto:
                continue
            contenuto = contenuto_comanda(ordine, reparto, del_reparto, imp, copia=copia)
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

    stile: insieme di "b" (grassetto), "alto" (altezza doppia), "largo" (larghezza doppia).
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
        dati += testo.encode("cp858" if accenti else "ascii", "replace") + b"\n"
    dati += ESC + b"!\x00" + ESC + b"a\x00"
    # Avanza la carta oltre la taglierina e taglia.
    dati += ESC + b"d\x04" + GS + b"V" + bytes([66, 0])
    return bytes(dati)


# ---------------------------------------------------------------------------
# Programma di stampa (agente)
# ---------------------------------------------------------------------------


def prendi_lavori(*, agente, ip=None, versione="", massimo=5):
    """Assegna all'agente le prossime stampe e le restituisce pronte da inviare."""
    adesso = timezone.now()
    with transaction.atomic():
        campi = {"ultimo_contatto": adesso}
        if ip:
            campi["indirizzo_ip"] = ip
        if versione:
            campi["versione"] = versione[:20]
        if not AgenteStampa.objects.filter(nome=agente).update(**campi):
            AgenteStampa.objects.get_or_create(nome=agente, defaults=campi)
        disponibili = (
            Q(stato=StatoStampa.IN_CODA)
            | Q(stato=StatoStampa.IN_STAMPA, presa_il__lt=adesso - TIMEOUT_IN_STAMPA)
            | Q(stato=StatoStampa.ERRORE, presa_il__lt=adesso - ATTESA_RIPROVA, creata_il__gte=adesso - DURATA_RIPROVE)
        )
        lavori = list(
            Stampa.objects.select_for_update(skip_locked=True, of=("self",))
            .filter(disponibili, stampante__attiva=True, stampante__connessione__in=CONNESSIONI_AGENTE)
            .filter(Q(stampante__agente="") | Q(stampante__agente__iexact=agente))
            .select_related("stampante")
            .order_by("creata_il", "pk")[:massimo]
        )
        for s in lavori:
            s.stato = StatoStampa.IN_STAMPA
            s.presa_il = adesso
            s.agente = agente
            s.tentativi += 1
        Stampa.objects.bulk_update(lavori, ["stato", "presa_il", "agente", "tentativi"])
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


def stato_stampa():
    """Per l'avviso in cassa: se servono agenti e se sono collegati."""
    from .models import Stampante

    servono = Stampante.objects.filter(attiva=True, connessione__in=CONNESSIONI_AGENTE).exists()
    ultimo = AgenteStampa.objects.order_by("-ultimo_contatto").first()
    return {
        "servono_agenti": servono,
        "collegato": bool(ultimo and ultimo.collegato),
        "ultimo_contatto": ultimo.ultimo_contatto if ultimo else None,
        "errori": Stampa.objects.filter(
            stato=StatoStampa.ERRORE, creata_il__gte=timezone.now() - timedelta(hours=6)
        ).count(),
    }
