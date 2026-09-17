"""Prodotti e servizi (serate/pranzi)."""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from .. import services
from ..forms import ProdottoForm, ServizioForm
from ..models import Prodotto, Servizio
from .comuni import giorno_servizio, ordina_per_categoria


def _prossimo(request, predefinito):
    prossimo = request.POST.get("next") or request.GET.get("next")
    if prossimo and url_has_allowed_host_and_scheme(prossimo, allowed_hosts={request.get_host()}):
        return prossimo
    return predefinito


@require_http_methods(["GET", "POST"])
def prodotto_nuovo(request):
    form = ProdottoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        prodotto = form.save()
        messages.success(request, f"Prodotto «{prodotto.nome}» creato.")
        return redirect(_prossimo(request, "prodotti"))
    return render(
        request,
        "magazzino/prodotto_form.html",
        {"form": form, "titolo": "Nuovo prodotto", "next": _prossimo(request, "")},
    )


@require_http_methods(["GET", "POST"])
def prodotto_modifica(request, pk):
    prodotto = get_object_or_404(Prodotto, pk=pk)
    form = ProdottoForm(request.POST or None, instance=prodotto)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Prodotto «{prodotto.nome}» salvato.")
        return redirect("prodotto_dettaglio", pk=prodotto.pk)
    return render(
        request,
        "magazzino/prodotto_form.html",
        {"form": form, "titolo": f"Modifica {prodotto.nome}", "prodotto": prodotto},
    )


@require_http_methods(["GET", "POST"])
def servizio_nuovo(request):
    iniziale = {"data": giorno_servizio(), "tipo": request.GET.get("tipo", "sera")}
    form = ServizioForm(request.POST or None, initial=iniziale)
    if request.method == "POST" and form.is_valid():
        servizio = form.save()
        messages.success(request, f"Creato: {servizio}.")
        return redirect("servizio_dettaglio", pk=servizio.pk)
    return render(request, "magazzino/servizio_form.html", {"form": form, "titolo": "Nuovo servizio"})


@require_http_methods(["GET", "POST"])
def servizio_modifica(request, pk):
    servizio = get_object_or_404(Servizio, pk=pk)
    form = ServizioForm(request.POST or None, instance=servizio)
    if request.method == "POST" and form.is_valid():
        servizio = form.save(commit=False)
        # Salva solo i campi del modulo: non deve riaprire un servizio terminato nel frattempo.
        servizio.save(update_fields=[f for f in form.Meta.fields if f != "piatti"])
        form.save_m2m()
        messages.success(request, "Servizio salvato.")
        return redirect("servizio_dettaglio", pk=servizio.pk)
    return render(
        request,
        "magazzino/servizio_form.html",
        {"form": form, "titolo": "Modifica servizio", "servizio": servizio},
    )


@require_http_methods(["GET", "POST"])
def servizio_elimina(request, pk):
    servizio = get_object_or_404(Servizio, pk=pk)
    ha_operazioni = servizio.operazioni.exists()
    if request.method == "POST":
        if ha_operazioni:
            messages.error(request, "Non si può eliminare: ci sono movimenti registrati. Annullali prima.")
            return redirect("servizio_dettaglio", pk=servizio.pk)
        nome = str(servizio)
        servizio.delete()
        messages.success(request, f"Eliminato: {nome}.")
        return redirect("servizi")
    return render(
        request, "magazzino/servizio_elimina.html", {"servizio": servizio, "ha_operazioni": ha_operazioni}
    )


@require_http_methods(["GET", "POST"])
def servizio_chiudi(request, pk):
    servizio = get_object_or_404(Servizio, pk=pk)
    d = servizio.desinenza
    if request.method == "POST":
        if services.chiudi_servizio(servizio=servizio, utente=request.user):
            messages.success(request, f"{servizio.get_tipo_display()} terminat{d}.")
        else:
            messages.info(request, f"{servizio.get_tipo_display()} era già terminat{d}.")
        return redirect("servizio_dettaglio", pk=servizio.pk)
    if servizio.chiuso:
        messages.info(request, f"{servizio.get_tipo_display()} è già terminat{d}.")
        return redirect("servizio_dettaglio", pk=servizio.pk)
    consumi = services.consumi_servizio(servizio)
    prodotti = Prodotto.objects.in_bulk(consumi.keys())
    non_rientrati = [
        (p, consumi[p.pk]["preso"] - consumi[p.pk]["rientrato"])
        for p in ordina_per_categoria(prodotti.values())
        if consumi[p.pk]["preso"] > consumi[p.pk]["rientrato"]
    ]
    return render(
        request, "magazzino/servizio_chiudi.html", {"servizio": servizio, "non_rientrati": non_rientrati}
    )


@require_POST
def servizio_riapri(request, pk):
    servizio = get_object_or_404(Servizio, pk=pk)
    d = servizio.desinenza
    if services.riapri_servizio(servizio=servizio):
        messages.success(request, f"{servizio.get_tipo_display()} riapert{d}: si possono di nuovo registrare prelievi e rientri.")
    else:
        messages.info(request, f"{servizio.get_tipo_display()} era già apert{d}.")
    return redirect("servizio_dettaglio", pk=servizio.pk)
