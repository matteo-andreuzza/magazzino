"""Esportazioni CSV (si aprono con Excel: separatore ';' e virgola decimale)."""

import csv

from django.http import HttpResponse
from django.utils import formats, timezone

from .. import services
from ..models import Movimento, Prodotto, Ubicazione
from ..quantita import ZERO, formatta_numero, nome_unita
from .comuni import ordina_per_categoria
from .pagine import dati_riepilogo, filtra_movimenti


def _risposta_csv(nome):
    risposta = HttpResponse(content_type="text/csv; charset=utf-8")
    adesso = timezone.localtime().strftime("%Y%m%d-%H%M")
    risposta["Content-Disposition"] = f'attachment; filename="{nome}-{adesso}.csv"'
    risposta.write("﻿")  # BOM: Excel riconosce l'UTF-8
    return risposta, csv.writer(risposta, delimiter=";")


def giacenze_csv(request):
    risposta, scrittore = _risposta_csv("giacenze")
    ubicazioni = list(Ubicazione.objects.all())
    stock = services.giacenze()
    scrittore.writerow(["Prodotto", "Categoria", "Unità"] + [u.nome for u in ubicazioni] + ["Totale"])
    con_giacenza = {pid for pid, _ in stock}
    for p in ordina_per_categoria(Prodotto.objects.filter(pk__in=con_giacenza)):
        valori = [stock.get((p.pk, u.pk), ZERO) for u in ubicazioni]
        scrittore.writerow(
            [p.nome, p.get_categoria_display(), nome_unita(p.unita)]
            + [formatta_numero(v) for v in valori]
            + [formatta_numero(sum(valori, ZERO))]
        )
    return risposta


def movimenti_csv(request):
    _, condizione = filtra_movimenti(request.GET)
    risposta, scrittore = _risposta_csv("movimenti")
    scrittore.writerow(
        ["Data e ora", "Operazione", "N. operazione", "Servizio", "Prodotto", "Categoria",
         "Ubicazione", "Quantità", "Unità", "Utente", "Note"]
    )
    movimenti = (
        Movimento.objects.filter(condizione)
        .select_related("operazione__servizio", "operazione__utente", "prodotto", "ubicazione")
        .order_by("operazione__creata_il", "pk")
    )
    for m in movimenti.iterator(chunk_size=2000):
        op = m.operazione
        scrittore.writerow([
            formats.date_format(timezone.localtime(op.creata_il), "d/m/Y H:i"),
            op.get_tipo_display(),
            op.pk,
            str(op.servizio) if op.servizio else "",
            m.prodotto.nome,
            m.prodotto.get_categoria_display(),
            m.ubicazione.nome,
            formatta_numero(m.quantita),
            nome_unita(m.prodotto.unita),
            op.utente.get_username() if op.utente else "",
            op.note,
        ])
    return risposta


def riepilogo_csv(request):
    servizi, righe = dati_riepilogo()
    risposta, scrittore = _risposta_csv("consumi")
    scrittore.writerow(
        ["Prodotto", "Categoria", "Unità"] + [str(s) for s in servizi] + ["Totale usato", "Scartato", "Giacenza attuale"]
    )
    for r in righe:
        p = r["prodotto"]
        scrittore.writerow(
            [p.nome, p.get_categoria_display(), nome_unita(p.unita)]
            + [formatta_numero(v) if v is not None else "" for v in r["valori"]]
            + [formatta_numero(r["totale"]), formatta_numero(r["scartato"]) if r["scartato"] else "",
               formatta_numero(r["giacenza"])]
        )
    return risposta
