"""Comande e ricevute su fogli A4 o A5, per le stampanti normali (laser o a getto d'inchiostro).

Si parte dalle stesse righe dei biglietti termici (stampa.impagina): il foglio è un
grande biglietto, con il carattere grande quanto basta a riempire la larghezza.
Ogni pagina si disegna come immagine in bianco e nero e poi si scrive nel formato
che la stampante accetta (vedi ipp.py): PWG raster o Apple raster (le «lingue» di
Mopria e AirPrint), oppure PDF o JPEG.
"""

import io
import re
import shutil
import struct
import subprocess
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .stampa import impagina

# larghezza e altezza in mm, nome per IPP
CARTE = {
    "A4": (210, 297, "iso_a4_210x297mm"),
    "A5": (148, 210, "iso_a5_148x210mm"),
}
MARGINE_MM = 10  # le stampanti non arrivano al bordo del foglio (di solito 4-5 mm)
INTERLINEA = 1.2

BIANCO, NERO = 255, 0

CARATTERI = [
    # (normale, grassetto): il primo che c'è. Nell'immagine Docker c'è DejaVu (fonts-dejavu-core).
    ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"),
    ("/usr/share/fonts/TTF/DejaVuSansMono.ttf", "/usr/share/fonts/TTF/DejaVuSansMono-Bold.ttf"),
    ("/usr/share/fonts/liberation/LiberationMono-Regular.ttf", "/usr/share/fonts/liberation/LiberationMono-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf"),
    ("/usr/share/fonts/noto/NotoSansMono-Regular.ttf", "/usr/share/fonts/noto/NotoSansMono-Bold.ttf"),
]


@lru_cache(maxsize=1)
def file_caratteri():
    """(normale, grassetto): un carattere a spaziatura fissa, come sui biglietti."""
    for normale, grassetto in CARATTERI:
        if Path(normale).exists():
            return normale, grassetto if Path(grassetto).exists() else normale
    if shutil.which("fc-match"):
        trovati = []
        for stile in ("monospace", "monospace:bold"):
            uscita = subprocess.run(["fc-match", "-f", "%{file}", stile], capture_output=True, text=True, timeout=5)
            trovati.append(uscita.stdout.strip())
        if trovati[0] and Path(trovati[0]).exists():
            return trovati[0], trovati[1] if Path(trovati[1]).exists() else trovati[0]
    raise RuntimeError("Manca un carattere a spaziatura fissa (in Docker: pacchetto fonts-dejavu-core).")


@lru_cache(maxsize=32)
def _carattere(grassetto, dimensione):
    normale, nero = file_caratteri()
    return ImageFont.truetype(nero if grassetto else normale, dimensione)


def misure(carta, dpi):
    larghezza_mm, altezza_mm, _ = CARTE[carta]
    return round(larghezza_mm / 25.4 * dpi), round(altezza_mm / 25.4 * dpi), round(MARGINE_MM / 25.4 * dpi)


def _dimensione_carattere(colonne, larghezza_utile):
    """Il carattere più grande con cui `colonne` caratteri stanno nella larghezza utile."""
    passo = _carattere(False, 100).getlength("0") / 100
    dimensione = int(larghezza_utile / (colonne * passo))
    while dimensione > 4 and _carattere(True, dimensione).getlength("0" * colonne) > larghezza_utile:
        dimensione -= 1
    return dimensione


def _striscia(testo, stile, allineamento, *, larghezza, dimensione, altezza_riga):
    """Una riga del biglietto disegnata a parte (poi si incolla nella pagina)."""
    alto, largo = "alto" in stile, "largo" in stile
    doppio = alto and largo
    carattere = _carattere("b" in stile, dimensione * 2 if doppio else dimensione)
    altezza = altezza_riga * (2 if doppio else 1)
    striscia = Image.new("L", (larghezza, altezza), BIANCO)
    disegno = ImageDraw.Draw(striscia)
    colore = NERO
    if "inverso" in stile:
        disegno.rectangle([0, 0, larghezza, altezza], fill=NERO)
        colore = BIANCO
    x = 0
    if allineamento == "centro":
        x = max((larghezza - carattere.getlength(testo)) / 2, 0)
    disegno.text((x, altezza / 2), testo, font=carattere, fill=colore, anchor="lm")
    if alto and not doppio:
        # Altezza doppia e larghezza normale, come le stampanti termiche.
        striscia = striscia.resize((larghezza, altezza * 2), Image.Resampling.BICUBIC)
    return striscia


def pagine(contenuto, *, carta="A4", colonne=42, dpi=300):
    """Le pagine (immagini in bianco e nero, modo «L» con solo 0 e 255) di un biglietto."""
    larghezza, altezza, margine = misure(carta, dpi)
    utile = larghezza - 2 * margine
    dimensione = _dimensione_carattere(colonne, utile)
    altezza_riga = round(dimensione * INTERLINEA)
    righe = impagina(contenuto, colonne)
    strisce = [
        _striscia(t, s, a, larghezza=utile, dimensione=dimensione, altezza_riga=altezza_riga) for s, a, t in righe
    ]

    def distribuisci(fondo):
        gruppi, y = [[]], margine
        for striscia in strisce:
            if y + striscia.height > fondo and gruppi[-1]:
                gruppi.append([])
                y = margine
            gruppi[-1].append(striscia)
            y += striscia.height
        return gruppi

    gruppi = distribuisci(altezza - margine)
    if len(gruppi) > 1:
        # Su più pagine: in fondo a ognuna il numero (e quello dell'ordine), si tiene una riga libera.
        gruppi = distribuisci(altezza - margine - altezza_riga)
    riferimento = next((t.strip() for s, a, t in righe if {"alto", "largo"} <= s and "inverso" not in s and a == "sx"), "")

    fogli = []
    for numero, gruppo in enumerate(gruppi, start=1):
        foglio = Image.new("L", (larghezza, altezza), BIANCO)
        y = margine
        for striscia in gruppo:
            foglio.paste(striscia, (margine, y))
            y += striscia.height
        if len(gruppi) > 1:
            piede = f"{riferimento} · pagina {numero} di {len(gruppi)}".lstrip(" ·")
            carattere = _carattere(True, dimensione)
            ImageDraw.Draw(foglio).text(
                (larghezza - margine, altezza - margine - altezza_riga / 2), piede, font=carattere, fill=NERO, anchor="rm"
            )
        # Solo bianco e nero: sulla carta il testo viene netto e i file restano piccoli.
        fogli.append(foglio.point(lambda v: BIANCO if v >= 128 else NERO))
    return fogli


# ---------------------------------------------------------------------------
# Formati
# ---------------------------------------------------------------------------

_CORSE = re.compile(rb"\x00+|\xff+")


def _comprimi(foglio, byte_per_pixel):
    """Le righe della pagina compresse come vogliono PWG raster e Apple raster: un byte con
    quante volte si ripete la riga, poi gruppi «ripeti questo pixel n volte» (n fino a 128).
    La pagina ha solo pixel bianchi e neri, quindi bastano le ripetizioni."""
    larghezza, altezza = foglio.size
    dati = foglio.tobytes()
    righe = [dati[i:i + larghezza] for i in range(0, len(dati), larghezza)]
    pixel = {NERO: bytes([NERO]) * byte_per_pixel, BIANCO: bytes([BIANCO]) * byte_per_pixel}
    uscita = bytearray()
    i = 0
    while i < altezza:
        riga = righe[i]
        uguali = 1
        while i + uguali < altezza and uguali < 256 and righe[i + uguali] == riga:
            uguali += 1
        uscita.append(uguali - 1)
        coperti = 0
        for corsa in _CORSE.finditer(riga):
            valore = pixel[riga[corsa.start()]]
            n = corsa.end() - corsa.start()
            coperti += n
            while n > 0:
                k = min(n, 128)
                uscita.append(k - 1)
                uscita += valore
                n -= k
        if coperti != larghezza:
            raise ValueError("la pagina deve contenere solo pixel bianchi e neri")
        i += uguali
    return bytes(uscita)


def pwg_raster(fogli, *, dpi, tipo="sgray_8", carta="A4"):
    """PWG raster (PWG 5102.4): il formato di IPP Everywhere e Mopria."""
    colori = 3 if tipo == "srgb_8" else 1
    larghezza_mm, altezza_mm, nome_carta = CARTE[carta]
    uscita = bytearray(b"RaS2")
    for foglio in fogli:
        larghezza, altezza = foglio.size
        h = bytearray(1796)

        def intero(posizione, valore):
            struct.pack_into(">I", h, posizione, valore)

        h[0:9] = b"PwgRaster"
        intero(276, dpi)  # HWResolution
        intero(280, dpi)
        intero(340, 1)  # NumCopies
        intero(352, round(larghezza_mm / 25.4 * 72))  # PageSize, in punti
        intero(356, round(altezza_mm / 25.4 * 72))
        intero(372, larghezza)  # Width
        intero(376, altezza)  # Height
        intero(384, 8)  # BitsPerColor
        intero(388, 8 * colori)  # BitsPerPixel
        intero(392, larghezza * colori)  # BytesPerLine
        intero(396, 0)  # ColorOrder: chunky
        intero(400, 19 if colori == 3 else 18)  # ColorSpace: sRGB / sGray
        intero(420, colori)  # NumColors
        intero(452, len(fogli))  # TotalPageCount
        intero(456, 1)  # CrossFeedTransform
        intero(460, 1)  # FeedTransform
        intero(472, larghezza)  # ImageBoxRight
        intero(476, altezza)  # ImageBoxBottom
        intero(480, 0xFFFFFF)  # AlternatePrimary
        h[1732:1732 + len(nome_carta)] = nome_carta.encode("ascii")  # PageSizeName
        uscita += h
        uscita += _comprimi(foglio, colori)
    return bytes(uscita)


def apple_raster(fogli, *, dpi, tipo="W8"):
    """Apple raster (URF): il formato delle stampanti AirPrint."""
    colori = 3 if tipo == "SRGB24" else 1
    uscita = bytearray(b"UNIRAST\x00" + struct.pack(">I", len(fogli)))
    for foglio in fogli:
        larghezza, altezza = foglio.size
        intestazione = bytearray(32)
        intestazione[0] = 8 * colori  # bit per pixel
        intestazione[1] = 1 if colori == 3 else 0  # sRGB / sGray
        intestazione[2] = 1  # solo fronte
        intestazione[3] = 4  # qualità normale
        struct.pack_into(">III", intestazione, 12, larghezza, altezza, dpi)
        uscita += intestazione
        uscita += _comprimi(foglio, colori)
    return bytes(uscita)


def pdf(fogli, *, dpi):
    buffer = io.BytesIO()
    bn = [f.convert("1") for f in fogli]
    bn[0].save(buffer, "PDF", save_all=True, append_images=bn[1:], resolution=dpi)
    return buffer.getvalue()


def jpeg(foglio, *, dpi):
    buffer = io.BytesIO()
    foglio.save(buffer, "JPEG", quality=85, dpi=(dpi, dpi))
    return buffer.getvalue()


def documenti(contenuto, *, formato, dpi, tipo="", carta="A4", colonne=42):
    """[bytes] da mandare alla stampante: uno solo, tranne il JPEG (una pagina per lavoro)."""
    fogli = pagine(contenuto, carta=carta, colonne=colonne, dpi=dpi)
    if formato == "image/pwg-raster":
        return [pwg_raster(fogli, dpi=dpi, tipo=tipo, carta=carta)]
    if formato == "image/urf":
        return [apple_raster(fogli, dpi=dpi, tipo=tipo)]
    if formato == "application/pdf":
        return [pdf(fogli, dpi=dpi)]
    if formato == "image/jpeg":
        return [jpeg(f, dpi=dpi) for f in fogli]
    raise ValueError(f"formato sconosciuto: {formato}")
