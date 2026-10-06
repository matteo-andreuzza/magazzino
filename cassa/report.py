"""Numeri della cassa: report della serata, statistiche della sagra e
collegamento con i consumi del magazzino."""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.utils import timezone

from bar import services as bar
from magazzino.models import Ingrediente, Movimento, Prodotto, Servizio, TipoOperazione
from magazzino.quantita import ZERO

from .models import Articolo, Comanda, Ordine, Pagamento, Postazione, Reparto, RigaOrdine

EURO_ZERO = Decimal("0.00")
IMPORTO = ExpressionWrapper(F("prezzo") * F("quantita"), output_field=DecimalField(max_digits=12, decimal_places=2))
NON_OMAGGIO = ~Q(ordine__pagamento=Pagamento.OMAGGIO)


def _ordine_listino(articolo):
    return (articolo.categoria.ordine, articolo.categoria.nome.lower(), articolo.ordine, articolo.nome.lower())


def _vuoto():
    return {
        "ordini": 0, "coperti": 0, "contanti": EURO_ZERO, "carta": EURO_ZERO, "incassato": EURO_ZERO,
        "omaggi": EURO_ZERO, "n_omaggi": 0, "paganti": 0, "scontrino_medio": None, "asporto": 0,
    }


def _completa(dati):
    dati["incassato"] = dati["contanti"] + dati["carta"]
    if dati["paganti"]:
        dati["scontrino_medio"] = (dati["incassato"] / dati["paganti"]).quantize(Decimal("0.01"))
    return dati


def incassi(ordini):
    """Totali di un queryset di ordini (non annullati) divisi per pagamento."""
    dati = _vuoto()
    for r in ordini.values("pagamento").annotate(
        n=Count("pk"), somma=Sum("totale"), persone=Sum("coperti"), asporto=Count("pk", filter=Q(asporto=True)),
    ).order_by():
        dati["ordini"] += r["n"]
        dati["asporto"] += r["asporto"]
        dati["coperti"] += r["persone"] or 0
        if r["pagamento"] == Pagamento.OMAGGIO:
            dati["omaggi"] += r["somma"]
            dati["n_omaggi"] += r["n"]
        else:
            dati[r["pagamento"]] += r["somma"]
            dati["paganti"] += r["n"]
    return _completa(dati)


def riepilogo_breve(servizio):
    """Per le pagine del magazzino. None se nella serata non ci sono ordini."""
    n = Ordine.objects.filter(servizio=servizio, annullato_il__isnull=True).count()
    if not n and not Ordine.objects.filter(servizio=servizio).exists():
        return None
    return {"ordini": n}


def venduto_articoli(righe):
    """Quantità e importo per articolo, nell'ordine del listino."""
    dati = righe.values("articolo_id").annotate(
        pezzi=Sum("quantita"),
        pezzi_omaggio=Sum("quantita", filter=~NON_OMAGGIO),
        incasso=Sum(IMPORTO, filter=NON_OMAGGIO),
    ).order_by()
    articoli = Articolo.objects.select_related("categoria").in_bulk([d["articolo_id"] for d in dati])
    risultato = [
        {
            "articolo": articoli[d["articolo_id"]],
            "quantita": d["pezzi"],
            "omaggi": d["pezzi_omaggio"] or 0,
            "importo": d["incasso"] or EURO_ZERO,
        }
        for d in dati
    ]
    risultato.sort(key=lambda r: _ordine_listino(r["articolo"]))
    return risultato


def per_categoria(venduti):
    gruppi = {}
    for r in venduti:
        c = r["articolo"].categoria
        g = gruppi.setdefault(c.pk, {"categoria": c, "quantita": 0, "importo": EURO_ZERO})
        g["quantita"] += r["quantita"]
        g["importo"] += r["importo"]
    return list(gruppi.values())


def andamento(ordini, minuti=15):
    """Ordini e incasso ogni `minuti`, dal primo all'ultimo ordine (orari locali)."""
    valori = list(ordini.values_list("creato_il", "totale", "pagamento").order_by("creato_il"))
    if not valori:
        return []

    def fascia(momento):
        locale = timezone.localtime(momento)
        return locale.replace(minute=locale.minute - locale.minute % minuti, second=0, microsecond=0)

    fasce = {}
    inizio, fine = fascia(valori[0][0]), fascia(valori[-1][0])
    corrente = inizio
    while corrente <= fine:
        fasce[corrente] = {"inizio": corrente, "ordini": 0, "incasso": EURO_ZERO}
        corrente += timedelta(minutes=minuti)
    for momento, totale, pagamento in valori:
        f = fasce[fascia(momento)]
        f["ordini"] += 1
        if pagamento != Pagamento.OMAGGIO:
            f["incasso"] += totale
    return list(fasce.values())


def _minuti(durate):
    return round(sum(d.total_seconds() for d in durate) / len(durate) / 60, 1)


def tempi_cucina(servizio):
    """Tempi del monitor cucina (solo comande segnate pronte a schermo)."""
    valori = list(
        Comanda.objects.filter(
            ordine__servizio=servizio, ordine__annullato_il__isnull=True, reparto=Reparto.CUCINA,
            pronta_il__isnull=False, iniziata_il__isnull=False,
        ).values_list("ordine__creato_il", "iniziata_il", "pronta_il")
    )
    if not valori:
        return None
    totali = [pronta - creato for creato, _, pronta in valori]
    return {
        "comande": len(valori),
        "attesa": _minuti([iniziata - creato for creato, iniziata, _ in valori]),
        "preparazione": _minuti([pronta - iniziata for _, iniziata, pronta in valori]),
        "totale": _minuti(totali),
        "massimo": round(max(totali).total_seconds() / 60),
    }


def consumo_per_porzione(servizi):
    """Per ogni prodotto: quanto ne è uscito dal magazzino per la cucina (preso - rientrato)
    e quante porzioni sono state vendute dei piatti che lo usano come ingrediente.

    È una stima: se un prodotto serve a più piatti il consumo viene diviso su tutte le loro porzioni.
    """
    servizi = list(servizi)
    porzioni_piatto = dict(
        RigaOrdine.objects.filter(
            ordine__servizio__in=servizi, ordine__annullato_il__isnull=True, articolo__piatto__isnull=False
        )
        .values("articolo__piatto_id")
        .annotate(q=Sum("quantita"))
        .values_list("articolo__piatto_id", "q")
        .order_by()
    )
    if not porzioni_piatto:
        return []
    usato = defaultdict(lambda: ZERO)
    for r in (
        Movimento.objects.filter(
            operazione__servizio__in=servizi, operazione__tipo__in=[TipoOperazione.CUCINA, TipoOperazione.RESO]
        )
        .values("prodotto_id")
        .annotate(totale=Sum("quantita"))
        .order_by()
    ):
        usato[r["prodotto_id"]] -= r["totale"]
    porzioni = defaultdict(int)
    piatti = defaultdict(list)
    for ing in Ingrediente.objects.filter(piatto_id__in=porzioni_piatto).select_related("piatto"):
        porzioni[ing.prodotto_id] += porzioni_piatto[ing.piatto_id]
        piatti[ing.prodotto_id].append(ing.piatto.nome)
    prodotti = Prodotto.objects.in_bulk([pid for pid in porzioni if usato[pid] > ZERO])
    righe = [
        {
            "prodotto": p,
            "usato": usato[p.pk],
            "porzioni": porzioni[p.pk],
            "per_porzione": (usato[p.pk] / porzioni[p.pk]).quantize(Decimal("0.001")),
            "piatti": sorted(set(piatti[p.pk])),
        }
        for p in prodotti.values()
    ]
    righe.sort(key=lambda r: r["prodotto"].nome.lower())
    return righe


def report_servizio(servizio):
    tutti = Ordine.objects.filter(servizio=servizio)
    validi = tutti.filter(annullato_il__isnull=True)
    annullati = tutti.filter(annullato_il__isnull=False).aggregate(n=Count("pk"), totale=Sum("totale"))
    postazioni = [
        (Postazione(p).label, incassi(validi.filter(postazione=p)))
        for p in validi.values_list("postazione", flat=True).distinct().order_by()
    ]
    venduti = venduto_articoli(RigaOrdine.objects.filter(ordine__in=validi))
    orari = validi.values_list("creato_il", flat=True)
    return {
        "totali": incassi(validi),
        "annullati": {"n": annullati["n"], "totale": annullati["totale"] or EURO_ZERO},
        "postazioni": postazioni if len(postazioni) > 1 else [],
        "venduti": venduti,
        "categorie": per_categoria(venduti),
        "andamento": andamento(validi),
        "orari": {"primo": orari.order_by("creato_il").first(), "ultimo": orari.order_by("-creato_il").first()},
        "cucina": tempi_cucina(servizio),
        "consumi": consumo_per_porzione([servizio]),
        "bar": bar.riepilogo_servizio(servizio),
    }


def statistiche():
    """Tutte le serate con ordini: totali per serata e venduto per articolo."""
    servizi = list(Servizio.objects.filter(ordini__isnull=False).distinct().order_by("data", "tipo", "nome"))
    per_servizio = {s.pk: _vuoto() for s in servizi}
    for r in (
        Ordine.objects.filter(annullato_il__isnull=True)
        .values("servizio_id", "pagamento")
        .annotate(n=Count("pk"), somma=Sum("totale"), persone=Sum("coperti"), asporto=Count("pk", filter=Q(asporto=True)))
        .order_by()
    ):
        d = per_servizio[r["servizio_id"]]
        d["ordini"] += r["n"]
        d["asporto"] += r["asporto"]
        d["coperti"] += r["persone"] or 0
        if r["pagamento"] == Pagamento.OMAGGIO:
            d["omaggi"] += r["somma"]
            d["n_omaggi"] += r["n"]
        else:
            d[r["pagamento"]] += r["somma"]
            d["paganti"] += r["n"]
    serate = [{"servizio": s, **_completa(per_servizio[s.pk])} for s in servizi]

    totale = _vuoto()
    for riga in serate:
        for chiave in ("ordini", "coperti", "contanti", "carta", "omaggi", "n_omaggi", "paganti", "asporto"):
            totale[chiave] += riga[chiave]
    _completa(totale)

    colonne = [s.pk for s in servizi]
    matrice = defaultdict(lambda: {"valori": dict.fromkeys(colonne, 0), "quantita": 0, "importo": EURO_ZERO})
    for r in (
        RigaOrdine.objects.filter(ordine__annullato_il__isnull=True)
        .values("articolo_id", "ordine__servizio_id")
        .annotate(q=Sum("quantita"), importo=Sum(IMPORTO, filter=NON_OMAGGIO))
        .order_by()
    ):
        m = matrice[r["articolo_id"]]
        m["valori"][r["ordine__servizio_id"]] = r["q"]
        m["quantita"] += r["q"]
        m["importo"] += r["importo"] or EURO_ZERO
    articoli = Articolo.objects.select_related("categoria").in_bulk(list(matrice))
    righe = [
        {"articolo": articoli[aid], "valori": [m["valori"][c] for c in colonne], "quantita": m["quantita"], "importo": m["importo"]}
        for aid, m in matrice.items()
    ]
    righe.sort(key=lambda r: _ordine_listino(r["articolo"]))
    return {
        "serate": serate,
        "totale": totale,
        "servizi": servizi,
        "articoli": righe,
        "consumi": consumo_per_porzione(servizi),
    }
