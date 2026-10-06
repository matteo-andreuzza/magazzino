"""Pagine di consultazione del magazzino del bar."""

from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from magazzino.models import Servizio
from magazzino.quantita import ZERO
from magazzino.views.comuni import giorno_servizio

from .. import services
from ..models import Bevanda, ContaFusti, OperazioneBar, Spina, Spinatrice, TipoBevanda, TipoFusto


def situazione(servizi_oggi):
    """Giacenze del bar e venduto di oggi, pronte per le pagine."""
    giacenze = services.giacenze_bevande()
    pieni = services.fusti_pieni()
    venduti = services.venduto(servizi_oggi) if servizi_oggi else {}
    attaccati = services.fusti_attaccati()
    alla_spina, altre = [], []
    for b in Bevanda.objects.prefetch_related("tipi_fusto"):
        venduto = venduti.get(b.pk, ZERO)
        if b.a_fusti:
            tipi = [(t, pieni.get(t.pk, ZERO)) for t in b.tipi_fusto.all() if t.attivo or pieni.get(t.pk)]
            suoi = [s for s in attaccati if s.tipo.bevanda_id == b.pk]
            if b.attiva or suoi or any(n for _, n in tipi):
                rimasti = [s.litri_rimasti for s in suoi]
                alla_spina.append({
                    "bevanda": b, "tipi": tipi, "attaccati": suoi, "venduto": venduto,
                    "litri_attaccati": sum((r for r in rimasti if r is not None), ZERO) if suoi else None,
                    "tare_provvisorie": [t for t, _ in tipi if t.tara is None or t.tara_provvisoria],
                })
        else:
            giacenza = giacenze.get(b.pk, ZERO)
            if b.attiva or giacenza:
                altre.append({"bevanda": b, "giacenza": giacenza, "venduto": venduto})
    return {"alla_spina": alla_spina, "altre": altre, "negativi": [r for r in altre if r["giacenza"] < ZERO]}


@require_GET
def inizio(request):
    oggi = giorno_servizio()
    servizi_oggi = list(Servizio.objects.filter(data=oggi))
    attaccati = services.fusti_attaccati()
    for s in servizi_oggi:
        s.operabile = services.motivo_sola_lettura(s) is None
        s.da_pesare = services.da_pesare(s, attaccati) if s.operabile else []
    return render(
        request,
        "bar/inizio.html",
        {
            "oggi": oggi,
            "servizi_oggi": servizi_oggi,
            **situazione(servizi_oggi),
            # La serata in corso non è ancora pesata: la sua media sarebbe falsata.
            "scorte": {r["bevanda"].pk: r for r in services.scorte_alla_spina(escludi=[
                s for s in servizi_oggi if s.operabile and s.da_pesare
            ])},
            "vuoto": not Bevanda.objects.exists(),
        },
    )


@require_GET
def consumi(request):
    colonne, righe, totali = services.consumi_per_serata()
    unita = [b.unita for b, _ in colonne]
    return render(
        request,
        "bar/consumi.html",
        {
            "colonne": colonne,
            "righe": [(s, list(zip(valori, unita))) for s, valori in righe],
            "totali": list(zip(totali, unita)),
        },
    )


@require_GET
def storico(request):
    operazioni = OperazioneBar.objects.select_related("utente").annotate(n_movimenti=Count("movimenti")).order_by("-creata_il", "-pk")
    pagina = Paginator(operazioni, 30).get_page(request.GET.get("pagina"))
    conte = (
        ContaFusti.objects.select_related("servizio")
        .annotate(
            n_pesate=Count("pesate", filter=Q(pesate__vuoto=False), distinct=True),
            n_attaccati=Count("fusti_aperti", distinct=True),
            n_finiti=Count("fusti_finiti", distinct=True),
        )
        .order_by("-pk")
    )
    return render(
        request,
        "bar/storico.html",
        {"pagina": pagina, "conte": conte, "ultima": services.ultima_conta()},
    )


@require_GET
def operazione(request, pk):
    op = get_object_or_404(OperazioneBar.objects.select_related("utente"), pk=pk)
    return render(
        request,
        "bar/operazione.html",
        {"operazione": op, "movimenti": op.movimenti.select_related("bevanda", "tipo_fusto")},
    )


@require_GET
def bevande(request):
    return render(
        request,
        "bar/bevande.html",
        {
            "bevande": Bevanda.objects.prefetch_related("articoli"),
            "alla_spina": Bevanda.objects.filter(tipo=TipoBevanda.FUSTI).prefetch_related("tipi_fusto"),
            "tare_provvisorie": TipoFusto.objects.filter(tara_provvisoria=True).exists(),
            "spinatrici": Spinatrice.objects.prefetch_related(Prefetch("spine", queryset=Spina.objects.select_related("bevanda"))),
        },
    )
