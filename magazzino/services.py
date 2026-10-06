"""Logica delle giacenze.

Le giacenze non sono salvate: sono sempre la somma dei movimenti, così non
possono "disallinearsi". Tutte le scritture passano da queste funzioni, che:

* bloccano le righe dei prodotti coinvolti (SELECT ... FOR UPDATE) così due
  persone che prelevano lo stesso prodotto nello stesso istante non possono
  portare la giacenza sotto zero;
* verificano che nessuna giacenza diventi negativa;
* registrano tutto o niente (transazione unica);
* ignorano il secondo invio dello stesso modulo (token univoco).
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from . import allegati as controllo_allegati
from .models import (
    TIPI_CON_ALLEGATI,
    TIPI_CON_SERVIZIO,
    Allegato,
    Movimento,
    Operazione,
    Prodotto,
    Servizio,
    TipoOperazione,
    Ubicazione,
)
from .quantita import ZERO


class ErroreMagazzino(Exception):
    """Operazione rifiutata: nessun movimento è stato registrato.

    `errori` è una lista di coppie (chiave, messaggio). La chiave indica la
    riga del modulo a cui si riferisce l'errore: (prodotto_id, ubicazione_id),
    prodotto_id oppure None.
    """

    def __init__(self, errori):
        if isinstance(errori, str):
            errori = [(None, errori)]
        self.errori = list(errori)
        super().__init__("\n".join(self.messaggi))

    @property
    def messaggi(self):
        return [messaggio for _, messaggio in self.errori]


@dataclass(frozen=True)
class Riga:
    prodotto: Prodotto
    ubicazione: Ubicazione
    quantita: Decimal  # con segno: positiva entra, negativa esce


# Segno ammesso per le quantità di ciascun tipo (0 = entrambi).
SEGNO = {
    TipoOperazione.INVENTARIO: 1,
    TipoOperazione.ACQUISTO: 1,
    TipoOperazione.RESO: 1,
    TipoOperazione.CUCINA: -1,
    TipoOperazione.SCARTO: -1,
    TipoOperazione.SPOSTAMENTO: 0,
    TipoOperazione.RETTIFICA: 0,
}


# ---------------------------------------------------------------------------
# Letture
# ---------------------------------------------------------------------------


def giacenze(prodotti=None, ubicazioni=None):
    """{(prodotto_id, ubicazione_id): quantità}, solo valori diversi da zero."""
    qs = Movimento.objects.all()
    if prodotti is not None:
        qs = qs.filter(prodotto__in=list(prodotti))
    if ubicazioni is not None:
        qs = qs.filter(ubicazione__in=list(ubicazioni))
    righe = qs.values("prodotto_id", "ubicazione_id").annotate(totale=Sum("quantita")).order_by()
    return {
        (r["prodotto_id"], r["ubicazione_id"]): r["totale"]
        for r in righe
        if r["totale"] is not None and r["totale"] != ZERO
    }


def giacenze_totali(prodotti=None):
    """{prodotto_id: quantità totale in tutte le ubicazioni}, solo diversi da zero."""
    qs = Movimento.objects.all()
    if prodotti is not None:
        qs = qs.filter(prodotto__in=list(prodotti))
    righe = qs.values("prodotto_id").annotate(totale=Sum("quantita")).order_by()
    return {
        r["prodotto_id"]: r["totale"]
        for r in righe
        if r["totale"] is not None and r["totale"] != ZERO
    }


def consumi_servizio(servizio, prodotti=None):
    """{prodotto_id: {"preso": q, "rientrato": q}} con quantità positive."""
    qs = Movimento.objects.filter(operazione__servizio=servizio)
    if prodotti is not None:
        qs = qs.filter(prodotto__in=list(prodotti))
    righe = (
        qs.values("prodotto_id", "operazione__tipo").annotate(totale=Sum("quantita")).order_by()
    )
    risultato = defaultdict(lambda: {"preso": ZERO, "rientrato": ZERO})
    for r in righe:
        if r["operazione__tipo"] == TipoOperazione.CUCINA:
            risultato[r["prodotto_id"]]["preso"] += -r["totale"]
        elif r["operazione__tipo"] == TipoOperazione.RESO:
            risultato[r["prodotto_id"]]["rientrato"] += r["totale"]
    return dict(risultato)


def prelievi_per_ubicazione(servizio):
    """{prodotto_id: {ubicazione_id: quantità presa}} per il servizio."""
    righe = (
        Movimento.objects.filter(operazione__servizio=servizio, operazione__tipo=TipoOperazione.CUCINA)
        .values("prodotto_id", "ubicazione_id")
        .annotate(totale=Sum("quantita"))
        .order_by()
    )
    risultato = defaultdict(dict)
    for r in righe:
        risultato[r["prodotto_id"]][r["ubicazione_id"]] = -r["totale"]
    return dict(risultato)


def prodotti_sotto_scorta():
    """Lista di (prodotto, giacenza totale) per i prodotti sotto la scorta minima."""
    prodotti = list(Prodotto.objects.filter(attivo=True, scorta_minima__isnull=False))
    totali = giacenze_totali(prodotti=[p.pk for p in prodotti])
    return [
        (p, totali.get(p.pk, ZERO))
        for p in prodotti
        if totali.get(p.pk, ZERO) < p.scorta_minima
    ]


# ---------------------------------------------------------------------------
# Scritture
# ---------------------------------------------------------------------------


def _blocca_servizio_aperto(servizio_id, messaggio_chiuso):
    # Va bloccato prima di tutto il resto (stesso ordine ovunque, niente stalli):
    # così nessuno registra mentre un altro termina il servizio.
    righe = list(
        Servizio.objects.select_for_update(no_key=True).filter(pk=servizio_id).values_list("chiuso_il", flat=True)
    )
    if not righe:
        raise ErroreMagazzino("Questo servizio non esiste più.")
    if righe[0] is not None:
        raise ErroreMagazzino(messaggio_chiuso)


def _blocca_prodotti(prodotto_ids):
    # Ordine fisso per evitare stalli tra transazioni concorrenti.
    list(Prodotto.objects.select_for_update().filter(pk__in=sorted(set(prodotto_ids))).order_by("pk"))


def _descrivi_altrove(prodotto, ubicazione_id, attuali):
    altrove = [
        (uid, q)
        for (pid, uid), q in attuali.items()
        if pid == prodotto.pk and uid != ubicazione_id and q > ZERO
    ]
    if not altrove:
        return ""
    nomi = Ubicazione.objects.in_bulk([uid for uid, _ in altrove])
    altrove.sort(key=lambda x: (nomi[x[0]].ordine, nomi[x[0]].nome))
    elenco = ", ".join(f"{nomi[uid].nome} ({prodotto.formatta(q)})" for uid, q in altrove)
    return f" Si trova anche in: {elenco}."


def _controlla(tipo, righe, servizio):
    segno = SEGNO[tipo]
    for r in righe:
        if r.quantita == ZERO or (segno > 0 and r.quantita < ZERO) or (segno < 0 and r.quantita > ZERO):
            raise ValueError(f"Quantità con segno non valido per {tipo}: {r.quantita}")

    prodotti = {r.prodotto.pk: r.prodotto for r in righe}
    ubicazioni = {r.ubicazione.pk: r.ubicazione for r in righe}
    delta = defaultdict(lambda: ZERO)
    for r in righe:
        delta[(r.prodotto.pk, r.ubicazione.pk)] += r.quantita

    errori = []
    attuali = giacenze(prodotti=prodotti.keys())
    for (pid, uid), variazione in delta.items():
        if variazione >= ZERO:
            continue
        disponibile = attuali.get((pid, uid), ZERO)
        if disponibile + variazione < ZERO:
            p = prodotti[pid]
            if disponibile > ZERO:
                messaggio = (
                    f"{p.nome}: in {ubicazioni[uid].nome} ce ne sono solo {p.formatta(disponibile)}, "
                    f"non puoi toglierne {p.formatta(-variazione)}."
                )
            else:
                messaggio = f"{p.nome}: in {ubicazioni[uid].nome} non ce n'è."
            errori.append(((pid, uid), messaggio + _descrivi_altrove(p, uid, attuali)))

    if tipo == TipoOperazione.RESO:
        consumi = consumi_servizio(servizio, prodotti=prodotti.keys())
        rientri = defaultdict(lambda: ZERO)
        for r in righe:
            rientri[r.prodotto.pk] += r.quantita
        for pid, rientro in rientri.items():
            p = prodotti[pid]
            c = consumi.get(pid, {"preso": ZERO, "rientrato": ZERO})
            massimo = max(c["preso"] - c["rientrato"], ZERO)
            if rientro > massimo:
                if c["preso"] == ZERO:
                    messaggio = f"{p.nome}: in questo servizio non è mai stato messo a cucinare."
                else:
                    messaggio = f"{p.nome}: in questo servizio ne sono stati messi a cucinare {p.formatta(c['preso'])}"
                    if c["rientrato"] > ZERO:
                        messaggio += f" e ne sono già rientrati {p.formatta(c['rientrato'])}"
                    messaggio += f", quindi ne possono rientrare al massimo {p.formatta(massimo)}."
                errori.append((pid, messaggio))

    if errori:
        raise ErroreMagazzino(errori)


def _registra(tipo, prodotto_ids, calcola_righe, *, utente, servizio=None, note="", token=None, allegati=()):
    """Crea un'operazione con i suoi movimenti. Restituisce (operazione, creata)."""
    if (tipo in TIPI_CON_SERVIZIO) != (servizio is not None):
        raise ValueError("Il servizio va indicato solo per i prelievi e i rientri.")
    if token is not None:
        esistente = Operazione.objects.filter(token=token).first()
        if esistente is not None:
            return esistente, False
    prodotto_ids = set(prodotto_ids)
    salvati = []
    try:
        with transaction.atomic():
            if servizio is not None:
                _blocca_servizio_aperto(
                    servizio.pk, "Il servizio è terminato: per registrare altro bisogna prima riaprirlo."
                )
            operazione = Operazione.objects.create(
                tipo=tipo, servizio=servizio, utente=utente, note=(note or "").strip()[:250], token=token
            )
            _blocca_prodotti(prodotto_ids)
            righe = calcola_righe()
            if not righe:
                raise ErroreMagazzino("Non hai inserito nessuna quantità.")
            if not {r.prodotto.pk for r in righe} <= prodotto_ids:
                raise ValueError("Righe con prodotti non bloccati.")
            _controlla(tipo, righe, servizio)
            Movimento.objects.bulk_create(
                Movimento(operazione=operazione, prodotto=r.prodotto, ubicazione=r.ubicazione, quantita=r.quantita)
                for r in righe
            )
            _salva_allegati(operazione, allegati, utente, salvati)
    except IntegrityError:
        _elimina_file(salvati)
        # Due invii identici arrivati insieme: il secondo trova il token già usato.
        if token is not None:
            esistente = Operazione.objects.filter(token=token).first()
            if esistente is not None:
                return esistente, False
        raise
    except Exception:
        _elimina_file(salvati)
        raise
    return operazione, True


# ---------------------------------------------------------------------------
# Allegati
# ---------------------------------------------------------------------------


def controlla_allegati(files):
    """Verifica i file caricati. Restituisce [(file, (tipo_contenuto, estensione))].

    Solleva ErroreMagazzino con un messaggio per ogni file non accettato.
    """
    files = list(files)
    if len(files) > settings.ALLEGATI_MAX_PER_INVIO:
        raise ErroreMagazzino(f"Puoi allegare al massimo {settings.ALLEGATI_MAX_PER_INVIO} file per volta.")
    controllati, errori = [], []
    for file in files:
        try:
            controllati.append((file, controllo_allegati.controlla(file)))
        except controllo_allegati.AllegatoNonValido as e:
            errori.append((None, str(e)))
    if errori:
        raise ErroreMagazzino(errori)
    return controllati


def _salva_allegati(operazione, controllati, utente, salvati):
    creati = []
    for file, (tipo_contenuto, estensione) in controllati:
        allegato = Allegato(
            operazione=operazione,
            nome=controllo_allegati.nome_pulito(file.name),
            tipo_contenuto=tipo_contenuto,
            dimensione=file.size,
            utente=utente,
        )
        allegato.file.save(f"allegato.{estensione}", file, save=False)
        salvati.append((allegato.file.storage, allegato.file.name))
        allegato.save()
        creati.append(allegato)
    return creati


def _elimina_file(salvati):
    for storage, nome in salvati:
        storage.delete(nome)


def aggiungi_allegati(*, operazione, files, utente):
    """Allega file a un carico già registrato. Restituisce gli allegati creati."""
    if operazione.tipo not in TIPI_CON_ALLEGATI:
        raise ErroreMagazzino("Si possono allegare file solo ai carichi (inventario e acquisti).")
    controllati = controlla_allegati(files)
    if not controllati:
        raise ErroreMagazzino("Scegli almeno un file da allegare.")
    salvati = []
    try:
        with transaction.atomic():
            if not Operazione.objects.select_for_update().filter(pk=operazione.pk).exists():
                raise ErroreMagazzino("Questa operazione è stata annullata.")
            creati = _salva_allegati(operazione, controllati, utente, salvati)
    except Exception:
        _elimina_file(salvati)
        raise
    return creati


# ---------------------------------------------------------------------------
# Foto dei prodotti
# ---------------------------------------------------------------------------


def _cambia_foto(prodotto, nuova):
    """nuova: (foto, miniatura) già preparate da foto.prepara(), oppure None per toglierla.

    I file vecchi si cancellano solo se il cambio va a buon fine; quelli nuovi
    si cancellano se qualcosa va storto.
    """
    salvati = []
    try:
        with transaction.atomic():
            p = Prodotto.objects.select_for_update().filter(pk=prodotto.pk).first()
            if p is None:
                raise ErroreMagazzino("Questo prodotto non esiste più.")
            vecchi = [(c.storage, c.name) for c in (p.foto, p.miniatura) if c]
            if nuova is None:
                p.foto, p.miniatura = "", ""
            else:
                nome = uuid.uuid4().hex
                grande, piccola = nuova
                p.foto.save(f"{nome}.jpg", grande, save=False)
                salvati.append((p.foto.storage, p.foto.name))
                p.miniatura.save(f"{nome}-piccola.jpg", piccola, save=False)
                salvati.append((p.miniatura.storage, p.miniatura.name))
            p.save(update_fields=["foto", "miniatura"])
            transaction.on_commit(lambda: _elimina_file(vecchi))
    except Exception:
        _elimina_file(salvati)
        raise
    prodotto.foto, prodotto.miniatura = p.foto, p.miniatura
    return prodotto


def salva_foto(*, prodotto, foto):
    """foto: (foto, miniatura) da magazzino.foto.prepara(). Sostituisce quella di prima."""
    return _cambia_foto(prodotto, foto)


def togli_foto(*, prodotto):
    return _cambia_foto(prodotto, None)


# ---------------------------------------------------------------------------
# Servizi: terminare e riaprire
# ---------------------------------------------------------------------------


def chiudi_servizio(*, servizio, utente):
    """Termina il servizio. Restituisce True se è stato chiuso adesso, False se lo era già."""
    with transaction.atomic():
        servizio = Servizio.objects.select_for_update(no_key=True).get(pk=servizio.pk)
        if servizio.chiuso:
            return False
        servizio.chiuso_il = timezone.now()
        servizio.chiuso_da = utente
        servizio.save(update_fields=["chiuso_il", "chiuso_da"])
    return True


def riapri_servizio(*, servizio):
    """Riapre un servizio terminato. Restituisce True se è stato riaperto adesso."""
    with transaction.atomic():
        servizio = Servizio.objects.select_for_update(no_key=True).get(pk=servizio.pk)
        if not servizio.chiuso:
            return False
        servizio.chiuso_il = None
        servizio.chiuso_da = None
        servizio.save(update_fields=["chiuso_il", "chiuso_da"])
    return True


def _positive(voci):
    voci = list(voci)
    for voce in voci:
        if voce[-1] is None or voce[-1] <= ZERO:
            raise ValueError("Le quantità devono essere positive.")
    return voci


def registra_carico(*, tipo, ubicazione, quantita, utente, note="", token=None, allegati=()):
    """quantita: iterabile di (prodotto, q>0). allegati: file caricati (fatture, bolle)."""
    if tipo not in (TipoOperazione.INVENTARIO, TipoOperazione.ACQUISTO):
        raise ValueError("Tipo di carico non valido.")
    voci = _positive(quantita)
    righe = [Riga(p, ubicazione, q) for p, q in voci]
    controllati = controlla_allegati(allegati)
    return _registra(
        tipo, [p.pk for p, _ in voci], lambda: righe,
        utente=utente, note=note, token=token, allegati=controllati,
    )


def registra_cucina(*, servizio, prelievi, utente, note="", token=None):
    """prelievi: iterabile di (prodotto, ubicazione, q>0)."""
    voci = _positive(prelievi)
    righe = [Riga(p, u, -q) for p, u, q in voci]
    return _registra(
        TipoOperazione.CUCINA, [p.pk for p, _, _ in voci], lambda: righe,
        utente=utente, servizio=servizio, note=note, token=token,
    )


def registra_reso(*, servizio, rientri, utente, note="", token=None):
    """rientri: iterabile di (prodotto, ubicazione di destinazione, q>0)."""
    voci = _positive(rientri)
    righe = [Riga(p, u, q) for p, u, q in voci]
    return _registra(
        TipoOperazione.RESO, [p.pk for p, _, _ in voci], lambda: righe,
        utente=utente, servizio=servizio, note=note, token=token,
    )


def registra_spostamento(*, da, a, quantita, utente, note="", token=None):
    """quantita: iterabile di (prodotto, q>0) spostati da `da` ad `a`."""
    if da.pk == a.pk:
        raise ErroreMagazzino("Partenza e destinazione devono essere diverse.")
    voci = _positive(quantita)
    righe = []
    for p, q in voci:
        righe.append(Riga(p, da, -q))
        righe.append(Riga(p, a, q))
    return _registra(
        TipoOperazione.SPOSTAMENTO, [p.pk for p, _ in voci], lambda: righe, utente=utente, note=note, token=token
    )


def registra_scarto(*, ubicazione, quantita, utente, note="", token=None):
    """quantita: iterabile di (prodotto, q>0) buttati."""
    voci = _positive(quantita)
    righe = [Riga(p, ubicazione, -q) for p, q in voci]
    return _registra(
        TipoOperazione.SCARTO, [p.pk for p, _ in voci], lambda: righe, utente=utente, note=note, token=token
    )


def registra_rettifica(*, ubicazione, conteggi, utente, note="", token=None):
    """conteggi: iterabile di (prodotto, quantità realmente contata >= 0).

    Registra la differenza tra quanto contato e la giacenza attuale,
    calcolata dopo aver bloccato i prodotti.
    """
    voci = list(conteggi)
    for _, q in voci:
        if q is None or q < ZERO:
            raise ValueError("Le quantità contate non possono essere negative.")

    def calcola():
        attuali = giacenze(prodotti=[p.pk for p, _ in voci], ubicazioni=[ubicazione.pk])
        righe = []
        for p, contato in voci:
            differenza = contato - attuali.get((p.pk, ubicazione.pk), ZERO)
            if differenza != ZERO:
                righe.append(Riga(p, ubicazione, differenza))
        if not righe:
            raise ErroreMagazzino("Le quantità contate coincidono già con le giacenze: non c'è niente da correggere.")
        return righe

    return _registra(
        TipoOperazione.RETTIFICA, [p.pk for p, _ in voci], calcola, utente=utente, note=note, token=token
    )


def annulla(*, operazione=None, movimento=None):
    """Elimina un'intera operazione oppure una sola riga (movimento).

    Rifiuta se l'annullamento porterebbe una giacenza sotto zero o renderebbe
    i rientri di un servizio maggiori dei prelievi.
    Restituisce (operazione_id, servizio_id, operazione_eliminata).
    """
    if (operazione is None) == (movimento is None):
        raise ValueError("Indica un'operazione oppure un movimento.")
    with transaction.atomic():
        if movimento is not None:
            op_id = movimento.operazione_id
        else:
            op_id = operazione.pk
        # Il servizio di un'operazione non cambia mai: si può leggere prima dei lock.
        servizio_id = Operazione.objects.filter(pk=op_id).values_list("servizio_id", flat=True).first()
        if servizio_id is not None:
            _blocca_servizio_aperto(
                servizio_id, "Il servizio è terminato: per annullare qualcosa bisogna prima riaprirlo."
            )
        if movimento is not None:
            ids_movimenti = [movimento.pk]
        else:
            ids_movimenti = list(Movimento.objects.filter(operazione_id=op_id).values_list("pk", flat=True))

        prodotto_ids = set(Movimento.objects.filter(pk__in=ids_movimenti).values_list("prodotto_id", flat=True))
        _blocca_prodotti(prodotto_ids)
        op = Operazione.objects.select_for_update().filter(pk=op_id).first()
        if op is None:
            raise ErroreMagazzino("Questa operazione è già stata annullata.")
        if movimento is not None and op.tipo == TipoOperazione.SPOSTAMENTO:
            raise ErroreMagazzino("Uno spostamento si annulla per intero, non una riga alla volta.")
        movimenti = list(
            Movimento.objects.filter(pk__in=ids_movimenti, operazione_id=op_id).select_related("prodotto", "ubicazione")
        )
        if len(movimenti) != len(ids_movimenti) or {m.prodotto_id for m in movimenti} != prodotto_ids:
            raise ErroreMagazzino("Nel frattempo qualcuno ha modificato questa operazione: ricarica la pagina.")

        prodotti = {m.prodotto_id: m.prodotto for m in movimenti}
        ubicazioni = {m.ubicazione_id: m.ubicazione for m in movimenti}
        delta = defaultdict(lambda: ZERO)
        for m in movimenti:
            delta[(m.prodotto_id, m.ubicazione_id)] -= m.quantita

        errori = []
        attuali = giacenze(prodotti=prodotto_ids)
        for (pid, uid), variazione in delta.items():
            if variazione >= ZERO:
                continue
            disponibile = attuali.get((pid, uid), ZERO)
            if disponibile + variazione < ZERO:
                p = prodotti[pid]
                errori.append((
                    (pid, uid),
                    f"{p.nome}: in {ubicazioni[uid].nome} ne restano {p.formatta(disponibile)}, "
                    f"annullando ne toglieresti {p.formatta(-variazione)}. "
                    "Probabilmente è già stato usato o spostato.",
                ))

        if op.tipo in TIPI_CON_SERVIZIO:
            consumi = consumi_servizio(op.servizio_id, prodotti=prodotto_ids)
            for pid in prodotto_ids:
                preso_tolto = sum((-m.quantita for m in movimenti if m.prodotto_id == pid and op.tipo == TipoOperazione.CUCINA), ZERO)
                rientro_tolto = sum((m.quantita for m in movimenti if m.prodotto_id == pid and op.tipo == TipoOperazione.RESO), ZERO)
                c = consumi.get(pid, {"preso": ZERO, "rientrato": ZERO})
                if c["rientrato"] - rientro_tolto > c["preso"] - preso_tolto:
                    errori.append((
                        pid,
                        f"{prodotti[pid].nome}: una parte è già stata registrata come rientrata dalla cucina. "
                        "Annulla prima il rientro.",
                    ))

        if errori:
            raise ErroreMagazzino(errori)

        Movimento.objects.filter(pk__in=ids_movimenti).delete()
        eliminata = movimento is None or not Movimento.objects.filter(operazione_id=op_id).exists()
        servizio_id = op.servizio_id
        if eliminata:
            op.delete()
        return op_id, servizio_id, eliminata
