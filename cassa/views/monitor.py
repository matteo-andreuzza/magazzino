"""Monitor della cucina (funzione smart)."""

from collections import defaultdict
from datetime import timedelta

from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from magazzino.models import Servizio

from .. import services
from ..models import Comanda, Reparto, RigaOrdine, StatoComanda
from ..services import impostazioni
from .comuni import funzione_attiva

# Le comande pronte restano a schermo finché non vengono ritirate, al massimo mezz'ora.
PRONTE_VISIBILI = timedelta(minutes=30)
# Gli ordini annullati restano a schermo (barrati) per 20 minuti, o finché non si tolgono.
ANNULLATE_VISIBILI = timedelta(minutes=20)


@require_GET
@funzione_attiva("monitor_cucina")
def monitor(request, servizio_pk):
    servizio = get_object_or_404(Servizio, pk=servizio_pk)
    return render(
        request,
        "cassa/monitor.html",
        {
            "servizio": servizio,
            "dati": {
                "url": {
                    "dati": reverse("cassa:monitor_dati", args=[servizio.pk]),
                    "azione": reverse("cassa:comanda_azione", args=[0, "AZIONE"]),
                },
            },
        },
    )


def _iso(momento):
    return momento.isoformat() if momento else None


def dati_monitor(servizio, reparto=Reparto.CUCINA):
    imp = impostazioni()
    adesso = timezone.now()
    comande = Comanda.objects.filter(ordine__servizio=servizio, reparto=reparto).select_related("ordine")
    if imp.monitor_cucina_dal:
        comande = comande.filter(ordine__creato_il__gte=imp.monitor_cucina_dal)
    valide = Q(ordine__annullato_il__isnull=True)
    comande = list(
        comande.filter(
            (valide & Q(stato__in=[StatoComanda.NUOVA, StatoComanda.IN_PREPARAZIONE]))
            | (valide & Q(stato=StatoComanda.PRONTA, pronta_il__gte=adesso - PRONTE_VISIBILI))
            | (Q(ordine__annullato_il__gte=adesso - ANNULLATE_VISIBILI) & ~Q(stato=StatoComanda.RITIRATA))
        ).order_by("ordine__creato_il", "pk")
    )
    righe = defaultdict(list)
    for r in RigaOrdine.objects.filter(ordine_id__in=[c.ordine_id for c in comande], reparto=reparto).order_by("pk"):
        righe[r.ordine_id].append(r)
    da_preparare = defaultdict(int)
    elenco = []
    for c in comande:
        o = c.ordine
        if not o.annullato and c.stato in (StatoComanda.NUOVA, StatoComanda.IN_PREPARAZIONE):
            for r in righe[o.pk]:
                da_preparare[r.descrizione] += r.quantita
        elenco.append({
            "id": c.pk,
            "numero": o.numero,
            "tavolo": o.tavolo,
            "asporto": o.asporto,
            "nome": o.nome,
            "note": o.note,
            "coperti": o.coperti,
            "stato": c.stato,
            "annullato": o.annullato,
            "creato": _iso(o.creato_il),
            "iniziata": _iso(c.iniziata_il),
            "pronta": _iso(c.pronta_il),
            "righe": [{"q": r.quantita, "t": r.descrizione, "n": r.note} for r in righe[o.pk]],
        })
    return {
        "ok": True,
        "ora": _iso(adesso),
        "comande": elenco,
        "daPreparare": [{"t": t, "q": q} for t, q in sorted(da_preparare.items(), key=lambda x: (-x[1], x[0].lower()))],
    }


@require_GET
@funzione_attiva("monitor_cucina", json_=True)
def monitor_dati(request, servizio_pk):
    servizio = get_object_or_404(Servizio, pk=servizio_pk)
    return JsonResponse(dati_monitor(servizio))


@require_POST
@funzione_attiva("monitor_cucina", json_=True)
def comanda_azione(request, pk, azione):
    comanda = get_object_or_404(Comanda, pk=pk)
    if azione not in services.AZIONI_COMANDA:
        return JsonResponse({"ok": False, "errori": ["Azione non valida."]}, status=400)
    comanda = services.cambia_stato_comanda(comanda=comanda, azione=azione)
    return JsonResponse({"ok": True, "stato": comanda.stato})
