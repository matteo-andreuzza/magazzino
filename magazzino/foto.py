"""Foto dei prodotti.

La foto scattata col telefono (anche 10 MB) si salva rimpicciolita, girata
per il verso giusto e in JPEG, più una miniatura quadrata per gli elenchi:
così le pagine restano leggere anche con il Wi-Fi della sagra.

Il formato si riconosce dai primi byte (come per gli allegati) e la foto
viene sempre riscritta da capo: nel file salvato non resta niente di quello
caricato (né dati nascosti né la posizione GPS dello scatto).
"""

from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

from .allegati import riconosci

LATO_FOTO = 1280
LATO_MINIATURA = 240
# Oltre questi pixel (più di una foto da 60 megapixel) non la si apre neanche.
MAX_PIXEL = 60_000_000
FORMATI = {"image/jpeg", "image/png", "image/webp"}


class FotoNonValida(ValueError):
    pass


def _rgb(img):
    """In RGB; le parti trasparenti (PNG) diventano bianche invece che nere."""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        fondo = Image.new("RGB", img.size, (255, 255, 255))
        fondo.paste(img, mask=img.getchannel("A"))
        return fondo
    return img.convert("RGB")


def _jpeg(img, qualita):
    uscita = BytesIO()
    img.save(uscita, "JPEG", quality=qualita, optimize=True, progressive=True)
    return ContentFile(uscita.getvalue())


def prepara(file):
    """File caricato -> (foto, miniatura) in JPEG. Solleva FotoNonValida."""
    if not file.size:
        raise FotoNonValida("Il file è vuoto.")
    if file.size > settings.FOTO_MAX_MB * 1024 * 1024:
        raise FotoNonValida(f"La foto è troppo grande (massimo {settings.FOTO_MAX_MB} MB).")
    file.seek(0)
    formato = riconosci(file.read(16))
    file.seek(0)
    if formato is not None and formato[0] == "image/heic":
        raise FotoNonValida(
            "Le foto HEIC dell'iPhone non si possono leggere: scattala dal modulo (diventa JPG da sola) "
            "oppure salvala come JPG."
        )
    if formato is None or formato[0] not in FORMATI:
        raise FotoNonValida("Scegli una foto (JPG, PNG o WebP).")
    try:
        with Image.open(file) as originale:
            if originale.width * originale.height > MAX_PIXEL:
                raise FotoNonValida("La foto ha troppi pixel: rimpiccioliscila e riprova.")
            originale.load()
            img = _rgb(ImageOps.exif_transpose(originale))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError, ValueError) as e:
        if isinstance(e, FotoNonValida):
            raise
        raise FotoNonValida("La foto non si riesce a leggere (forse è rovinata): riprova con un'altra.") from e
    grande = img.copy()
    grande.thumbnail((LATO_FOTO, LATO_FOTO), Image.Resampling.LANCZOS)
    piccola = ImageOps.fit(img, (LATO_MINIATURA, LATO_MINIATURA), Image.Resampling.LANCZOS)
    return _jpeg(grande, 82), _jpeg(piccola, 80)
