"""Pagine di consultazione."""

from collections import defaultdict
from datetime import date

from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from .. import services
from ..models import (
    Ingrediente,
    Menu,
    Movimento,
    Operazione,
    Prodotto,
    Servizio,
    TipoOperazione,
    Ubicazione,
)
from ..quantita import ZERO
from .comuni import giorno_servizio, operazioni_recenti, ordina_per_categoria


def home(request):
    oggi = giorno_servizio()
    return render(
        request,
        "magazzino/home.html",
        {
            "oggi": oggi,
            "servizi_oggi": Servizio.objects.filter(data=oggi),
            "prossimi": Servizio.objects.filter(data__gt=oggi).order_by("data", "tipo")[:3],
            "sotto_scorta": services.prodotti_sotto_scorta(),
            "ultime": operazioni_recenti(Operazione.objects.all())[:6],
        },
    )


def _schede_prodotti(prodotti, giacenze, ubicazioni):
    """[(categoria, [scheda])] con scheda = dict(prodotto, totale, dettagli, sotto_scorta)."""
    per_categoria = defaultdict(list)
    for p in ordina_per_categoria(prodotti):
        dettagli = [(u, giacenze[(p.pk, u.pk)]) for u in ubicazioni if (p.pk, u.pk) in giacenze]
        totale = sum((q for _, q in dettagli), ZERO)
        per_categoria[p.get_categoria_display()].append(
            {
                "prodotto": p,
                "totale": totale,
                "dettagli": dettagli,
                "sotto_scorta": p.scorta_minima is not None and totale < p.scorta_minima,
            }
        )
    return list(per_categoria.items())


def giacenze(request):
    tutti = request.GET.get("tutti") == "1"
    stock = services.giacenze()
    ubicazioni = list(Ubicazione.objects.all())
    con_giacenza = {pid for pid, _ in stock}
    prodotti = [p for p in Prodotto.objects.all() if p.pk in con_giacenza or (tutti and p.attivo)]
    return render(
        request,
        "magazzino/giacenze.html",
        {
            "categorie": _schede_prodotti(prodotti, stock, ubicazioni),
            "tutti": tutti,
            "n_prodotti": len(prodotti),
        },
    )


def ubicazioni(request):
    stock = services.giacenze()
    conteggi = defaultdict(int)
    for (_, uid), q in stock.items():
        if q != ZERO:
            conteggi[uid] += 1
    elenco = [(u, conteggi.get(u.pk, 0)) for u in Ubicazione.objects.all()]
    return render(request, "magazzino/ubicazioni.html", {"elenco": elenco})


def ubicazione_dettaglio(request, pk):
    ubicazione = get_object_or_404(Ubicazione, pk=pk)
    stock = services.giacenze(ubicazioni=[ubicazione.pk])
    prodotti = Prodotto.objects.in_bulk({pid for pid, _ in stock})
    per_categoria = defaultdict(list)
    for p in ordina_per_categoria(prodotti.values()):
        per_categoria[p.get_categoria_display()].append((p, stock[(p.pk, ubicazione.pk)]))
    return render(
        request,
        "magazzino/ubicazione_dettaglio.html",
        {"ubicazione": ubicazione, "categorie": list(per_categoria.items()), "n_prodotti": len(prodotti)},
    )


def _scegli(request, titolo, descrizione, voci, indietro=None):
    return render(
        request,
        "magazzino/scegli_ubicazione.html",
        {"titolo": titolo, "descrizione": descrizione, "voci": voci, "indietro": indietro},
    )


def _conteggi_per_ubicazione():
    conteggi = defaultdict(int)
    for (_, uid), q in services.giacenze().items():
        if q > ZERO:
            conteggi[uid] += 1
    return conteggi


def _descrivi_conteggio(n):
    if n == 0:
        return "vuoto"
    return "1 prodotto" if n == 1 else f"{n} prodotti"


def carico_scelta(request):
    conteggi = _conteggi_per_ubicazione()
    voci = [
        (reverse("carico", args=[u.pk]), u.nome, _descrivi_conteggio(conteggi.get(u.pk, 0)))
        for u in Ubicazione.objects.filter(attiva=True)
    ]
    return _scegli(request, "Carica merce", "Dove stai mettendo la merce?", voci)


def scarto_scelta(request):
    conteggi = _conteggi_per_ubicazione()
    voci = [
        (reverse("scarto", args=[u.pk]), u.nome, _descrivi_conteggio(conteggi[u.pk]))
        for u in Ubicazione.objects.all()
        if conteggi.get(u.pk)
    ]
    return _scegli(request, "Scarto", "Da dove butti la merce?", voci)


def rettifica_scelta(request):
    conteggi = _conteggi_per_ubicazione()
    voci = [
        (reverse("rettifica", args=[u.pk]), u.nome, _descrivi_conteggio(conteggi.get(u.pk, 0)))
        for u in Ubicazione.objects.all()
        if u.attiva or conteggi.get(u.pk)
    ]
    return _scegli(request, "Correggi conteggio", "Quale frigo, congelatore o dispensa hai contato?", voci)


def spostamento_scelta(request):
    conteggi = _conteggi_per_ubicazione()
    voci = [
        (reverse("spostamento_destinazione", args=[u.pk]), u.nome, _descrivi_conteggio(conteggi[u.pk]))
        for u in Ubicazione.objects.all()
        if conteggi.get(u.pk)
    ]
    return _scegli(request, "Sposta merce", "Da dove prendi la merce?", voci)


def spostamento_destinazione(request, da_pk):
    da = get_object_or_404(Ubicazione, pk=da_pk)
    conteggi = _conteggi_per_ubicazione()
    voci = [
        (reverse("spostamento", args=[da.pk, u.pk]), u.nome, _descrivi_conteggio(conteggi.get(u.pk, 0)))
        for u in Ubicazione.objects.filter(attiva=True).exclude(pk=da.pk)
    ]
    return _scegli(
        request, f"Sposta da {da.nome}", "Dove la metti?", voci, indietro=reverse("spostamento_scelta")
    )


def menu(request):
    totali = services.giacenze_totali()
    menu_list = Menu.objects.prefetch_related("piatti__ingredienti__prodotto")
    return render(request, "magazzino/menu.html", {"menu_list": menu_list, "totali": totali})


def prodotti(request):
    totali = services.giacenze_totali()
    per_categoria = defaultdict(list)
    tutti = Prodotto.objects.annotate(n_movimenti=Count("movimenti"))
    for p in ordina_per_categoria(tutti):
        per_categoria[p.get_categoria_display()].append((p, totali.get(p.pk, ZERO)))
    return render(request, "magazzino/prodotti.html", {"categorie": list(per_categoria.items())})


def prodotto_dettaglio(request, pk):
    prodotto = get_object_or_404(Prodotto, pk=pk)
    stock = services.giacenze(prodotti=[prodotto.pk])
    dettagli = [(u, stock[(prodotto.pk, u.pk)]) for u in Ubicazione.objects.all() if (prodotto.pk, u.pk) in stock]
    movimenti = (
        Movimento.objects.filter(prodotto=prodotto)
        .select_related("operazione__servizio", "operazione__utente", "ubicazione")
        .order_by("-operazione__creata_il", "-pk")[:100]
    )
    ingredienti = Ingrediente.objects.filter(prodotto=prodotto).select_related("piatto__menu")
    return render(
        request,
        "magazzino/prodotto_dettaglio.html",
        {
            "prodotto": prodotto,
            "dettagli": dettagli,
            "totale": sum((q for _, q in dettagli), ZERO),
            "movimenti": movimenti,
            "ingredienti": ingredienti,
        },
    )


def servizi(request):
    elenco = Servizio.objects.annotate(
        n_operazioni=Count("operazioni", distinct=True), n_piatti=Count("piatti", distinct=True)
    ).order_by("-data", "tipo", "nome")
    return render(request, "magazzino/servizi.html", {"elenco": elenco, "oggi": giorno_servizio()})


def servizio_dettaglio(request, pk):
    servizio = get_object_or_404(Servizio.objects.select_related("chiuso_da").prefetch_related("piatti__menu"), pk=pk)
    consumi = services.consumi_servizio(servizio)
    prodotti = Prodotto.objects.in_bulk(consumi.keys())
    riepilogo = [
        {
            "prodotto": p,
            "preso": consumi[p.pk]["preso"],
            "rientrato": consumi[p.pk]["rientrato"],
            "usato": consumi[p.pk]["preso"] - consumi[p.pk]["rientrato"],
        }
        for p in ordina_per_categoria(prodotti.values())
    ]
    operazioni = operazioni_recenti(servizio.operazioni.all()).prefetch_related(
        "movimenti__prodotto", "movimenti__ubicazione"
    )
    return render(
        request,
        "magazzino/servizio_dettaglio.html",
        {"servizio": servizio, "riepilogo": riepilogo, "operazioni": operazioni},
    )


def filtra_movimenti(dati):
    """Filtri comuni allo storico e all'esportazione. Restituisce (filtri, Q per i movimenti)."""
    filtri = {k: (dati.get(k) or "").strip() for k in ("tipo", "servizio", "ubicazione", "prodotto", "giorno")}
    condizione = Q()
    if filtri["tipo"] in TipoOperazione.values:
        condizione &= Q(operazione__tipo=filtri["tipo"])
    else:
        filtri["tipo"] = ""
    for campo, chiave in (("servizio", "operazione__servizio_id"), ("ubicazione", "ubicazione_id"), ("prodotto", "prodotto_id")):
        if filtri[campo].isdigit():
            condizione &= Q(**{chiave: int(filtri[campo])})
        else:
            filtri[campo] = ""
    if filtri["giorno"]:
        try:
            giorno = date.fromisoformat(filtri["giorno"])
        except ValueError:
            filtri["giorno"] = ""
        else:
            condizione &= Q(operazione__creata_il__date=giorno)
    return filtri, condizione


def operazioni(request):
    filtri, condizione = filtra_movimenti(request.GET)
    ids = Movimento.objects.filter(condizione).values("operazione_id")
    qs = operazioni_recenti(Operazione.objects.filter(pk__in=ids)).prefetch_related(
        "movimenti__prodotto", "movimenti__ubicazione"
    )
    pagina = Paginator(qs, 30).get_page(request.GET.get("pagina"))
    parametri = request.GET.copy()
    parametri.pop("pagina", None)
    return render(
        request,
        "magazzino/operazioni.html",
        {
            "pagina": pagina,
            "filtri": filtri,
            "parametri": parametri.urlencode(),
            "tipi": TipoOperazione.choices,
            "servizi": Servizio.objects.all(),
            "ubicazioni": Ubicazione.objects.all(),
            "prodotti": Prodotto.objects.all(),
        },
    )


def operazione_dettaglio(request, pk):
    operazione = get_object_or_404(Operazione.objects.select_related("servizio", "utente"), pk=pk)
    movimenti = operazione.movimenti.select_related("prodotto", "ubicazione")
    return render(
        request,
        "magazzino/operazione_dettaglio.html",
        {
            "operazione": operazione,
            "movimenti": movimenti,
            "spostamento": operazione.tipo == TipoOperazione.SPOSTAMENTO,
            # Le operazioni di un servizio terminato non si possono annullare.
            "bloccata": bool(operazione.servizio and operazione.servizio.chiuso),
            "allegati": operazione.allegati.select_related("utente"),
            "allegati_max_mb": settings.ALLEGATI_MAX_MB,
        },
    )


def dati_riepilogo():
    """Consumo netto (messo a cucinare - rientrato) per prodotto e servizio."""
    servizi_elenco = list(Servizio.objects.order_by("data", "tipo", "nome"))
    righe_db = (
        Movimento.objects.filter(operazione__tipo__in=[TipoOperazione.CUCINA, TipoOperazione.RESO])
        .values("prodotto_id", "operazione__servizio_id")
        .annotate(totale=Sum("quantita"))
        .order_by()
    )
    usato = defaultdict(dict)
    for r in righe_db:
        usato[r["prodotto_id"]][r["operazione__servizio_id"]] = -r["totale"]
    scarti = {
        r["prodotto_id"]: -r["totale"]
        for r in Movimento.objects.filter(operazione__tipo=TipoOperazione.SCARTO)
        .values("prodotto_id")
        .annotate(totale=Sum("quantita"))
        .order_by()
    }
    totali = services.giacenze_totali()
    prodotti_ids = set(usato) | set(scarti)
    righe = []
    for p in ordina_per_categoria(Prodotto.objects.filter(pk__in=prodotti_ids)):
        valori = [usato[p.pk].get(s.pk) for s in servizi_elenco] if p.pk in usato else [None] * len(servizi_elenco)
        righe.append(
            {
                "prodotto": p,
                "valori": valori,
                "totale": sum((v for v in valori if v is not None), ZERO),
                "scartato": scarti.get(p.pk),
                "giacenza": totali.get(p.pk, ZERO),
            }
        )
    servizi_usati = {sid for per_servizio in usato.values() for sid in per_servizio}
    colonne = [i for i, s in enumerate(servizi_elenco) if s.pk in servizi_usati]
    servizi_elenco = [servizi_elenco[i] for i in colonne]
    for riga in righe:
        riga["valori"] = [riga["valori"][i] for i in colonne]
    return servizi_elenco, righe


def riepilogo(request):
    servizi_elenco, righe = dati_riepilogo()
    return render(request, "magazzino/riepilogo.html", {"servizi": servizi_elenco, "righe": righe})

