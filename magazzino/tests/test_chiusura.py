from datetime import date
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from magazzino import services
from magazzino.models import Operazione, Prodotto, Servizio, TipoOperazione, Ubicazione
from magazzino.services import ErroreMagazzino
from magazzino.views.comuni import giorno_servizio


class BaseChiusura(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.utente = get_user_model().objects.create_user("cucina", password="segreta-123")
        cls.frigo1 = Ubicazione.objects.get(nome="Frigo 1")
        cls.pollo = Prodotto.objects.get(nome="Pollo")
        cls.serata = Servizio.objects.create(data=date(2026, 9, 18))
        cls.pranzo = Servizio.objects.create(data=date(2026, 9, 20), tipo="pranzo", nome="Pranzo paesano")

    def setUp(self):
        self.client.force_login(self.utente)
        services.registra_carico(
            tipo=TipoOperazione.ACQUISTO, ubicazione=self.frigo1, quantita=[(self.pollo, D("10"))], utente=self.utente
        )

    def preleva(self, q, servizio=None):
        op, _ = services.registra_cucina(
            servizio=servizio or self.serata, prelievi=[(self.pollo, self.frigo1, D(q))], utente=self.utente
        )
        return op

    def chiudi(self, servizio=None):
        return services.chiudi_servizio(servizio=servizio or self.serata, utente=self.utente)


class ChiusuraServiziTest(BaseChiusura):
    def test_chiudere_blocca_prelievi_e_rientri(self):
        self.preleva("4")
        self.assertTrue(self.chiudi())
        self.serata.refresh_from_db()
        self.assertTrue(self.serata.chiuso)
        self.assertEqual(self.serata.chiuso_da, self.utente)
        with self.assertRaisesMessage(ErroreMagazzino, "terminato"):
            self.preleva("1")
        with self.assertRaisesMessage(ErroreMagazzino, "terminato"):
            services.registra_reso(
                servizio=self.serata, rientri=[(self.pollo, self.frigo1, D("1"))], utente=self.utente
            )
        self.assertEqual(Operazione.objects.filter(servizio=self.serata).count(), 1)

    def test_gli_altri_servizi_e_il_magazzino_restano_utilizzabili(self):
        self.chiudi()
        self.preleva("2", servizio=self.pranzo)
        services.registra_scarto(ubicazione=self.frigo1, quantita=[(self.pollo, D("1"))], utente=self.utente)
        self.assertEqual(services.giacenze()[(self.pollo.pk, self.frigo1.pk)], D("7"))

    def test_chiudere_due_volte_non_cambia_niente(self):
        self.assertTrue(self.chiudi())
        prima = Servizio.objects.get(pk=self.serata.pk).chiuso_il
        self.assertFalse(self.chiudi())
        self.assertEqual(Servizio.objects.get(pk=self.serata.pk).chiuso_il, prima)

    def test_riaprire_permette_di_nuovo_di_registrare(self):
        self.chiudi()
        self.assertTrue(services.riapri_servizio(servizio=self.serata))
        self.assertFalse(services.riapri_servizio(servizio=self.serata))
        self.serata.refresh_from_db()
        self.assertIsNone(self.serata.chiuso_da)
        self.preleva("1")

    def test_non_si_annulla_in_un_servizio_chiuso(self):
        op = self.preleva("3")
        self.chiudi()
        with self.assertRaisesMessage(ErroreMagazzino, "riaprirlo"):
            services.annulla(operazione=op)
        with self.assertRaisesMessage(ErroreMagazzino, "riaprirlo"):
            services.annulla(movimento=op.movimenti.get())
        services.riapri_servizio(servizio=self.serata)
        services.annulla(operazione=op)
        self.assertFalse(Operazione.objects.filter(pk=op.pk).exists())

    def test_annullare_carichi_non_dipende_dai_servizi(self):
        self.chiudi()
        carico = Operazione.objects.get(tipo=TipoOperazione.ACQUISTO)
        services.annulla(operazione=carico)
        self.assertFalse(Operazione.objects.filter(pk=carico.pk).exists())


class ChiusuraVisteTest(BaseChiusura):
    def test_pagina_di_conferma_mostra_cosa_non_e_rientrato(self):
        self.preleva("4")
        services.registra_reso(servizio=self.serata, rientri=[(self.pollo, self.frigo1, D("1"))], utente=self.utente)
        risposta = self.client.get(reverse("servizio_chiudi", args=[self.serata.pk]))
        self.assertContains(risposta, "Terminare la serata?")
        self.assertContains(risposta, "non ancora rientrato")
        self.assertContains(risposta, "3 kg")
        self.assertContains(risposta, reverse("servizio_reso", args=[self.serata.pk]))
        self.assertFalse(Servizio.objects.get(pk=self.serata.pk).chiuso)

    def test_pranzo_al_maschile(self):
        risposta = self.client.get(reverse("servizio_chiudi", args=[self.pranzo.pk]))
        self.assertContains(risposta, "Terminare il pranzo?")
        risposta = self.client.post(reverse("servizio_chiudi", args=[self.pranzo.pk]), follow=True)
        self.assertContains(risposta, "Pranzo terminato.")
        self.assertContains(risposta, "Riapri il pranzo")

    def test_termina_e_riapri(self):
        risposta = self.client.post(reverse("servizio_chiudi", args=[self.serata.pk]), follow=True)
        self.assertRedirects(risposta, reverse("servizio_dettaglio", args=[self.serata.pk]))
        self.assertContains(risposta, "Serata terminata.")
        self.assertNotContains(risposta, reverse("servizio_cucina", args=[self.serata.pk]))
        servizio = Servizio.objects.get(pk=self.serata.pk)
        self.assertTrue(servizio.chiuso)
        self.assertEqual(servizio.chiuso_da, self.utente)

        self.assertEqual(self.client.get(reverse("servizio_riapri", args=[self.serata.pk])).status_code, 405)
        risposta = self.client.post(reverse("servizio_riapri", args=[self.serata.pk]), follow=True)
        self.assertContains(risposta, "Serata riaperta")
        self.assertContains(risposta, reverse("servizio_cucina", args=[self.serata.pk]))
        self.assertFalse(Servizio.objects.get(pk=self.serata.pk).chiuso)

    def test_moduli_della_cucina_non_si_aprono_a_servizio_chiuso(self):
        self.chiudi()
        for nome in ("servizio_cucina", "servizio_reso"):
            with self.subTest(nome=nome):
                risposta = self.client.get(reverse(nome, args=[self.serata.pk]), follow=True)
                self.assertRedirects(risposta, reverse("servizio_dettaglio", args=[self.serata.pk]))
                self.assertContains(risposta, "riaprirla")

    def test_invio_del_modulo_dopo_la_chiusura_non_registra(self):
        # Il modulo era aperto sul telefono quando qualcuno ha terminato la serata.
        pagina = self.client.get(reverse("servizio_cucina", args=[self.serata.pk]))
        token = pagina.context["token"]
        self.chiudi()
        self.client.post(
            reverse("servizio_cucina", args=[self.serata.pk]),
            {"token": token, f"q-{self.pollo.pk}-{self.frigo1.pk}": "2"},
        )
        self.assertFalse(Operazione.objects.filter(tipo=TipoOperazione.CUCINA).exists())

    def test_home_mostra_il_servizio_terminato(self):
        oggi = Servizio.objects.create(data=giorno_servizio())
        risposta = self.client.get(reverse("home"))
        self.assertContains(risposta, reverse("servizio_chiudi", args=[oggi.pk]))
        services.chiudi_servizio(servizio=oggi, utente=self.utente)
        risposta = self.client.get(reverse("home"))
        self.assertContains(risposta, "terminata")
        self.assertNotContains(risposta, reverse("servizio_cucina", args=[oggi.pk]))
        self.assertNotContains(risposta, reverse("servizio_chiudi", args=[oggi.pk]))

    def test_annullare_da_servizio_chiuso_rimanda_al_dettaglio(self):
        op = self.preleva("2")
        self.chiudi()
        dettaglio = self.client.get(reverse("operazione_dettaglio", args=[op.pk]))
        self.assertNotContains(dettaglio, reverse("operazione_annulla", args=[op.pk]))
        self.assertContains(dettaglio, "riapri la serata")
        for url in (
            reverse("operazione_annulla", args=[op.pk]),
            reverse("movimento_annulla", args=[op.movimenti.get().pk]),
        ):
            with self.subTest(url=url):
                self.assertRedirects(self.client.get(url), reverse("operazione_dettaglio", args=[op.pk]))
                self.client.post(url)
                self.assertTrue(Operazione.objects.filter(pk=op.pk).exists())

    def test_modificare_un_servizio_chiuso_non_lo_riapre(self):
        self.chiudi()
        self.client.post(
            reverse("servizio_modifica", args=[self.serata.pk]),
            {"data": "2026-09-18", "tipo": "sera", "nome": "Serata del pesce", "note": ""},
        )
        servizio = Servizio.objects.get(pk=self.serata.pk)
        self.assertEqual(servizio.nome, "Serata del pesce")
        self.assertTrue(servizio.chiuso)

    def test_elenco_servizi_segna_i_terminati(self):
        self.chiudi()
        self.assertContains(self.client.get(reverse("servizi")), "terminata")
