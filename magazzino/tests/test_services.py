import uuid
from datetime import date
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from magazzino import services
from magazzino.models import Movimento, Operazione, Prodotto, Servizio, TipoOperazione, Ubicazione
from magazzino.services import ErroreMagazzino


class BaseMagazzino(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.utente = get_user_model().objects.create_user("volontario", password="x")
        cls.frigo1 = Ubicazione.objects.get(nome="Frigo 1")
        cls.frigo2 = Ubicazione.objects.get(nome="Frigo 2")
        cls.cong = Ubicazione.objects.get(nome="Congelatore esterno 1")
        cls.pollo = Prodotto.objects.get(nome="Pollo")  # kg
        cls.salamelle = Prodotto.objects.get(nome="Salamelle")  # pz
        cls.serata = Servizio.objects.create(data=date(2026, 9, 18))
        cls.serata2 = Servizio.objects.create(data=date(2026, 9, 19))

    def carica(self, prodotto, ubicazione, q, tipo=TipoOperazione.ACQUISTO):
        op, creata = services.registra_carico(
            tipo=tipo, ubicazione=ubicazione, quantita=[(prodotto, D(q))], utente=self.utente
        )
        self.assertTrue(creata)
        return op

    def giacenza(self, prodotto, ubicazione):
        return services.giacenze().get((prodotto.pk, ubicazione.pk), D("0"))


class DatiInizialiTest(TestCase):
    def test_frighi_e_congelatori(self):
        nomi = list(Ubicazione.objects.values_list("nome", flat=True))
        self.assertEqual(
            nomi,
            [
                "Frigo 1", "Frigo 2", "Congelatore interno 1", "Congelatore interno 2",
                "Congelatore esterno 1", "Congelatore esterno 2", "Frigo bar", "Dispensa",
            ],
        )

    def test_menu_caricato(self):
        from magazzino.models import Menu, Piatto

        self.assertEqual(
            list(Menu.objects.values_list("nome", flat=True)),
            ["Menu della sagra", "Solo una sera", "Pranzo anniversari", "Pranzo paesano"],
        )
        grigliata = Piatto.objects.get(nome="Grigliata mista")
        self.assertEqual(
            [i.prodotto.nome for i in grigliata.ingredienti.all()],
            ["Pollo", "Salamelle", "Costicine", "Polenta"],
        )
        paella = Piatto.objects.get(nome="Paella")
        self.assertIn("Zafferano", [i.prodotto.nome for i in paella.ingredienti.all()])
        self.assertEqual(Piatto.objects.filter(nome="Antipasto").count(), 2)

    def test_magazzino_vuoto_all_inizio(self):
        self.assertFalse(Movimento.objects.exists())


class CaricoTest(BaseMagazzino):
    def test_carico_e_somma(self):
        self.carica(self.pollo, self.frigo1, "10", TipoOperazione.INVENTARIO)
        self.carica(self.pollo, self.frigo1, "2,5".replace(",", "."))
        self.carica(self.pollo, self.cong, "30")
        self.assertEqual(self.giacenza(self.pollo, self.frigo1), D("12.5"))
        self.assertEqual(services.giacenze_totali()[self.pollo.pk], D("42.5"))

    def test_somma_decimali_esatta(self):
        for _ in range(10):
            self.carica(self.pollo, self.frigo1, "0.1")
        self.assertEqual(self.giacenza(self.pollo, self.frigo1), D("1"))

    def test_tipo_non_valido(self):
        with self.assertRaises(ValueError):
            services.registra_carico(
                tipo=TipoOperazione.CUCINA, ubicazione=self.frigo1, quantita=[(self.pollo, D("1"))], utente=None
            )

    def test_quantita_non_positive_rifiutate(self):
        for q in (D("0"), D("-1"), None):
            with self.subTest(q=q), self.assertRaises(ValueError):
                services.registra_carico(
                    tipo=TipoOperazione.ACQUISTO, ubicazione=self.frigo1, quantita=[(self.pollo, q)], utente=None
                )
        self.assertFalse(Operazione.objects.exists())

    def test_nessuna_riga(self):
        with self.assertRaises(ErroreMagazzino):
            services.registra_carico(tipo=TipoOperazione.ACQUISTO, ubicazione=self.frigo1, quantita=[], utente=None)
        self.assertFalse(Operazione.objects.exists())

    def test_token_doppio_invio(self):
        token = uuid.uuid4()
        op1, creata1 = services.registra_carico(
            tipo=TipoOperazione.ACQUISTO, ubicazione=self.frigo1, quantita=[(self.pollo, D("5"))],
            utente=self.utente, token=token,
        )
        op2, creata2 = services.registra_carico(
            tipo=TipoOperazione.ACQUISTO, ubicazione=self.frigo1, quantita=[(self.pollo, D("5"))],
            utente=self.utente, token=token,
        )
        self.assertTrue(creata1)
        self.assertFalse(creata2)
        self.assertEqual(op1.pk, op2.pk)
        self.assertEqual(self.giacenza(self.pollo, self.frigo1), D("5"))

    def test_token_libero_dopo_errore(self):
        token = uuid.uuid4()
        with self.assertRaises(ErroreMagazzino):
            services.registra_cucina(
                servizio=self.serata, prelievi=[(self.pollo, self.frigo1, D("1"))], utente=None, token=token
            )
        self.carica(self.pollo, self.frigo1, "3")
        _, creata = services.registra_cucina(
            servizio=self.serata, prelievi=[(self.pollo, self.frigo1, D("1"))], utente=None, token=token
        )
        self.assertTrue(creata)


class CucinaTest(BaseMagazzino):
    def test_prelievo(self):
        self.carica(self.pollo, self.frigo1, "10")
        op, _ = services.registra_cucina(
            servizio=self.serata, prelievi=[(self.pollo, self.frigo1, D("4"))], utente=self.utente
        )
        self.assertEqual(op.tipo, TipoOperazione.CUCINA)
        self.assertEqual(op.servizio, self.serata)
        self.assertEqual(self.giacenza(self.pollo, self.frigo1), D("6"))
        self.assertEqual(services.consumi_servizio(self.serata)[self.pollo.pk], {"preso": D("4"), "rientrato": D("0"), "venduto": D("0")})

    def test_aggiunte_durante_la_serata_si_sommano(self):
        self.carica(self.pollo, self.frigo1, "10")
        services.registra_cucina(servizio=self.serata, prelievi=[(self.pollo, self.frigo1, D("4"))], utente=None)
        services.registra_cucina(servizio=self.serata, prelievi=[(self.pollo, self.frigo1, D("3"))], utente=None)
        self.assertEqual(services.consumi_servizio(self.serata)[self.pollo.pk]["preso"], D("7"))
        self.assertEqual(self.giacenza(self.pollo, self.frigo1), D("3"))

    def test_non_si_preleva_piu_del_disponibile(self):
        self.carica(self.pollo, self.frigo1, "5")
        self.carica(self.pollo, self.cong, "20")
        with self.assertRaises(ErroreMagazzino) as ctx:
            services.registra_cucina(
                servizio=self.serata, prelievi=[(self.pollo, self.frigo1, D("6"))], utente=None
            )
        errore = ctx.exception
        self.assertEqual(errore.errori[0][0], (self.pollo.pk, self.frigo1.pk))
        self.assertIn("ce ne sono solo 5 kg", errore.messaggi[0])
        self.assertIn("Congelatore esterno 1 (20 kg)", errore.messaggi[0])
        self.assertEqual(self.giacenza(self.pollo, self.frigo1), D("5"))
        self.assertFalse(Operazione.objects.filter(tipo=TipoOperazione.CUCINA).exists())

    def test_righe_duplicate_vengono_sommate_nel_controllo(self):
        self.carica(self.pollo, self.frigo1, "5")
        with self.assertRaises(ErroreMagazzino):
            services.registra_cucina(
                servizio=self.serata,
                prelievi=[(self.pollo, self.frigo1, D("3")), (self.pollo, self.frigo1, D("3"))],
                utente=None,
            )
        self.assertEqual(self.giacenza(self.pollo, self.frigo1), D("5"))

    def test_tutto_o_niente(self):
        self.carica(self.pollo, self.frigo1, "5")
        self.carica(self.salamelle, self.frigo1, "10")
        with self.assertRaises(ErroreMagazzino):
            services.registra_cucina(
                servizio=self.serata,
                prelievi=[(self.pollo, self.frigo1, D("2")), (self.salamelle, self.frigo1, D("11"))],
                utente=None,
            )
        self.assertEqual(self.giacenza(self.pollo, self.frigo1), D("5"))
        self.assertEqual(self.giacenza(self.salamelle, self.frigo1), D("10"))

    def test_prelievo_esatto_azzera(self):
        self.carica(self.pollo, self.frigo1, "5.25")
        services.registra_cucina(servizio=self.serata, prelievi=[(self.pollo, self.frigo1, D("5.25"))], utente=None)
        self.assertNotIn((self.pollo.pk, self.frigo1.pk), services.giacenze())

    def test_servizio_obbligatorio(self):
        with self.assertRaises(ValueError):
            services._registra(TipoOperazione.CUCINA, [], lambda: [], utente=None)

    def test_vincolo_db_servizio(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Operazione.objects.create(tipo=TipoOperazione.CUCINA)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Operazione.objects.create(tipo=TipoOperazione.ACQUISTO, servizio=self.serata)

    def test_vincolo_db_quantita_zero(self):
        op = self.carica(self.pollo, self.frigo1, "1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Movimento.objects.create(operazione=op, prodotto=self.pollo, ubicazione=self.frigo1, quantita=0)


class ResoTest(BaseMagazzino):
    def setUp(self):
        self.carica(self.pollo, self.frigo1, "10")
        services.registra_cucina(servizio=self.serata, prelievi=[(self.pollo, self.frigo1, D("8"))], utente=None)

    def test_rientro(self):
        services.registra_reso(servizio=self.serata, rientri=[(self.pollo, self.frigo2, D("3"))], utente=None)
        self.assertEqual(self.giacenza(self.pollo, self.frigo1), D("2"))
        self.assertEqual(self.giacenza(self.pollo, self.frigo2), D("3"))
        self.assertEqual(
            services.consumi_servizio(self.serata)[self.pollo.pk], {"preso": D("8"), "rientrato": D("3"), "venduto": D("0")}
        )

    def test_non_rientra_piu_di_quanto_uscito(self):
        services.registra_reso(servizio=self.serata, rientri=[(self.pollo, self.frigo1, D("5"))], utente=None)
        with self.assertRaises(ErroreMagazzino) as ctx:
            services.registra_reso(servizio=self.serata, rientri=[(self.pollo, self.frigo1, D("3.001"))], utente=None)
        self.assertIn("al massimo 3 kg", ctx.exception.messaggi[0])
        services.registra_reso(servizio=self.serata, rientri=[(self.pollo, self.frigo1, D("3"))], utente=None)

    def test_rientro_in_altro_servizio_rifiutato(self):
        with self.assertRaises(ErroreMagazzino) as ctx:
            services.registra_reso(servizio=self.serata2, rientri=[(self.pollo, self.frigo1, D("1"))], utente=None)
        self.assertIn("non è mai stato messo a cucinare", ctx.exception.messaggi[0])

    def test_rientro_su_piu_ubicazioni_somma(self):
        with self.assertRaises(ErroreMagazzino):
            services.registra_reso(
                servizio=self.serata,
                rientri=[(self.pollo, self.frigo1, D("5")), (self.pollo, self.frigo2, D("4"))],
                utente=None,
            )


class SpostamentoScartoRettificaTest(BaseMagazzino):
    def test_spostamento(self):
        self.carica(self.pollo, self.cong, "20")
        op, _ = services.registra_spostamento(
            da=self.cong, a=self.frigo1, quantita=[(self.pollo, D("7.5"))], utente=None
        )
        self.assertEqual(op.movimenti.count(), 2)
        self.assertEqual(self.giacenza(self.pollo, self.cong), D("12.5"))
        self.assertEqual(self.giacenza(self.pollo, self.frigo1), D("7.5"))
        self.assertEqual(services.giacenze_totali()[self.pollo.pk], D("20"))

    def test_spostamento_oltre_disponibile(self):
        self.carica(self.pollo, self.cong, "2")
        with self.assertRaises(ErroreMagazzino):
            services.registra_spostamento(da=self.cong, a=self.frigo1, quantita=[(self.pollo, D("3"))], utente=None)
        self.assertEqual(self.giacenza(self.pollo, self.frigo1), D("0"))

    def test_spostamento_stessa_ubicazione(self):
        with self.assertRaises(ErroreMagazzino):
            services.registra_spostamento(da=self.cong, a=self.cong, quantita=[(self.pollo, D("1"))], utente=None)

    def test_scarto(self):
        self.carica(self.pollo, self.frigo1, "3")
        services.registra_scarto(ubicazione=self.frigo1, quantita=[(self.pollo, D("1"))], utente=None, note="scaduto")
        self.assertEqual(self.giacenza(self.pollo, self.frigo1), D("2"))
        with self.assertRaises(ErroreMagazzino):
            services.registra_scarto(ubicazione=self.frigo1, quantita=[(self.pollo, D("2.5"))], utente=None)

    def test_rettifica(self):
        self.carica(self.pollo, self.frigo1, "10")
        self.carica(self.salamelle, self.frigo1, "40")
        op, _ = services.registra_rettifica(
            ubicazione=self.frigo1,
            conteggi=[(self.pollo, D("8.5")), (self.salamelle, D("40")), (Prodotto.objects.get(nome="Seppie"), D("2"))],
            utente=None,
        )
        # Solo i prodotti con differenza generano movimenti.
        self.assertEqual(op.movimenti.count(), 2)
        self.assertEqual(self.giacenza(self.pollo, self.frigo1), D("8.5"))
        self.assertEqual(self.giacenza(self.salamelle, self.frigo1), D("40"))

    def test_rettifica_a_zero(self):
        self.carica(self.pollo, self.frigo1, "10")
        services.registra_rettifica(ubicazione=self.frigo1, conteggi=[(self.pollo, D("0"))], utente=None)
        self.assertEqual(self.giacenza(self.pollo, self.frigo1), D("0"))

    def test_rettifica_senza_differenze(self):
        self.carica(self.pollo, self.frigo1, "10")
        with self.assertRaisesMessage(ErroreMagazzino, "niente da correggere"):
            services.registra_rettifica(ubicazione=self.frigo1, conteggi=[(self.pollo, D("10"))], utente=None)
        self.assertEqual(Operazione.objects.count(), 1)

    def test_sotto_scorta(self):
        self.pollo.scorta_minima = D("5")
        self.pollo.save()
        self.assertEqual(services.prodotti_sotto_scorta(), [(self.pollo, D("0"))])
        self.carica(self.pollo, self.frigo1, "5")
        self.assertEqual(services.prodotti_sotto_scorta(), [])


class AnnullaTest(BaseMagazzino):
    def test_annulla_carico(self):
        op = self.carica(self.pollo, self.frigo1, "10")
        services.annulla(operazione=op)
        self.assertFalse(Operazione.objects.exists())
        self.assertEqual(self.giacenza(self.pollo, self.frigo1), D("0"))

    def test_non_annulla_carico_gia_usato(self):
        op = self.carica(self.pollo, self.frigo1, "10")
        services.registra_cucina(servizio=self.serata, prelievi=[(self.pollo, self.frigo1, D("4"))], utente=None)
        with self.assertRaises(ErroreMagazzino) as ctx:
            services.annulla(operazione=op)
        self.assertIn("già stato usato", ctx.exception.messaggi[0])
        self.assertTrue(Operazione.objects.filter(pk=op.pk).exists())
        self.assertEqual(self.giacenza(self.pollo, self.frigo1), D("6"))

    def test_annulla_una_riga(self):
        self.carica(self.pollo, self.frigo1, "10")
        self.carica(self.salamelle, self.frigo1, "10")
        op, _ = services.registra_cucina(
            servizio=self.serata,
            prelievi=[(self.pollo, self.frigo1, D("2")), (self.salamelle, self.frigo1, D("4"))],
            utente=None,
        )
        riga = op.movimenti.get(prodotto=self.salamelle)
        op_id, servizio_id, eliminata = services.annulla(movimento=riga)
        self.assertEqual((op_id, servizio_id, eliminata), (op.pk, self.serata.pk, False))
        self.assertEqual(self.giacenza(self.salamelle, self.frigo1), D("10"))
        self.assertEqual(self.giacenza(self.pollo, self.frigo1), D("8"))
        # Annullando l'ultima riga sparisce anche l'operazione.
        _, _, eliminata = services.annulla(movimento=op.movimenti.get())
        self.assertTrue(eliminata)
        self.assertFalse(Operazione.objects.filter(pk=op.pk).exists())

    def test_non_annulla_prelievo_gia_rientrato(self):
        self.carica(self.pollo, self.frigo1, "10")
        op, _ = services.registra_cucina(
            servizio=self.serata, prelievi=[(self.pollo, self.frigo1, D("5"))], utente=None
        )
        reso, _ = services.registra_reso(
            servizio=self.serata, rientri=[(self.pollo, self.frigo1, D("2"))], utente=None
        )
        with self.assertRaisesMessage(ErroreMagazzino, "Annulla prima il rientro"):
            services.annulla(operazione=op)
        services.annulla(operazione=reso)
        services.annulla(operazione=op)
        self.assertEqual(self.giacenza(self.pollo, self.frigo1), D("10"))

    def test_annulla_spostamento(self):
        self.carica(self.pollo, self.cong, "10")
        op, _ = services.registra_spostamento(da=self.cong, a=self.frigo1, quantita=[(self.pollo, D("4"))], utente=None)
        with self.assertRaisesMessage(ErroreMagazzino, "per intero"):
            services.annulla(movimento=op.movimenti.first())
        services.annulla(operazione=op)
        self.assertEqual(self.giacenza(self.pollo, self.cong), D("10"))
        self.assertEqual(self.giacenza(self.pollo, self.frigo1), D("0"))

    def test_non_annulla_spostamento_gia_usato(self):
        self.carica(self.pollo, self.cong, "10")
        op, _ = services.registra_spostamento(da=self.cong, a=self.frigo1, quantita=[(self.pollo, D("4"))], utente=None)
        services.registra_cucina(servizio=self.serata, prelievi=[(self.pollo, self.frigo1, D("1"))], utente=None)
        with self.assertRaises(ErroreMagazzino):
            services.annulla(operazione=op)

    def test_annulla_due_volte(self):
        op = self.carica(self.pollo, self.frigo1, "10")
        copia = Operazione.objects.get(pk=op.pk)
        services.annulla(operazione=op)
        with self.assertRaises(ErroreMagazzino):
            services.annulla(operazione=copia)

    def test_annulla_rettifica(self):
        self.carica(self.pollo, self.frigo1, "10")
        op, _ = services.registra_rettifica(ubicazione=self.frigo1, conteggi=[(self.pollo, D("7"))], utente=None)
        services.annulla(operazione=op)
        self.assertEqual(self.giacenza(self.pollo, self.frigo1), D("10"))
