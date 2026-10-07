"""Le chiamate tra i due server (sagra e casa): chiave, allegati e il client HTTP.

- Chi risponde controlla la chiave (COPIA_CHIAVE, uguale sui due server; con la chiave vuota non
  risponde a nessuno) e dà gli allegati che gli chiedono, solo dalla cartella degli allegati.
- Chi chiama (la sagra per la sincronia, o chi si riallinea) usa `apri` e `scarica`.

Le API sono in views.py; cosa si scambiano i due server è in sincronia.py.
"""

import json
import os
import secrets
import shutil
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from django.conf import settings

from .copia import CopiaNonRiuscita

NOME_INVIATA = "copia-inviata"  # battito: l'ultima volta che qualcuno ha preso la copia completa
TIMEOUT = 30


# ---------------------------------------------------------------------------
# Lato di chi risponde
# ---------------------------------------------------------------------------


def chiave_valida(request):
    chiave = settings.COPIA_CHIAVE
    tipo, _, ricevuta = request.headers.get("Authorization", "").partition(" ")
    return bool(chiave) and tipo.lower() == "bearer" and secrets.compare_digest(ricevuta.strip().encode(), chiave.encode())


def elenco_allegati():
    """[[percorso, dimensione]] dei file caricati (fatture, foto)."""
    radice = Path(settings.MEDIA_ROOT)
    if not radice.is_dir():
        return []
    return sorted(
        [str(f.relative_to(radice)), f.stat().st_size]
        for f in radice.rglob("*")
        if f.is_file() and not f.name.endswith(".tmp")
    )


def percorso_allegato(percorso):
    """Il percorso nella cartella degli allegati, o None se ne uscirebbe (es. «../../config»)."""
    radice = Path(settings.MEDIA_ROOT).resolve()
    file = (radice / str(percorso)).resolve()
    return file if radice in file.parents else None


def file_allegato(percorso):
    """Il file richiesto, solo se esiste davvero dentro la cartella degli allegati."""
    file = percorso_allegato(percorso)
    return file if file is not None and file.is_file() else None


# ---------------------------------------------------------------------------
# Lato di chi chiama
# ---------------------------------------------------------------------------


class AltroNonRaggiungibile(CopiaNonRiuscita):
    pass


def _richiesta(base, via, dati=None, tipo=None, **parametri):
    indirizzo = base.rstrip("/") + "/" + via
    if parametri:
        indirizzo += "?" + urllib.parse.urlencode(parametri)
    intestazioni = {"Authorization": f"Bearer {settings.COPIA_CHIAVE}", "User-Agent": "sincronia-sagra"}
    if tipo:
        intestazioni["Content-Type"] = tipo
    return urllib.request.Request(indirizzo, data=dati, headers=intestazioni, method="POST" if dati is not None else "GET")


def apri(base, via, *, dati=None, tipo=None, timeout=TIMEOUT, **parametri):
    """La risposta dell'altro server (da usare con «with»). Con `dati` è un POST."""
    try:
        return urllib.request.urlopen(_richiesta(base, via, dati, tipo, **parametri), timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code in (401, 404):
            raise CopiaNonRiuscita(
                "L'altro server rifiuta la richiesta: COPIA_CHIAVE deve essere uguale nel file .env dei due server "
                "(e tutti e due devono avere il programma aggiornato)."
            ) from None
        dettaglio = ""
        try:
            dettaglio = " ".join(json.loads(e.read().decode("utf-8")).get("errori", []))
        except (ValueError, OSError, AttributeError):
            pass
        raise CopiaNonRiuscita(dettaglio or f"L'altro server ha risposto con un errore (HTTP {e.code}).") from None
    except (urllib.error.URLError, OSError) as e:
        motivo = getattr(e, "reason", None) or e
        raise AltroNonRaggiungibile(f"L'altro server ({base}) non è raggiungibile ({motivo}): spento o senza internet?") from None


def scarica(base, via, destinazione, **parametri):
    with apri(base, via, **parametri) as risposta, open(destinazione, "wb") as f:
        shutil.copyfileobj(risposta, f)
    return os.path.getsize(destinazione)
