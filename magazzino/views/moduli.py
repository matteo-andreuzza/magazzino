"""Moduli che registrano movimenti di magazzino."""

import re

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .. import services
from ..models import (
    Allegato,
    Ingrediente,
    Movimento,
    Operazione,
    Prodotto,
    Servizio,
    TipoOperazione,
    TipoUbicazione,
    Ubicazione,
)
from ..quantita import ZERO
from ..services import ErroreMagazzino
from .comuni import (
    RigaModulo,
    aggiungi_righe_inviate,
    assegna_errori,
    conta_righe,
    esito_registrazione,
    leggi_righe,
    leggi_token,
    nuovo_token,
    ordina_per_categoria,
    vai_a_operazione,
)

_CHIAVE_DOPPIA = re.compile(r"^(\d+)-(\d+)$")


def _elabora(request, righe, *, crea_riga, registra, messaggio, ammetti_zero=False, controlli=None):
    """Gestione comune dei moduli a elenco.

    Restituisce (risposta, contesto): se la risposta non è None la
    registrazione è andata a buon fine e bisogna restituirla.
    """
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
        # Doppio invio (doppio tocco, pagina ricaricata): non registrare di nuovo.
        gia_registrata = Operazione.objects.filter(token=token).first()
        if gia_registrata is not None:
            esito_registrazione(request, gia_registrata, False, "")
            return vai_a_operazione(gia_registrata), contesto

    errori += aggiungi_righe_inviate(dati, righe, crea_riga)
    valori, _ = leggi_righe(dati, righe, ammetti_zero=ammetti_zero)
    if controlli is not None:
        errori += controlli(valori)
    if any(r.errore for r in righe):
        errori.insert(0, "Correggi le righe segnate in rosso.")
    elif not valori and not errori:
        errori.append("Non hai inserito nessuna quantità.")

    if not errori:
        try:
            operazione, creata = registra(valori, contesto["note"], token)
        except ErroreMagazzino as e:
            generali = assegna_errori(righe, e)
            if any(r.errore for r in righe):
                generali.insert(0, "Correggi le righe segnate in rosso.")
            errori += generali
        else:
            esito_registrazione(request, operazione, creata, messaggio(operazione, valori))
            return vai_a_operazione(operazione), contesto

    contesto["errori"] = errori
    return None, contesto


def _gruppi(righe):
    visti = []
    for r in righe:
        if r.gruppo not in visti:
            visti.append(r.gruppo)
    return visti


# ---------------------------------------------------------------------------
# Carico: inventario iniziale e acquisti
# ---------------------------------------------------------------------------

TIPI_CARICO = [
    (TipoOperazione.INVENTARIO, "Inventario iniziale", "Quello che c'era già in magazzino a inizio sagra."),
    (TipoOperazione.ACQUISTO, "Acquisto / consegna", "Merce comprata o arrivata adesso."),
]


def carico(request, ubicazione_pk):
    ubicazione = get_object_or_404(Ubicazione, pk=ubicazione_pk)
    giacenze = services.giacenze(ubicazioni=[ubicazione.pk])

    def riga_per(prodotto):
        return RigaModulo(
            chiave=str(prodotto.pk),
            prodotto=prodotto,
            ubicazione=ubicazione,
            attuale=giacenze.get((prodotto.pk, ubicazione.pk), ZERO),
            gruppo=prodotto.get_categoria_display(),
        )

    def crea_riga(chiave):
        prodotto = Prodotto.objects.filter(pk=int(chiave)).first() if chiave.isdigit() else None
        return riga_per(prodotto) if prodotto else None

    righe = [riga_per(p) for p in ordina_per_categoria(Prodotto.objects.filter(attivo=True))]
    tipo = request.POST.get("tipo", "") if request.method == "POST" else ""
    files = request.FILES.getlist("allegati") if request.method == "POST" else []

    def controlli(valori):
        errori = []
        if tipo not in (TipoOperazione.INVENTARIO, TipoOperazione.ACQUISTO):
            errori.append("Scegli se è l'inventario iniziale o un acquisto.")
        try:
            services.controlla_allegati(files)
        except ErroreMagazzino as e:
            errori += e.messaggi
        return errori

    def registra(valori, note, token):
        return services.registra_carico(
            tipo=tipo,
            ubicazione=ubicazione,
            quantita=[(r.prodotto, v) for r, v in valori],
            utente=request.user,
            note=note,
            token=token,
            allegati=files,
        )

    def messaggio(operazione, valori):
        testo = f"Carico registrato in {ubicazione.nome}: {conta_righe(len(valori))}"
        n = Allegato.objects.filter(operazione=operazione).count()
        if n:
            testo += ", 1 allegato" if n == 1 else f", {n} allegati"
        return testo + "."

    risposta, contesto = _elabora(
        request, righe, crea_riga=crea_riga, registra=registra, controlli=controlli, messaggio=messaggio
    )
    if risposta:
        return risposta
    if files and contesto["errori"]:
        contesto["errori"].append("Dopo un errore i file scelti non restano nel modulo: selezionali di nuovo.")
    contesto.update(
        titolo=f"Carica merce in {ubicazione.nome}",
        sottotitolo="Scrivi la quantità solo per i prodotti che stai mettendo qui. Le righe vuote vengono ignorate.",
        ubicazione=ubicazione,
        tipi_carico=TIPI_CARICO,
        tipo=tipo,
        etichetta_attuale="Già presente qui",
        etichetta_input="Aggiungi",
        testo_pulsante="Registra carico",
        gruppi=_gruppi(righe),
        placeholder_note="Note (es. fornitore)",
        mostra_nuovo_prodotto=True,
        allegati_ammessi=True,
        annulla_url=reverse("ubicazione_dettaglio", args=[ubicazione.pk]),
    )
    return render(request, "magazzino/modulo_righe.html", contesto)


# ---------------------------------------------------------------------------
# Servizio: messo a cucinare / rientrato
# ---------------------------------------------------------------------------


def _servizio_terminato(request, servizio):
    d = servizio.desinenza
    messages.info(request, f"{servizio.get_tipo_display()} terminat{d}: per registrare altro bisogna prima riaprirl{d}.")
    return redirect("servizio_dettaglio", pk=servizio.pk)


def servizio_cucina(request, pk):
    servizio = get_object_or_404(Servizio, pk=pk)
    if servizio.chiuso:
        return _servizio_terminato(request, servizio)
    ubicazioni = Ubicazione.objects.in_bulk()
    giacenze = services.giacenze()
    prodotti = Prodotto.objects.in_bulk({pid for pid, _ in giacenze})
    prodotti_menu = set(
        Ingrediente.objects.filter(piatto__servizi=servizio).values_list("prodotto_id", flat=True)
    )
    consumi = services.consumi_servizio(servizio)

    def riga_per(prodotto, ubicazione):
        disponibile = giacenze.get((prodotto.pk, ubicazione.pk), ZERO)
        info = []
        preso = consumi.get(prodotto.pk, {}).get("preso", ZERO)
        if preso > ZERO:
            info.append(f"già messo a cucinare: {prodotto.formatta(preso)}")
        return RigaModulo(
            chiave=f"{prodotto.pk}-{ubicazione.pk}",
            prodotto=prodotto,
            ubicazione=ubicazione,
            attuale=disponibile,
            massimo=max(disponibile, ZERO),
            gruppo=ubicazione.nome,
            in_menu=prodotto.pk in prodotti_menu,
            info=info,
        )

    def crea_riga(chiave):
        m = _CHIAVE_DOPPIA.match(chiave)
        if not m:
            return None
        prodotto = Prodotto.objects.filter(pk=int(m[1])).first()
        ubicazione = ubicazioni.get(int(m[2]))
        if prodotto is None or ubicazione is None:
            return None
        return riga_per(prodotto, ubicazione)

    righe = [
        riga_per(prodotti[pid], ubicazioni[uid])
        for (pid, uid), q in giacenze.items()
        if q > ZERO
    ]
    righe.sort(key=lambda r: (r.ubicazione.ordine, r.ubicazione.nome, r.prodotto.nome.lower()))

    def registra(valori, note, token):
        return services.registra_cucina(
            servizio=servizio,
            prelievi=[(r.prodotto, r.ubicazione, v) for r, v in valori],
            utente=request.user,
            note=note,
            token=token,
        )

    risposta, contesto = _elabora(
        request,
        righe,
        crea_riga=crea_riga,
        registra=registra,
        messaggio=lambda op, valori: f"Registrato: {conta_righe(len(valori))} messe a cucinare.",
    )
    if risposta:
        return risposta
    contesto.update(
        titolo="Metti a cucinare",
        servizio=servizio,
        sottotitolo=(
            "Scrivi quanto prendi da ogni frigo o congelatore. Puoi tornare qui durante il servizio "
            "per aggiungere altro: ogni invio si somma ai precedenti."
        ),
        etichetta_attuale="Disponibile",
        etichetta_input="Prendo",
        testo_pulsante="Registra: messo a cucinare",
        gruppi=_gruppi(righe),
        filtro_menu=bool(prodotti_menu),
        placeholder_note="Note (facoltative)",
        annulla_url=reverse("servizio_dettaglio", args=[servizio.pk]),
    )
    return render(request, "magazzino/modulo_righe.html", contesto)


def servizio_reso(request, pk):
    servizio = get_object_or_404(Servizio, pk=pk)
    if servizio.chiuso:
        return _servizio_terminato(request, servizio)
    consumi = services.consumi_servizio(servizio)
    prelievi = services.prelievi_per_ubicazione(servizio)
    ubicazioni_attive = list(Ubicazione.objects.filter(attiva=True))
    per_id = {str(u.pk): u for u in ubicazioni_attive}

    def riga_per(prodotto):
        c = consumi.get(prodotto.pk, {"preso": ZERO, "rientrato": ZERO})
        massimo = max(c["preso"] - c["rientrato"], ZERO)
        info = [f"messo a cucinare: {prodotto.formatta(c['preso'])}"]
        if c["rientrato"] > ZERO:
            info.append(f"già rientrato: {prodotto.formatta(c['rientrato'])}")
        # Si propone il frigo da cui è uscito, ma mai un congelatore: la merce
        # scongelata non va ricongelata, quindi lì la scelta deve essere esplicita.
        da_dove = prelievi.get(prodotto.pk, {})
        scelta = ""
        if da_dove:
            piu_usata = per_id.get(str(max(da_dove.items(), key=lambda x: x[1])[0]))
            if piu_usata is not None and piu_usata.tipo != TipoUbicazione.CONGELATORE:
                scelta = str(piu_usata.pk)
        return RigaModulo(
            chiave=str(prodotto.pk),
            prodotto=prodotto,
            attuale=massimo,
            massimo=massimo,
            gruppo=prodotto.get_categoria_display(),
            info=info,
            scegli_ubicazione=True,
            ubicazione_scelta=scelta,
            testo_massimo=(
                f"Ne possono rientrare al massimo {prodotto.formatta(massimo)}."
                if massimo > ZERO
                else "Non c'è niente da far rientrare per questo prodotto."
            ),
        )

    def crea_riga(chiave):
        prodotto = Prodotto.objects.filter(pk=int(chiave)).first() if chiave.isdigit() else None
        return riga_per(prodotto) if prodotto else None

    prodotti = Prodotto.objects.in_bulk(consumi.keys())
    righe = [
        riga_per(p)
        for p in ordina_per_categoria(prodotti.values())
        if consumi[p.pk]["preso"] - consumi[p.pk]["rientrato"] > ZERO
    ]

    def controlli(valori):
        for r in righe:
            r.ubicazione_scelta = request.POST.get(f"u-{r.prodotto.pk}", "")
        for r, _ in valori:
            r.ubicazione = per_id.get(r.ubicazione_scelta)
            if r.ubicazione is None:
                r.errore = "Scegli dove lo rimetti."
        return []

    def registra(valori, note, token):
        return services.registra_reso(
            servizio=servizio,
            rientri=[(r.prodotto, r.ubicazione, v) for r, v in valori],
            utente=request.user,
            note=note,
            token=token,
        )

    risposta, contesto = _elabora(
        request,
        righe,
        crea_riga=crea_riga,
        registra=registra,
        controlli=controlli,
        messaggio=lambda op, valori: f"Registrato: {conta_righe(len(valori))} rientrate in magazzino.",
    )
    if risposta:
        return risposta
    contesto.update(
        titolo="Rientro dalla cucina",
        servizio=servizio,
        sottotitolo=(
            "Quello che non è stato usato e torna in frigo o in dispensa. "
            "Scrivi la quantità e scegli dove lo rimetti: la merce scongelata non va rimessa in congelatore."
        ),
        etichetta_attuale="Può rientrare al massimo",
        etichetta_input="Rientra",
        testo_pulsante="Registra rientro",
        gruppi=_gruppi(righe),
        ubicazioni_scelta=ubicazioni_attive,
        placeholder_note="Note (facoltative)",
        elenco_vuoto="In questo servizio non c'è niente da far rientrare.",
        annulla_url=reverse("servizio_dettaglio", args=[servizio.pk]),
    )
    return render(request, "magazzino/modulo_righe.html", contesto)


# ---------------------------------------------------------------------------
# Spostamento, scarto, correzione conteggio
# ---------------------------------------------------------------------------


def _righe_presenti(ubicazione, giacenze, info=None):
    prodotti = Prodotto.objects.in_bulk(
        {pid for (pid, uid), q in giacenze.items() if uid == ubicazione.pk and q > ZERO}
    )

    def riga_per(prodotto):
        disponibile = giacenze.get((prodotto.pk, ubicazione.pk), ZERO)
        return RigaModulo(
            chiave=str(prodotto.pk),
            prodotto=prodotto,
            ubicazione=ubicazione,
            attuale=disponibile,
            massimo=max(disponibile, ZERO),
            gruppo=prodotto.get_categoria_display(),
            info=info(prodotto) if info else [],
        )

    def crea_riga(chiave):
        prodotto = Prodotto.objects.filter(pk=int(chiave)).first() if chiave.isdigit() else None
        return riga_per(prodotto) if prodotto else None

    return [riga_per(p) for p in ordina_per_categoria(prodotti.values())], crea_riga


def spostamento(request, da_pk, a_pk):
    da = get_object_or_404(Ubicazione, pk=da_pk)
    a = get_object_or_404(Ubicazione, pk=a_pk)
    if da.pk == a.pk:
        messages.error(request, "Partenza e destinazione devono essere diverse.")
        return redirect("spostamento_destinazione", da_pk=da.pk)
    giacenze = services.giacenze(ubicazioni=[da.pk, a.pk])

    def info(prodotto):
        return [f"in {a.nome}: {prodotto.formatta(giacenze.get((prodotto.pk, a.pk), ZERO))}"]

    righe, crea_riga = _righe_presenti(da, giacenze, info)

    def registra(valori, note, token):
        return services.registra_spostamento(
            da=da, a=a, quantita=[(r.prodotto, v) for r, v in valori], utente=request.user, note=note, token=token
        )

    risposta, contesto = _elabora(
        request,
        righe,
        crea_riga=crea_riga,
        registra=registra,
        messaggio=lambda op, valori: f"Spostamento registrato da {da.nome} a {a.nome}: {conta_righe(len(valori))}.",
    )
    if risposta:
        return risposta
    contesto.update(
        titolo=f"Sposta da {da.nome} a {a.nome}",
        sottotitolo="Per esempio per scongelare: scrivi quanto sposti di ogni prodotto.",
        etichetta_attuale=f"In {da.nome}",
        etichetta_input="Sposto",
        testo_pulsante="Registra spostamento",
        gruppi=_gruppi(righe),
        placeholder_note="Note (facoltative)",
        elenco_vuoto=f"In {da.nome} non c'è niente.",
        annulla_url=reverse("ubicazione_dettaglio", args=[da.pk]),
    )
    return render(request, "magazzino/modulo_righe.html", contesto)


def scarto(request, ubicazione_pk):
    ubicazione = get_object_or_404(Ubicazione, pk=ubicazione_pk)
    giacenze = services.giacenze(ubicazioni=[ubicazione.pk])
    righe, crea_riga = _righe_presenti(ubicazione, giacenze)

    def registra(valori, note, token):
        return services.registra_scarto(
            ubicazione=ubicazione,
            quantita=[(r.prodotto, v) for r, v in valori],
            utente=request.user,
            note=note,
            token=token,
        )

    risposta, contesto = _elabora(
        request,
        righe,
        crea_riga=crea_riga,
        registra=registra,
        messaggio=lambda op, valori: f"Scarto registrato in {ubicazione.nome}: {conta_righe(len(valori))}.",
    )
    if risposta:
        return risposta
    contesto.update(
        titolo=f"Scarto da {ubicazione.nome}",
        sottotitolo="Merce buttata perché scaduta, rovinata o caduta. Scrivi il motivo nelle note.",
        etichetta_attuale=f"In {ubicazione.nome}",
        etichetta_input="Butto",
        testo_pulsante="Registra scarto",
        gruppi=_gruppi(righe),
        placeholder_note="Motivo (es. scaduto)",
        elenco_vuoto=f"In {ubicazione.nome} non c'è niente.",
        annulla_url=reverse("ubicazione_dettaglio", args=[ubicazione.pk]),
    )
    return render(request, "magazzino/modulo_righe.html", contesto)


def rettifica(request, ubicazione_pk):
    ubicazione = get_object_or_404(Ubicazione, pk=ubicazione_pk)
    giacenze = services.giacenze(ubicazioni=[ubicazione.pk])
    presenti = {pid for (pid, _), q in giacenze.items() if q != ZERO}

    def riga_per(prodotto):
        return RigaModulo(
            chiave=str(prodotto.pk),
            prodotto=prodotto,
            ubicazione=ubicazione,
            attuale=giacenze.get((prodotto.pk, ubicazione.pk), ZERO),
            gruppo=prodotto.get_categoria_display(),
        )

    def crea_riga(chiave):
        prodotto = Prodotto.objects.filter(pk=int(chiave)).first() if chiave.isdigit() else None
        return riga_per(prodotto) if prodotto else None

    prodotti = Prodotto.objects.filter(attivo=True) | Prodotto.objects.filter(pk__in=presenti)
    righe = [riga_per(p) for p in ordina_per_categoria(prodotti.distinct())]

    def registra(valori, note, token):
        return services.registra_rettifica(
            ubicazione=ubicazione,
            conteggi=[(r.prodotto, v) for r, v in valori],
            utente=request.user,
            note=note,
            token=token,
        )

    def messaggio(operazione, valori):
        n = operazione.movimenti.count()
        return f"Correzione registrata in {ubicazione.nome}: {n} prodott{'o' if n == 1 else 'i'} corrett{'o' if n == 1 else 'i'}."

    risposta, contesto = _elabora(
        request, righe, crea_riga=crea_riga, registra=registra, messaggio=messaggio, ammetti_zero=True
    )
    if risposta:
        return risposta
    contesto.update(
        titolo=f"Correggi conteggio: {ubicazione.nome}",
        sottotitolo=(
            "Conta cosa c'è davvero e scrivi la quantità reale (anche 0). "
            "Le righe lasciate vuote non vengono toccate."
        ),
        etichetta_attuale="Secondo l'app",
        etichetta_input="Contato",
        testo_pulsante="Registra correzione",
        gruppi=_gruppi(righe),
        filtro_presenti=True,
        placeholder_note="Note (facoltative)",
        annulla_url=reverse("ubicazione_dettaglio", args=[ubicazione.pk]),
    )
    return render(request, "magazzino/modulo_righe.html", contesto)


# ---------------------------------------------------------------------------
# Annullamento
# ---------------------------------------------------------------------------


def _servizio_terminato_annulla(request, operazione):
    servizio = operazione.servizio
    d = servizio.desinenza
    messages.info(request, f"{servizio.get_tipo_display()} terminat{d}: per annullare bisogna prima riaprirl{d}.")
    return redirect("operazione_dettaglio", pk=operazione.pk)


@require_http_methods(["GET", "POST"])
def operazione_annulla(request, pk):
    operazione = Operazione.objects.select_related("servizio", "utente").filter(pk=pk).first()
    if operazione is None:
        messages.info(request, "Questa operazione non esiste più (forse è già stata annullata).")
        return redirect("operazioni")
    if request.method == "GET" and operazione.servizio and operazione.servizio.chiuso:
        return _servizio_terminato_annulla(request, operazione)
    if request.method == "POST":
        try:
            _, servizio_id, _ = services.annulla(operazione=operazione)
        except ErroreMagazzino as e:
            for m in e.messaggi:
                messages.error(request, m)
            return redirect("operazione_dettaglio", pk=pk)
        messages.success(request, "Operazione annullata: le giacenze sono tornate come prima.")
        if servizio_id:
            return redirect("servizio_dettaglio", pk=servizio_id)
        return redirect("operazioni")
    movimenti = operazione.movimenti.select_related("prodotto", "ubicazione")
    return render(
        request,
        "magazzino/annulla.html",
        {
            "operazione": operazione,
            "movimenti": movimenti,
            "intera": True,
            "allegati_persi": operazione.allegati.count(),
        },
    )


@require_http_methods(["GET", "POST"])
def movimento_annulla(request, pk):
    movimento = Movimento.objects.select_related("operazione__servizio", "prodotto", "ubicazione").filter(pk=pk).first()
    if movimento is None:
        messages.info(request, "Questa riga non esiste più (forse è già stata annullata).")
        return redirect("operazioni")
    operazione = movimento.operazione
    if request.method == "GET" and operazione.servizio and operazione.servizio.chiuso:
        return _servizio_terminato_annulla(request, operazione)
    if operazione.tipo == TipoOperazione.SPOSTAMENTO:
        messages.error(request, "Uno spostamento si annulla per intero, non una riga alla volta.")
        return redirect("operazione_dettaglio", pk=operazione.pk)
    if request.method == "POST":
        try:
            op_id, servizio_id, eliminata = services.annulla(movimento=movimento)
        except ErroreMagazzino as e:
            for m in e.messaggi:
                messages.error(request, m)
            return redirect("operazione_dettaglio", pk=operazione.pk)
        messages.success(request, "Riga annullata: la giacenza è tornata come prima.")
        if not eliminata:
            return redirect("operazione_dettaglio", pk=op_id)
        if servizio_id:
            return redirect("servizio_dettaglio", pk=servizio_id)
        return redirect("operazioni")
    # Annullando l'ultima riga sparisce anche l'operazione, con i suoi allegati.
    ultima_riga = not operazione.movimenti.exclude(pk=movimento.pk).exists()
    return render(
        request,
        "magazzino/annulla.html",
        {
            "operazione": operazione,
            "movimenti": [movimento],
            "intera": False,
            "allegati_persi": operazione.allegati.count() if ultima_riga else 0,
        },
    )
