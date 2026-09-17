"""Lettura e formattazione delle quantità.

Chi usa l'app scrive in italiano ("2,5") ma la tastiera del telefono può
proporre il punto ("2.5"): accettiamo entrambi come separatore decimale.
"""

import re
from decimal import Decimal

ZERO = Decimal("0")
MAX_QUANTITA = Decimal("100000")
DECIMALI = 3

_NUMERO = re.compile(r"^([0-9]+([.,][0-9]*)?|[.,][0-9]+)$")

# unità -> (singolare, plurale)
FORME_UNITA = {
    "kg": ("kg", "kg"),
    "l": ("l", "l"),
    "pz": ("pz", "pz"),
    "conf": ("conf.", "conf."),
    "busta": ("busta", "buste"),
    "vaso": ("vaso", "vasi"),
    "vaschetta": ("vaschetta", "vaschette"),
    "cartone": ("cartone", "cartoni"),
    "bottiglia": ("bottiglia", "bottiglie"),
    "lattina": ("lattina", "lattine"),
    "mazzo": ("mazzo", "mazzi"),
}


class QuantitaNonValida(ValueError):
    pass


def leggi_quantita(testo, *, ammetti_zero=False):
    """Converte il testo inserito in Decimal.

    Restituisce None se il campo è vuoto (o vale zero e lo zero non è
    ammesso: "0" significa "niente"). Solleva QuantitaNonValida se il testo
    non è una quantità accettabile.
    """
    if testo is None:
        return None
    s = str(testo).strip().replace(" ", "")
    if s == "":
        return None
    if s.startswith("-"):
        raise QuantitaNonValida("La quantità non può essere negativa.")
    if not _NUMERO.match(s):
        raise QuantitaNonValida("Scrivi solo un numero, per esempio 3 oppure 2,5.")
    s = s.replace(",", ".")
    if s.endswith("."):
        s = s[:-1]
    if s.startswith("."):
        s = "0" + s
    valore = Decimal(s)
    if valore == ZERO:
        if ammetti_zero:
            return ZERO
        return None
    if valore.normalize().as_tuple().exponent < -DECIMALI:
        raise QuantitaNonValida(f"Usa al massimo {DECIMALI} decimali.")
    if valore > MAX_QUANTITA:
        raise QuantitaNonValida("Quantità troppo grande: controlla di aver scritto bene.")
    return valore


def formatta_numero(valore):
    """Decimal -> testo italiano senza zeri inutili: 2.500 -> "2,5"."""
    if valore is None or valore == "":
        return ""
    d = Decimal(valore)
    if d == ZERO:
        return "0"
    testo = format(d.normalize(), "f")
    return testo.replace(".", ",")


def nome_unita(unita, valore=None):
    singolare, plurale = FORME_UNITA.get(unita, (unita, unita))
    if valore is not None and abs(Decimal(valore)) == 1:
        return singolare
    return plurale


def formatta_quantita(valore, unita):
    return f"{formatta_numero(valore)} {nome_unita(unita, valore)}"
