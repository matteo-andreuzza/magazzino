"""La guida per chi usa il programma (e un controllo di salute per Docker e Kubernetes)."""

from django.contrib.auth.decorators import login_not_required
from django.db import connection
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET


@require_GET
def guida(request):
    return render(request, "magazzino/guida.html")


@require_GET
@login_not_required
def salute(request):
    """Risponde «ok» se l'app e il database funzionano: per i controlli automatici (Docker, Kubernetes)."""
    try:
        with connection.cursor() as cursore:
            cursore.execute("SELECT 1")
    except Exception:
        return HttpResponse("database non raggiungibile\n", status=503, content_type="text/plain")
    return HttpResponse("ok\n", content_type="text/plain")
