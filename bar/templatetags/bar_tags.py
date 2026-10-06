"""Disegni della pagina dei fusti: i fusti e le spinatrici del bancone.

Sono SVG semplici, a fumetto, fatti per riconoscere le cose a colpo d'occhio:

* il fusto ha la forma e il colore del suo tipo («alto nero», «largo
  argento», il 50 litri d'acciaio) e dentro la bevanda del suo colore (bionda,
  rossa, vino bianco, vino rosso), alta quanto ne resta; sul bollino i litri;
* la spinatrice ha una spina per ogni rubinetto, con il pomello del colore
  della bevanda e sotto il bicchiere (calice per il vino), pieno se alla spina
  è attaccato un fusto.

I colori vengono dal foglio di stile (classi f-…, s-…, l-…), così vanno anche
in modalità scura; le sfumature sono definite una volta sola nella pagina (vedi
bar/_definizioni_svg.html).
"""

from django import template
from django.utils.safestring import mark_safe

from magazzino.quantita import formatta_numero

register = template.Library()

# Geometria delle forme (viewBox largo 100). Sullo schermo i fusti hanno tutti la
# stessa larghezza: quello alto risulta alto, il largo basso e tozzo, il piccolo piccolo.
FORME = {
    "alto": {
        "alto": 150, "x": 8, "largo": 84, "anello": 20, "corpo_x": 12, "corpo_largo": 76,
        "dentro": (28, 126), "nervature": (50, 98), "etichetta": (74, 17), "maniglie": True, "testo": (14, 9),
    },
    "largo": {
        "alto": 104, "x": 2, "largo": 96, "anello": 16, "corpo_x": 5, "corpo_largo": 90,
        "dentro": (24, 84), "nervature": (52,), "etichetta": (52, 15), "maniglie": True, "testo": (13, 8),
    },
    "basso": {
        "alto": 108, "x": 17, "largo": 66, "anello": 16, "corpo_x": 20, "corpo_largo": 60,
        "dentro": (24, 86), "nervature": (36,), "etichetta": (62, 13), "maniglie": False, "testo": (11, 7),
    },
}


@register.simple_tag
def fusto_svg(tipo, riempimento=1.0, classe=""):
    """Un fusto del tipo indicato, pieno per la frazione `riempimento` (da 0 a 1)."""
    g = FORME.get(tipo.forma, FORME["alto"])
    alto, x, largo, anello = g["alto"], g["x"], g["largo"], g["anello"]
    cx, cl = g["corpo_x"], g["corpo_largo"]
    alto_dentro, basso_dentro = g["dentro"]
    frazione = min(max(float(riempimento or 0), 0.0), 1.0)
    liquido = tipo.bevanda.colore
    h_dentro = (basso_dentro - alto_dentro) * frazione
    parti = [
        f'<svg class="fusto-svg forma-{tipo.forma} {classe}" viewBox="0 0 100 {alto}" aria-hidden="true" focusable="false">',
        # Valvola (la «testa» dove si attacca il tubo della spina).
        '<rect class="f-valvola" x="40" y="0" width="20" height="12" rx="3"/>',
        '<rect class="f-valvola-centro" x="45" y="3" width="10" height="5" rx="2"/>',
        f'<rect class="f-corpo c-{tipo.colore}" x="{cx}" y="{anello}" width="{cl}" height="{alto - 2 * anello}" rx="4"/>',
        # La «finestra» per vedere quanto ne resta.
        f'<rect class="f-finestra" x="{cx + 3}" y="{alto_dentro}" width="{cl - 6}" height="{basso_dentro - alto_dentro}" rx="2"/>',
    ]
    if h_dentro > 0.5:
        y = basso_dentro - h_dentro
        parti.append(f'<rect class="f-liquido l-{liquido}" x="{cx + 3}" y="{y:.1f}" width="{cl - 6}" height="{h_dentro:.1f}" rx="2"/>')
        parti.append(f'<rect class="f-schiuma l-{liquido}" x="{cx + 3}" y="{y:.1f}" width="{cl - 6}" height="{min(4.0, h_dentro):.1f}" rx="2"/>')
    parti.append(f'<rect class="f-riflesso" x="{cx + 6}" y="{anello + 8}" width="5" height="{alto - 2 * anello - 12}" rx="2.5"/>')
    for y in g["nervature"]:
        parti.append(f'<rect class="f-nervatura c-{tipo.colore}" x="{x + 1}" y="{y}" width="{largo - 2}" height="6" rx="3"/>')
    # Anelli sopra e sotto, con i buchi delle maniglie nei fusti grandi.
    parti.append(f'<rect class="f-anello c-{tipo.colore}" x="{x}" y="10" width="{largo}" height="{anello - 2}" rx="6"/>')
    if g["maniglie"]:
        parti.append(f'<rect class="f-maniglia" x="{x + 10}" y="15" width="20" height="7" rx="3.5"/>')
        parti.append(f'<rect class="f-maniglia" x="{x + largo - 30}" y="15" width="20" height="7" rx="3.5"/>')
    parti.append(f'<rect class="f-anello c-{tipo.colore}" x="{x}" y="{alto - anello - 2}" width="{largo}" height="{anello}" rx="6"/>')
    cy, r = g["etichetta"]
    grande, piccolo = g["testo"]
    parti.append(f'<circle class="f-bollino l-{liquido}" cx="50" cy="{cy}" r="{r}"/>')
    parti.append(
        f'<text class="f-litri l-{liquido}" x="50" y="{cy + 1}" text-anchor="middle" dominant-baseline="middle" '
        f'font-size="{grande}">{formatta_numero(tipo.litri)}<tspan font-size="{piccolo}"> L</tspan></text>'
    )
    parti.append("</svg>")
    return mark_safe("".join(parti))


LARGO_SPINA = 120


def _rubinetto(i, spina, bevanda, attaccato):
    """Pomello, beccuccio e bicchiere (calice per il vino) di una spina, centrati sulla sua colonna."""
    c = LARGO_SPINA * i + LARGO_SPINA // 2
    colore = bevanda.colore if bevanda else "nessuno"
    parti = [
        f'<rect class="s-leva" x="{c - 6}" y="2" width="12" height="32" rx="6"/>',
        f'<circle class="s-pomello l-{colore}" cx="{c}" cy="9" r="9"/>',
        f'<rect class="s-beccuccio" x="{c - 4}" y="48" width="8" height="14" rx="2"/>',
    ]
    vino = bevanda is not None and bevanda.vino
    if vino:
        # Calice: coppa, gambo e piede.
        parti.append(f'<path class="s-bicchiere" d="M{c - 15} 86 L{c + 15} 86 Q{c + 15} 108 {c} 109 Q{c - 15} 108 {c - 15} 86 Z"/>')
        parti.append(f'<rect class="s-bicchiere" x="{c - 1.5}" y="109" width="3" height="13"/>')
        parti.append(f'<rect class="s-bicchiere" x="{c - 10}" y="121" width="20" height="4" rx="2"/>')
        if attaccato:
            parti.append(f'<rect class="s-getto l-{colore}" x="{c - 1.5}" y="62" width="3" height="30" rx="1.5"/>')
            parti.append(f'<path class="s-liquido l-{colore}" d="M{c - 13.5} 95 L{c + 13.5} 95 Q{c + 13} 106.5 {c} 107 Q{c - 13} 106.5 {c - 13.5} 95 Z"/>')
    else:
        parti.append(f'<path class="s-bicchiere" d="M{c - 16} 86 L{c + 16} 86 L{c + 13} 126 L{c - 13} 126 Z"/>')
        if attaccato:
            parti.append(f'<rect class="s-getto l-{colore}" x="{c - 1.5}" y="62" width="3" height="40" rx="1.5"/>')
            parti.append(f'<path class="s-liquido l-{colore}" d="M{c - 13.4} 100 L{c + 13.4} 100 L{c + 12.2} 124 L{c - 12.2} 124 Z"/>')
            parti.append(f'<path class="s-schiuma l-{colore}" d="M{c - 14} 92 Q{c - 8} 86 {c} 91 Q{c + 7} 86 {c + 14} 92 L{c + 13.6} 100 L{c - 13.6} 100 Z"/>')
    return "".join(parti)


@register.simple_tag
def spinatrice_svg(spine):
    """La spinatrice sul bancone, con una spina per ogni voce di `spine`: [(spina, fusto attaccato o None)].

    Ogni spina occupa la stessa larghezza (LARGO_SPINA): sotto il disegno le
    colonne dei fusti attaccati, larghe uguali, cadono proprio sotto la loro spina.
    """
    n = max(len(spine), 1)
    largo = LARGO_SPINA * n
    centro = largo / 2
    parti = [
        f'<svg class="spinatrice-svg" viewBox="0 0 {largo} 150" aria-hidden="true" focusable="false">',
        # Bancone e vaschetta.
        f'<rect class="s-bancone" x="0" y="134" width="{largo}" height="16" rx="3"/>',
        f'<rect class="s-bancone-bordo" x="0" y="134" width="{largo}" height="4" rx="2"/>',
        f'<rect class="s-vaschetta" x="14" y="126" width="{largo - 28}" height="9" rx="3"/>',
        f'<rect class="s-griglia" x="18" y="127" width="{largo - 36}" height="2" rx="1"/>',
        # Colonna e testa che porta le spine.
        f'<rect class="s-colonna" x="{centro - 11}" y="40" width="22" height="88" rx="5"/>',
        f'<rect class="s-riflesso" x="{centro - 7}" y="46" width="4" height="76" rx="2"/>',
        f'<rect class="s-testa" x="{LARGO_SPINA / 2 - 32}" y="28" width="{largo - LARGO_SPINA + 64}" height="22" rx="11"/>',
        f'<rect class="s-riflesso" x="{LARGO_SPINA / 2 - 24}" y="32" width="{largo - LARGO_SPINA + 48}" height="4" rx="2"/>',
    ]
    for i, (spina, attaccato) in enumerate(spine):
        bevanda = attaccato.tipo.bevanda if attaccato else spina.bevanda
        parti.append(_rubinetto(i, spina, bevanda, attaccato))
    parti.append("</svg>")
    return mark_safe("".join(parti))
