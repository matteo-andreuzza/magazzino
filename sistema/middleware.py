"""Middleware del sistema.

- MonitoraggioMiddleware: segna i dispositivi collegati (per il pannello) e salva gli
  errori delle pagine, a risposta finita;
- SolaLetturaMiddleware: sulla copia remota (COPIA_DI) non si modifica niente.
"""

import hashlib
import logging
import time
from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from . import errori
from .models import Dispositivo

log = logging.getLogger("sistema")

SALTATE = ("/static/", "/salute/", "/favicon")
OGNI_SECONDI = 20  # per lo stesso dispositivo si scrive al massimo ogni tanto


def ruolo(request):
    """Cosa sta facendo il dispositivo, dalla pagina che ha chiesto."""
    p = request.path
    if p.startswith("/mcp"):
        return "Claude (MCP)"
    if p.startswith("/cassa/serate/"):
        if "/monitor" in p:
            return "Monitor cucina"
        if p.rstrip("/").endswith("/bar") or request.GET.get("postazione") == "bar":
            return "Cassa bar"
        if any(f"/{x}/" in p for x in ("ordina", "stato")) or p.count("/") == 4:
            return "Cassa ingresso"
        return "Cassa"
    for inizio, nome in (("/cassa/", "Cassa"), ("/bar/", "Bar"), ("/sistema/", "Pannello tecnico"), ("/admin/", "Amministrazione")):
        if p.startswith(inizio):
            return nome
    return "Magazzino"


def tipo_dispositivo(agente):
    a = agente.lower()
    if "ipad" in a or "tablet" in a or ("android" in a and "mobile" not in a):
        return "tablet"
    if "mobile" in a or "iphone" in a or "android" in a:
        return "telefono"
    return "computer"


def nome_browser(agente):
    for chiave, nome in (("edg/", "Edge"), ("firefox/", "Firefox"), ("chrome/", "Chrome"), ("safari/", "Safari")):
        if chiave in agente.lower():
            sistema = next((s for s in ("Android", "iPhone", "iPad", "Windows", "Mac OS", "Linux") if s in agente), "")
            return f"{nome} {sistema}".strip()
    return agente[:40]


class MonitoraggioMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.scritti = {}  # chiave -> (time.monotonic(), ruolo)

    def __call__(self, request):
        risposta = self.get_response(request)
        try:
            errori.salva_errore_della_richiesta(request)
            if not request.path.startswith(SALTATE):
                self._segna(request, risposta)
        except Exception:  # noqa: BLE001 - il monitoraggio non deve mai rompere una pagina
            log.exception("Monitoraggio non riuscito")
        return risposta

    def _segna(self, request, risposta):
        ip = request.META.get("REMOTE_ADDR") or None
        if request.path.startswith("/mcp") and risposta.status_code < 400:
            chiave, utente, tipo = f"claude:{ip}", "", "claude"
        elif getattr(request, "user", None) and request.user.is_authenticated and request.session.session_key:
            chiave = hashlib.sha1(request.session.session_key.encode()).hexdigest()
            utente = request.user.get_username()
            tipo = tipo_dispositivo(request.headers.get("User-Agent", ""))
        else:
            return
        cosa = ruolo(request)
        adesso = time.monotonic()
        ultimo = self.scritti.get(chiave)
        if ultimo and adesso - ultimo[0] < OGNI_SECONDI and ultimo[1] == cosa:
            return
        self.scritti[chiave] = (adesso, cosa)
        campi = {
            "tipo": tipo, "ruolo": cosa, "utente": utente, "indirizzo_ip": ip,
            "browser": nome_browser(request.headers.get("User-Agent", "")),
            "ultima_pagina": request.path[:200], "ultimo_il": timezone.now(),
        }
        aggiornati = Dispositivo.objects.filter(chiave=chiave).update(**campi)
        if not aggiornati:
            Dispositivo.objects.get_or_create(chiave=chiave, defaults=campi)
            # Ogni tanto si fa pulizia dei dispositivi spariti da una settimana.
            Dispositivo.objects.filter(ultimo_il__lt=timezone.now() - timedelta(days=7)).delete()
        if len(self.scritti) > 500:
            self.scritti.clear()


def _e_sola_lettura(errore):
    while errore is not None:
        if getattr(errore, "sqlstate", None) == "25006":  # read_only_sql_transaction
            return True
        errore = errore.__cause__
    return False


class SolaLetturaMiddleware:
    """Sulla copia remota si guarda e basta: i dati arrivano dalla sagra e verrebbero sovrascritti.

    Le richieste che modificano ricevono una pagina che lo spiega (tranne entrare e uscire);
    le altre girano in una transazione di sola lettura (PostgreSQL), così anche una pagina
    che provasse a scrivere viene fermata dal database.
    """

    # Entrare, uscire e il pannello del sistema (scrive solo nelle sue tabelle, che sono di questo server).
    PERMESSE = ("/accounts/login/", "/accounts/logout/", "/sistema/")
    SICURI = ("GET", "HEAD", "OPTIONS")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not settings.COPIA_DI or request.path.startswith(self.PERMESSE):
            return self.get_response(request)
        if request.method not in self.SICURI and not request.path.startswith("/mcp"):
            return self.rifiuta(request)
        if connection.vendor != "postgresql":
            return self.get_response(request)
        with transaction.atomic():
            with connection.cursor() as cursore:
                cursore.execute("SET TRANSACTION READ ONLY")
            return self.get_response(request)

    def process_exception(self, request, exception):
        if settings.COPIA_DI and _e_sola_lettura(exception):
            transaction.set_rollback(True)
            return self.rifiuta(request)
        return None

    @staticmethod
    def rifiuta(request):
        testo = "Questa è la copia di sola lettura dei dati della sagra: le modifiche si fanno sul programma della sagra."
        if request.path.startswith(("/cassa/serate/", "/cassa/api/", "/mcp")) or "json" in request.headers.get("Accept", ""):
            return JsonResponse({"ok": False, "errori": [testo]}, status=403)
        return render(request, "sistema/sola_lettura.html", {"testo": testo}, status=403)
