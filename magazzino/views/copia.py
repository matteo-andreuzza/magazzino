"""Copia dei dati da scaricare (es. su una chiavetta a fine serata)."""

import tempfile

from django.contrib import messages
from django.http import FileResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET

from sistema import copia


@require_GET
def copia_dati(request):
    if not request.user.is_staff:
        return render(request, "magazzino/solo_staff.html", status=403)
    return render(request, "magazzino/copia_dati.html", {"nome_file": copia.nome_file()})


@require_GET
def copia_dati_scarica(request):
    if not request.user.is_staff:
        return render(request, "magazzino/solo_staff.html", status=403)
    file = tempfile.TemporaryFile()  # si cancella da solo quando il download è finito
    try:
        copia.scrivi_copia(file)
    except copia.CopiaNonRiuscita as e:
        file.close()
        messages.error(request, str(e))
        return redirect("copia_dati")
    file.seek(0)
    risposta = FileResponse(file, as_attachment=True, filename=copia.nome_file(), content_type="application/x-tar")
    risposta["Cache-Control"] = "no-store"
    return risposta
