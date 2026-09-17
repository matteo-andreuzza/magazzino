import re
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.db.models import Count
from django.shortcuts import redirect
from django.utils import timezone

from ..models import Categoria
from ..quantita import QuantitaNonValida, leggi_quantita

# Dopo mezzanotte la serata è ancora quella del giorno prima.
ORA_CAMBIO_GIORNO = 5

ORDINE_CATEGORIE = {valore: i for i, valore in enumerate(Categoria.values)}


def giorno_servizio(adesso=None):
    adesso = timezone.localtime(adesso)
    return (adesso - timedelta(hours=ORA_CAMBIO_GIORNO)).date()


def nuovo_token():
    return str(uuid.uuid4())


def leggi_token(dati):
    try:
        return uuid.UUID(dati.get("token", ""))
    except (ValueError, TypeError, AttributeError):
        return None


@dataclass
class RigaModulo:
    """Una riga di un modulo "a elenco" (carico, prelievo, spostamento…)."""

    chiave: str
    prodotto: object
    ubicazione: object = None
    attuale: Decimal | None = None
    massimo: Decimal | None = None
    gruppo: str = ""
    in_menu: bool = False
    info: list = field(default_factory=list)
    testo: str = ""
    errore: str = ""
    # Solo per il rientro dalla cucina: in quale ubicazione rimetterlo.
    scegli_ubicazione: bool = False
    ubicazione_scelta: str = ""
    # Messaggio se si supera il massimo (altrimenti uno generico).
    testo_massimo: str = ""

    @property
    def nome_campo(self):
        return f"q-{self.chiave}"


_CHIAVE_CAMPO = re.compile(r"^q-(.+)$")


def aggiungi_righe_inviate(dati, righe, crea_riga):
    """Aggiunge le righe compilate nel modulo che nel frattempo sono sparite
    dall'elenco (es. qualcuno ha preso tutto): non vanno ignorate in silenzio.

    `crea_riga(chiave)` restituisce una RigaModulo oppure None se la chiave
    non corrisponde a niente. Restituisce la lista di errori generali.
    """
    presenti = {r.chiave for r in righe}
    errori = []
    for nome, valore in dati.items():
        corrispondenza = _CHIAVE_CAMPO.match(nome)
        if not corrispondenza or not valore.strip():
            continue
        chiave = corrispondenza.group(1)
        if chiave in presenti:
            continue
        riga = crea_riga(chiave)
        if riga is None:
            errori.append("Il modulo contiene una riga non riconosciuta: ricarica la pagina.")
            continue
        righe.append(riga)
        presenti.add(chiave)
    return errori


def leggi_righe(dati, righe, *, ammetti_zero=False):
    """Legge le quantità scritte. Restituisce (lista di (riga, valore), ok)."""
    valori = []
    ok = True
    for riga in righe:
        riga.testo = (dati.get(riga.nome_campo) or "").strip()
        riga.errore = ""
        try:
            valore = leggi_quantita(riga.testo, ammetti_zero=ammetti_zero)
        except QuantitaNonValida as e:
            riga.errore = str(e)
            ok = False
            continue
        if valore is None:
            continue
        if riga.massimo is not None and valore > riga.massimo:
            if riga.testo_massimo:
                riga.errore = riga.testo_massimo
            elif riga.massimo > 0:
                riga.errore = f"Ce ne sono solo {riga.prodotto.formatta(riga.massimo)}."
            else:
                riga.errore = "Non ce n'è più: qualcuno l'ha già preso o spostato."
            ok = False
            continue
        valori.append((riga, valore))
    return valori, ok


def assegna_errori(righe, errore):
    """Distribuisce sulle righe gli errori di ErroreMagazzino. Restituisce quelli generali."""
    generali = []
    for chiave, messaggio in errore.errori:
        trovate = []
        if isinstance(chiave, tuple):
            pid, uid = chiave
            trovate = [
                r for r in righe
                if r.testo and r.prodotto.pk == pid and (r.ubicazione is None or r.ubicazione.pk == uid)
            ]
        elif chiave is not None:
            trovate = [r for r in righe if r.testo and r.prodotto.pk == chiave]
        for r in trovate:
            r.errore = messaggio
        if not trovate:
            generali.append(messaggio)
    return generali


def ordina_per_categoria(prodotti):
    return sorted(prodotti, key=lambda p: (ORDINE_CATEGORIE.get(p.categoria, 999), p.nome.lower()))


def esito_registrazione(request, operazione, creata, testo):
    if creata:
        messages.success(request, testo)
    else:
        messages.info(request, "Questo modulo era già stato registrato: il secondo invio è stato ignorato.")


def vai_a_operazione(operazione):
    if operazione.servizio_id:
        return redirect("servizio_dettaglio", pk=operazione.servizio_id)
    return redirect("operazione_dettaglio", pk=operazione.pk)


def conta_righe(n):
    return "1 riga" if n == 1 else f"{n} righe"


def operazioni_recenti(qs):
    # Con annotate+Count Django ignora Meta.ordering: l'ordine va sempre esplicito.
    return (
        qs.select_related("servizio", "utente")
        .annotate(n_movimenti=Count("movimenti", distinct=True), n_allegati=Count("allegati", distinct=True))
        .order_by("-creata_il", "-pk")
    )
