"""Giacenze del bar: carichi e correzioni meno il venduto in cassa."""

import uuid
from decimal import Decimal as D

from django.urls import reverse

from bar import services
from bar.models import MovimentoBar, OperazioneBar, TipoOperazioneBar
from bar.services import ErroreBar
from cassa import services as cassa
from cassa.models import Articolo, RigaOrdine

from .base import BaseBar


class GiacenzeTest(BaseBar):
    def setUp(self):
        super().setUp()
        self.inventario((self.coca_b, "48"), (self.vino, "20"), (self.f30, "6"))

    def test_le_vendite_tolgono_dal_bar(self):
        self.ordina([(self.coca, 3), (self.ombra, 4), (self.grigliata, 1)])
        self.ordina([(self.coca, 2)], pagamento="omaggio")  # anche gli omaggi escono dal magazzino
        annullato, _ = self.ordina([(self.coca, 10)])
        cassa.annulla_ordine(ordine=annullato, utente=None)  # gli annullati no
        giacenze = services.giacenze_bevande()
        self.assertEqual(giacenze[self.coca_b.pk], D("43"))
        self.assertEqual(giacenze[self.vino.pk], D("19.6"))
        self.assertNotIn(self.birra.pk, giacenze)  # la birra si conta in fusti
        self.assertEqual(services.venduto([self.serata]), {self.coca_b.pk: D("5"), self.vino.pk: D("0.4")})

    def test_birra_venduta_in_litri(self):
        self.ordina([(self.birra_piccola, 10), (self.caraffa, 2)])
        self.assertEqual(services.venduto([self.serata])[self.birra.pk], D("6.3"))
        self.assertEqual(services.fusti_pieni(), {self.f30.pk: D("6")})

    def test_il_venduto_resta_quello_della_vendita(self):
        # Cambiare dopo il collegamento dell'articolo non cambia il venduto già registrato.
        self.ordina([(self.birra_piccola, 10)])
        Articolo.objects.filter(pk=self.birra_piccola.pk).update(quantita_bevanda=D("0.4"), bevanda=None)
        riga = RigaOrdine.objects.get(articolo=self.birra_piccola)
        self.assertEqual((riga.bevanda, riga.quantita_bevanda), (self.birra, D("0.330")))
        self.assertEqual(services.venduto([self.serata])[self.birra.pk], D("3.3"))
        self.ordina([(self.birra_piccola, 1)])
        self.assertEqual(services.venduto([self.serata])[self.birra.pk], D("3.3"))

    def test_sotto_zero_non_blocca_la_cassa(self):
        ordine, creato = self.ordina([(self.coca, 50)])
        self.assertTrue(creato)
        self.assertEqual(services.giacenze_bevande()[self.coca_b.pk], D("-2"))

    def test_correzione_e_annullamento(self):
        self.ordina([(self.coca, 8)])
        op, _ = services.registra_rettifica(conteggi=[(self.coca_b, D("38")), (self.f30, D("5"))], utente=None)
        self.assertEqual(services.giacenze_bevande()[self.coca_b.pk], D("38"))
        self.assertEqual(services.fusti_pieni()[self.f30.pk], D("5"))
        self.assertEqual(sorted(MovimentoBar.objects.filter(operazione=op).values_list("quantita", flat=True)), [D("-2"), D("-1")])
        with self.assertRaisesMessage(ErroreBar, "coincidono già"):
            services.registra_rettifica(conteggi=[(self.coca_b, D("38"))], utente=None)
        self.assertTrue(services.annulla_operazione(op))
        self.assertEqual(services.giacenze_bevande()[self.coca_b.pk], D("40"))
        self.assertFalse(services.annulla_operazione(op))

    def test_fusti_interi(self):
        with self.assertRaises(ErroreBar):
            services.registra_carico(tipo=TipoOperazioneBar.CARICO, voci=[(self.f30, D("1.5"))], utente=None)

    def test_doppio_invio(self):
        token = uuid.uuid4()
        op, creata = services.registra_carico(tipo=TipoOperazioneBar.CARICO, voci=[(self.coca_b, D("24"))], utente=None, token=token)
        di_nuovo, creata2 = services.registra_carico(tipo=TipoOperazioneBar.CARICO, voci=[(self.coca_b, D("24"))], utente=None, token=token)
        self.assertEqual((creata, creata2, di_nuovo), (True, False, op))
        self.assertEqual(services.giacenze_bevande()[self.coca_b.pk], D("72"))


class VisteBarTest(BaseBar):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.utente)

    def test_carico_dal_modulo(self):
        url = reverse("bar:carico")
        dati = {"token": str(uuid.uuid4()), "tipo": "inventario", f"q-b{self.coca_b.pk}": "48", f"q-f{self.f30.pk}": "6,5"}
        risposta = self.client.post(url, dati)
        self.assertContains(risposta, "I fusti si contano interi.")
        dati[f"q-f{self.f30.pk}"] = "6"
        self.assertRedirects(self.client.post(url, dati), reverse("bar:inizio"))
        self.assertEqual(services.fusti_pieni(), {self.f30.pk: D("6")})
        risposta = self.client.get(reverse("bar:inizio"))
        self.assertContains(risposta, "48 lattine")
        self.assertContains(risposta, "6 fusti")
        self.assertEqual(OperazioneBar.objects.count(), 1)

    def test_correggi_dal_modulo(self):
        self.inventario((self.coca_b, "48"))
        self.ordina([(self.coca, 5)])
        risposta = self.client.post(reverse("bar:correggi"), {"token": str(uuid.uuid4()), f"q-b{self.coca_b.pk}": "40"})
        self.assertRedirects(risposta, reverse("bar:inizio"))
        self.assertEqual(services.giacenze_bevande()[self.coca_b.pk], D("40"))

    def test_consumi_per_serata(self):
        self.ordina([(self.birra_piccola, 10), (self.coca, 2)])
        self.ordina([(self.ombra, 3)], servizio=self.serata2)
        risposta = self.client.get(reverse("bar:consumi"))
        self.assertContains(risposta, "3,3 l")
        self.assertContains(risposta, "2 lattine")
        self.assertContains(risposta, "0,3 l")

    def test_bevande_solo_staff(self):
        self.assertEqual(self.client.get(reverse("bar:bevanda_nuova")).status_code, 403)
        self.client.force_login(self.staff)
        risposta = self.client.post(reverse("bar:bevanda_nuova"), {
            "nome": "Sprite", "tipo": "lattina", "colore": "bionda", "ordine": "0", "attiva": "on",
        })
        self.assertRedirects(risposta, reverse("bar:bevande"))
        risposta = self.client.post(reverse("bar:fusto_modifica", args=[self.f30.pk]), {
            "bevanda": self.birra.pk, "nome": "Fusto da 30 litri", "litri": "30", "tara": "9,8", "colore": "acciaio",
            "forma": "alto", "ordine": "2", "attivo": "on",
        })
        self.assertRedirects(risposta, reverse("bar:bevande"))
        self.f30.refresh_from_db()
        self.assertEqual(self.f30.tara, D("9.8"))
