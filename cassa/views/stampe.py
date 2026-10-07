"""Stampa dal browser e interfaccia per il programma di stampa (agente)."""

import secrets
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .. import stampa
from ..models import Connessione, Documento, Ordine, Reparto, Stampa
from ..services import impostazioni
from .comuni import errore_json, indirizzo_sicuro, leggi_json, solo_staff

FILE_AGENTE = Path(__file__).resolve().parent.parent / "agente" / "agente_stampa.py"


def _biglietti(contenuti):
    """[(titolo, righe impaginate)] per il modello HTML (sempre 42 colonne, come la carta da 80 mm)."""
    return [(titolo, stampa.impagina(contenuto, 42)) for titolo, contenuto in contenuti]


@require_GET
@xframe_options_sameorigin  # la cassa la apre in un riquadro nascosto per stampare
def stampe_browser(request):
    """Stampa dal browser le stampe indicate e le segna come stampate."""
    ids = [int(i) for i in request.GET.getlist("id") if i.isdigit()]
    stampe = list(Stampa.objects.filter(pk__in=ids).select_related("stampante", "ordine").order_by("pk"))
    return render(
        request,
        "cassa/biglietti.html",
        {
            "biglietti": _biglietti([(str(s), s.contenuto) for s in stampe]),
            "da_segnare": [s.pk for s in stampe if s.stampante.connessione == Connessione.BROWSER],
            "torna": indirizzo_sicuro(request, request.GET.get("torna"), ""),
            "automatica": request.GET.get("auto", "1") == "1",
        },
    )


@require_POST
def stampe_browser_fatto(request):
    dati = leggi_json(request)
    ids = dati.get("ids") if dati else None
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        return errore_json("Richiesta non valida.")
    return JsonResponse({"ok": True, "segnate": stampa.segna_stampate_dal_browser(ids)})


@require_GET
def ordine_biglietti(request, pk):
    """Comande e ricevuta di un ordine da stampare con la stampante del computer (senza passare dalla coda)."""
    o = get_object_or_404(Ordine, pk=pk)
    imp = impostazioni()
    righe = list(o.righe.all())
    cosa = request.GET.get("cosa", "")
    contenuti = []
    for reparto in Reparto.values:
        del_reparto = [r for r in righe if r.reparto == reparto]
        if del_reparto and cosa in ("", reparto):
            contenuti.append((f"Comanda {Reparto(reparto).label.lower()}", stampa.contenuto_comanda(o, reparto, del_reparto, imp, copia=True)))
    if cosa in ("", "ricevuta"):
        contenuti.append((Documento.RICEVUTA.label, stampa.contenuto_ricevuta(o, righe, imp, copia=True)))
    return render(
        request,
        "cassa/biglietti.html",
        {
            "biglietti": _biglietti(contenuti),
            "da_segnare": [],
            "torna": reverse("cassa:ordine", args=[o.pk]),
            "automatica": request.GET.get("auto") == "1",
        },
    )


@require_POST
def stampa_riprova(request, pk):
    s = get_object_or_404(Stampa, pk=pk)
    if stampa.riprova(s):
        messages.success(request, "Stampa rimessa in coda.")
    else:
        messages.info(request, "Questa stampa non è in errore.")
    predefinito = reverse("cassa:ordine", args=[s.ordine_id]) if s.ordine_id else reverse("cassa:stampanti")
    return redirect(indirizzo_sicuro(request, request.POST.get("torna"), predefinito))


# ---------------------------------------------------------------------------
# Programma di stampa
# ---------------------------------------------------------------------------


@require_GET
@solo_staff
def agente_scarica(request):
    """Il programma di stampa, già configurato con indirizzo del server e chiave."""
    imp = impostazioni()
    testo = FILE_AGENTE.read_text(encoding="utf-8")
    testo = testo.replace('SERVER = ""', f"SERVER = {request.build_absolute_uri('/')!r}", 1)
    testo = testo.replace('TOKEN = ""', f"TOKEN = {imp.token_agente!r}", 1)
    risposta = HttpResponse(testo, content_type="text/x-python; charset=utf-8")
    risposta["Content-Disposition"] = 'attachment; filename="agente_stampa.py"'
    risposta["Cache-Control"] = "no-store"
    return risposta


def _agente_autorizzato(request):
    intestazione = request.headers.get("Authorization", "")
    tipo, _, chiave = intestazione.partition(" ")
    if tipo.lower() != "bearer" or not chiave:
        return False
    return secrets.compare_digest(chiave.strip().encode(), impostazioni().token_agente.encode())


def _nome_agente(dati):
    nome = " ".join(str(dati.get("agente") or "").split())[:60]
    # Il nome del servizio di stampa del server non si può usare.
    return nome if nome and nome != stampa.NOME_SERVER else None


def _ip(request):
    return request.META.get("REMOTE_ADDR") or None


@login_not_required
@csrf_exempt
@require_POST
def api_agente_lavori(request):
    if not _agente_autorizzato(request):
        return JsonResponse({"ok": False, "errori": ["Chiave non valida."]}, status=401)
    dati = leggi_json(request)
    nome = _nome_agente(dati) if dati else None
    if nome is None:
        return errore_json("Manca il nome del computer (agente).")
    try:
        massimo = min(max(int(dati.get("massimo", 5)), 1), 20)
    except (TypeError, ValueError):
        massimo = 5
    lavori = stampa.prendi_lavori(
        agente=nome, ip=_ip(request), versione=str(dati.get("versione") or ""), massimo=massimo
    )
    return JsonResponse({"ok": True, "lavori": lavori})


@login_not_required
@csrf_exempt
@require_POST
def api_agente_esito(request):
    if not _agente_autorizzato(request):
        return JsonResponse({"ok": False, "errori": ["Chiave non valida."]}, status=401)
    dati = leggi_json(request)
    nome = _nome_agente(dati) if dati else None
    if nome is None or not isinstance(dati.get("id"), int) or not isinstance(dati.get("ok"), bool):
        return errore_json("Richiesta non valida.")
    registrato = stampa.registra_esito(
        agente=nome, stampa_id=dati["id"], ok=dati["ok"], errore=str(dati.get("errore") or "")
    )
    return JsonResponse({"ok": True, "registrato": registrato})
