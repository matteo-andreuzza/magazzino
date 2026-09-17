from decimal import Decimal, InvalidOperation

CENTESIMO = Decimal("0.01")


def euro(valore, simbolo=True):
    """Decimal -> "1.234,50 €"."""
    if valore is None or valore == "":
        return ""
    q = Decimal(valore).quantize(CENTESIMO)
    intero, decimali = f"{abs(q):.2f}".split(".")
    testo = f"{'-' if q < 0 else ''}{int(intero):,}".replace(",", ".") + f",{decimali}"
    return f"{testo} €" if simbolo else testo


def leggi_euro(testo):
    """ "12,50" / "12.5" / "12" -> Decimal con due decimali. None se vuoto; ValueError se non valido."""
    s = str(testo or "").strip().replace("€", "").replace(" ", "")
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        valore = Decimal(s)
    except InvalidOperation:
        raise ValueError("Scrivi il prezzo in euro, per esempio 12,50.") from None
    if not valore.is_finite() or valore < 0:
        raise ValueError("Il prezzo non può essere negativo.")
    if valore != valore.quantize(CENTESIMO):
        raise ValueError("Il prezzo può avere al massimo due decimali.")
    if valore >= Decimal("100000"):
        raise ValueError("Prezzo troppo alto: controlla di aver scritto bene.")
    return valore.quantize(CENTESIMO)
