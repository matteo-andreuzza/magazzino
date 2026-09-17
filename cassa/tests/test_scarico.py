from decimal import Decimal as D

from django.urls import reverse

from cassa import report, services
from cassa.models import Articolo
from magazzino import services as magazzino
from magazzino.models import Operazione, Prodotto, TipoOperazione, Ubicazione
from magazzino.views.pagine import dati_riepilogo

from .base import BaseCassa


class ScaricoVendutoTest(BaseCassa):
    def setUp(self):
        super().setUp()
        self.frigo_bar = Ubicazione.objects.get(nome="Frigo bar")
        self.dispensa = Ubicazione.objects.get(nome="Dispensa")
        self.lattine = Prodotto.objects.create(nome="Coca-Cola lattine", categoria="bevande", unita="lattina")
        Articolo.objects.filter(pk=self.coca.pk).update(prodotto=self.lattine, quantita_prodotto=D("1"))
        self.imposta(scarico_magazzino=True, ubicazione_scarico=self.frigo_bar)
        magazzino.registra_carico(tipo=TipoOperazione.ACQUISTO, ubicazione=self.dispensa, quantita=[(self.lattine, D("100"))], utente=None)
        magazzino.registra_carico(tipo=TipoOperazione.ACQUISTO, ubicazione=self.frigo_bar, quantita=[(self.lattine, D("10"))], utente=None)
        self.client.force_login(self.utente)
        self.url = reverse("servizio_venduto", args=[self.serata.pk])

    def test_da_scaricare(self):
        self.ordina([(self.coca, 12), (self.grigliata, 1)])
        annullato, _ = self.ordina([(self.coca, 5)])
        services.annulla_ordine(ordine=annullato, utente=None)
        self.assertEqual(report.da_scaricare(self.serata), {self.lattine.pk: {"venduto": D("12"), "scaricato": D("0"), "mancante": D("12")}})

    def test_proposta_divisa_partendo_dal_frigo_bar(self):
        self.ordina([(self.coca, 12)])
        risposta = self.client.get(self.url)
        self.assertEqual(risposta.status_code, 200)
        righe = {r.chiave: r.testo for r in risposta.context["righe"]}
        self.assertEqual(righe, {f"{self.lattine.pk}-{self.frigo_bar.pk}": "10", f"{self.lattine.pk}-{self.dispensa.pk}": "2"})
        self.assertEqual(risposta.context["avvisi"], [])

    def test_registra_e_poi_niente_da_scaricare(self):
        self.ordina([(self.coca, 12)])
        dati = {"token": "3f1d7a9e-1c2b-4a5d-9e8f-000000000001", "note": ""}
        dati[f"q-{self.lattine.pk}-{self.frigo_bar.pk}"] = "10"
        dati[f"q-{self.lattine.pk}-{self.dispensa.pk}"] = "2"
        risposta = self.client.post(self.url, dati)
        self.assertRedirects(risposta, reverse("servizio_dettaglio", args=[self.serata.pk]))
        operazione = Operazione.objects.get(tipo=TipoOperazione.VENDITA)
        self.assertEqual(operazione.servizio, self.serata)
        giacenze = magazzino.giacenze()
        self.assertNotIn((self.lattine.pk, self.frigo_bar.pk), giacenze)  # frigo bar vuotato
        self.assertEqual(giacenze[(self.lattine.pk, self.dispensa.pk)], D("98"))
        self.assertEqual(report.da_scaricare(self.serata)[self.lattine.pk]["mancante"], D("0"))
        self.assertEqual(magazzino.consumi_servizio(self.serata)[self.lattine.pk]["venduto"], D("12"))
        _, righe = dati_riepilogo()
        self.assertEqual(next(r for r in righe if r["prodotto"] == self.lattine)["totale"], D("12"))
        self.assertContains(self.client.get(self.url), "Non c&#x27;è niente da scaricare")
        # Lo scarico si annulla dallo storico come ogni operazione.
        magazzino.annulla(operazione=operazione)
        self.assertEqual(report.da_scaricare(self.serata)[self.lattine.pk]["mancante"], D("12"))

    def test_avviso_se_in_magazzino_non_basta(self):
        self.ordina([(self.coca, 120)])
        risposta = self.client.get(self.url)
        self.assertEqual(len(risposta.context["righe"]), 2)
        self.assertIn("ne mancano 10 lattine", risposta.context["avvisi"][0])

    def test_avviso_se_scaricato_piu_del_venduto(self):
        ordine, _ = self.ordina([(self.coca, 5)])
        magazzino.registra_vendita(servizio=self.serata, prelievi=[(self.lattine, self.frigo_bar, D("5"))], utente=None)
        services.annulla_ordine(ordine=ordine, utente=None)
        risposta = self.client.get(self.url)
        self.assertEqual(risposta.context["righe"], [])
        self.assertIn("scaricato 5 lattine più del venduto", risposta.context["avvisi"][0])

    def test_spento(self):
        self.imposta(scarico_magazzino=False)
        risposta = self.client.get(self.url)
        self.assertRedirects(risposta, reverse("servizio_dettaglio", args=[self.serata.pk]))

    def test_serata_terminata_senza_movimenti_lo_segnala(self):
        self.ordina([(self.coca, 3)])
        risposta = self.client.get(reverse("servizio_dettaglio", args=[self.serata.pk]))
        self.assertContains(risposta, "ancora da scaricare")
        self.assertContains(self.client.get(reverse("servizio_chiudi", args=[self.serata.pk])), "non ancora scaricato")
