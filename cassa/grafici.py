"""Grafici dei report, disegnati sul server in SVG (niente librerie esterne:
funzionano anche senza internet e si stampano).

Colori: palette validata per chi non distingue i colori (serie 1 blu, serie 2
arancio, su superficie bianca). Ogni grafico ha accanto la tabella con gli
stessi numeri; il riquadro al passaggio del mouse (grafici.js) aggiunge
dettagli, non li nasconde.
"""

import json
import math
from decimal import Decimal

from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe

SERIE = ["#2a78d6", "#eb6834"]
GRIGLIA = "#e1e0d9"
ASSE = "#c3c2b7"
TESTO_TENUE = "#6f6d67"
TESTO = "#1f1d1a"

LARGHEZZA = 640
ALTEZZA_GRAFICO = 180
MARGINE_SX = 48
MARGINE_DX = 12
MARGINE_ALTO = 22
FASCIA_ASSE = 30
SPESSORE_MAX = 24
RAGGIO = 4


def _scala(massimo, tacche=4):
    """Passo "tondo" (1, 2, 5 × 10^k) e fondo scala."""
    if massimo <= 0:
        return 1, tacche
    grezzo = float(massimo) / tacche
    potenza = 10 ** math.floor(math.log10(grezzo))
    for multiplo in (1, 2, 2.5, 5, 10):
        passo = multiplo * potenza
        if passo >= grezzo:
            break
    if passo < 1:
        passo = 1
    passo = int(passo) if float(passo).is_integer() else passo
    return passo, passo * math.ceil(float(massimo) / passo)


def _colonna(x, y, larghezza, altezza, arrotonda=True):
    """Rettangolo con gli angoli in alto arrotondati e la base squadrata."""
    if altezza <= 0:
        return ""
    r = min(RAGGIO, larghezza / 2, altezza) if arrotonda else 0
    return (
        f"M{x:.1f},{y + altezza:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
        f"H{x + larghezza - r:.1f} Q{x + larghezza:.1f},{y:.1f} {x + larghezza:.1f},{y + r:.1f} "
        f"V{y + altezza:.1f} Z"
    )


def _numero_asse(valore):
    if isinstance(valore, float) and not valore.is_integer():
        return f"{valore:.1f}".replace(".", ",")
    return f"{int(valore):,}".replace(",", ".")


def _suggerimento(titolo, righe):
    """Attributi per il riquadro: righe = [(valore, etichetta, colore o "")]."""
    return format_html(
        ' tabindex="0" data-tip="{}"', json.dumps({"titolo": titolo, "righe": righe}, ensure_ascii=False)
    )


def colonne_impilate(etichette, serie, *, titolo, formato, suggerimenti_titoli=None, etichetta_ogni=1):
    """Colonne (anche impilate) nel tempo o per serata.

    serie: [(nome, [valori])] — una sola serie = colonne semplici.
    formato: funzione valore -> testo (per etichette e riquadro).
    """
    n = len(etichette)
    if not n:
        return ""
    totali = [sum((s[1][i] for s in serie), 0) for i in range(n)]
    massimo = max(totali)
    passo, fondo = _scala(massimo)
    area = LARGHEZZA - MARGINE_SX - MARGINE_DX
    fascia = area / n
    spessore = min(SPESSORE_MAX, max(fascia - 2, 2))
    altezza_totale = MARGINE_ALTO + ALTEZZA_GRAFICO + FASCIA_ASSE
    base = MARGINE_ALTO + ALTEZZA_GRAFICO

    def y_di(valore):
        return base - ALTEZZA_GRAFICO * float(valore) / float(fondo)

    parti = [
        f'<svg class="grafico" viewBox="0 0 {LARGHEZZA} {altezza_totale}" role="img" aria-label="{escape(titolo)}" '
        f'xmlns="http://www.w3.org/2000/svg">'
    ]
    # Griglia e tacche (linee sottili e continue).
    valore = 0
    while valore <= fondo + 1e-9:
        y = y_di(valore)
        parti.append(f'<line x1="{MARGINE_SX}" x2="{LARGHEZZA - MARGINE_DX}" y1="{y:.1f}" y2="{y:.1f}" '
                     f'stroke="{ASSE if valore == 0 else GRIGLIA}" stroke-width="1"/>')
        parti.append(f'<text x="{MARGINE_SX - 6}" y="{y + 4:.1f}" text-anchor="end" class="g-tacca">{_numero_asse(valore)}</text>')
        valore += passo

    indice_picco = totali.index(massimo)
    for i, etichetta in enumerate(etichette):
        centro = MARGINE_SX + fascia * i + fascia / 2
        x = centro - spessore / 2
        # Segmenti dal basso verso l'alto, separati da 2 px di superficie.
        cima = base
        segmenti = [(nome, valori[i], SERIE[k % len(SERIE)]) for k, (nome, valori) in enumerate(serie)]
        visibili = [s for s in segmenti if s[1] > 0]
        for j, (_, v, colore) in enumerate(visibili):
            altezza = ALTEZZA_GRAFICO * float(v) / float(fondo)
            ultimo = j == len(visibili) - 1
            y = cima - altezza
            disegno_altezza = altezza - (0 if ultimo else 2)
            if disegno_altezza > 0:
                parti.append(f'<path d="{_colonna(x, y + (0 if ultimo else 2), spessore, disegno_altezza, arrotonda=ultimo)}" fill="{colore}"/>')
            cima = y
        if i == indice_picco and massimo > 0:
            parti.append(f'<text x="{centro:.1f}" y="{y_di(massimo) - 6:.1f}" text-anchor="middle" class="g-valore">{escape(formato(massimo))}</text>')
        if i % etichetta_ogni == 0:
            parti.append(f'<text x="{centro:.1f}" y="{base + 18}" text-anchor="middle" class="g-tacca">{escape(etichetta)}</text>')
        righe = [[formato(v), nome, SERIE[k % len(SERIE)]] for k, (nome, v, _) in enumerate(segmenti)]
        if len(serie) > 1:
            righe.append([formato(totali[i]), "Totale", ""])
        titolo_tip = suggerimenti_titoli[i] if suggerimenti_titoli else etichetta
        # Area sensibile: tutta la fascia, più grande della colonna.
        parti.append(
            f'<rect class="g-bersaglio" x="{MARGINE_SX + fascia * i:.1f}" y="{MARGINE_ALTO}" width="{fascia:.1f}" '
            f'height="{ALTEZZA_GRAFICO}" fill="transparent"{_suggerimento(titolo_tip, righe)}/>'
        )
    parti.append("</svg>")
    legenda = ""
    if len(serie) > 1:
        legenda = format_html(
            '<div class="g-legenda">{}</div>',
            mark_safe("".join(
                format_html('<span><i style="background:{}"></i>{}</span>', SERIE[k % len(SERIE)], nome)
                for k, (nome, _) in enumerate(serie)
            )),
        )
    return mark_safe(f'{legenda}<div class="g-scorrimento">{"".join(parti)}</div>')


def barre(voci, *, formato):
    """Barre orizzontali in HTML (i nomi vanno a capo da soli sul telefono).

    voci: [(nome, valore, [righe del riquadro])]
    """
    if not voci:
        return ""
    massimo = max(v for _, v, _ in voci) or 1
    righe = []
    for nome, valore, dettagli in voci:
        percentuale = float(Decimal(valore) / Decimal(massimo) * 100) if massimo else 0
        righe.append(format_html(
            '<div class="g-barra"{}><span class="g-barra-nome">{}</span>'
            '<span class="g-barra-traccia"><span class="g-barra-riempimento" style="width:{}%;background:{}"></span>'
            '<span class="g-barra-valore">{}</span></span></div>',
            _suggerimento(nome, dettagli), nome, f"{max(percentuale, 0.5):.2f}", SERIE[0], formato(valore),
        ))
    return mark_safe(f'<div class="g-barre">{"".join(righe)}</div>')
