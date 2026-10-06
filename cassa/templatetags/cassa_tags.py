from django import template

from ..formati import euro as formatta_euro

register = template.Library()


@register.filter
def euro(valore):
    """12.5 -> "12,50 €"."""
    return formatta_euro(valore)


@register.filter
def euro_numero(valore):
    """12.5 -> "12,50" (senza simbolo, per i campi dei moduli)."""
    return formatta_euro(valore, simbolo=False)


@register.filter
def minuti(valore):
    if valore is None:
        return ""
    testo = f"{valore:.1f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{testo} min"


@register.inclusion_tag("cassa/_biglietto.html")
def biglietto(righe):
    return {"righe": righe}


@register.filter
def classi_riga(stile):
    classi = []
    if "b" in stile:
        classi.append("b-grassetto")
    if "alto" in stile:
        classi.append("b-alto")
    if "largo" in stile:
        classi.append("b-largo")
    if "inverso" in stile:
        classi.append("b-inverso")
    return " ".join(classi)
