"""Fusti alla spina: le spinatrici disegnate, con le operazioni della serata.

Sulla pagina si fa quello che si fa al bancone, quando lo si fa: trascinando
un fusto pieno su una spina lo si attacca, trascinando quello attaccato tra i
consumati lo si segna finito, toccandolo lo si pesa a fine serata (oppure si
pesano tutti insieme con «Pesata di fine serata»). Ogni operazione è un modulo
normale (funziona anche senza trascinare: si tocca il fusto e si sceglie);
bar.js lo invia senza ricaricare la pagina.
"""

from collections import defaultdict

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from magazzino.models import Servizio
from magazzino.quantita import ZERO, QuantitaNonValida, leggi_quantita
from magazzino.views.comuni import giorno_servizio

from .. import services
from ..models import Bevanda, ContaFusti, Fusto, Spina, TipoBevanda, TipoFusto
from ..services import ErroreBar


def _servizi_oggi():
    return list(Servizio.objects.filter(data=giorno_servizio()).order_by("tipo", "nome"))


def _ultima_modificabile(oggi):
    """La serata più recente prima di oggi, se i suoi fusti si possono ancora sistemare (pesate scritte il giorno dopo)."""
    s = Servizio.objects.filter(data__lt=giorno_servizio()).order_by("-data", "-tipo").first()
    if s is None or services.motivo_sola_lettura(s) is not None:
        return None
    return s


@require_GET
def fusti(request):
    """I fusti di adesso: porta alla serata di oggi su cui si possono fare le operazioni."""
    oggi = _servizi_oggi()
    operabili = [s for s in oggi if services.motivo_sola_lettura(s) is None]
    if operabili:
        con_fusti = [s for s in operabili if ContaFusti.objects.filter(servizio=s).exists()]
        scelto = (con_fusti or operabili)[-1]
        return redirect("bar:conta_fusti", servizio_pk=scelto.pk)
    return _scena(request, None, oggi)


@require_GET
def conta_fusti(request, servizio_pk):
    servizio = get_object_or_404(Servizio, pk=servizio_pk)
    return _scena(request, servizio, _servizi_oggi())


def _pieni(attaccati):
    """Per ogni bevanda alla spina i suoi tipi di fusto con quanti pieni ci sono."""
    pieni = services.fusti_pieni()
    attaccati_tipi = {s.tipo.pk for s in attaccati}
    gruppi = []
    for b in Bevanda.objects.filter(tipo=TipoBevanda.FUSTI).prefetch_related("tipi_fusto"):
        tipi = [
            # Nel disegno la pila ha al massimo tre fusti (zero pieni: un fusto sbiadito).
            {"tipo": t, "pieni": pieni.get(t.pk, ZERO), "pila": range(min(max(int(pieni.get(t.pk, 0)), 1), 3))}
            for t in b.tipi_fusto.all()
            if t.attivo or pieni.get(t.pk) or t.pk in attaccati_tipi
        ]
        if tipi and (b.attiva or any(p["pieni"] for p in tipi)):
            gruppi.append({"bevanda": b, "tipi": tipi})
    return gruppi


def _consumati(conta_stasera):
    """I fusti finiti divisi per serata, dalla prima."""
    per_serata = defaultdict(list)
    serate = {}
    for s in services.fusti_consumati():
        conta = s.fusto.finito_con
        serate[conta.pk] = conta.servizio
        s.di_stasera = conta_stasera is not None and conta.pk == conta_stasera.pk
        per_serata[conta.pk].append(s)
    # Nella stessa serata in ordine di spina (la bionda con la bionda), poi come sono finiti.
    def ordine(s):
        spina = s.fusto.spina
        return (spina.spinatrice.ordine, spina.spinatrice_id, spina.ordine, spina.pk) if spina else (0, 0, 0, 0)

    return [{"servizio": serate[c], "fusti": sorted(per_serata[c], key=ordine)} for c in sorted(per_serata)]


def _scena(request, servizio, servizi_oggi):
    attaccati = services.fusti_attaccati()
    conta = ContaFusti.objects.filter(servizio=servizio).first() if servizio else None
    motivo = services.motivo_sola_lettura(servizio) if servizio else None
    operabile = servizio is not None and motivo is None
    # Per ogni fusto attaccato: com'era prima di stasera e com'è stasera.
    for s in attaccati:
        s.pesata_stasera = s.pesata_di(conta.pk) if conta else None
        s.prima_di_stasera = s.peso_prima(conta.pk) if conta else s.ultimo_peso
        s.pesato_prima = any(p.conta_id < conta.pk for p in s.pesate) if conta else bool(s.pesate)
        s.di_stasera = conta is not None and s.fusto.aperto_con_id == conta.pk
    spinatrici = [
        {"spinatrice": sp, "spine": [{"spina": spina, "fusto": stato} for spina, stato in spine], "disegno": spine}
        for sp, spine in services.spinatrici_in_uso(attaccati)
    ]
    spine = [g for gruppo in spinatrici for g in gruppo["spine"]]
    righe = services.dettaglio_conta(conta)[0] if conta else []
    consumati = _consumati(conta)
    da_pesare = [s for s in attaccati if s.pesata_stasera is None] if operabile else []
    return render(
        request,
        "bar/fusti.html",
        {
            "servizio": servizio,
            "servizi_oggi": servizi_oggi,
            "ultima_modificabile": _ultima_modificabile(servizi_oggi) if servizio is None else None,
            "conta": conta,
            "operabile": operabile,
            "motivo": motivo,
            "spinatrici": spinatrici,
            "spine": spine,
            "attaccati": [g for g in spine if g["fusto"]],
            "pieni": _pieni(attaccati),
            "consumati": consumati,
            "n_consumati": sum(len(g["fusti"]) for g in consumati),
            "da_pesare": da_pesare,
            "righe": righe,
            "bevande": services.confronto_serata(servizio, conta) if servizio else [],
            "scoperte": services.serate_scoperte(servizio) if operabile and conta is None else [],
            "tare_provvisorie": TipoFusto.objects.filter(tara_provvisoria=True, attivo=True).exists(),
        },
    )


def _oggetto(modello, valore):
    if not str(valore or "").isdigit():
        return None
    return modello.objects.filter(pk=int(valore)).first()


def _peso(testo):
    try:
        return leggi_quantita(testo)
    except QuantitaNonValida:
        raise ErroreBar("Scrivi il peso in chili, per esempio 18,4.") from None


def _pesa_tutti(request, servizio):
    pesi = []
    for chiave, valore in request.POST.items():
        if not chiave.startswith("peso_") or not valore.strip():
            continue
        fusto = _oggetto(Fusto, chiave[5:])
        if fusto is None:
            raise ErroreBar("Nel frattempo un fusto è stato tolto: guarda di nuovo.")
        pesi.append((fusto, _peso(valore)))
    pesate, avvisi = services.pesa_tutti(servizio=servizio, pesi=pesi, utente=request.user)
    n = len(pesate)
    messages.success(request, f"Pesata di fine serata salvata: {n} fust{'o' if n == 1 else 'i'}.")
    for a in avvisi:
        messages.warning(request, a)


@require_POST
def fusti_azione(request, servizio_pk):
    servizio = get_object_or_404(Servizio, pk=servizio_pk)
    torna = redirect(reverse("bar:conta_fusti", args=[servizio.pk]))
    azione = request.POST.get("azione", "")
    spina = _oggetto(Spina, request.POST.get("spina"))
    tipo = _oggetto(TipoFusto, request.POST.get("tipo"))
    fusto = _oggetto(Fusto, request.POST.get("fusto"))
    attuale = _oggetto(Fusto, request.POST.get("attuale"))
    servono = {
        "attacca": (spina, tipo), "cambia": (spina, tipo, attuale), "finito": (fusto,), "pesa": (fusto,),
        "togli_pesata": (fusto,), "stacca": (fusto,), "riattacca": (fusto,), "pesa_tutti": (), "azzera": (),
    }
    if azione not in servono:
        messages.error(request, "Operazione sconosciuta: ricarica la pagina.")
        return torna
    if any(o is None for o in servono[azione]):
        messages.error(request, "Nel frattempo qualcosa è cambiato (un fusto o una spina non c'è più): guarda di nuovo.")
        return torna
    try:
        if azione == "attacca":
            services.attacca(servizio=servizio, spina=spina, tipo=tipo, utente=request.user)
            messages.success(request, f"{tipo.nome} attaccato a «{spina.nome}».")
        elif azione == "cambia":
            services.cambia(servizio=servizio, spina=spina, tipo=tipo, attuale=attuale, utente=request.user)
            messages.success(request, f"Fusto cambiato: quello finito è tra i finiti, a «{spina.nome}» adesso c'è un "
                                      f"{tipo.nome.lower()}.")
        elif azione == "finito":
            services.finisci(servizio=servizio, fusto=fusto, utente=request.user)
            messages.success(request, f"{fusto.tipo.nome} spostato tra i finiti.")
        elif azione == "pesa":
            peso = _peso(request.POST.get("peso", ""))
            pesata = services.pesa(servizio=servizio, fusto=fusto, peso=peso, utente=request.user)
            rimasti = services.stati_fusti(Fusto.objects.filter(pk=fusto.pk))[0].litri_rimasti
            resto = f", restano circa {services.formatta_litri(rimasti)}" if rimasti is not None else ""
            messages.success(request, f"Peso salvato: {services.formatta_kg(peso)}{resto}.")
            for a in pesata.avvisi:
                messages.warning(request, a)
        elif azione == "pesa_tutti":
            _pesa_tutti(request, servizio)
        elif azione == "togli_pesata":
            if services.togli_pesata(servizio=servizio, fusto=fusto):
                messages.success(request, "Pesata di stasera tolta: il fusto è di nuovo da pesare.")
        elif azione == "stacca":
            services.stacca(servizio=servizio, fusto=fusto)
            messages.success(request, f"{fusto.tipo.nome} rimesso tra i pieni.")
        elif azione == "riattacca":
            f = services.riattacca(servizio=servizio, fusto=fusto, spina=spina)
            messages.success(request, f"{fusto.tipo.nome} di nuovo attaccato a «{f.spina.nome}».")
        elif azione == "azzera":
            nomi = services.azzera(servizio=servizio, spine=[spina] if spina else None)
            if nomi:
                messages.success(request, f"Annullato quello registrato stasera ({', '.join(nomi)}): i fusti sono come a inizio serata.")
            else:
                messages.info(request, "Stasera non c'era niente da annullare.")
    except ErroreBar as e:
        for m in e.messaggi:
            messages.error(request, m)
    return torna
