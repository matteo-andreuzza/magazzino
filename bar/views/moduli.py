"""Carichi e correzioni del magazzino del bar (stesso modulo a elenco del magazzino)."""

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from magazzino.quantita import ZERO
from magazzino.views.comuni import RigaModulo, aggiungi_righe_inviate, conta_righe, leggi_righe, leggi_token, nuovo_token

from .. import services
from ..models import Bevanda, OperazioneBar, TipoFusto, TipoOperazioneBar
from ..services import ErroreBar

TIPI_CARICO = [
    (TipoOperazioneBar.INVENTARIO, "Inventario iniziale", "Quello che c'è già a inizio sagra: lattine, bottiglie, fusti pieni."),
    (TipoOperazioneBar.CARICO, "Consegna / acquisto", "Merce arrivata adesso."),
]


GRUPPI = {"lattina": "Lattine", "bottiglia": "Bottiglie", "litri": "A litri"}
ORDINE_GRUPPI = list(GRUPPI.values())


def _gruppo(cosa):
    # I fusti pieni divisi per bevanda: «Fusto da 50 litri» c'è sia per la bionda sia per la rossa.
    return f"Fusti di {cosa.bevanda.nome.lower()}" if isinstance(cosa, TipoFusto) else GRUPPI[cosa.tipo]


def _ordine(riga):
    cosa = riga.prodotto
    if isinstance(cosa, TipoFusto):
        return (len(ORDINE_GRUPPI), cosa.bevanda.ordine, cosa.bevanda.nome.lower(), cosa.ordine, cosa.nome.lower())
    return (ORDINE_GRUPPI.index(riga.gruppo), 0, "", cosa.ordine, cosa.nome.lower())


def _righe(attuali, *, solo_attive=True):
    """Una riga per ogni bevanda (non alla spina) e per ogni tipo di fusto pieno."""
    bevande, pieni = attuali

    def riga_per(cosa):
        fusto = isinstance(cosa, TipoFusto)
        return RigaModulo(
            chiave=f"f{cosa.pk}" if fusto else f"b{cosa.pk}",
            prodotto=cosa,
            attuale=(pieni if fusto else bevande).get(cosa.pk, ZERO),
            gruppo=_gruppo(cosa),
        )

    def crea_riga(chiave):
        modello = {"f": TipoFusto, "b": Bevanda}.get(chiave[:1])
        cosa = modello.objects.filter(pk=int(chiave[1:])).first() if modello and chiave[1:].isdigit() else None
        if cosa is None or (isinstance(cosa, Bevanda) and cosa.a_fusti):
            return None
        return riga_per(cosa)

    elenco = [b for b in Bevanda.objects.exclude(tipo="fusti") if b.attiva or not solo_attive or bevande.get(b.pk)]
    elenco += [t for t in TipoFusto.objects.select_related("bevanda") if t.attivo or not solo_attive or pieni.get(t.pk)]
    righe = [riga_per(c) for c in elenco]
    righe.sort(key=_ordine)
    return righe, crea_riga


def _elabora(request, righe, *, crea_riga, registra, messaggio, ammetti_zero=False, controlli=None):
    """Come nel magazzino: restituisce (risposta, contesto); con una risposta la registrazione è riuscita."""
    contesto = {"righe": righe, "errori": [], "token": nuovo_token(), "note": ""}
    if request.method != "POST":
        return None, contesto
    dati = request.POST
    contesto["note"] = dati.get("note", "").strip()
    token = leggi_token(dati)
    errori = []
    if token is None:
        errori.append("Il modulo non è valido: ricarica la pagina e riprova.")
    else:
        contesto["token"] = str(token)
        if OperazioneBar.objects.filter(token=token).exists():
            messages.info(request, "Questo modulo era già stato registrato: il secondo invio è stato ignorato.")
            return redirect("bar:inizio"), contesto

    errori += aggiungi_righe_inviate(dati, righe, crea_riga)
    valori, _ = leggi_righe(dati, righe, ammetti_zero=ammetti_zero)
    for r, v in valori:
        if isinstance(r.prodotto, TipoFusto) and v != v.to_integral_value():
            r.errore = "I fusti si contano interi."
    valori = [(r, v) for r, v in valori if not r.errore]
    if controlli is not None:
        errori += controlli(valori)
    if any(r.errore for r in righe):
        errori.insert(0, "Correggi le righe segnate in rosso.")
    elif not valori and not errori:
        errori.append("Non hai inserito nessuna quantità.")

    if not errori:
        try:
            operazione, creata = registra(valori, contesto["note"], token)
        except ErroreBar as e:
            per_chiave = {r.chiave: r for r in righe}
            for chiave, testo in e.errori:
                if chiave in per_chiave:
                    per_chiave[chiave].errore = testo
                else:
                    errori.append(testo)
        else:
            if creata:
                messages.success(request, messaggio(operazione, valori))
            else:
                messages.info(request, "Questo modulo era già stato registrato: il secondo invio è stato ignorato.")
            return redirect("bar:inizio"), contesto
    contesto["errori"] = errori
    return None, contesto


def _attuali():
    return services.giacenze_bevande(), services.fusti_pieni()


@require_http_methods(["GET", "POST"])
def carico(request):
    righe, crea_riga = _righe(_attuali())
    tipo = request.POST.get("tipo", "") if request.method == "POST" else request.GET.get("tipo", "")

    def controlli(valori):
        if tipo not in (TipoOperazioneBar.INVENTARIO, TipoOperazioneBar.CARICO):
            return ["Scegli se è l'inventario iniziale o una consegna."]
        return []

    def registra(valori, note, token):
        return services.registra_carico(
            tipo=tipo, voci=[(r.prodotto, v) for r, v in valori], utente=request.user, note=note, token=token
        )

    risposta, contesto = _elabora(
        request, righe, crea_riga=crea_riga, registra=registra, controlli=controlli,
        messaggio=lambda op, valori: f"{op.get_tipo_display()} del bar registrato: {conta_righe(len(valori))}.",
    )
    if risposta:
        return risposta
    contesto.update(
        titolo="Carica il bar",
        sottotitolo="Scrivi quante lattine, bottiglie e fusti pieni arrivano. Le righe vuote vengono ignorate.",
        tipi_carico=TIPI_CARICO,
        tipo=tipo,
        etichetta_attuale="In magazzino",
        etichetta_input="Aggiungi",
        testo_pulsante="Registra carico",
        gruppi=_gruppi(righe),
        placeholder_note="Note (es. fornitore)",
        elenco_vuoto="Non ci sono bevande: aggiungile da «Bevande e fusti».",
        annulla_url=reverse("bar:inizio"),
    )
    return render(request, "magazzino/modulo_righe.html", contesto)


@require_http_methods(["GET", "POST"])
def correggi(request):
    righe, crea_riga = _righe(_attuali(), solo_attive=False)

    def registra(valori, note, token):
        return services.registra_rettifica(
            conteggi=[(r.prodotto, v) for r, v in valori], utente=request.user, note=note, token=token
        )

    def messaggio(operazione, valori):
        n = operazione.movimenti.count()
        return f"Correzione registrata: {n} giacenz{'a' if n == 1 else 'e'} corrett{'a' if n == 1 else 'e'}."

    risposta, contesto = _elabora(
        request, righe, crea_riga=crea_riga, registra=registra, messaggio=messaggio, ammetti_zero=True
    )
    if risposta:
        return risposta
    contesto.update(
        titolo="Correggi le giacenze del bar",
        sottotitolo=(
            "Conta cosa c'è davvero (lattine, bottiglie, fusti pieni non ancora attaccati) e scrivi "
            "la quantità reale, anche 0. Le righe vuote non vengono toccate. I fusti attaccati si pesano a fine "
            "serata dalla pagina dei fusti."
        ),
        etichetta_attuale="Secondo l'app",
        etichetta_input="Contato",
        testo_pulsante="Registra correzione",
        gruppi=_gruppi(righe),
        placeholder_note="Note (facoltative)",
        annulla_url=reverse("bar:inizio"),
    )
    return render(request, "magazzino/modulo_righe.html", contesto)


def _gruppi(righe):
    visti = []
    for r in righe:
        if r.gruppo not in visti:
            visti.append(r.gruppo)
    return visti


@require_http_methods(["GET", "POST"])
def operazione_annulla(request, pk):
    op = OperazioneBar.objects.filter(pk=pk).first()
    if op is None:
        messages.info(request, "Questa operazione non esiste più (forse è già stata annullata).")
        return redirect("bar:storico")
    if request.method == "POST":
        services.annulla_operazione(op)
        messages.success(request, "Operazione annullata: le giacenze del bar sono tornate come prima.")
        return redirect("bar:storico")
    return render(
        request,
        "bar/operazione_annulla.html",
        {"operazione": op, "movimenti": op.movimenti.select_related("bevanda", "tipo_fusto")},
    )
