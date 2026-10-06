from datetime import datetime, timedelta
from decimal import Decimal as D
from zoneinfo import ZoneInfo

from django.urls import reverse
from django.utils import timezone

from cassa import report, services
from cassa.models import Comanda, Ordine, Postazione, Reparto
from magazzino import services as magazzino
from magazzino.models import Prodotto, Servizio, TipoOperazione, Ubicazione

from .base import BaseCassa

ROMA = ZoneInfo("Europe/Rome")


class IncassiTest(BaseCassa):
    def test_totali_per_pagamento(self):
        self.ordina([(self.grigliata, 2)], coperti=2)  # 25 contanti
        self.ordina([(self.coca, 2)], pagamento="carta", coperti=1)  # 5 carta
        self.ordina([(self.pollo, 1)], pagamento="omaggio")  # 9 omaggio
        annullato, _ = self.ordina([(self.paella, 1)])
        services.annulla_ordine(ordine=annullato, utente=None)
        dati = report.report_servizio(self.serata)
        t = dati["totali"]
        self.assertEqual((t["contanti"], t["carta"], t["incassato"], t["omaggi"]), (D("25"), D("5"), D("30"), D("9")))
        self.assertEqual((t["ordini"], t["paganti"], t["n_omaggi"], t["coperti"]), (3, 2, 1, 3))
        self.assertEqual(t["scontrino_medio"], D("15.00"))
        self.assertEqual(dati["annullati"], {"n": 1, "totale": D("14.00")})
        venduti = {r["articolo"].nome: (r["quantita"], r["omaggi"], r["importo"]) for r in dati["venduti"]}
        self.assertEqual(venduti, {
            "Grigliata mista": (2, 0, D("25.00")), "Coca-Cola": (2, 0, D("5.00")), "Solo pollo": (1, 1, D("0.00")),
        })
        self.assertEqual([r["articolo"].nome for r in dati["venduti"]], ["Grigliata mista", "Solo pollo", "Coca-Cola"])
        self.assertEqual(
            {c["categoria"].nome: (c["quantita"], c["importo"]) for c in dati["categorie"]},
            {"Secondi": (3, D("25.00")), "Bevande": (2, D("5.00"))},
        )
        self.assertEqual(dati["postazioni"], [])

    def test_per_postazione(self):
        self.imposta(cassa_bar=True)
        self.ordina([(self.grigliata, 1)])
        self.ordina([(self.coca, 1)], postazione=Postazione.BAR, nome="", tavolo="")
        postazioni = dict(report.report_servizio(self.serata)["postazioni"])
        self.assertEqual(postazioni["Cassa all'ingresso"]["incassato"], D("12.50"))
        self.assertEqual(postazioni["Cassa del bar"]["incassato"], D("2.50"))

    def test_andamento_a_quarti_d_ora(self):
        for ora, minuto in [(19, 2), (19, 14), (19, 50)]:
            ordine, _ = self.ordina([(self.coca, 1)])
            Ordine.objects.filter(pk=ordine.pk).update(creato_il=datetime(2026, 9, 18, ora, minuto, tzinfo=ROMA))
        fasce = report.andamento(Ordine.objects.all())
        self.assertEqual([(f["inizio"].strftime("%H:%M"), f["ordini"]) for f in fasce], [
            ("19:00", 2), ("19:15", 0), ("19:30", 0), ("19:45", 1),
        ])
        self.assertEqual(fasce[0]["incasso"], D("5.00"))

    def test_tempi_cucina(self):
        ordine, _ = self.ordina([(self.grigliata, 1)])
        creato = ordine.creato_il
        Comanda.objects.filter(ordine=ordine).update(
            iniziata_il=creato + timedelta(minutes=4), pronta_il=creato + timedelta(minutes=16), stato="pronta"
        )
        tempi = report.tempi_cucina(self.serata)
        self.assertEqual((tempi["comande"], tempi["attesa"], tempi["preparazione"], tempi["totale"], tempi["massimo"]), (1, 4.0, 12.0, 16.0, 16))

    def test_statistiche(self):
        altra = Servizio.objects.create(data=self.serata.data + timedelta(days=1))
        self.ordina([(self.grigliata, 2)])
        self.ordina([(self.grigliata, 1), (self.coca, 1)], servizio=altra, pagamento="carta")
        dati = report.statistiche()
        self.assertEqual([r["servizio"] for r in dati["serate"]], [self.serata, altra])
        self.assertEqual(dati["totale"]["incassato"], D("40.00"))
        griglia = next(r for r in dati["articoli"] if r["articolo"] == self.grigliata)
        self.assertEqual((griglia["valori"], griglia["quantita"], griglia["importo"]), ([2, 1], 3, D("37.50")))

    def test_pagina_statistiche_e_csv(self):
        self.client.force_login(self.utente)
        self.ordina([(self.grigliata, 2)])
        risposta = self.client.get(reverse("cassa:statistiche"))
        self.assertContains(risposta, "25,00 €")
        self.assertContains(risposta, 'class="grafico"')
        testo = self.client.get(reverse("cassa:statistiche_csv")).content.decode("utf-8-sig")
        self.assertIn("Grigliata mista;Secondi;2;2;25,00", testo)


class ConsumiTest(BaseCassa):
    def test_consumo_per_porzione(self):
        frigo = Ubicazione.objects.get(nome="Frigo 1")
        pollo = Prodotto.objects.get(nome="Pollo")
        polenta = Prodotto.objects.get(nome="Polenta")
        magazzino.registra_carico(tipo=TipoOperazione.ACQUISTO, ubicazione=frigo, quantita=[(pollo, D("30")), (polenta, D("10"))], utente=None)
        magazzino.registra_cucina(servizio=self.serata, prelievi=[(pollo, frigo, D("12")), (polenta, frigo, D("5"))], utente=None)
        magazzino.registra_reso(servizio=self.serata, rientri=[(pollo, frigo, D("2"))], utente=None)
        self.ordina([(self.grigliata, 30), (self.pollo, 10)])
        consumi = {c["prodotto"].nome: c for c in report.consumo_per_porzione([self.serata])}
        # Pollo: 10 kg per 40 porzioni (grigliata + solo pollo); polenta: 5 kg per le stesse 40.
        self.assertEqual((consumi["Pollo"]["usato"], consumi["Pollo"]["porzioni"], consumi["Pollo"]["per_porzione"]), (D("10"), 40, D("0.250")))
        self.assertEqual(consumi["Pollo"]["piatti"], ["Grigliata mista", "Solo pollo"])
        self.assertEqual(consumi["Polenta"]["per_porzione"], D("0.125"))
        self.assertNotIn("Salamelle", consumi)  # ingrediente ma mai uscito dal magazzino

    def test_annullati_non_contano(self):
        ordine, _ = self.ordina([(self.grigliata, 1)])
        services.annulla_ordine(ordine=ordine, utente=None)
        self.assertEqual(report.consumo_per_porzione([self.serata]), [])
        self.assertIsNone(report.riepilogo_breve(Servizio.objects.create(data=self.serata.data + timedelta(days=3))))
        self.assertEqual(report.riepilogo_breve(self.serata), {"ordini": 0})

    def test_tempi_senza_monitor(self):
        self.ordina([(self.grigliata, 1)])
        self.assertIsNone(report.tempi_cucina(self.serata))
        self.assertEqual(Comanda.objects.get().reparto, Reparto.CUCINA)
        self.assertLess(Comanda.objects.get().ordine.creato_il, timezone.now() + timedelta(seconds=1))
