"""Errori ed eccezioni non previsti, salvati per il pannello del sistema.

- pagine: il segnale di Django `got_request_exception` mette da parte l'eccezione e
  il middleware (middleware.py) la salva a risposta finita, fuori da qualsiasi
  transazione;
- servizi (stampa, sincronia) e comandi: chiamano registra_errore() direttamente.

Le eccezioni uguali (stessa origine, stesso tipo, stesso punto del codice) non si
ripetono: si conta quante volte sono successe.
"""

import hashlib
import logging
import sys
import traceback
from pathlib import Path

from django.conf import settings
from django.core.signals import got_request_exception
from django.db import DatabaseError, transaction
from django.db.models import F
from django.utils import timezone

from .models import Errore

log = logging.getLogger("sistema")

MASSIMO_ERRORI = 300  # oltre, i più vecchi si cancellano
ATTRIBUTO = "_sistema_errore"


def _punto(errore):
    """Il punto del NOSTRO codice più vicino all'errore, es. «cassa/services.py:131 crea_ordine»."""
    base = str(Path(settings.BASE_DIR).resolve())
    righe = traceback.extract_tb(errore.__traceback__) if errore.__traceback__ else []
    nostre = [r for r in righe if r.filename.startswith(base) and "/.venv/" not in r.filename]
    scelta = (nostre or righe or [None])[-1]
    if scelta is None:
        return ""
    nome = scelta.filename[len(base) + 1:] if scelta.filename.startswith(base) else Path(scelta.filename).name
    return f"{nome}:{scelta.lineno} {scelta.name}"


def registra_errore(origine, errore, *, dove="", utente=""):
    """Salva l'eccezione. Non solleva mai: un errore nel registrare un errore si scrive solo nel log."""
    try:
        tipo = f"{type(errore).__module__}.{type(errore).__qualname__}".removeprefix("builtins.")
        punto = _punto(errore)
        traccia = "".join(traceback.format_exception(errore))[-20000:]
        firma = hashlib.sha1(f"{origine}|{tipo}|{punto}".encode()).hexdigest()
        adesso = timezone.now()
        campi = {
            "messaggio": str(errore)[:2000],
            "dove": (dove or punto)[:300],
            "utente": utente[:150],
            "traccia": traccia,
            "ultimo_il": adesso,
            "visto": False,
        }
        with transaction.atomic():
            aggiornati = Errore.objects.filter(firma=firma).update(volte=F("volte") + 1, **campi)
            if not aggiornati:
                Errore.objects.create(firma=firma, origine=origine, tipo=tipo[:120], primo_il=adesso, **campi)
                vecchi = Errore.objects.order_by("-ultimo_il").values_list("pk", flat=True)[MASSIMO_ERRORI:]
                Errore.objects.filter(pk__in=list(vecchi)).delete()
    except DatabaseError:
        log.exception("Errore non salvato nel pannello del sistema")


def salva_errore_della_richiesta(request):
    """Chiamata dal middleware a risposta finita."""
    dati = getattr(request, ATTRIBUTO, None)
    if dati is None:
        return
    delattr(request, ATTRIBUTO)
    utente = request.user.get_username() if getattr(request, "user", None) and request.user.is_authenticated else ""
    registra_errore("web", dati, dove=f"{request.method} {request.path}", utente=utente)


def _su_eccezione(sender, request=None, **kwargs):
    errore = sys.exc_info()[1]
    if request is not None and errore is not None:
        setattr(request, ATTRIBUTO, errore)


def collega_segnali():
    got_request_exception.connect(_su_eccezione, dispatch_uid="sistema-errori")

