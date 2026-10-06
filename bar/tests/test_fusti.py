"""Fusti alla spinatrice: i casi raccontati da chi gestisce il bar.

Con la densità della birra (1,01 kg/l) e le tare dei tipi di fusto (5 kg il
15 litri, 10 kg il 30 litri) i pesi scelti danno litri tondi: un 15 litri
pieno pesa 20,15 kg, uno con 6 litri dentro 11,06 kg.
"""

from datetime import date
from decimal import Decimal as D

from django.urls import reverse

from bar import services
from bar.models import ContaFusti, Fusto, Pesata, Spina, TipoFusto
from bar.services import ErroreBar
from magazzino import services as magazzino
from magazzino.models import Servizio
from magazzino.views.comuni import giorno_servizio

from .base import BaseBar


class FustiTest(BaseBar):
    def setUp(self):
        super().setUp()
        self.inventario((self.f15, "4"), (self.f30, "6"))

    def prima_serata(self):
        # Attaccato un 30 litri, finito e cambiato con un 15 litri, che a fine serata pesa 11,06 kg (6 litri dentro).
        trenta = self.attacca(self.f30)
        quindici = self.cambia(self.f15, attuale=trenta)
        self.pesa(quindici, "11.06")
        return trenta, quindici

    def test_prima_serata(self):
        trenta, quindici = self.prima_serata()
        self.assertEqual(self.spillato(self.serata), D("39.0"))  # 30 + 9
        self.assertEqual(services.fusti_pieni(), {self.f15.pk: D("3"), self.f30.pk: D("5")})
        attaccati = services.fusti_attaccati()
        self.assertEqual([s.fusto for s in attaccati], [quindici])
        self.assertEqual(services.arrotonda(attaccati[0].litri_rimasti), D("6.0"))
        self.assertEqual([s.fusto for s in services.fusti_consumati()], [trenta])
        self.assertEqual(services.fusti_consumati()[0].litri_spillati, D("30"))
        self.assertEqual(ContaFusti.objects.count(), 1)

    def test_avanzato_da_ieri_si_pesa_e_basta(self):
        _, quindici = self.prima_serata()
        # Seconda sera: non è finito, a fine serata si pesa (8,03 kg: 3 litri usciti).
        self.assertEqual([s.fusto for s in services.da_pesare(self.serata2)], [quindici])
        self.pesa(quindici, "8.03", servizio=self.serata2)
        self.assertEqual(self.spillato(self.serata2), D("3.0"))
        self.assertEqual(services.da_pesare(self.serata2), [])
        # Terza sera: non usato, stesso peso.
        self.pesa(quindici, "8.03", servizio=self.serata3)
        self.assertEqual(self.spillato(self.serata3), D("0.0"))
        # Ripesarlo nella stessa serata corregge la pesata, non ne aggiunge un'altra.
        self.pesa(quindici, "7.02", servizio=self.serata3)
        self.assertEqual(Pesata.objects.filter(fusto=quindici, conta__servizio=self.serata3).count(), 1)
        self.assertEqual(self.spillato(self.serata3), D("1.0"))

    def test_finito_vale_la_tara_e_la_tara_corretta_corregge_le_serate(self):
        _, quindici = self.prima_serata()
        self.finisci(quindici, servizio=self.serata2)
        self.assertEqual(self.spillato(self.serata2), D("6.0"))
        self.assertFalse(Pesata.objects.get(fusto=quindici, vuoto=True).misurata)
        # L'amministrazione scrive la tara vera: cambiano anche i conti già fatti, e il fusto dà sempre 15 litri.
        TipoFusto.objects.filter(pk=self.f15.pk).update(tara=D("4.9"))
        self.assertEqual(self.spillato(self.serata), D("38.9"))
        self.assertEqual(self.spillato(self.serata2), D("6.1"))
        self.assertEqual(services.stati_fusti(Fusto.objects.filter(pk=quindici.pk))[0].litri_spillati, D("15"))

    def test_spina_occupata_e_cambio_sbagliato(self):
        trenta = self.attacca(self.f30)
        with self.assertRaisesMessage(ErroreBar, "C'è già un fusto attaccato"):
            self.attacca(self.f15)
        altro = Fusto(pk=trenta.pk + 100)
        with self.assertRaisesMessage(ErroreBar, "Nel frattempo il fusto attaccato è cambiato"):
            self.cambia(self.f15, attuale=altro)
        self.assertEqual([s.fusto for s in services.fusti_attaccati()], [trenta])

    def test_due_spine(self):
        terza = Spina.objects.create(spinatrice=self.spina.spinatrice, nome="Terza spina", ordine=3)
        self.attacca(self.f30)
        self.attacca(self.f15, spina=terza)
        self.assertEqual(len(services.fusti_attaccati()), 2)
        self.assertEqual(len(services.spine_in_uso()), 5)  # Bionda, Rossa, Terza, Bianco, Rosso

    def test_alla_spina_solo_i_fusti_della_sua_bevanda(self):
        rossa = Spina.objects.get(nome="Rossa")
        with self.assertRaisesMessage(ErroreBar, "Alla spina «Rossa» va Birra rossa"):
            self.attacca(self.f30, spina=rossa)
        trenta = self.attacca(self.f30)
        with self.assertRaisesMessage(ErroreBar, "Alla spina «Bionda» va Birra bionda"):
            self.cambia(TipoFusto.objects.get(bevanda=self.rossa), attuale=trenta)

    def test_pesate_impossibili(self):
        _, quindici = self.prima_serata()
        casi = [
            ("12", "Pesa più dell'ultima volta (11,06 kg)"),
            ("4", "Pesa meno di un fusto vuoto (un «Fusto da 15 litri» vuoto pesa 5 kg)"),
            ("0", "Scrivi il peso in chili"),
            ("250", "Scrivi il peso in chili"),
        ]
        for peso, messaggio in casi:
            with self.subTest(peso=peso), self.assertRaisesMessage(ErroreBar, messaggio):
                self.pesa(quindici, peso, servizio=self.serata2)
        # Un fusto nuovo può pesare poco più del peso da pieno stimato, non molto di più.
        self.finisci(quindici, servizio=self.serata2)
        trenta = self.attacca(self.f30, servizio=self.serata2)
        self.pesa(trenta, "41", servizio=self.serata2)
        with self.assertRaisesMessage(ErroreBar, "Pesa più di un fusto pieno"):
            self.pesa(trenta, "45", servizio=self.serata2)
        # Un fusto finito non si pesa.
        with self.assertRaisesMessage(ErroreBar, "tra i finiti"):
            self.pesa(quindici, "5", servizio=self.serata2)

    def test_errori_si_correggono(self):
        trenta, quindici = self.prima_serata()
        # Attaccato per sbaglio stasera: torna tra i pieni.
        services.stacca(servizio=self.serata, fusto=quindici)
        self.assertFalse(Fusto.objects.filter(pk=quindici.pk).exists())
        self.assertEqual(services.fusti_pieni(), {self.f15.pk: D("4"), self.f30.pk: D("5")})
        # Spostato tra i consumati per sbaglio: torna alla spina, senza la pesata da vuoto.
        services.riattacca(servizio=self.serata, fusto=trenta)
        self.assertEqual([s.fusto for s in services.fusti_attaccati()], [trenta])
        self.assertFalse(Pesata.objects.filter(fusto=trenta).exists())
        self.pesa(trenta, "30.2")
        self.assertTrue(services.togli_pesata(servizio=self.serata, fusto=trenta))
        self.assertFalse(services.togli_pesata(servizio=self.serata, fusto=trenta))
        # Annullato tutto, la serata resta senza fusti (e senza la sua conta).
        services.stacca(servizio=self.serata, fusto=trenta)
        self.assertFalse(ContaFusti.objects.exists())
        self.assertEqual(services.fusti_pieni(), {self.f15.pk: D("4"), self.f30.pk: D("6")})

    def test_errori_delle_serate_prima_non_si_annullano(self):
        trenta, quindici = self.prima_serata()
        with self.assertRaisesMessage(ErroreBar, "da una serata prima"):
            services.stacca(servizio=self.serata2, fusto=quindici)
        with self.assertRaisesMessage(ErroreBar, "finito in una serata prima"):
            services.riattacca(servizio=self.serata2, fusto=trenta)

    def test_solo_l_ultima_serata(self):
        _, quindici = self.prima_serata()
        self.pesa(quindici, "10", servizio=self.serata2)
        # La sera prima ormai è passata…
        with self.assertRaisesMessage(ErroreBar, "Dopo questa serata"):
            self.pesa(quindici, "9", servizio=self.serata)
        # …e una serata più vecchia senza fusti non può cominciare adesso.
        vecchia = Servizio.objects.create(data=date(2026, 9, 10))
        with self.assertRaisesMessage(ErroreBar, "più recente"):
            self.pesa(quindici, "9", servizio=vecchia)
        # Il pranzo viene prima della sera dello stesso giorno.
        pranzo = Servizio.objects.create(data=self.serata2.data, tipo="pranzo")
        self.assertIsNotNone(services.motivo_sola_lettura(pranzo))
        sera_dopo = self.serata3
        self.assertIsNone(services.motivo_sola_lettura(sera_dopo))

    def test_serata_terminata_si_sistema_fino_alla_serata_dopo(self):
        # Le pesate si scrivono spesso il giorno dopo, a serata già terminata.
        magazzino.chiudi_servizio(servizio=self.serata, utente=None)
        trenta = self.attacca(self.f30)
        self.pesa(trenta, "30.2")
        self.assertIsNone(services.motivo_sola_lettura(self.serata))
        # Appena si registrano i fusti della serata dopo, quella prima resta da guardare.
        self.pesa(trenta, "25", servizio=self.serata2)
        with self.assertRaisesMessage(ErroreBar, "Dopo questa serata ci sono già i fusti"):
            self.pesa(trenta, "30", servizio=self.serata)


class FustiVisteTest(BaseBar):
    def setUp(self):
        super().setUp()
        self.inventario((self.f15, "4"), (self.f30, "6"))
        self.client.force_login(self.utente)
        self.url = reverse("bar:conta_fusti", args=[self.serata.pk])
        self.azione = reverse("bar:fusti_azione", args=[self.serata.pk])

    def test_la_pagina_dei_fusti_porta_alla_serata_di_oggi(self):
        self.assertContains(self.client.get(reverse("bar:fusti")), "Oggi non c'è una serata aperta")
        oggi = Servizio.objects.create(data=giorno_servizio())
        self.assertRedirects(self.client.get(reverse("bar:fusti")), reverse("bar:conta_fusti", args=[oggi.pk]))

    def test_una_serata_dal_bancone(self):
        self.ordina([(self.birra_piccola, 10), (self.caraffa, 2)])  # 6,3 litri venduti
        pagina = self.client.get(self.url)
        self.assertContains(pagina, "Trascina qui un fusto nuovo di birra bionda")
        self.assertContains(pagina, "Fusto da 30 litri")
        self.assertContains(pagina, 'data-trascina="pieno"')
        # Attacca un 30 litri, poi lo cambia con un 15 litri e a fine serata lo pesa.
        risposta = self.client.post(self.azione, {"azione": "attacca", "spina": self.spina.pk, "tipo": self.f30.pk})
        self.assertRedirects(risposta, self.url, fetch_redirect_response=False)
        trenta = Fusto.objects.get()
        pagina = self.client.get(self.url)
        self.assertContains(pagina, "Fusto da 30 litri attaccato a «Bionda».")
        self.assertContains(pagina, "da pesare")
        self.client.post(self.azione, {
            "azione": "cambia", "spina": self.spina.pk, "tipo": self.f15.pk, "attuale": trenta.pk,
        })
        quindici = Fusto.objects.get(finito_con__isnull=True)
        risposta = self.client.post(self.azione, {"azione": "pesa", "fusto": quindici.pk, "peso": "11,06"}, follow=True)
        self.assertContains(risposta, "Peso salvato: 11,06 kg, restano circa 6,0 l.")
        self.assertContains(risposta, "≈ 39,0 l")  # uscita dai fusti
        self.assertContains(risposta, "6,3 l")  # venduta
        self.assertContains(risposta, "32,7 l")  # differenza
        self.assertContains(risposta, '<span class="etichetta ok">11,06 kg</span>', html=True)

    def test_errori_come_messaggi(self):
        trenta = self.attacca(self.f30)
        casi = [
            ({"azione": "attacca", "spina": self.spina.pk, "tipo": self.f15.pk}, "C&#x27;è già un fusto attaccato"),
            ({"azione": "pesa", "fusto": trenta.pk, "peso": "tanto"}, "Scrivi il peso in chili"),
            ({"azione": "pesa", "fusto": "999"}, "Nel frattempo qualcosa è cambiato"),
            ({"azione": "boh"}, "Operazione sconosciuta"),
        ]
        for dati, messaggio in casi:
            with self.subTest(messaggio=messaggio):
                self.assertContains(self.client.post(self.azione, dati, follow=True), messaggio)

    def test_serata_passata_solo_da_guardare(self):
        trenta = self.attacca(self.f30)
        self.pesa(trenta, "30.2", servizio=self.serata2)
        pagina = self.client.get(self.url)
        self.assertContains(pagina, "Solo da guardare")
        self.assertNotContains(pagina, 'data-trascina=')
        self.assertNotContains(pagina, 'name="azione"')
        risposta = self.client.post(self.azione, {"azione": "finito", "fusto": trenta.pk}, follow=True)
        self.assertContains(risposta, "Dopo questa serata")
        self.assertIsNone(Fusto.objects.get().finito_con)

    def test_tare_provvisorie(self):
        TipoFusto.objects.exclude(pk=self.f30.pk).update(tara_provvisoria=False)
        TipoFusto.objects.filter(pk=self.f30.pk).update(tara_provvisoria=True)
        self.assertContains(self.client.get(self.url), "Litri e tare provvisori")
        self.client.force_login(self.staff)
        risposta = self.client.post(reverse("bar:fusto_modifica", args=[self.f30.pk]), {
            "bevanda": self.birra.pk, "nome": "Fusto da 30 litri", "litri": "30", "tara": "11", "colore": "acciaio",
            "forma": "alto", "ordine": "1", "attivo": "on",
        })
        self.assertRedirects(risposta, reverse("bar:bevande"))
        self.assertFalse(TipoFusto.objects.filter(tara_provvisoria=True).exists())
        self.assertNotContains(self.client.get(self.url), "Litri e tare provvisori")
        risposta = self.client.post(reverse("bar:fusto_modifica", args=[self.f30.pk]), {
            "bevanda": self.birra.pk, "nome": "Fusto da 30 litri", "litri": "30", "tara": "", "colore": "acciaio",
            "forma": "alto", "ordine": "1", "attivo": "on",
        })
        self.assertContains(risposta, "Scrivi quanto pesa un fusto vuoto")

    def test_stessa_tara_per_lo_stesso_fusto_delle_due_birre(self):
        bionda, rossa = (TipoFusto.objects.get(bevanda=b, litri=50) for b in (self.birra, self.rossa))
        self.client.force_login(self.staff)
        risposta = self.client.post(reverse("bar:fusto_modifica", args=[bionda.pk]), {
            "bevanda": self.birra.pk, "nome": bionda.nome, "litri": "50", "tara": "13,2", "colore": "acciaio",
            "forma": "alto", "ordine": "1", "attivo": "on", "anche_gli_altri": "on",
        })
        self.assertRedirects(risposta, reverse("bar:bevande"))
        rossa.refresh_from_db()
        self.assertEqual((rossa.tara, rossa.tara_provvisoria), (D("13.2"), False))

    def test_spine_solo_staff(self):
        self.assertEqual(self.client.get(reverse("bar:spina_nuova")).status_code, 403)
        self.client.force_login(self.staff)
        risposta = self.client.post(reverse("bar:spina_nuova"), {
            "spinatrice": self.spina.spinatrice_id, "nome": "Spina 3", "ordine": "3", "attiva": "on",
        })
        self.assertRedirects(risposta, reverse("bar:bevande"))
        self.attacca(self.f30)
        risposta = self.client.post(reverse("bar:spina_modifica", args=[self.spina.pk]), {
            "spinatrice": self.spina.spinatrice_id, "nome": "Bionda", "bevanda": self.birra.pk, "ordine": "1",
        })
        self.assertContains(risposta, "C&#x27;è un fusto attaccato")
        risposta = self.client.post(reverse("bar:spina_modifica", args=[self.spina.pk]), {
            "spinatrice": self.spina.spinatrice_id, "nome": "Bionda", "bevanda": self.rossa.pk, "ordine": "1", "attiva": "on",
        })
        self.assertContains(risposta, "Adesso c&#x27;è attaccato un fusto di Birra bionda")

    def test_terminare_la_serata_ricorda_la_pesata(self):
        self.attacca(self.f30)
        self.client.force_login(self.utente)
        pagina = self.client.get(reverse("servizio_chiudi", args=[self.serata.pk]))
        self.assertContains(pagina, "Il fusto attaccato alla spina non è ancora stato pesato")
        self.pesa(Fusto.objects.get(), "30.2")
        self.assertNotContains(self.client.get(reverse("servizio_chiudi", args=[self.serata.pk])), "non è ancora stato pesato")


class FineSerataTest(BaseBar):
    """Pesata di tutti i fusti insieme e serata rifatta da capo, dalla pagina dei fusti."""

    def setUp(self):
        super().setUp()
        self.rossa_50 = TipoFusto.objects.get(bevanda=self.rossa, litri=50)
        TipoFusto.objects.filter(pk=self.rossa_50.pk).update(tara=D("5"), tara_provvisoria=False)
        self.inventario((self.f30, "6"), (self.rossa_50, "2"))
        self.spina_rossa = Spina.objects.get(nome="Rossa")
        self.client.force_login(self.utente)
        self.url = reverse("bar:conta_fusti", args=[self.serata.pk])
        self.azione = reverse("bar:fusti_azione", args=[self.serata.pk])

    def test_le_due_spinatrici_disegnate(self):
        pagina = self.client.get(self.url)
        for testo in ("Birra", "Vino", "Bionda", "Rossa", "Bianco", "Rosso", "Fusti nuovi", "Fusti finiti", "Largo argento"):
            self.assertContains(pagina, testo)
        # Il fusto della rossa si trascina solo sulle spine della rossa.
        self.assertContains(pagina, f'data-bevanda="{self.rossa.pk}"')
        self.assertContains(pagina, f'data-bevanda-spina="{self.rossa.pk}"')

    def test_pesata_di_tutti_i_fusti(self):
        trenta = self.attacca(self.f30)
        rossa = self.attacca(self.rossa_50, spina=self.spina_rossa)
        self.assertContains(self.client.get(self.url), "Pesata di fine serata")
        # Un peso sbagliato: non si salva niente, nemmeno quello giusto.
        risposta = self.client.post(self.azione, {"azione": "pesa_tutti", f"peso_{trenta.pk}": "20", f"peso_{rossa.pk}": "70"}, follow=True)
        self.assertContains(risposta, "Rossa: Pesa più di un fusto pieno")
        self.assertFalse(Pesata.objects.exists())
        risposta = self.client.post(self.azione, {"azione": "pesa_tutti", f"peso_{trenta.pk}": "20,3", f"peso_{rossa.pk}": "45"}, follow=True)
        self.assertContains(risposta, "Pesata di fine serata salvata: 2 fusti.")
        self.assertEqual(Pesata.objects.count(), 2)
        self.assertEqual(services.da_pesare(self.serata), [])

    def test_rifare_da_capo_la_serata(self):
        trenta = self.attacca(self.f30)
        self.pesa(trenta, "30")
        quindici = self.cambia(self.f15, attuale=trenta, servizio=self.serata2)
        self.pesa(quindici, "15", servizio=self.serata2)
        risposta = self.client.post(reverse("bar:fusti_azione", args=[self.serata2.pk]), {"azione": "azzera"}, follow=True)
        self.assertContains(risposta, "i fusti sono come a inizio serata")
        # Sabato non c'è più niente: il 30 litri di venerdì è di nuovo attaccato, col suo peso di venerdì.
        self.assertFalse(ContaFusti.objects.filter(servizio=self.serata2).exists())
        self.assertEqual([(s.fusto, s.ultimo_peso) for s in services.fusti_attaccati()], [(trenta, D("30"))])
        self.assertEqual(services.fusti_pieni()[self.f30.pk], D("5"))
        self.assertFalse(Fusto.objects.filter(pk=quindici.pk).exists())


class RevisioneTest(BaseBar):
    """Casi trovati rileggendo il codice."""

    def setUp(self):
        super().setUp()
        self.inventario((self.f30, "6"))

    def test_serata_con_i_fusti_non_si_elimina(self):
        self.attacca(self.f30)
        self.client.force_login(self.utente)
        risposta = self.client.post(reverse("servizio_elimina", args=[self.serata.pk]), follow=True)
        self.assertContains(risposta, "ci sono i fusti del bar registrati")
        self.assertTrue(Servizio.objects.filter(pk=self.serata.pk).exists())

    def test_cambiare_solo_il_colore_lascia_la_tara_provvisoria(self):
        alto = TipoFusto.objects.get(nome="Alto nero")
        self.assertTrue(alto.tara_provvisoria)
        self.client.force_login(self.staff)
        dati = {"bevanda": alto.bevanda_id, "nome": "Alto nero", "litri": "25", "tara": "5", "colore": "blu", "forma": "alto",
                "ordine": "1", "attivo": "on"}
        self.assertRedirects(self.client.post(reverse("bar:fusto_modifica", args=[alto.pk]), dati), reverse("bar:bevande"))
        alto.refresh_from_db()
        self.assertEqual((alto.colore, alto.tara_provvisoria), ("blu", True))
        # Confermandoli esplicitamente diventano definitivi.
        self.client.post(reverse("bar:fusto_modifica", args=[alto.pk]), {**dati, "confermati": "on"})
        alto.refresh_from_db()
        self.assertFalse(alto.tara_provvisoria)

    def test_la_bevanda_di_un_tipo_usato_non_cambia(self):
        self.attacca(self.f30)
        self.client.force_login(self.staff)
        self.client.post(reverse("bar:fusto_modifica", args=[self.f30.pk]), {
            "bevanda": self.rossa.pk, "nome": "Fusto da 30 litri", "litri": "30", "tara": "10", "colore": "acciaio",
            "forma": "alto", "ordine": "1", "attivo": "on",
        })
        self.f30.refresh_from_db()
        self.assertEqual(self.f30.bevanda, self.birra)

    def test_fusto_di_ieri_riattaccato_solo_alla_sua_spina(self):
        terza = Spina.objects.create(spinatrice=self.spina.spinatrice, nome="Terza", ordine=3)
        trenta = self.attacca(self.f30)
        self.pesa(trenta, "30")
        self.finisci(trenta, servizio=self.serata2)
        with self.assertRaisesMessage(ErroreBar, "Era attaccato a «Bionda» da una serata prima"):
            services.riattacca(servizio=self.serata2, fusto=trenta, spina=terza)
        services.riattacca(servizio=self.serata2, fusto=trenta)
        self.assertEqual([s.fusto for s in services.fusti_attaccati()], [trenta])
