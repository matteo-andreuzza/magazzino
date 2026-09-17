import hashlib
import json
from decimal import Decimal
from functools import wraps

from django.http import JsonResponse
from django.shortcuts import render
from django.utils.http import url_has_allowed_host_and_scheme

from ..models import ImpostazioniCassa, StatoStampa
from ..services import impostazioni


def solo_staff(vista):
    """Configurazione della cassa: solo per chi ha accesso all'amministrazione."""

    @wraps(vista)
    def controlla(request, *args, **kwargs):
        if not request.user.is_staff:
            return render(request, "cassa/solo_staff.html", status=403)
        return vista(request, *args, **kwargs)

    return controlla


def funzione_attiva(nome, *, json_=False):
    """Le funzioni smart spente non si aprono (e non compaiono nei menu)."""

    def decoratore(vista):
        @wraps(vista)
        def controlla(request, *args, **kwargs):
            if not getattr(impostazioni(), nome):
                etichetta = ImpostazioniCassa._meta.get_field(nome).verbose_name
                if json_:
                    return JsonResponse({"ok": False, "errori": [f"La funzione «{etichetta}» non è attiva."]}, status=404)
                return render(request, "cassa/funzione_spenta.html", {"funzione": etichetta}, status=404)
            return vista(request, *args, **kwargs)

        return controlla

    return decoratore


def leggi_json(request):
    try:
        dati = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return None
    return dati if isinstance(dati, dict) else None


def errore_json(messaggi, status=400, **altro):
    if isinstance(messaggi, str):
        messaggi = [messaggi]
    return JsonResponse({"ok": False, "errori": messaggi, **altro}, status=status)


def centesimi(valore):
    return int((Decimal(valore) * 100).to_integral_value())


def versione_listino(articoli):
    """Impronta di ciò che la cassa mostra: se cambia, la pagina va aggiornata."""
    impronta = hashlib.sha1()
    for a in articoli:
        impronta.update(
            f"{a.pk}|{a.nome}|{a.prezzo}|{a.esaurito}|{a.ordine}|{a.categoria_id}|"
            f"{a.categoria.nome}|{a.categoria.colore}|{a.categoria.ordine};".encode()
        )
    return impronta.hexdigest()[:16]


def riassunto_stampe(stampe):
    """Stato complessivo delle stampe di un ordine: "errore", "coda", "ok" o "" (nessuna)."""
    stati = {s.stato for s in stampe if s.stato != StatoStampa.ANNULLATA}
    if StatoStampa.ERRORE in stati:
        return "errore"
    if stati & {StatoStampa.IN_CODA, StatoStampa.IN_STAMPA}:
        return "coda"
    if StatoStampa.STAMPATA in stati:
        return "ok"
    return ""


def indirizzo_sicuro(request, url, predefinito):
    if url and url_has_allowed_host_and_scheme(url, allowed_hosts={request.get_host()}):
        return url
    return predefinito
