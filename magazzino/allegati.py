"""Controllo dei file allegati ai carichi (fatture, bolle, foto).

Il formato si riconosce dai primi byte del file, non dall'estensione né da
quanto dichiara il browser: così un file travestito da PDF non viene mai
restituito come qualcos'altro.
"""

import os

from django.conf import settings

_HEIF = {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"}


class AllegatoNonValido(ValueError):
    pass


def riconosci(testa):
    """Primi byte del file -> (tipo_contenuto, estensione) oppure None."""
    if testa.startswith(b"%PDF-"):
        return "application/pdf", "pdf"
    if testa.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpg"
    if testa.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    if testa[:4] == b"RIFF" and testa[8:12] == b"WEBP":
        return "image/webp", "webp"
    if testa[4:8] == b"ftyp" and testa[8:12] in _HEIF:
        return "image/heic", "heic"
    return None


def nome_pulito(nome):
    nome = os.path.basename((nome or "").replace("\\", "/")).strip()
    return nome[-200:] or "allegato"


def controlla(file):
    """Restituisce (tipo_contenuto, estensione) o solleva AllegatoNonValido."""
    nome = nome_pulito(file.name)
    if not file.size:
        raise AllegatoNonValido(f"{nome}: il file è vuoto.")
    if file.size > settings.ALLEGATI_MAX_MB * 1024 * 1024:
        raise AllegatoNonValido(f"{nome}: è troppo grande (massimo {settings.ALLEGATI_MAX_MB} MB).")
    file.seek(0)
    testa = file.read(16)
    file.seek(0)
    formato = riconosci(testa)
    if formato is None:
        raise AllegatoNonValido(f"{nome}: formato non accettato. Allega una foto (JPG, PNG, HEIC) o un PDF.")
    return formato
