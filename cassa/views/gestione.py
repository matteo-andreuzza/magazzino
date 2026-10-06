"""Listino, impostazioni e stampanti."""

from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from .. import services, stampa
from ..formati import euro, leggi_euro
from ..forms import ArticoloForm, ImpostazioniForm, StampanteForm
from ..models import CONNESSIONI_AGENTE, AgenteStampa, Articolo, Categoria, Stampa, Stampante, StatoStampa
from ..services import impostazioni
from .comuni import solo_staff


@require_http_methods(["GET", "POST"])
def listino(request):
    articoli = list(Articolo.objects.select_related("categoria", "piatto", "bevanda"))
    errori = {}
    if request.method == "POST":
        if not request.user.is_staff:
            return render(request, "cassa/solo_staff.html", status=403)
        modifiche = []
        for a in articoli:
            if f"presente-{a.pk}" not in request.POST:
                continue
            testo = request.POST.get(f"prezzo-{a.pk}", "")
            try:
                prezzo = leggi_euro(testo)
            except ValueError as e:
                errori[a.pk] = (testo, str(e))
                continue
            attivo = request.POST.get(f"attivo-{a.pk}") == "1"
            esaurito = request.POST.get(f"esaurito-{a.pk}") == "1"
            if (prezzo, attivo, esaurito) != (a.prezzo, a.attivo, a.esaurito):
                a.prezzo, a.attivo, a.esaurito = prezzo, attivo, esaurito
                modifiche.append(a)
        if not errori:
            with transaction.atomic():
                Articolo.objects.bulk_update(modifiche, ["prezzo", "attivo", "esaurito"])
            if modifiche:
                messages.success(request, f"Listino salvato: {len(modifiche)} articol{'o' if len(modifiche) == 1 else 'i'} modificat{'o' if len(modifiche) == 1 else 'i'}.")
            else:
                messages.info(request, "Nessuna modifica da salvare.")
            return redirect("cassa:listino")
    gruppi = []
    for a in articoli:
        a.testo_prezzo, a.errore_prezzo = errori.get(a.pk, (euro(a.prezzo, simbolo=False) if a.prezzo is not None else "", ""))
        if not gruppi or gruppi[-1][0] != a.categoria:
            gruppi.append((a.categoria, []))
        gruppi[-1][1].append(a)
    return render(
        request,
        "cassa/listino.html",
        {"gruppi": gruppi, "errori": bool(errori), "n_categorie": Categoria.objects.count()},
    )


@require_http_methods(["GET", "POST"])
@solo_staff
def articolo_nuovo(request):
    form = ArticoloForm(request.POST or None, initial={"categoria": request.GET.get("categoria")})
    if request.method == "POST" and form.is_valid():
        articolo = form.save()
        messages.success(request, f"Articolo «{articolo.nome}» creato.")
        return redirect("cassa:listino")
    return render(request, "cassa/articolo_form.html", {"form": form, "titolo": "Nuovo articolo"})


@require_http_methods(["GET", "POST"])
@solo_staff
def articolo_modifica(request, pk):
    articolo = get_object_or_404(Articolo, pk=pk)
    form = ArticoloForm(request.POST or None, instance=articolo)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Articolo «{articolo.nome}» salvato.")
        return redirect("cassa:listino")
    return render(
        request,
        "cassa/articolo_form.html",
        {"form": form, "titolo": f"Modifica {articolo.nome}", "articolo": articolo, "venduto": articolo.righe.exists()},
    )


@require_http_methods(["GET", "POST"])
@solo_staff
def impostazioni_cassa(request):
    imp = impostazioni()
    form = ImpostazioniForm(request.POST or None, instance=imp)
    if request.method == "POST" and form.is_valid():
        services.salva_impostazioni(form.instance)
        messages.success(request, "Impostazioni salvate.")
        return redirect("cassa:impostazioni")
    return render(request, "cassa/impostazioni.html", {"form": form, "imp": imp})


@require_http_methods(["GET"])
@solo_staff
def stampanti(request):
    imp = impostazioni()
    return render(
        request,
        "cassa/stampanti.html",
        {
            "stampanti": Stampante.objects.all(),
            "agenti": AgenteStampa.objects.all()[:10],
            "servono_agenti": Stampante.objects.filter(attiva=True, connessione__in=CONNESSIONI_AGENTE).exists(),
            "imp": imp,
            "server": request.build_absolute_uri("/"),
            "errori": Stampa.objects.filter(stato=StatoStampa.ERRORE).select_related("stampante", "ordine").order_by("-creata_il")[:20],
        },
    )


@require_http_methods(["GET", "POST"])
@solo_staff
def stampante_nuova(request):
    form = StampanteForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        s = form.save()
        messages.success(request, f"Stampante «{s.nome}» aggiunta. Ora scegli in Impostazioni cosa stampa.")
        return redirect("cassa:stampanti")
    return render(request, "cassa/stampante_form.html", {"form": form, "titolo": "Nuova stampante"})


@require_http_methods(["GET", "POST"])
@solo_staff
def stampante_modifica(request, pk):
    s = get_object_or_404(Stampante, pk=pk)
    form = StampanteForm(request.POST or None, instance=s)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Stampante «{s.nome}» salvata.")
        return redirect("cassa:stampanti")
    return render(request, "cassa/stampante_form.html", {"form": form, "titolo": f"Modifica {s.nome}", "stampante": s})


@require_POST
@solo_staff
def stampante_prova(request, pk):
    s = get_object_or_404(Stampante, pk=pk)
    lavoro = stampa.accoda_prova(s, impostazioni())
    if s.dal_browser:
        return redirect(reverse("cassa:stampe_browser") + f"?id={lavoro.pk}&torna={reverse('cassa:stampanti')}")
    messages.success(request, f"Stampa di prova inviata a «{s.nome}».")
    return redirect("cassa:stampanti")


@require_POST
@solo_staff
def token_rigenera(request):
    services.rigenera_token_agente(impostazioni())
    messages.success(
        request,
        "Nuova chiave creata: i programmi di stampa già installati non funzionano più finché non li riscarichi.",
    )
    return redirect("cassa:stampanti")
