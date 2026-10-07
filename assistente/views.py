"""Server MCP via HTTP e pagina per le chiavi di accesso."""

import json
from datetime import timedelta
from functools import wraps
from urllib.parse import urlsplit

from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from sistema.services import e_copia

from .models import ChiaveMCP
from .protocollo import VERSIONI, gestisci_testo
from .strumenti import STRUMENTI, Contesto

MAX_CORPO = 1024 * 1024


def _rifiuta(stato, messaggio, **intestazioni):
    risposta = JsonResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": messaggio}}, status=stato)
    for nome, valore in intestazioni.items():
        risposta[nome.replace("_", "-")] = valore
    return risposta


def _origine_estranea(request):
    """Le richieste dai browser (con Origin) devono venire dall'app stessa: protegge da siti malevoli."""
    origine = request.headers.get("Origin")
    if not origine:
        return False
    return urlsplit(origine).netloc.lower() != request.get_host().lower()


@csrf_exempt
@login_not_required
def mcp(request):
    if request.method != "POST":
        # Niente flusso di notifiche dal server (GET) né sessioni da chiudere (DELETE).
        risposta = HttpResponse(status=405)
        risposta["Allow"] = "POST"
        return risposta
    if _origine_estranea(request):
        return _rifiuta(403, "Origine non ammessa.")
    intestazione = request.headers.get("Authorization", "")
    tipo, _, testo = intestazione.partition(" ")
    chiave = ChiaveMCP.trova(testo.strip()) if tipo.lower() == "bearer" else None
    if chiave is None or not chiave.utente.is_active:
        return _rifiuta(
            401, "Chiave mancante o non valida: creane una dall'app (Magazzino → Claude).",
            WWW_Authenticate='Bearer realm="magazzino-sagra", error="invalid_token"',
        )
    versione = request.headers.get("MCP-Protocol-Version")
    if versione and versione not in VERSIONI:
        return _rifiuta(400, f"Versione del protocollo non gestita: {versione}.")
    if len(request.body) > MAX_CORPO:
        return _rifiuta(413, "Richiesta troppo grande.")

    # Sulla copia remota (sola lettura) Claude può solo leggere.
    copia = e_copia()
    adesso = timezone.now()
    if not copia and (chiave.ultimo_uso is None or adesso - chiave.ultimo_uso > timedelta(minutes=1)):
        ChiaveMCP.objects.filter(pk=chiave.pk).update(ultimo_uso=adesso)

    risposta = gestisci_testo(request.body.decode("utf-8", errors="replace"),
                              Contesto(utente=chiave.utente, scrittura=chiave.scrittura and not copia))
    if risposta is None:
        return HttpResponse(status=202)  # solo notifiche: niente da rispondere
    return HttpResponse(json.dumps(risposta, ensure_ascii=False), content_type="application/json", status=200)


@login_not_required
def non_trovato(request, resto=""):
    # Claude Code, se la chiave è sbagliata, cerca qui un accesso OAuth: non c'è (si usa la chiave).
    return JsonResponse({"error": "not_found"}, status=404)


# ---------------------------------------------------------------------------
# Pagina «Claude»: chiavi di accesso (solo staff)
# ---------------------------------------------------------------------------


def solo_staff(vista):
    @wraps(vista)
    def controlla(request, *args, **kwargs):
        if not request.user.is_staff:
            return render(request, "assistente/solo_staff.html", status=403)
        return vista(request, *args, **kwargs)

    return controlla


@require_http_methods(["GET", "POST"])
@solo_staff
def chiavi(request):
    nuova = None
    if request.method == "POST":
        nome = request.POST.get("nome", "").strip()[:60]
        if not nome:
            messages.error(request, "Dai un nome alla chiave, per esempio «Claude Code sul mio portatile».")
        else:
            chiave, testo = ChiaveMCP.crea(nome=nome, utente=request.user, scrittura=request.POST.get("scrittura") == "on")
            nuova = {"chiave": chiave, "testo": testo}
    indirizzo = request.build_absolute_uri(reverse("mcp"))
    return render(
        request,
        "assistente/chiavi.html",
        {
            "chiavi": ChiaveMCP.objects.select_related("utente"),
            "nuova": nuova,
            "indirizzo": indirizzo,
            "in_chiaro": indirizzo.startswith("http://") and request.get_host().split(":")[0] not in ("localhost", "127.0.0.1"),
            "strumenti": sorted(STRUMENTI.values(), key=lambda s: (s.scrive, s.titolo)),
        },
    )


@require_POST
@solo_staff
def chiave_revoca(request, pk):
    chiave = get_object_or_404(ChiaveMCP, pk=pk)
    if chiave.revocata_il is None:
        chiave.revocata_il = timezone.now()
        chiave.save(update_fields=["revocata_il"])
        messages.success(request, f"Chiave «{chiave.nome}» revocata: non funziona più.")
    return redirect("assistente:chiavi")
