"""Report della serata e statistiche della sagra."""

import csv
from datetime import timedelta

from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import formats, timezone
from django.views.decorators.http import require_GET

from magazzino.models import Servizio
from magazzino.quantita import formatta_numero

from .. import grafici, report
from ..formati import euro
from ..models import Ordine, Postazione
from ..services import impostazioni


def _grafico_andamento(fasce):
    if len(fasce) < 2:
        return ""
    etichette = [formats.time_format(f["inizio"], "H:i") for f in fasce]
    # Un'etichetta ogni ora (o meno fitte se la serata è lunga).
    ogni = max(4, -(-len(fasce) // 8))
    fine = [formats.time_format(f["inizio"] + timedelta(minutes=15), "H:i") for f in fasce]
    return grafici.colonne_impilate(
        etichette,
        [("Ordini", [f["ordini"] for f in fasce])],
        titolo="Ordini ogni quarto d'ora",
        formato=lambda v: f"{v} ordin{'e' if v == 1 else 'i'}",
        suggerimenti_titoli=[f"{a}–{b} · {euro(f['incasso'])}" for a, b, f in zip(etichette, fine, fasce)],
        etichetta_ogni=ogni,
    )


def _grafico_articoli(venduti, quanti=12):
    migliori = sorted(venduti, key=lambda r: (-r["quantita"], r["articolo"].nome.lower()))[:quanti]
    return grafici.barre(
        [
            (
                r["articolo"].nome,
                r["quantita"],
                [[str(r["quantita"]), "venduti", ""], [euro(r["importo"]), "incasso", ""]]
                + ([[str(r["omaggi"]), "omaggio", ""]] if r["omaggi"] else []),
            )
            for r in migliori
        ],
        formato=str,
    )


@require_GET
def report_serata(request, servizio_pk):
    servizio = get_object_or_404(Servizio, pk=servizio_pk)
    dati = report.report_servizio(servizio)
    return render(
        request,
        "cassa/report.html",
        {
            "servizio": servizio,
            "dati": dati,
            "grafico_andamento": _grafico_andamento(dati["andamento"]),
            "grafico_articoli": _grafico_articoli(dati["venduti"]),
            "da_scaricare": impostazioni().scarico_magazzino and any(
                v["mancante"] > 0 for v in report.da_scaricare(servizio).values()
            ),
        },
    )


def _risposta_csv(nome):
    risposta = HttpResponse(content_type="text/csv; charset=utf-8")
    adesso = timezone.localtime().strftime("%Y%m%d-%H%M")
    risposta["Content-Disposition"] = f'attachment; filename="{nome}-{adesso}.csv"'
    risposta.write("﻿")  # BOM: Excel riconosce l'UTF-8
    return risposta, csv.writer(risposta, delimiter=";")


@require_GET
def report_csv(request, servizio_pk):
    servizio = get_object_or_404(Servizio, pk=servizio_pk)
    dati = report.report_servizio(servizio)
    risposta, scrittore = _risposta_csv(f"venduto-{servizio.data:%Y%m%d}")
    scrittore.writerow(["Articolo", "Categoria", "Reparto", "Quantità", "Di cui omaggio", "Incasso (€)"])
    for r in dati["venduti"]:
        a = r["articolo"]
        scrittore.writerow([a.nome, a.categoria.nome, a.get_reparto_display(), r["quantita"], r["omaggi"], euro(r["importo"], simbolo=False)])
    return risposta


@require_GET
def ordini_csv(request, servizio_pk):
    servizio = get_object_or_404(Servizio, pk=servizio_pk)
    risposta, scrittore = _risposta_csv(f"ordini-{servizio.data:%Y%m%d}")
    scrittore.writerow([
        "Numero", "Ora", "Postazione", "Tavolo", "Nome", "Coperti", "Articoli", "Totale (€)", "Pagamento",
        "Annullato", "Motivo annullamento", "Utente", "Note",
    ])
    ordini = (
        Ordine.objects.filter(servizio=servizio).select_related("utente").prefetch_related("righe").order_by("numero")
    )
    for o in ordini:
        scrittore.writerow([
            o.numero,
            formats.time_format(timezone.localtime(o.creato_il), "H:i"),
            Postazione(o.postazione).label,
            o.tavolo,
            o.nome,
            o.coperti or "",
            ", ".join(f"{r.quantita} {r.descrizione}" + (f" ({r.note})" if r.note else "") for r in o.righe.all()),
            euro(o.totale, simbolo=False),
            o.get_pagamento_display(),
            "sì" if o.annullato else "",
            o.motivo_annullamento,
            o.utente.get_username() if o.utente else "",
            o.note,
        ])
    return risposta


@require_GET
def statistiche(request):
    dati = report.statistiche()
    serate = dati["serate"]
    grafico = ""
    if serate:
        grafico = grafici.colonne_impilate(
            [formats.date_format(r["servizio"].data, "D j/m") for r in serate],
            [("Contanti", [r["contanti"] for r in serate]), ("Carta", [r["carta"] for r in serate])],
            titolo="Incasso per serata",
            formato=euro,
            suggerimenti_titoli=[str(r["servizio"]) for r in serate],
            etichetta_ogni=max(1, -(-len(serate) // 10)),
        )
    return render(
        request,
        "cassa/statistiche.html",
        {
            "dati": dati,
            "grafico_incassi": grafico,
            "grafico_articoli": _grafico_articoli(
                [{"articolo": r["articolo"], "quantita": r["quantita"], "importo": r["importo"], "omaggi": 0} for r in dati["articoli"]],
                quanti=15,
            ),
        },
    )


@require_GET
def statistiche_csv(request):
    dati = report.statistiche()
    risposta, scrittore = _risposta_csv("statistiche-cassa")
    scrittore.writerow(["Serata", "Ordini", "Coperti", "Contanti (€)", "Carta (€)", "Incassato (€)", "Omaggi (€)", "Scontrino medio (€)"])
    for r in dati["serate"] + [{"servizio": "Totale", **dati["totale"]}]:
        scrittore.writerow([
            str(r["servizio"]), r["ordini"], r["coperti"], euro(r["contanti"], simbolo=False), euro(r["carta"], simbolo=False),
            euro(r["incassato"], simbolo=False), euro(r["omaggi"], simbolo=False),
            euro(r["scontrino_medio"], simbolo=False) if r["scontrino_medio"] is not None else "",
        ])
    scrittore.writerow([])
    scrittore.writerow(["Articolo", "Categoria"] + [str(s) for s in dati["servizi"]] + ["Totale", "Incasso (€)"])
    for r in dati["articoli"]:
        scrittore.writerow(
            [r["articolo"].nome, r["articolo"].categoria.nome] + r["valori"] + [r["quantita"], euro(r["importo"], simbolo=False)]
        )
    if dati["consumi"]:
        scrittore.writerow([])
        scrittore.writerow(["Prodotto", "Usato dal magazzino", "Unità", "Porzioni vendute", "Per porzione", "Piatti"])
        for c in dati["consumi"]:
            p = c["prodotto"]
            scrittore.writerow([p.nome, formatta_numero(c["usato"]), p.unita, c["porzioni"], formatta_numero(c["per_porzione"]), ", ".join(c["piatti"])])
    return risposta
