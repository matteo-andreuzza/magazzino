"""Pannello del sistema (/sistema/, solo staff) e API della copia remota."""

import tempfile
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache, wraps
from pathlib import Path

import markdown
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.db import connection
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from markdown.extensions.toc import slugify_unicode

from . import copia, copia_remota, monitor
from .models import Battito, Dispositivo, Errore
from .services import e_copia, segnala

GUIDA = Path(settings.BASE_DIR) / "docs" / "MANUTENZIONE.md"


def solo_staff(vista):
    @wraps(vista)
    def controlla(request, *args, **kwargs):
        if not request.user.is_staff:
            return render(request, "magazzino/solo_staff.html", status=403)
        return vista(request, *args, **kwargs)

    return controlla


@lru_cache(maxsize=2)
def _guida_html(modificata):
    testo = GUIDA.read_text(encoding="utf-8")
    md = markdown.Markdown(
        extensions=["tables", "fenced_code", "toc", "sane_lists"],
        extension_configs={"toc": {"slugify": slugify_unicode, "permalink": "¶", "toc_depth": "2-3"}},
    )
    html = md.convert(testo)
    return html, md.toc


def guida_programmatore():
    """(html, indice) di docs/MANUTENZIONE.md; si rilegge solo quando il file cambia."""
    try:
        return _guida_html(GUIDA.stat().st_mtime)
    except OSError:
        return "<p>Manca il file docs/MANUTENZIONE.md.</p>", ""


@require_GET
@solo_staff
def pannello(request):
    html, indice = guida_programmatore()
    return render(request, "sistema/pannello.html", {
        "stato": monitor.stato(request), "guida": html, "indice": indice,
        "copia": e_copia(), "copia_di": settings.COPIA_DI,
    })


@require_GET
@solo_staff
def stato_json(request):
    return JsonResponse(monitor.stato(request))


def _torna(ancora):
    return redirect(reverse("sistema:pannello") + ancora)


@require_POST
@solo_staff
def errori_visti(request):
    Errore.objects.filter(visto=False).update(visto=True)
    return _torna("#errori")


@require_POST
@solo_staff
def errori_svuota(request):
    tolti, _ = Errore.objects.filter(visto=True).delete()
    messages.success(request, f"Tolti {tolti} errori già visti.")
    return _torna("#errori")


@require_POST
@solo_staff
def dispositivi_dimentica(request):
    Dispositivo.objects.all().delete()
    messages.success(request, "Elenco dei dispositivi svuotato: ricompaiono appena usano l'app.")
    return _torna("#dispositivi")


@require_POST
@solo_staff
def stampanti_controlla(request):
    from cassa.models import CONNESSIONI_SERVER, Stampante
    from cassa.servizio_stampa import rileva

    stampanti = list(Stampante.objects.filter(attiva=True, connessione__in=CONNESSIONI_SERVER))

    def controlla(stampante):
        try:
            return rileva(stampante)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=max(len(stampanti), 1)) as gruppo:
        list(gruppo.map(controlla, stampanti))
    messages.success(request, f"Controllate {len(stampanti)} stampanti di rete.")
    return _torna("#schema")


@require_POST
@solo_staff
def copia_adesso(request):
    if not e_copia():
        raise Http404
    battito = Battito.objects.filter(nome=copia_remota.NOME).first()
    if battito is None:
        messages.error(request, "Il servizio «copia» non è acceso su questo server.")
    else:
        battito.dettagli = {**battito.dettagli, "adesso": True, "forza": request.POST.get("forza") == "1"}
        battito.save(update_fields=["dettagli"])
        messages.success(request, "Copia chiesta: parte entro 10 secondi.")
    return _torna("#schema")


# ---------------------------------------------------------------------------
# API della copia remota (le chiama il server di casa, con COPIA_CHIAVE)
# ---------------------------------------------------------------------------


def _con_chiave(vista):
    @wraps(vista)
    def controlla(request, *args, **kwargs):
        if not settings.COPIA_CHIAVE:
            raise Http404("Copia remota non configurata.")
        if not copia_remota.chiave_valida(request):
            return JsonResponse({"ok": False, "errori": ["Chiave non valida."]}, status=401)
        return vista(request, *args, **kwargs)

    return login_not_required(require_GET(controlla))


@_con_chiave
def api_copia(request):
    return JsonResponse({**copia.info(), "allegati": copia_remota.elenco_allegati()})


@_con_chiave
def api_copia_database(request):
    file = tempfile.TemporaryFile()
    try:
        copia.copia_database(file)
    except copia.CopiaNonRiuscita as e:
        file.close()
        return JsonResponse({"ok": False, "errori": [str(e)]}, status=503)
    dimensione = file.tell()
    file.seek(0)
    segnala(copia_remota.NOME_INVIATA, dettagli={"ip": request.META.get("REMOTE_ADDR", ""), "dimensione": dimensione})
    return FileResponse(file, content_type="application/gzip", filename="database.sql.gz")


@_con_chiave
def api_copia_allegato(request):
    file = copia_remota.file_allegato(request.GET.get("percorso", ""))
    if file is None:
        raise Http404
    return FileResponse(file.open("rb"))
