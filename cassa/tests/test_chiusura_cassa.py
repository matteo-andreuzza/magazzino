"""Chiusura della cassa a fine serata e ordini omaggio."""

from django.urls import reverse

from cassa import services
from cassa.models import Pagamento, Postazione
from cassa.services import ErroreCassa

from .base import BaseCassa


class ChiusuraCassaTest(BaseCassa):
    def test_chiusa_non_registra_e_si_riapre(self):
        self.imposta(cassa_bar=True)
        ordine, _ = self.ordina([(self.grigliata, 1)])
        self.assertTrue(services.chiudi_cassa(servizio=self.serata, utente=self.utente))
        self.assertFalse(services.chiudi_cassa(servizio=self.serata, utente=self.utente))
        with self.assertRaisesMessage(ErroreCassa, "La cassa è chiusa"):
            self.ordina([(self.grigliata, 1)])
        with self.assertRaisesMessage(ErroreCassa, "La cassa è chiusa"):
            self.ordina([(self.coca, 1)], postazione=Postazione.BAR, nome="", tavolo="")
        with self.assertRaisesMessage(ErroreCassa, "La cassa è chiusa"):
            services.annulla_ordine(ordine=ordine, utente=self.utente)
        # La ristampa resta possibile.
        self.assertTrue(services.ristampa(ordine=ordine, cosa="cucina"))
        self.assertTrue(services.riapri_cassa(servizio=self.serata))
        self.assertFalse(services.riapri_cassa(servizio=self.serata))
        self.assertTrue(self.ordina([(self.grigliata, 1)])[1])

    def test_dalle_pagine(self):
        self.client.force_login(self.utente)
        self.ordina([(self.grigliata, 2)], pagamento=Pagamento.CARTA)
        url_chiudi = reverse("cassa:chiudi", args=[self.serata.pk])
        self.assertContains(self.client.get(url_chiudi), "Chiudere la cassa?")
        risposta = self.client.post(url_chiudi)
        self.assertRedirects(risposta, reverse("cassa:report", args=[self.serata.pk]))
        risposta = self.client.get(risposta.url)
        self.assertContains(risposta, "Riepilogo della serata")
        self.assertContains(risposta, "Riapri la cassa")
        self.assertContains(risposta, "Fine serata")
        # Nessun fusto attaccato: non c'è niente da pesare.
        self.assertNotContains(risposta, "Pesa i fusti rimasti attaccati")
        # La cassa si vede bloccata e lo stato lo dice alle altre casse aperte.
        risposta = self.client.get(reverse("cassa:cassa", args=[self.serata.pk]))
        self.assertContains(risposta, "Cassa chiusa")
        self.assertContains(risposta, "pos-bloccata")
        self.assertTrue(self.client.get(reverse("cassa:stato", args=[self.serata.pk])).json()["chiuso"])
        self.assertRedirects(self.client.get(url_chiudi), reverse("cassa:report", args=[self.serata.pk]))
        risposta = self.client.post(reverse("cassa:riapri", args=[self.serata.pk]))
        self.assertRedirects(risposta, reverse("cassa:cassa", args=[self.serata.pk]))
        self.assertFalse(self.client.get(reverse("cassa:stato", args=[self.serata.pk])).json()["chiuso"])


class OmaggioTest(BaseCassa):
    def test_omaggio_senza_nome_e_tavolo(self):
        ordine, _ = self.ordina([(self.grigliata, 1)], pagamento=Pagamento.OMAGGIO, nome="", tavolo="")
        self.assertEqual((ordine.nome, ordine.tavolo), ("Volontario", "XXX"))
        # Se nome e tavolo ci sono, restano quelli.
        ordine, _ = self.ordina([(self.grigliata, 1)], pagamento=Pagamento.OMAGGIO, nome="Gino", tavolo="")
        self.assertEqual((ordine.nome, ordine.tavolo), ("Gino", "XXX"))

    def test_pagato_vuole_nome_e_tavolo(self):
        with self.assertRaises(ErroreCassa) as e:
            self.ordina([(self.grigliata, 1)], nome="", tavolo="")
        self.assertEqual(e.exception.messaggi, ["Manca il nome del cliente.", "Manca il numero del tavolo."])

    def test_la_cassa_sa_cosa_scrivere(self):
        self.client.force_login(self.utente)
        risposta = self.client.get(reverse("cassa:cassa", args=[self.serata.pk]))
        self.assertEqual(risposta.context["dati"]["omaggio"], {"nome": "Volontario", "tavolo": "XXX"})
        self.assertContains(risposta, "tavolo XXX")
