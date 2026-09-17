from django import template

from ..quantita import formatta_numero, formatta_quantita, nome_unita

register = template.Library()


@register.filter
def num(valore):
    """2.500 -> "2,5"."""
    return formatta_numero(valore)


@register.filter
def qta(valore, unita):
    """Quantità con unità: {{ q|qta:prodotto.unita }} -> "2,5 kg"."""
    if valore is None or valore == "":
        return ""
    return formatta_quantita(valore, unita)


@register.filter
def unita(codice, valore=None):
    return nome_unita(codice, valore)


@register.filter
def prendi(dizionario, chiave):
    if not dizionario:
        return None
    return dizionario.get(chiave)


@register.filter
def qta_segno(valore, unita):
    """Come qta ma con il segno anche per le entrate: "+2,5 kg", "-3 pz"."""
    if valore is None or valore == "":
        return ""
    testo = formatta_quantita(valore, unita)
    return f"+{testo}" if valore > 0 else testo
