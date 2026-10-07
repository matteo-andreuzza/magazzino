"""Pannello del sistema (/sistema/, solo staff) e API tra i due server (sincronia e copie)."""

import tempfile
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache, wraps
from pathlib import Path

import markdown
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.db import connection
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from markdown.extensions.toc import slugify_unicode

from . import copia, copia_remota, monitor, sincronia
from .models import Battito, Conflitto, Dispositivo, Errore
from .services import segnala

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
        "server": settings.SERVER_NOME, "sincronia_con": settings.SINCRONIA_CON,
        "ogni_secondi": settings.SINCRONIA_OGNI_SECONDI, "conflitti": monitor.conflitti(),
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
def sincronia_adesso(request):
    riallinea = request.POST.get("riallinea") == "1"
    if not settings.SINCRONIA_CON:
        raise Http404
    if riallinea and request.POST.get("conferma") != "1":
        messages.error(request, "Per riallineare spunta la conferma: i dati di qui vengono sostituiti da quelli di casa.")
    elif not sincronia.chiedi_giro(riallinea_=riallinea):
        messages.error(request, "Il servizio «sincronia» non è acceso su questo server (COMPOSE_PROFILES=sincronia).")
    elif riallinea:
        messages.success(request, "Riallineamento chiesto: parte entro pochi secondi (prima si salva una copia di qui).")
    else:
        messages.success(request, "Giro chiesto: parte entro pochi secondi.")
    return _torna("#sincronia")


@require_POST
@solo_staff
def conflitto_risolvi(request, pk):
    conflitto = get_object_or_404(Conflitto, pk=pk)
    altro = sincronia.altro(settings.SERVER_NOME)
    if request.POST.get("tieni") == "altro":
        try:
            sincronia.unisci(conflitto)
        except sincronia.SincroniaFerma as e:
            messages.error(request, str(e))
        else:
            messages.success(request, f"Unite: resta quella {'di casa' if altro == 'casa' else 'della sagra'}; "
                                      "le righe collegate passano a lei al prossimo giro.")
    else:
        sincronia.chiedi_all_altro(conflitto)
        messages.success(request, f"Chiesto {'a casa' if altro == 'casa' else 'alla sagra'} di tenere questa: "
                                  "succede al prossimo giro.")
    return _torna("#sincronia")


# ---------------------------------------------------------------------------
# API tra i due server (con COPIA_CHIAVE): sincronia, copia completa per riallinearsi, file
# ---------------------------------------------------------------------------


def _con_chiave(vista, metodo=require_GET):
    @wraps(vista)
    def controlla(request, *args, **kwargs):
        if not settings.COPIA_CHIAVE:
            raise Http404("Sincronia non configurata.")
        if not copia_remota.chiave_valida(request):
            return JsonResponse({"ok": False, "errori": ["Chiave non valida."]}, status=401)
        return vista(request, *args, **kwargs)

    return csrf_exempt(login_not_required(metodo(controlla)))


def _con_chiave_post(vista):
    return _con_chiave(vista, require_POST)


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


@_con_chiave_post
def api_sincronia(request):
    """La sagra manda le sue modifiche e riceve quelle di qui (vedi sincronia.scambio)."""
    try:
        richiesta = sincronia.leggi_messaggio(request.read())
    except (OSError, ValueError, EOFError):
        return JsonResponse({"ok": False, "errori": ["Messaggio non valido."]}, status=400)
    try:
        risposta, esito = sincronia.scambio(richiesta)
    except sincronia.SincroniaFerma as e:
        segnala(sincronia.NOME, stato="errore", messaggio=str(e)[:300], dettagli={
            **(Battito.objects.filter(nome=sincronia.NOME).values_list("dettagli", flat=True).first() or {}),
            "ultimo_contatto": timezone.now().isoformat()})
        return JsonResponse({"ok": False, "errori": [str(e)]}, status=409)
    precedenti = Battito.objects.filter(nome=sincronia.NOME).values_list("dettagli", flat=True).first() or {}
    adesso = timezone.now()
    dettagli = {
        **precedenti, "ultimo_contatto": adesso.isoformat(), "ip": request.META.get("REMOTE_ADDR", ""),
        "ricevute": len(richiesta.get("modifiche") or []), "inviate": len(risposta["modifiche"]),
        "in_coda_altro": richiesta.get("in_coda", 0), "conflitti_altro": richiesta.get("conflitti") or [],
        "conflitti": Conflitto.objects.count(), "in_coda": risposta["in_coda"],
        "esiti_richieste": risposta["esiti_richieste"] or precedenti.get("esiti_richieste", []),
    }
    if sincronia._pulizia_dovuta(precedenti):
        sincronia.pulisci_file()
        dettagli["pulizia_file_il"] = adesso.isoformat()
    problemi = dettagli["conflitti"] or dettagli["conflitti_altro"]
    segnala(sincronia.NOME, stato="avviso" if problemi else "ok",
            messaggio=f"La sagra si è fatta sentire alle {timezone.localtime(adesso):%H:%M}"
                      + (": ci sono conflitti, vedi sotto." if problemi else "."), dettagli=dettagli)
    return HttpResponse(sincronia.scrivi_messaggio(risposta), content_type=sincronia.TIPO_MESSAGGIO)


@_con_chiave_post
def api_sincronia_allegato(request):
    """Un file che una riga di qui usa e che c'è solo dall'altra parte."""
    if not sincronia.ricevi_file(request.GET.get("percorso", ""), request):
        raise Http404
    return JsonResponse({"ok": True})
