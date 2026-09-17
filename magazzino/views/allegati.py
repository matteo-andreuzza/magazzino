"""Fatture e bolle allegate ai carichi."""

from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from .. import services
from ..models import Allegato, Operazione
from ..services import ErroreMagazzino


def allegato_file(request, pk):
    allegato = get_object_or_404(Allegato, pk=pk)
    try:
        contenuto = allegato.file.storage.open(allegato.file.name, "rb")
    except FileNotFoundError:
        raise Http404("Il file non si trova più sul disco.")
    # Le foto HEIC (iPhone) molti browser non le mostrano: meglio scaricarle.
    scarica = request.GET.get("scarica") == "1" or not (allegato.immagine or allegato.tipo_contenuto == "application/pdf")
    risposta = FileResponse(
        contenuto, content_type=allegato.tipo_contenuto, as_attachment=scarica, filename=allegato.nome
    )
    risposta["Cache-Control"] = "private, max-age=3600"
    return risposta


@require_POST
def operazione_allegati(request, pk):
    operazione = get_object_or_404(Operazione, pk=pk)
    try:
        creati = services.aggiungi_allegati(
            operazione=operazione, files=request.FILES.getlist("allegati"), utente=request.user
        )
    except ErroreMagazzino as e:
        for messaggio in e.messaggi:
            messages.error(request, messaggio)
    else:
        messages.success(request, "Allegato aggiunto." if len(creati) == 1 else f"{len(creati)} allegati aggiunti.")
    return redirect("operazione_dettaglio", pk=operazione.pk)


@require_http_methods(["GET", "POST"])
def allegato_elimina(request, pk):
    allegato = Allegato.objects.select_related("operazione").filter(pk=pk).first()
    if allegato is None:
        messages.info(request, "Questo allegato non esiste più.")
        return redirect("operazioni")
    if request.method == "POST":
        operazione_id = allegato.operazione_id
        allegato.delete()
        messages.success(request, f"Allegato «{allegato.nome}» eliminato.")
        return redirect("operazione_dettaglio", pk=operazione_id)
    return render(request, "magazzino/allegato_elimina.html", {"allegato": allegato})
