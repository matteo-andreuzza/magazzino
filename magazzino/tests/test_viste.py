import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal as D
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from magazzino import services
from magazzino.models import (
    Movimento,
    Operazione,
    Piatto,
    Prodotto,
    Servizio,
    TipoOperazione,
    Ubicazione,
)
from magazzino.views.comuni import giorno_servizio


class BaseViste(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.utente = get_user_model().objects.create_user("cucina", password="segreta-123")
        cls.frigo1 = Ubicazione.objects.get(nome="Frigo 1")
        cls.frigo2 = Ubicazione.objects.get(nome="Frigo 2")
        cls.cong = Ubicazione.objects.get(nome="Congelatore esterno 1")
        cls.pollo = Prodotto.objects.get(nome="Pollo")
        cls.salamelle = Prodotto.objects.get(nome="Salamelle")
        cls.seppie = Prodotto.objects.get(nome="Seppie")
        cls.serata = Servizio.objects.create(data=date(2026, 9, 18))

    def setUp(self):
        self.client.force_login(self.utente)

    def carica(self, prodotto, ubicazione, q):
        services.registra_carico(
            tipo=TipoOperazione.ACQUISTO, ubicazione=ubicazione, quantita=[(prodotto, D(q))], utente=self.utente
        )

    def giacenza(self, prodotto, ubicazione):
        return services.giacenze().get((prodotto.pk, ubicazione.pk), D("0"))


class AccessoTest(BaseViste):
    def test_serve_il_login(self):
        self.client.logout()
        for nome, args in [("home", []), ("giacenze", []), ("carico", [self.frigo1.pk]), ("giacenze_csv", [])]:
            with self.subTest(nome=nome):
                risposta = self.client.get(reverse(nome, args=args))
                self.assertEqual(risposta.status_code, 302)
                self.assertTrue(risposta["Location"].startswith(reverse("login")))

    def test_pagina_login(self):
        self.client.logout()
        self.assertContains(self.client.get(reverse("login")), "Password")
        risposta = self.client.post(reverse("login"), {"username": "cucina", "password": "segreta-123"})
        self.assertRedirects(risposta, reverse("home"))

    def test_logout(self):
        risposta = self.client.post(reverse("logout"))
        self.assertRedirects(risposta, reverse("login"))


class PagineTest(BaseViste):
    def test_tutte_le_pagine_si_aprono(self):
        self.carica(self.pollo, self.cong, "20")
        self.carica(self.salamelle, self.frigo1, "50")
        op_cucina, _ = services.registra_cucina(
            servizio=self.serata, prelievi=[(self.salamelle, self.frigo1, D("10"))], utente=self.utente
        )
        services.registra_reso(servizio=self.serata, rientri=[(self.salamelle, self.frigo1, D("2"))], utente=None)
        services.registra_scarto(ubicazione=self.cong, quantita=[(self.pollo, D("1"))], utente=None)
        movimento = op_cucina.movimenti.first()
        pagine = [
            ("home", []), ("giacenze", []), ("giacenze_csv", []), ("ubicazioni", []),
            ("ubicazione_dettaglio", [self.frigo1.pk]), ("carico_scelta", []), ("carico", [self.frigo1.pk]),
            ("spostamento_scelta", []), ("spostamento_destinazione", [self.cong.pk]),
            ("spostamento", [self.cong.pk, self.frigo1.pk]), ("scarto_scelta", []), ("scarto", [self.cong.pk]),
            ("rettifica_scelta", []), ("rettifica", [self.cong.pk]), ("servizi", []), ("servizio_nuovo", []),
            ("servizio_dettaglio", [self.serata.pk]), ("servizio_modifica", [self.serata.pk]),
            ("servizio_elimina", [self.serata.pk]), ("servizio_cucina", [self.serata.pk]),
            ("servizio_reso", [self.serata.pk]), ("operazioni", []), ("movimenti_csv", []),
            ("operazione_dettaglio", [op_cucina.pk]), ("operazione_annulla", [op_cucina.pk]),
            ("movimento_annulla", [movimento.pk]), ("prodotti", []), ("prodotto_nuovo", []),
            ("prodotto_dettaglio", [self.pollo.pk]), ("prodotto_modifica", [self.pollo.pk]), ("menu", []),
            ("riepilogo", []), ("riepilogo_csv", []),
        ]
        for nome, args in pagine:
            with self.subTest(pagina=nome):
                self.assertEqual(self.client.get(reverse(nome, args=args)).status_code, 200)
        self.assertEqual(self.client.get(reverse("operazioni") + "?tipo=cucina&prodotto=abc&giorno=xx").status_code, 200)

    def test_ordine_operazioni_e_servizi(self):
        adesso = timezone.now()
        for i, prodotto in enumerate([self.pollo, self.salamelle, self.seppie]):
            op, _ = services.registra_carico(
                tipo=TipoOperazione.ACQUISTO, ubicazione=self.frigo1, quantita=[(prodotto, D("1"))], utente=None
            )
            # Le più vecchie hanno pk più alto: l'ordine deve seguire la data.
            Operazione.objects.filter(pk=op.pk).update(creata_il=adesso - timedelta(hours=i))
        for pagina in ("operazioni", "home"):
            with self.subTest(pagina=pagina):
                ops = list(self.client.get(reverse(pagina)).context["pagina" if pagina == "operazioni" else "ultime"])
                self.assertEqual([o.creata_il for o in ops], sorted([o.creata_il for o in ops], reverse=True))
                self.assertEqual(len(ops), 3)
        Servizio.objects.create(data=date(2026, 9, 25))
        Servizio.objects.create(data=date(2026, 9, 10))
        Servizio.objects.create(data=date(2026, 9, 18), tipo="pranzo")
        elenco = list(self.client.get(reverse("servizi")).context["elenco"])
        self.assertEqual(
            [(s.data, s.tipo) for s in elenco],
            [(date(2026, 9, 25), "sera"), (date(2026, 9, 18), "pranzo"), (date(2026, 9, 18), "sera"),
             (date(2026, 9, 10), "sera")],
        )

    def test_sempre_in_italiano_anche_con_telefono_in_inglese(self):
        risposta = self.client.get(
            reverse("servizio_dettaglio", args=[self.serata.pk]), HTTP_ACCEPT_LANGUAGE="en-US,en;q=0.9"
        )
        self.assertContains(risposta, "Serata di venerdì 18 settembre 2026")
        self.assertNotContains(risposta, "Friday")

    def test_home_senza_servizio_propone_di_crearlo(self):
        self.assertContains(self.client.get(reverse("home")), "Crea la serata di oggi")

    def test_home_con_servizio_di_oggi(self):
        oggi = Servizio.objects.create(data=giorno_servizio())
        self.assertContains(self.client.get(reverse("home")), reverse("servizio_cucina", args=[oggi.pk]))

    def test_dopo_mezzanotte_e_ancora_la_sera_prima(self):
        roma = ZoneInfo("Europe/Rome")
        self.assertEqual(giorno_servizio(datetime(2026, 9, 19, 1, 30, tzinfo=roma)), date(2026, 9, 18))
        self.assertEqual(giorno_servizio(datetime(2026, 9, 19, 5, 0, tzinfo=roma)), date(2026, 9, 19))
        self.assertEqual(giorno_servizio(datetime(2026, 9, 19, 18, 0, tzinfo=roma)), date(2026, 9, 19))

    def test_giacenze_mostra_ubicazioni(self):
        self.carica(self.pollo, self.cong, "12.5")
        self.carica(self.pollo, self.frigo1, "3")
        risposta = self.client.get(reverse("giacenze"))
        self.assertContains(risposta, "15,5 kg")
        self.assertContains(risposta, "Congelatore esterno 1: <strong>12,5 kg</strong>", html=False)

    def test_csv_giacenze(self):
        self.carica(self.pollo, self.cong, "12.5")
        testo = self.client.get(reverse("giacenze_csv")).content.decode("utf-8-sig")
        righe = testo.splitlines()
        self.assertTrue(righe[0].startswith("Prodotto;Categoria;Unità;Frigo 1;"))
        self.assertIn("Pollo;Carne;kg;0;0;0;0;12,5;0;0;0;12,5", righe)

    def test_csv_movimenti_filtrato(self):
        self.carica(self.pollo, self.cong, "2")
        self.carica(self.seppie, self.frigo1, "3")
        testo = self.client.get(reverse("movimenti_csv") + f"?prodotto={self.seppie.pk}").content.decode("utf-8-sig")
        self.assertIn("Seppie", testo)
        self.assertNotIn("Pollo", testo)

    def test_riepilogo_consumi(self):
        self.carica(self.pollo, self.frigo1, "20")
        serata2 = Servizio.objects.create(data=date(2026, 9, 19))
        services.registra_cucina(servizio=self.serata, prelievi=[(self.pollo, self.frigo1, D("8"))], utente=None)
        services.registra_reso(servizio=self.serata, rientri=[(self.pollo, self.frigo1, D("1.5"))], utente=None)
        services.registra_cucina(servizio=serata2, prelievi=[(self.pollo, self.frigo1, D("4"))], utente=None)
        risposta = self.client.get(reverse("riepilogo"))
        self.assertContains(risposta, "6,5 kg")
        self.assertContains(risposta, "10,5 kg")
        testo = self.client.get(reverse("riepilogo_csv")).content.decode("utf-8-sig")
        self.assertIn("Pollo;Carne;kg;6,5;4;10,5;;9,5", testo)


class CaricoVistaTest(BaseViste):
    def url(self):
        return reverse("carico", args=[self.frigo1.pk])

    def test_carico_riuscito(self):
        risposta = self.client.post(
            self.url(),
            {"token": str(uuid.uuid4()), "tipo": "inventario", f"q-{self.pollo.pk}": "12,5",
             f"q-{self.salamelle.pk}": "40", f"q-{self.seppie.pk}": "", "note": "Macelleria"},
        )
        op = Operazione.objects.get()
        self.assertRedirects(risposta, reverse("operazione_dettaglio", args=[op.pk]))
        self.assertEqual(op.tipo, TipoOperazione.INVENTARIO)
        self.assertEqual(op.utente, self.utente)
        self.assertEqual(op.note, "Macelleria")
        self.assertEqual(op.movimenti.count(), 2)
        self.assertEqual(self.giacenza(self.pollo, self.frigo1), D("12.5"))

    def test_doppio_invio_registra_una_volta(self):
        dati = {"token": str(uuid.uuid4()), "tipo": "acquisto", f"q-{self.pollo.pk}": "5"}
        self.client.post(self.url(), dati)
        risposta = self.client.post(self.url(), dati, follow=True)
        self.assertContains(risposta, "il secondo invio è stato ignorato")
        self.assertEqual(Operazione.objects.count(), 1)
        self.assertEqual(self.giacenza(self.pollo, self.frigo1), D("5"))

    def test_tipo_obbligatorio_e_valori_conservati(self):
        risposta = self.client.post(self.url(), {"token": str(uuid.uuid4()), f"q-{self.pollo.pk}": "5"})
        self.assertEqual(risposta.status_code, 200)
        self.assertContains(risposta, "Non è stato registrato niente")
        self.assertContains(risposta, "inventario iniziale o un acquisto")
        self.assertContains(risposta, 'value="5"')
        self.assertFalse(Operazione.objects.exists())

    def test_numero_non_valido(self):
        risposta = self.client.post(
            self.url(),
            {"token": str(uuid.uuid4()), "tipo": "acquisto", f"q-{self.pollo.pk}": "5",
             f"q-{self.salamelle.pk}": "due"},
        )
        self.assertContains(risposta, "Scrivi solo un numero")
        self.assertContains(risposta, 'value="due"')
        self.assertFalse(Operazione.objects.exists())

    def test_modulo_vuoto(self):
        risposta = self.client.post(self.url(), {"token": str(uuid.uuid4()), "tipo": "acquisto"})
        self.assertContains(risposta, "Non hai inserito nessuna quantità")

    def test_token_mancante(self):
        risposta = self.client.post(self.url(), {"tipo": "acquisto", f"q-{self.pollo.pk}": "5"})
        self.assertContains(risposta, "ricarica la pagina")
        self.assertFalse(Operazione.objects.exists())

    def test_riga_sconosciuta(self):
        risposta = self.client.post(
            self.url(), {"token": str(uuid.uuid4()), "tipo": "acquisto", "q-999999": "5"}
        )
        self.assertContains(risposta, "riga non riconosciuta")
        self.assertFalse(Operazione.objects.exists())

    def test_prodotto_non_attivo_non_in_elenco(self):
        self.seppie.attivo = False
        self.seppie.save()
        risposta = self.client.get(self.url())
        self.assertNotContains(risposta, f'name="q-{self.seppie.pk}"')
        self.assertContains(risposta, f'name="q-{self.pollo.pk}"')


class CucinaVistaTest(BaseViste):
    def url(self):
        return reverse("servizio_cucina", args=[self.serata.pk])

    def test_elenco_solo_prodotti_presenti(self):
        self.carica(self.pollo, self.cong, "20")
        risposta = self.client.get(self.url())
        self.assertContains(risposta, f'name="q-{self.pollo.pk}-{self.cong.pk}"')
        self.assertNotContains(risposta, f'name="q-{self.salamelle.pk}-')
        self.assertNotContains(risposta, "data-solo-menu")

    def test_filtro_menu_se_ci_sono_piatti(self):
        self.carica(self.pollo, self.cong, "20")
        self.carica(self.seppie, self.cong, "5")
        self.serata.piatti.add(Piatto.objects.get(nome="Solo pollo"))
        risposta = self.client.get(self.url())
        self.assertContains(risposta, "data-solo-menu")
        contenuto = risposta.content.decode()
        self.assertIn('data-nome="Pollo" data-gruppo="Congelatore esterno 1" data-menu="1"', contenuto)
        self.assertIn('data-nome="Seppie" data-gruppo="Congelatore esterno 1" data-menu="0"', contenuto)

    def test_prelievo_riuscito(self):
        self.carica(self.pollo, self.cong, "20")
        self.carica(self.pollo, self.frigo1, "5")
        risposta = self.client.post(
            self.url(),
            {"token": str(uuid.uuid4()), f"q-{self.pollo.pk}-{self.cong.pk}": "7,5",
             f"q-{self.pollo.pk}-{self.frigo1.pk}": "5"},
        )
        self.assertRedirects(risposta, reverse("servizio_dettaglio", args=[self.serata.pk]))
        self.assertEqual(self.giacenza(self.pollo, self.cong), D("12.5"))
        self.assertEqual(self.giacenza(self.pollo, self.frigo1), D("0"))
        dettaglio = self.client.get(reverse("servizio_dettaglio", args=[self.serata.pk]))
        self.assertContains(dettaglio, "12,5 kg")

    def test_oltre_il_disponibile(self):
        self.carica(self.pollo, self.cong, "2")
        risposta = self.client.post(
            self.url(), {"token": str(uuid.uuid4()), f"q-{self.pollo.pk}-{self.cong.pk}": "3"}
        )
        self.assertContains(risposta, "Ce ne sono solo 2 kg")
        self.assertContains(risposta, "con-errore")
        self.assertEqual(self.giacenza(self.pollo, self.cong), D("2"))

    def test_riga_sparita_nel_frattempo_non_viene_ignorata(self):
        """Qualcuno prende tutto mentre un altro sta compilando il modulo."""
        self.carica(self.pollo, self.cong, "2")
        token = str(uuid.uuid4())
        self.client.get(self.url())
        services.registra_cucina(servizio=self.serata, prelievi=[(self.pollo, self.cong, D("2"))], utente=None)
        risposta = self.client.post(self.url(), {"token": token, f"q-{self.pollo.pk}-{self.cong.pk}": "1"})
        self.assertEqual(risposta.status_code, 200)
        self.assertContains(risposta, "Non ce n&#x27;è più")
        self.assertContains(risposta, 'value="1"')
        self.assertEqual(Operazione.objects.filter(tipo=TipoOperazione.CUCINA).count(), 1)


class ResoVistaTest(BaseViste):
    def setUp(self):
        super().setUp()
        self.carica(self.pollo, self.frigo1, "10")
        services.registra_cucina(servizio=self.serata, prelievi=[(self.pollo, self.frigo1, D("6"))], utente=None)

    def url(self):
        return reverse("servizio_reso", args=[self.serata.pk])

    def test_ubicazione_preselezionata(self):
        risposta = self.client.get(self.url())
        self.assertContains(risposta, f'<option value="{self.frigo1.pk}" selected>', html=False)

    def test_congelatore_mai_preselezionato(self):
        self.carica(self.seppie, self.cong, "5")
        services.registra_cucina(servizio=self.serata, prelievi=[(self.seppie, self.cong, D("5"))], utente=None)
        riga = next(r for r in self.client.get(self.url()).context["righe"] if r.prodotto == self.seppie)
        self.assertEqual(riga.ubicazione_scelta, "")
        self.assertNotContains(self.client.get(self.url()), f'<option value="{self.cong.pk}" selected>', html=False)

    def test_rientro_riuscito(self):
        risposta = self.client.post(
            self.url(),
            {"token": str(uuid.uuid4()), f"q-{self.pollo.pk}": "2", f"u-{self.pollo.pk}": str(self.frigo2.pk)},
        )
        self.assertRedirects(risposta, reverse("servizio_dettaglio", args=[self.serata.pk]))
        self.assertEqual(self.giacenza(self.pollo, self.frigo2), D("2"))

    def test_ubicazione_obbligatoria(self):
        risposta = self.client.post(
            self.url(), {"token": str(uuid.uuid4()), f"q-{self.pollo.pk}": "2", f"u-{self.pollo.pk}": ""}
        )
        self.assertContains(risposta, "Scegli dove lo rimetti")
        self.assertFalse(Operazione.objects.filter(tipo=TipoOperazione.RESO).exists())

    def test_oltre_il_prelevato(self):
        risposta = self.client.post(
            self.url(),
            {"token": str(uuid.uuid4()), f"q-{self.pollo.pk}": "6,5", f"u-{self.pollo.pk}": str(self.frigo1.pk)},
        )
        self.assertContains(risposta, "al massimo 6 kg")
        self.assertFalse(Operazione.objects.filter(tipo=TipoOperazione.RESO).exists())


class AltriModuliVistaTest(BaseViste):
    def test_spostamento(self):
        self.carica(self.pollo, self.cong, "10")
        url = reverse("spostamento", args=[self.cong.pk, self.frigo1.pk])
        risposta = self.client.post(url, {"token": str(uuid.uuid4()), f"q-{self.pollo.pk}": "4"})
        op = Operazione.objects.get(tipo=TipoOperazione.SPOSTAMENTO)
        self.assertRedirects(risposta, reverse("operazione_dettaglio", args=[op.pk]))
        self.assertEqual(self.giacenza(self.pollo, self.frigo1), D("4"))
        self.assertEqual(self.giacenza(self.pollo, self.cong), D("6"))

    def test_spostamento_stessa_ubicazione(self):
        risposta = self.client.get(reverse("spostamento", args=[self.cong.pk, self.cong.pk]))
        self.assertRedirects(risposta, reverse("spostamento_destinazione", args=[self.cong.pk]))

    def test_scarto(self):
        self.carica(self.pollo, self.cong, "10")
        self.client.post(
            reverse("scarto", args=[self.cong.pk]),
            {"token": str(uuid.uuid4()), f"q-{self.pollo.pk}": "1", "note": "scaduto"},
        )
        self.assertEqual(self.giacenza(self.pollo, self.cong), D("9"))
        self.assertEqual(Operazione.objects.get(tipo=TipoOperazione.SCARTO).note, "scaduto")

    def test_rettifica_con_zero(self):
        self.carica(self.pollo, self.cong, "10")
        self.carica(self.salamelle, self.cong, "10")
        risposta = self.client.post(
            reverse("rettifica", args=[self.cong.pk]),
            {"token": str(uuid.uuid4()), f"q-{self.pollo.pk}": "0", f"q-{self.salamelle.pk}": "",
             f"q-{self.seppie.pk}": "3"},
            follow=True,
        )
        self.assertContains(risposta, "2 prodotti corretti")
        self.assertEqual(self.giacenza(self.pollo, self.cong), D("0"))
        self.assertEqual(self.giacenza(self.salamelle, self.cong), D("10"))
        self.assertEqual(self.giacenza(self.seppie, self.cong), D("3"))

    def test_rettifica_senza_differenze(self):
        self.carica(self.pollo, self.cong, "10")
        risposta = self.client.post(
            reverse("rettifica", args=[self.cong.pk]), {"token": str(uuid.uuid4()), f"q-{self.pollo.pk}": "10"}
        )
        self.assertContains(risposta, "niente da correggere")


class AnnullaVistaTest(BaseViste):
    def test_annulla_operazione(self):
        self.carica(self.pollo, self.cong, "10")
        op = Operazione.objects.get()
        self.assertContains(self.client.get(reverse("operazione_annulla", args=[op.pk])), "Sì, annulla")
        risposta = self.client.post(reverse("operazione_annulla", args=[op.pk]))
        self.assertRedirects(risposta, reverse("operazioni"))
        self.assertFalse(Operazione.objects.exists())
        # Secondo invio: nessun errore, solo un avviso.
        risposta = self.client.post(reverse("operazione_annulla", args=[op.pk]), follow=True)
        self.assertContains(risposta, "già stata annullata")

    def test_annulla_rifiutato_mostra_errore(self):
        self.carica(self.pollo, self.cong, "10")
        op = Operazione.objects.get()
        services.registra_cucina(servizio=self.serata, prelievi=[(self.pollo, self.cong, D("5"))], utente=None)
        risposta = self.client.post(reverse("operazione_annulla", args=[op.pk]), follow=True)
        self.assertContains(risposta, "già stato usato")
        self.assertTrue(Operazione.objects.filter(pk=op.pk).exists())

    def test_annulla_riga_torna_al_servizio(self):
        self.carica(self.pollo, self.cong, "10")
        op, _ = services.registra_cucina(servizio=self.serata, prelievi=[(self.pollo, self.cong, D("5"))], utente=None)
        risposta = self.client.post(reverse("movimento_annulla", args=[op.movimenti.get().pk]))
        self.assertRedirects(risposta, reverse("servizio_dettaglio", args=[self.serata.pk]))
        self.assertEqual(self.giacenza(self.pollo, self.cong), D("10"))

    def test_riga_di_spostamento_non_annullabile(self):
        self.carica(self.pollo, self.cong, "10")
        op, _ = services.registra_spostamento(da=self.cong, a=self.frigo1, quantita=[(self.pollo, D("1"))], utente=None)
        risposta = self.client.post(reverse("movimento_annulla", args=[op.movimenti.first().pk]))
        self.assertRedirects(risposta, reverse("operazione_dettaglio", args=[op.pk]), fetch_redirect_response=False)
        self.assertEqual(Movimento.objects.filter(operazione=op).count(), 2)


class ProdottoVistaTest(BaseViste):
    def test_crea_prodotto_e_torna_al_carico(self):
        prossimo = reverse("carico", args=[self.frigo1.pk])
        risposta = self.client.post(
            reverse("prodotto_nuovo"),
            {"nome": "Olio per friggere", "categoria": "dispensa", "unita": "l", "scorta_minima": "10,5",
             "note": "", "attivo": "on", "next": prossimo},
        )
        self.assertRedirects(risposta, prossimo)
        prodotto = Prodotto.objects.get(nome="Olio per friggere")
        self.assertEqual(prodotto.scorta_minima, D("10.5"))

    def test_next_esterno_ignorato(self):
        risposta = self.client.post(
            reverse("prodotto_nuovo"),
            {"nome": "Ghiaccio", "categoria": "altro", "unita": "kg", "attivo": "on", "next": "https://esempio.com/"},
        )
        self.assertRedirects(risposta, reverse("prodotti"))

    def test_nome_duplicato_senza_distinzione_maiuscole(self):
        risposta = self.client.post(
            reverse("prodotto_nuovo"), {"nome": "POLLO", "categoria": "carne", "unita": "kg", "attivo": "on"}
        )
        self.assertContains(risposta, "Esiste già un prodotto con questo nome")
        self.assertEqual(Prodotto.objects.filter(nome__iexact="pollo").count(), 1)

    def test_unita_bloccata_dopo_i_movimenti(self):
        self.carica(self.pollo, self.frigo1, "3")
        risposta = self.client.post(
            reverse("prodotto_modifica", args=[self.pollo.pk]),
            {"nome": "Pollo", "categoria": "carne", "unita": "pz", "scorta_minima": "", "attivo": "on"},
        )
        self.assertRedirects(risposta, reverse("prodotto_dettaglio", args=[self.pollo.pk]))
        self.pollo.refresh_from_db()
        self.assertEqual(self.pollo.unita, "kg")

    def test_unita_modificabile_senza_movimenti(self):
        self.client.post(
            reverse("prodotto_modifica", args=[self.salamelle.pk]),
            {"nome": "Salamelle", "categoria": "carne", "unita": "kg", "attivo": "on"},
        )
        self.salamelle.refresh_from_db()
        self.assertEqual(self.salamelle.unita, "kg")


class ServizioVistaTest(BaseViste):
    def test_crea_servizio_con_piatti(self):
        paella = Piatto.objects.get(nome="Paella")
        risposta = self.client.post(
            reverse("servizio_nuovo"),
            {"data": "2026-09-20", "tipo": "sera", "nome": "", "piatti": [str(paella.pk)], "note": ""},
        )
        servizio = Servizio.objects.get(data=date(2026, 9, 20))
        self.assertRedirects(risposta, reverse("servizio_dettaglio", args=[servizio.pk]))
        self.assertEqual(list(servizio.piatti.all()), [paella])

    def test_servizio_duplicato(self):
        risposta = self.client.post(reverse("servizio_nuovo"), {"data": "2026-09-18", "tipo": "sera", "nome": ""})
        self.assertContains(risposta, "Esiste già un servizio")
        self.assertEqual(Servizio.objects.filter(data=date(2026, 9, 18)).count(), 1)

    def test_due_pranzi_con_nomi_diversi(self):
        for nome in ("Pranzo anniversari", "Pranzo paesano"):
            self.client.post(reverse("servizio_nuovo"), {"data": "2026-09-21", "tipo": "pranzo", "nome": nome})
        self.assertEqual(Servizio.objects.filter(data=date(2026, 9, 21)).count(), 2)

    def test_form_mostra_piatti_raggruppati(self):
        risposta = self.client.get(reverse("servizio_nuovo"))
        self.assertContains(risposta, "Pranzo anniversari")
        self.assertContains(risposta, "Grigliata mista")

    def test_elimina_solo_senza_operazioni(self):
        self.carica(self.pollo, self.cong, "10")
        services.registra_cucina(servizio=self.serata, prelievi=[(self.pollo, self.cong, D("1"))], utente=None)
        self.client.post(reverse("servizio_elimina", args=[self.serata.pk]))
        self.assertTrue(Servizio.objects.filter(pk=self.serata.pk).exists())
        vuoto = Servizio.objects.create(data=date(2026, 9, 25))
        self.assertRedirects(self.client.post(reverse("servizio_elimina", args=[vuoto.pk])), reverse("servizi"))
        self.assertFalse(Servizio.objects.filter(pk=vuoto.pk).exists())


class AdminTest(BaseViste):
    def test_admin_operazioni_in_sola_lettura(self):
        admin = get_user_model().objects.create_superuser("admin", password="x")
        self.client.force_login(admin)
        self.carica(self.pollo, self.cong, "10")
        op = Operazione.objects.get()
        self.assertEqual(self.client.get(reverse("admin:magazzino_operazione_changelist")).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:magazzino_operazione_change", args=[op.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:magazzino_operazione_add")).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:magazzino_operazione_delete", args=[op.pk])).status_code, 403)
        for nome in ("prodotto", "menu", "piatto", "servizio", "ubicazione"):
            with self.subTest(nome=nome):
                self.assertEqual(self.client.get(reverse(f"admin:magazzino_{nome}_changelist")).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:magazzino_prodotto_change", args=[self.pollo.pk])).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("admin:magazzino_piatto_change", args=[Piatto.objects.first().pk])).status_code, 200
        )


class GuidaENavigazioneTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.utente = get_user_model().objects.create_user("guida", password="x")

    def setUp(self):
        self.client.force_login(self.utente)

    def test_guida(self):
        risposta = self.client.get(reverse("guida"))
        self.assertContains(risposta, "Prima della sagra")
        self.assertContains(risposta, 'id="tara"')
        self.assertContains(risposta, 'id="coca-cola"')
        self.assertContains(risposta, 'id="asporto"')
        self.assertContains(risposta, 'class="sezione-guida')

    def test_salute_senza_login(self):
        self.client.logout()
        risposta = self.client.get(reverse("salute"))
        self.assertEqual((risposta.status_code, risposta.content), (200, b"ok\n"))

    def test_la_barra_dice_dove_si_e(self):
        risposta = self.client.get(reverse("giacenze"))
        self.assertContains(risposta, 'class="sezione-magazzino')
        self.assertContains(risposta, f'<a href="{reverse("giacenze")}" aria-current="page">Giacenze</a>', html=True)
        self.assertNotContains(risposta, f'<a href="{reverse("prodotti")}" aria-current="page">Prodotti</a>', html=True)
        risposta = self.client.get(reverse("bar:storico"))
        self.assertContains(risposta, 'class="sezione-bar')
        self.assertContains(risposta, f'<a href="{reverse("bar:storico")}" aria-current="page">Storico</a>', html=True)
