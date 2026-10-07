"""Servizi del sistema: segnali di vita, quale server è (casa o sagra) e versione del codice."""

import hashlib
import os
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.db import DEFAULT_DB_ALIAS
from django.utils import timezone

from .models import Battito


def e_casa():
    """True sul server di casa (SERVER_NOME=casa): qui non si stampa, le stampanti sono in sagra."""
    return settings.SERVER_NOME == "casa"


def segnala(nome, *, stato="ok", messaggio="", dettagli=None, avviato_il=None, using=DEFAULT_DB_ALIAS):
    """Un servizio si fa vivo (scrive solo quello che gli si passa, il resto resta com'era)."""
    campi = {"ultimo_il": timezone.now(), "stato": stato, "messaggio": messaggio[:300]}
    if dettagli is not None:
        campi["dettagli"] = dettagli
    if avviato_il is not None:
        campi["avviato_il"] = avviato_il
    if not Battito.objects.using(using).filter(nome=nome).update(**campi):
        Battito.objects.using(using).get_or_create(nome=nome, defaults=campi)


ESCLUSE_DALL_IMPRONTA = {"dati", "backup", "staticfiles", "graphify-out", "node_modules", "rclone", "__pycache__"}
ESTENSIONI_CODICE = {".py", ".html", ".js", ".css", ".json", ".sh", ".yml", ".txt"}


@lru_cache(maxsize=1)
def impronta_codice():
    """Impronta (8 caratteri) dei file del programma: due server con la stessa impronta hanno lo stesso codice."""
    base = Path(settings.BASE_DIR)
    somma = hashlib.sha1()
    for cartella, sottocartelle, file in os.walk(base):
        sottocartelle[:] = sorted(d for d in sottocartelle if d not in ESCLUSE_DALL_IMPRONTA and not d.startswith("."))
        for nome in sorted(file):
            percorso = Path(cartella) / nome
            if percorso.suffix in ESTENSIONI_CODICE:
                somma.update(str(percorso.relative_to(base)).encode())
                somma.update(percorso.read_bytes())
    return somma.hexdigest()[:8]
