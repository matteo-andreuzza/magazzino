"""Ordini della cassa.

Come per il magazzino, tutte le scritture passano da qui:

* un ordine si registra tutto o niente, insieme alle sue comande e stampe;
* il numero progressivo si calcola con la serata bloccata, così con più casse
  non escono numeri doppi (e nessuno termina la serata a metà registrazione);
* il secondo invio dello stesso ordine (stesso token) viene ignorato;
* i prezzi si leggono dal listino al momento dell'invio: se nel frattempo sono
  cambiati l'ordine viene rifiutato, così il totale detto al cliente è quello
  registrato.
"""

from dataclasses import dataclass
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Max, Q
from django.utils import timezone

from magazzino.models import Servizio

from . import stampa
from .formati import euro
from .models import (
    Articolo,
    Comanda,
    Documento,
    ImpostazioniCassa,
    Ordine,
    Pagamento,
    Postazione,
    Reparto,
    RigaOrdine,
    StatoComanda,
)

MAX_QUANTITA = 999
MAX_RIGHE = 80
# Gli omaggi ai volontari non hanno un cliente né un tavolo.
NOME_OMAGGIO = "Volontario"
TAVOLO_OMAGGIO = "XXX"
# Gli ordini da asporto non hanno un tavolo: sulla comanda e negli elenchi c'è «ASP».
TAVOLO_ASPORTO = "ASP"


class ErroreCassa(Exception):
    """Operazione rifiutata: non è stato registrato niente.

    `ricarica` indica che il listino mostrato in cassa non è più aggiornato.
    """

    def __init__(self, messaggi, *, ricarica=False):
        if isinstance(messaggi, str):
            messaggi = [messaggi]
        self.messaggi = list(messaggi)
        self.ricarica = ricarica
        super().__init__("\n".join(self.messaggi))


def impostazioni():
    return ImpostazioniCassa.objects.get_or_create(pk=1)[0]


@dataclass(frozen=True)
class Voce:
    articolo_id: int
    quantita: int
    note: str = ""


def _pulisci(testo, lunghezza):
    return " ".join(str(testo or "").split())[:lunghezza]


def articoli_in_vendita(servizio, postazione):
    """Articoli che la postazione vende in questo servizio (esauriti compresi).

    Se la serata ha i suoi piatti, gli articoli collegati ad altri piatti del
    menu non compaiono (es. la paella nelle sere senza paella).
    """
    qs = Articolo.objects.filter(attivo=True, prezzo__isnull=False).select_related("categoria")
    if postazione == Postazione.BAR:
        qs = qs.filter(reparto=Reparto.BAR)
    piatti = list(servizio.piatti.values_list("pk", flat=True))
    if piatti:
        qs = qs.filter(Q(piatto__isnull=True) | Q(piatto__in=piatti))
    return qs


def _unisci_voci(voci):
    """Somma le voci uguali (stesso articolo e stessa nota), mantenendo l'ordine."""
    unite = {}
    for v in voci:
        chiave = (v.articolo_id, _pulisci(v.note, 60))
        unite[chiave] = unite.get(chiave, 0) + v.quantita
    return [Voce(articolo_id=a, quantita=q, note=n) for (a, n), q in unite.items()]


def _servizio_aperto(servizio_id, messaggio):
    s = Servizio.objects.select_for_update(no_key=True).filter(pk=servizio_id).first()
    if s is None:
        raise ErroreCassa("Questa serata non esiste più.")
    if s.chiuso:
        d = s.desinenza
        raise ErroreCassa(f"{s.get_tipo_display()} terminat{d}: {messaggio} bisogna prima riaprirl{d} dal magazzino.")
    if s.cassa_chiusa:
        raise ErroreCassa(f"La cassa è chiusa: {messaggio} bisogna prima riaprirla.")
    return s


def crea_ordine(
    *, servizio, postazione, voci, pagamento, utente, nome="", tavolo="", coperti=None, note="",
    totale_atteso=None, token=None, asporto=False,
):
    """Registra un ordine con comande e stampe. Restituisce (ordine, creato).

    Da asporto (solo alla cassa dell'ingresso) il tavolo è «ASP» e non ci sono coperti.
    """
    if postazione not in Postazione.values:
        raise ValueError(f"Postazione non valida: {postazione}")
    nome, tavolo, note = _pulisci(nome, 40), _pulisci(tavolo, 10), _pulisci(note, 200)
    asporto = bool(asporto) and postazione == Postazione.INGRESSO
    if asporto:
        tavolo, coperti = TAVOLO_ASPORTO, None
    voci = [v for v in voci if v.quantita]

    if token is not None:
        esistente = Ordine.objects.filter(token=token).first()
        if esistente is not None:
            return esistente, False

    imp = impostazioni()
    errori = []
    if postazione == Postazione.BAR and not imp.cassa_bar:
        raise ErroreCassa("La cassa al bar non è attiva: si attiva dalle impostazioni della cassa.")
    if postazione == Postazione.INGRESSO and pagamento == Pagamento.OMAGGIO:
        nome, tavolo = nome or NOME_OMAGGIO, tavolo or TAVOLO_OMAGGIO
    if postazione == Postazione.INGRESSO:
        if not nome:
            errori.append("Manca il nome del cliente.")
        if not tavolo:
            errori.append("Manca il numero del tavolo.")
    if pagamento not in Pagamento.values:
        errori.append("Scegli come paga il cliente.")
    if coperti is not None and not 0 < coperti <= 500:
        errori.append("Il numero di coperti non è valido.")
    if not voci:
        errori.append("L'ordine è vuoto.")
    voci = _unisci_voci(voci)
    if len(voci) > MAX_RIGHE:
        errori.append(f"Troppe righe: al massimo {MAX_RIGHE} per ordine.")
    if any(not 0 < v.quantita <= MAX_QUANTITA for v in voci):
        errori.append(f"Le quantità vanno da 1 a {MAX_QUANTITA}.")
    if errori:
        raise ErroreCassa(errori)

    try:
        with transaction.atomic():
            s = _servizio_aperto(servizio.pk, "per registrare altri ordini")
            ids = {v.articolo_id for v in voci}
            vendibili = articoli_in_vendita(s, postazione).in_bulk(ids)
            for v in voci:
                articolo = vendibili.get(v.articolo_id)
                if articolo is None:
                    nome_articolo = Articolo.objects.filter(pk=v.articolo_id).values_list("nome", flat=True).first()
                    errori.append(f"«{nome_articolo}» non è in vendita." if nome_articolo else "Un articolo non esiste più.")
                elif articolo.esaurito:
                    errori.append(f"«{articolo.nome}» è esaurito.")
            if errori:
                raise ErroreCassa(errori, ricarica=True)

            totale = sum((vendibili[v.articolo_id].prezzo * v.quantita for v in voci), Decimal("0"))
            if totale_atteso is not None and totale != totale_atteso:
                raise ErroreCassa(
                    f"Nel frattempo il listino è cambiato: il totale adesso è {euro(totale)}. "
                    "Controlla con il cliente e conferma di nuovo.",
                    ricarica=True,
                )

            numero = (Ordine.objects.filter(servizio=s).aggregate(m=Max("numero"))["m"] or 0) + 1
            ordine = Ordine.objects.create(
                servizio=s, numero=numero, postazione=postazione, nome=nome, tavolo=tavolo, asporto=asporto, coperti=coperti,
                note=note, pagamento=pagamento, totale=totale, utente=utente, token=token,
            )
            righe = RigaOrdine.objects.bulk_create(_riga(ordine, vendibili[v.articolo_id], v) for v in voci)
            reparti = [r for r in Reparto.values if any(riga.reparto == r for riga in righe)]
            Comanda.objects.bulk_create(Comanda(ordine=ordine, reparto=r) for r in reparti)
            stampa.accoda_ordine(ordine, righe, imp)
    except IntegrityError:
        # Due invii dello stesso ordine arrivati insieme: il secondo trova il token già usato.
        if token is not None:
            esistente = Ordine.objects.filter(token=token).first()
            if esistente is not None:
                return esistente, False
        raise
    return ordine, True


def _riga(ordine, articolo, voce):
    return RigaOrdine(
        ordine=ordine, articolo=articolo, descrizione=articolo.nome, reparto=articolo.reparto, prezzo=articolo.prezzo,
        quantita=voce.quantita, note=voce.note,
        bevanda_id=articolo.bevanda_id, quantita_bevanda=articolo.quantita_bevanda if articolo.bevanda_id else None,
    )


def annulla_ordine(*, ordine, utente, motivo=""):
    """Annulla un ordine: non conta più negli incassi e in cucina esce «ANNULLATO».

    Restituisce (ordine, annullato_adesso).
    """
    with transaction.atomic():
        _servizio_aperto(ordine.servizio_id, "per annullare un ordine")
        o = Ordine.objects.select_for_update().filter(pk=ordine.pk).first()
        if o is None:
            raise ErroreCassa("Questo ordine non esiste più.")
        if o.annullato:
            return o, False
        o.annullato_il = timezone.now()
        o.annullato_da = utente
        o.motivo_annullamento = _pulisci(motivo, 200)
        o.save(update_fields=["annullato_il", "annullato_da", "motivo_annullamento"])
        stampa.accoda_annullo(o, list(o.righe.all()), impostazioni())
    return o, True


def chiudi_cassa(*, servizio, utente):
    """Fine serata: la cassa (ingresso e bar) non registra più ordini né annullamenti.

    Il magazzino resta aperto per i rientri e la pesata dei fusti.
    Restituisce True se è stata chiusa adesso, False se lo era già.
    """
    with transaction.atomic():
        s = Servizio.objects.select_for_update(no_key=True).get(pk=servizio.pk)
        if s.cassa_chiusa:
            return False
        s.cassa_chiusa_il = timezone.now()
        s.cassa_chiusa_da = utente
        s.save(update_fields=["cassa_chiusa_il", "cassa_chiusa_da"])
    return True


def riapri_cassa(*, servizio):
    """Restituisce True se è stata riaperta adesso, False se era già aperta."""
    with transaction.atomic():
        s = Servizio.objects.select_for_update(no_key=True).get(pk=servizio.pk)
        if not s.cassa_chiusa:
            return False
        s.cassa_chiusa_il = None
        s.cassa_chiusa_da = None
        s.save(update_fields=["cassa_chiusa_il", "cassa_chiusa_da"])
    return True


def ristampa(*, ordine, cosa):
    """Stampa di nuovo la comanda di un reparto ("cucina", "bar") o la ricevuta ("ricevuta")."""
    if ordine.annullato:
        raise ErroreCassa("L'ordine è annullato: non si ristampa.")
    imp = impostazioni()
    if stampa.stampanti_ordine(ordine, imp, cosa) is None:
        raise ErroreCassa("Non c'è una stampante attiva impostata per questo biglietto (vedi Impostazioni).")
    righe = list(ordine.righe.all())
    if cosa in Reparto.values and not any(r.reparto == cosa for r in righe):
        raise ErroreCassa("In questo ordine non c'è niente per questo reparto.")
    return stampa.accoda_ordine(ordine, righe, imp, solo=cosa, copia=True)


# ---------------------------------------------------------------------------
# Monitor cucina
# ---------------------------------------------------------------------------

AZIONI_COMANDA = {"inizia", "pronta", "ritira", "indietro"}


def cambia_stato_comanda(*, comanda, azione):
    """Avanza (o riporta indietro di un passo) una comanda. Ripetere un'azione non fa niente."""
    if azione not in AZIONI_COMANDA:
        raise ValueError(f"Azione non valida: {azione}")
    with transaction.atomic():
        c = Comanda.objects.select_for_update(of=("self",)).select_related("ordine").get(pk=comanda.pk)
        adesso = timezone.now()
        if azione == "inizia" and c.stato == StatoComanda.NUOVA:
            c.stato, c.iniziata_il = StatoComanda.IN_PREPARAZIONE, adesso
        elif azione == "pronta" and c.stato in (StatoComanda.NUOVA, StatoComanda.IN_PREPARAZIONE):
            c.stato, c.pronta_il = StatoComanda.PRONTA, adesso
            c.iniziata_il = c.iniziata_il or adesso
        elif azione == "ritira" and (c.stato == StatoComanda.PRONTA or (c.ordine.annullato and c.stato != StatoComanda.RITIRATA)):
            c.stato, c.ritirata_il = StatoComanda.RITIRATA, adesso
        elif azione == "indietro":
            if c.stato == StatoComanda.RITIRATA:
                c.ritirata_il = None
                c.stato = (
                    StatoComanda.PRONTA if c.pronta_il
                    else StatoComanda.IN_PREPARAZIONE if c.iniziata_il
                    else StatoComanda.NUOVA
                )
            elif c.stato == StatoComanda.PRONTA:
                c.stato, c.pronta_il = StatoComanda.IN_PREPARAZIONE, None
            elif c.stato == StatoComanda.IN_PREPARAZIONE:
                c.stato, c.iniziata_il = StatoComanda.NUOVA, None
        c.save(update_fields=["stato", "iniziata_il", "pronta_il", "ritirata_il"])
    return c


# ---------------------------------------------------------------------------
# Listino e impostazioni
# ---------------------------------------------------------------------------


def imposta_esaurito(*, articolo, esaurito):
    Articolo.objects.filter(pk=articolo.pk).update(esaurito=bool(esaurito))
    articolo.esaurito = bool(esaurito)
    return articolo


def salva_impostazioni(imp):
    """Salva le impostazioni; quando si accende il monitor cucina ricorda da quando
    (le comande precedenti, mai gestite a schermo, non compaiono)."""
    with transaction.atomic():
        prima = ImpostazioniCassa.objects.select_for_update().filter(pk=imp.pk).values_list("monitor_cucina", flat=True).first()
        if imp.monitor_cucina and not prima:
            imp.monitor_cucina_dal = timezone.now()
        imp.save()
    return imp


def rigenera_token_agente(imp):
    from .models import genera_token

    imp.token_agente = genera_token()
    imp.save(update_fields=["token_agente", "modificate_il"])
    return imp.token_agente


def documenti_ristampabili(ordine):
    """[(cosa, etichetta)] per i pulsanti di ristampa."""
    reparti = set(ordine.righe.values_list("reparto", flat=True))
    voci = [(r, f"Comanda {Reparto(r).label.lower()}") for r in Reparto.values if r in reparti]
    voci.append(("ricevuta", Documento.RICEVUTA.label))
    return voci
