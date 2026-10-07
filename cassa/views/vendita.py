"""Cassa: presa degli ordini, elenco, annullamento e ristampa."""

import uuid
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import formats, timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from magazzino.models import Servizio
from magazzino.views.comuni import giorno_servizio

from .. import report, services, stampa
from ..formati import euro
from ..models import (
    Articolo,
    Connessione,
    Ordine,
    Pagamento,
    Postazione,
    Reparto,
    StatoStampa,
)
from ..services import ErroreCassa, Voce, articoli_in_vendita, impostazioni
from .comuni import centesimi, errore_json, funzione_attiva, leggi_json, riassunto_stampe, versione_listino


def _controlli_preparazione(imp):
    """Cose da sistemare prima di aprire la cassa."""
    avvisi = []
    senza_prezzo = Articolo.objects.filter(attivo=True, prezzo__isnull=True).count()
    if senza_prezzo:
        avvisi.append((
            f"{senza_prezzo} articol{'o' if senza_prezzo == 1 else 'i'} senza prezzo: in cassa non compaiono.",
            reverse("cassa:listino"),
        ))
    if not Articolo.objects.filter(attivo=True, prezzo__isnull=False, reparto=Reparto.BAR).exists():
        avvisi.append(("Nessuna bevanda (articolo del bar) in vendita.", reverse("cassa:articolo_nuovo")))
    if imp.stampante_cucina is None:
        avvisi.append(("Non è impostata la stampante delle comande della cucina.", reverse("cassa:impostazioni")))
    if imp.stampante_bar is None:
        avvisi.append((
            "Non è impostata la stampante della comanda del bar: le bevande ordinate all'ingresso non escono su carta.",
            reverse("cassa:impostazioni"),
        ))
    if imp.ricevuta_cliente and imp.stampante_ricevute is None:
        avvisi.append(("La ricevuta per il cliente è attiva ma manca la stampante delle ricevute.", reverse("cassa:impostazioni")))
    stato = stampa.stato_stampa()
    if stato["avviso"]:
        avvisi.append((stato["avviso"], reverse("cassa:stampanti")))
    return avvisi


@require_GET
def inizio(request):
    imp = impostazioni()
    oggi = giorno_servizio()
    recenti = (
        Servizio.objects.annotate(
            n_ordini=Count("ordini", filter=Q(ordini__annullato_il__isnull=True)), n_tutti=Count("ordini")
        )
        .filter(n_tutti__gt=0)
        .order_by("-data", "tipo", "nome")[:12]
    )
    return render(
        request,
        "cassa/inizio.html",
        {
            "oggi": oggi,
            "servizi_oggi": Servizio.objects.filter(data=oggi),
            "recenti": recenti,
            "avvisi": _controlli_preparazione(imp),
            "imp": imp,
        },
    )


def _ordine_json(ordine, stampe=None):
    stampe = list(ordine.stampe.all()) if stampe is None else stampe
    return {
        "id": ordine.pk,
        "numero": ordine.numero,
        "nome": ordine.nome,
        "tavolo": ordine.tavolo,
        "asporto": ordine.asporto,
        "totale": centesimi(ordine.totale),
        "totaleTesto": euro(ordine.totale),
        "pagamento": ordine.get_pagamento_display(),
        "ora": formats.time_format(timezone.localtime(ordine.creato_il), "H:i"),
        "annullato": ordine.annullato,
        "stampa": riassunto_stampe(stampe),
        "daStampare": [
            s.pk for s in stampe
            if s.stampante.connessione == Connessione.BROWSER and s.stato == StatoStampa.IN_CODA
        ],
        "url": reverse("cassa:ordine", args=[ordine.pk]),
    }


def _pagina_cassa(request, servizio_pk, postazione):
    servizio = get_object_or_404(Servizio.objects.select_related("cassa_chiusa_da"), pk=servizio_pk)
    imp = impostazioni()
    articoli = list(articoli_in_vendita(servizio, postazione))
    categorie = []
    for a in articoli:
        if a.categoria not in categorie:
            categorie.append(a.categoria)

    copia = None
    if request.GET.get("copia", "").isdigit():
        originale = Ordine.objects.filter(pk=int(request.GET["copia"]), servizio=servizio).first()
        if originale is not None:
            vendibili = {a.pk for a in articoli if not a.esaurito}
            righe = list(originale.righe.all())
            copia = {
                "nome": originale.nome,
                "tavolo": "" if originale.asporto else originale.tavolo,
                "asporto": originale.asporto,
                "coperti": originale.coperti,
                "note": originale.note,
                "righe": [{"a": r.articolo_id, "q": r.quantita, "n": r.note} for r in righe if r.articolo_id in vendibili],
                "mancanti": [r.descrizione for r in righe if r.articolo_id not in vendibili],
                "numero": originale.numero,
            }

    dati = {
        "servizio": servizio.pk,
        "postazione": postazione,
        "obbligatori": postazione == Postazione.INGRESSO,
        "versione": versione_listino(articoli),
        "articoli": {
            str(a.pk): {"nome": a.nome, "prezzo": centesimi(a.prezzo), "esaurito": a.esaurito, "categoria": a.categoria_id}
            for a in articoli
        },
        "copia": copia,
        "omaggio": {"nome": services.NOME_OMAGGIO, "tavolo": services.TAVOLO_OMAGGIO},
        "asporto": {"tavolo": services.TAVOLO_ASPORTO, "ammesso": postazione == Postazione.INGRESSO},
        "url": {
            "ordina": reverse("cassa:ordina", args=[servizio.pk]),
            "stato": reverse("cassa:stato", args=[servizio.pk]) + f"?postazione={postazione}",
            "esaurito": reverse("cassa:articolo_esaurito", args=[0]),
            "stampeBrowser": reverse("cassa:stampe_browser"),
        },
    }
    return render(
        request,
        "cassa/cassa.html",
        {
            "servizio": servizio,
            "postazione": postazione,
            "bar": postazione == Postazione.BAR,
            "categorie": categorie,
            "articoli": articoli,
            "pagamenti": Pagamento.choices,
            "dati": dati,
            "imp": imp,
            "bloccata": servizio.chiuso or servizio.cassa_chiusa,
            "senza_prezzo": Articolo.objects.filter(attivo=True, prezzo__isnull=True).count() if request.user.is_staff else 0,
        },
    )


@require_GET
def cassa(request, servizio_pk):
    return _pagina_cassa(request, servizio_pk, Postazione.INGRESSO)


@require_GET
@funzione_attiva("cassa_bar")
def cassa_bar(request, servizio_pk):
    return _pagina_cassa(request, servizio_pk, Postazione.BAR)


def _intero(valore, *, vuoto=None):
    if valore in (None, ""):
        return vuoto
    if isinstance(valore, bool) or not isinstance(valore, (int, str)):
        raise ValueError
    return int(valore)


@require_POST
def ordina(request, servizio_pk):
    servizio = get_object_or_404(Servizio, pk=servizio_pk)
    dati = leggi_json(request)
    if dati is None:
        return errore_json("Richiesta non valida: ricarica la pagina.")
    try:
        token = uuid.UUID(str(dati.get("token", "")))
    except ValueError:
        return errore_json("Richiesta non valida: ricarica la pagina.")
    postazione = dati.get("postazione")
    if postazione not in Postazione.values:
        return errore_json("Postazione non valida: ricarica la pagina.")
    try:
        voci = [
            Voce(articolo_id=_intero(v.get("articolo")), quantita=_intero(v.get("quantita")), note=str(v.get("note") or ""))
            for v in dati.get("righe") or []
        ]
        coperti = _intero(dati.get("coperti")) or None
        totale = _intero(dati.get("totale"))
    except (ValueError, TypeError, AttributeError):
        return errore_json("Richiesta non valida: ricarica la pagina.")
    if any(v.articolo_id is None or v.quantita is None for v in voci):
        return errore_json("Richiesta non valida: ricarica la pagina.")
    try:
        ordine, creato = services.crea_ordine(
            servizio=servizio,
            postazione=postazione,
            voci=voci,
            pagamento=str(dati.get("pagamento") or ""),
            utente=request.user,
            nome=str(dati.get("nome") or ""),
            tavolo=str(dati.get("tavolo") or ""),
            coperti=coperti,
            note=str(dati.get("note") or ""),
            totale_atteso=None if totale is None else Decimal(totale) / 100,
            token=token,
            asporto=dati.get("asporto") is True,
        )
    except ErroreCassa as e:
        return errore_json(e.messaggi, status=409, ricarica=e.ricarica)
    stampe = list(ordine.stampe.select_related("stampante"))
    return JsonResponse({"ok": True, "nuovo": creato, "ordine": _ordine_json(ordine, stampe)})


@require_GET
def stato(request, servizio_pk):
    servizio = get_object_or_404(Servizio, pk=servizio_pk)
    postazione = request.GET.get("postazione")
    if postazione not in Postazione.values:
        postazione = Postazione.INGRESSO
    ultimi = list(
        Ordine.objects.filter(servizio=servizio, postazione=postazione)
        .prefetch_related("stampe__stampante")
        .order_by("-creato_il", "-pk")[:8]
    )
    stato_stampa = stampa.stato_stampa()
    return JsonResponse({
        "ok": True,
        "versione": versione_listino(articoli_in_vendita(servizio, postazione)),
        "chiuso": servizio.chiuso or servizio.cassa_chiusa,
        "stampa": {
            "avviso": stato_stampa["avviso"],
            "errori": stato_stampa["errori"],
        },
        "ordini": [_ordine_json(o) for o in ultimi],
    })


@require_POST
def articolo_esaurito(request, pk):
    articolo = get_object_or_404(Articolo, pk=pk)
    dati = leggi_json(request)
    if dati is None or not isinstance(dati.get("esaurito"), bool):
        return errore_json("Richiesta non valida.")
    services.imposta_esaurito(articolo=articolo, esaurito=dati["esaurito"])
    return JsonResponse({"ok": True, "esaurito": articolo.esaurito})


# ---------------------------------------------------------------------------
# Elenco e dettaglio ordini
# ---------------------------------------------------------------------------


@require_GET
def ordini(request, servizio_pk):
    servizio = get_object_or_404(Servizio, pk=servizio_pk)
    # Dalla cassa del bar si vedono solo gli ordini del bar (e la pagina sta nella sezione Bar).
    solo_bar = request.GET.get("postazione") == Postazione.BAR
    ordini_serata = Ordine.objects.filter(servizio=servizio)
    if solo_bar:
        ordini_serata = ordini_serata.filter(postazione=Postazione.BAR)
    elenco = list(
        ordini_serata
        .select_related("utente")
        .prefetch_related("righe", "stampe")
        .order_by("-numero")
    )
    for o in elenco:
        o.stato_stampe = riassunto_stampe(o.stampe.all())
    return render(
        request,
        "cassa/ordini.html",
        {"servizio": servizio, "elenco": elenco, "n_annullati": sum(1 for o in elenco if o.annullato), "bar": solo_bar},
    )


@require_GET
def ordine(request, pk):
    o = get_object_or_404(Ordine.objects.select_related("servizio", "utente", "annullato_da"), pk=pk)
    return render(
        request,
        "cassa/ordine.html",
        {
            "ordine": o,
            "righe": o.righe.all(),
            "comande": o.comande.all(),
            "stampe": o.stampe.select_related("stampante"),
            "ristampabili": services.documenti_ristampabili(o),
            "monitor": impostazioni().monitor_cucina,
            "bar": o.postazione == Postazione.BAR,
            "url_cassa": reverse(
                "cassa:cassa_bar" if o.postazione == Postazione.BAR else "cassa:cassa", args=[o.servizio_id]
            ),
        },
    )


@require_http_methods(["GET", "POST"])
def ordine_annulla(request, pk):
    o = get_object_or_404(Ordine.objects.select_related("servizio"), pk=pk)
    if request.method == "POST":
        try:
            o, annullato = services.annulla_ordine(ordine=o, utente=request.user, motivo=request.POST.get("motivo", ""))
        except ErroreCassa as e:
            for m in e.messaggi:
                messages.error(request, m)
            return redirect("cassa:ordine", pk=o.pk)
        if annullato:
            messages.success(request, f"Ordine n. {o.numero} annullato.")
        else:
            messages.info(request, f"L'ordine n. {o.numero} era già annullato.")
        if request.POST.get("rifai") == "1":
            nome = "cassa:cassa_bar" if o.postazione == Postazione.BAR else "cassa:cassa"
            return redirect(reverse(nome, args=[o.servizio_id]) + f"?copia={o.pk}")
        return redirect("cassa:ordine", pk=o.pk)
    if o.annullato:
        messages.info(request, f"L'ordine n. {o.numero} è già annullato.")
        return redirect("cassa:ordine", pk=o.pk)
    if o.servizio.cassa_chiusa and not o.servizio.chiuso:
        messages.info(request, "La cassa è chiusa: per annullare o correggere un ordine riaprila.")
        return redirect("cassa:ordine", pk=o.pk)
    stampate = o.stampe.filter(stato__in=[StatoStampa.IN_STAMPA, StatoStampa.STAMPATA, StatoStampa.ERRORE]).exists()
    return render(request, "cassa/ordine_annulla.html", {"ordine": o, "righe": o.righe.all(), "stampate": stampate})


@require_POST
def ordine_ristampa(request, pk):
    o = get_object_or_404(Ordine, pk=pk)
    cosa = request.POST.get("cosa", "")
    if cosa not in [*Reparto.values, "ricevuta"]:
        messages.error(request, "Scegli cosa ristampare.")
        return redirect("cassa:ordine", pk=o.pk)
    try:
        stampe = services.ristampa(ordine=o, cosa=cosa)
    except ErroreCassa as e:
        for m in e.messaggi:
            messages.error(request, m)
        return redirect("cassa:ordine", pk=o.pk)
    dal_browser = [s.pk for s in stampe if s.stampante.connessione == Connessione.BROWSER]
    if dal_browser:
        parametri = urlencode([("id", i) for i in dal_browser] + [("torna", reverse("cassa:ordine", args=[o.pk]))])
        return redirect(reverse("cassa:stampe_browser") + "?" + parametri)
    messages.success(request, "Ristampa inviata alla stampante.")
    return redirect("cassa:ordine", pk=o.pk)


# ---------------------------------------------------------------------------
# Chiusura della cassa a fine serata
# ---------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def cassa_chiudi(request, servizio_pk):
    servizio = get_object_or_404(Servizio, pk=servizio_pk)
    if request.method == "POST":
        if services.chiudi_cassa(servizio=servizio, utente=request.user):
            messages.success(request, "Cassa chiusa: non si registrano più ordini. Ecco il riepilogo della serata.")
        else:
            messages.info(request, "La cassa era già chiusa.")
        return redirect("cassa:report", servizio_pk=servizio.pk)
    if servizio.cassa_chiusa or servizio.chiuso:
        return redirect("cassa:report", servizio_pk=servizio.pk)
    ordini = Ordine.objects.filter(servizio=servizio)
    return render(
        request,
        "cassa/chiudi.html",
        {
            "servizio": servizio,
            "totali": report.incassi(ordini.filter(annullato_il__isnull=True)),
            "in_stampa": ordini.filter(stampe__stato__in=[StatoStampa.IN_CODA, StatoStampa.IN_STAMPA]).distinct().count(),
            "errori_stampa": ordini.filter(stampe__stato=StatoStampa.ERRORE).distinct().count(),
        },
    )


@require_POST
def cassa_riapri(request, servizio_pk):
    servizio = get_object_or_404(Servizio, pk=servizio_pk)
    if servizio.chiuso:
        d = servizio.desinenza
        messages.error(request, f"{servizio.get_tipo_display()} terminat{d}: si riapre dal magazzino.")
    elif services.riapri_cassa(servizio=servizio):
        messages.success(request, "Cassa riaperta: si possono di nuovo registrare ordini.")
    else:
        messages.info(request, "La cassa era già aperta.")
    return redirect("cassa:cassa", servizio_pk=servizio.pk)
