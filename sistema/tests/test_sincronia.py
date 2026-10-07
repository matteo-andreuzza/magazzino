"""Sincronia vera tra due database PostgreSQL: «default» fa la sagra, «altro» fa casa.

Solo sul server di prova (PostgreSQL): settings.py aggiunge il database «altro» quando si lanciano i test.
Lo scambio passa dal JSON come su internet, ma senza HTTP (giro con `chiama`).
"""

import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal as D
from unittest import mock, skipUnless

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connections
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from magazzino.models import Movimento, Operazione, Prodotto, TipoOperazione, Ubicazione
from sistema import sincronia
from sistema.models import Battito, Conflitto, Errore, Modifica

SAGRA, CASA = "default", "altro"
CI_SONO_DUE_DATABASE = "altro" in settings.DATABASES and connections["default"].vendor == "postgresql"


def andata_e_ritorno(messaggio):
    """Come su internet: JSON e ritorno (niente oggetti Python che passano per sbaglio)."""
    return sincronia.leggi_messaggio(sincronia.scrivi_messaggio(messaggio))


def giro():
    """La sagra fa un giro con casa."""
    def chiama(richiesta):
        risposta, _ = sincronia.scambio(andata_e_ritorno(richiesta), using=CASA)
        return andata_e_ritorno(risposta)

    return sincronia.giro(using=SAGRA, chiama=chiama, scrivi=lambda *_: None)


def coda(using):
    return set(Modifica.objects.using(using).values_list("tabella", "chiave"))


@skipUnless(CI_SONO_DUE_DATABASE, "serve PostgreSQL con il secondo database dei test (server di prova)")
class SincroniaTest(TestCase):
    databases = {SAGRA, CASA} if CI_SONO_DUE_DATABASE else {SAGRA}

    def prodotto(self, using, nome, **campi):
        return Prodotto.objects.using(using).create(nome=nome, unita="pz", **campi)

    def carico(self, using, prodotto, quanto, ubicazione_nome="Frigo 1"):
        ubicazione = Ubicazione.objects.using(using).get(nome=ubicazione_nome)
        operazione = Operazione.objects.using(using).create(tipo=TipoOperazione.ACQUISTO)
        Movimento.objects.using(using).create(operazione=operazione, prodotto=prodotto, ubicazione=ubicazione, quantita=D(quanto))
        return operazione

    def svuota_code(self):
        giro()
        self.assertEqual(coda(SAGRA), set())
        self.assertEqual(coda(CASA), set())

    # --- la coda ---------------------------------------------------------

    def test_trigger_una_voce_per_riga(self):
        p = self.prodotto(SAGRA, "Prodotto di prova")
        voce = Modifica.objects.get(tabella="magazzino_prodotto", chiave=str(p.pk))
        p.note = "rossi"
        p.save()
        dopo = Modifica.objects.get(tabella="magazzino_prodotto", chiave=str(p.pk))
        self.assertGreater(dopo.numero, voce.numero)
        p.save()  # niente di cambiato: non riparte
        self.assertEqual(Modifica.objects.get(pk=dopo.pk).numero, dopo.numero)
        chiave = str(p.pk)
        p.delete()  # la cancellazione resta in coda (al posto della modifica)
        self.assertGreater(Modifica.objects.get(tabella="magazzino_prodotto", chiave=chiave).numero, dopo.numero)

    def test_tabelle_di_un_solo_server_restano_fuori(self):
        Modifica.objects.all().delete()
        Battito.objects.create(nome="prova", ultimo_il=timezone.now())
        Errore.objects.create(firma="x", origine="web", tipo="ValueError")
        self.assertEqual(coda(SAGRA), set())

    def test_senza_chiave_niente_trigger_ne_coda(self):
        self.prodotto(SAGRA, "Prima")
        with mock.patch.object(sincronia, "configurata", return_value=False):
            sincronia.prepara(SAGRA)
        self.assertEqual(coda(SAGRA), set())  # la coda di prima non serve più
        self.prodotto(SAGRA, "Senza coda")
        self.assertEqual(coda(SAGRA), set())
        self.assertEqual(self.prodotto(SAGRA, "Sempre pari").pk % 2, 0)
        sincronia.prepara(SAGRA)
        p = self.prodotto(SAGRA, "Con coda")
        self.assertEqual(coda(SAGRA), {("magazzino_prodotto", str(p.pk))})

    def test_id_pari_alla_sagra_e_dispari_a_casa(self):
        self.assertEqual(self.prodotto(SAGRA, "Zucchine").pk % 2, 0)
        self.assertEqual(self.prodotto(CASA, "Melanzane").pk % 2, 1)

    # --- lo scambio ------------------------------------------------------

    def test_scambio_nei_due_sensi(self):
        self.prodotto(SAGRA, "Zucchine")
        self.prodotto(CASA, "Melanzane")
        esito = giro()
        for using in (SAGRA, CASA):
            self.assertTrue(Prodotto.objects.using(using).filter(nome="Zucchine").exists())
            self.assertTrue(Prodotto.objects.using(using).filter(nome="Melanzane").exists())
        self.assertEqual((esito["inviate"], esito["ricevute"]), (1, 1))
        self.assertEqual(coda(SAGRA), set())
        self.assertEqual(coda(CASA), set())
        # Le righe arrivate non ripartono indietro.
        self.assertEqual(giro()["inviate"] + giro()["ricevute"], 0)

    def test_modifiche_e_cancellazioni(self):
        p = self.prodotto(SAGRA, "Zucchine")
        giro()
        Prodotto.objects.using(CASA).filter(pk=p.pk).update(note="da finire")
        giro()
        self.assertEqual(Prodotto.objects.get(pk=p.pk).note, "da finire")
        Prodotto.objects.filter(pk=p.pk).delete()
        giro()
        self.assertFalse(Prodotto.objects.using(CASA).filter(pk=p.pk).exists())

    def test_carico_fatto_a_casa_arriva_con_le_righe_collegate(self):
        p = self.prodotto(CASA, "Mozzarelle")
        operazione = self.carico(CASA, p, "12")
        giro()
        movimento = Movimento.objects.get(operazione_id=operazione.pk)
        self.assertEqual((movimento.prodotto.nome, movimento.quantita), ("Mozzarelle", D("12")))

    def test_vince_la_modifica_piu_recente(self):
        p = self.prodotto(SAGRA, "Zucchine")
        giro()
        Prodotto.objects.filter(pk=p.pk).update(note="sagra")
        Prodotto.objects.using(CASA).filter(pk=p.pk).update(note="casa")  # dopo: vince questa
        giro()
        self.assertEqual(Prodotto.objects.get(pk=p.pk).note, "casa")
        self.assertEqual(Prodotto.objects.using(CASA).get(pk=p.pk).note, "casa")
        self.assertEqual(coda(SAGRA) | coda(CASA), set())

    def test_conferme_perse_niente_doppioni(self):
        p = self.prodotto(SAGRA, "Zucchine")
        self.carico(SAGRA, p, "3")
        richiesta = {**sincronia.messaggio_base(SAGRA), "modifiche": sincronia.da_inviare(
            tabelle=sincronia.schema(SAGRA), using=SAGRA).modifiche}
        sincronia.scambio(andata_e_ritorno(richiesta), using=CASA)  # la risposta si perde per strada
        self.svuota_code()
        self.assertEqual(Movimento.objects.using(CASA).filter(prodotto_id=p.pk).count(), 1)

    def test_tante_righe_in_piu_messaggi(self):
        with mock.patch.object(sincronia, "PER_VOLTA", 7):
            for i in range(30):
                self.prodotto(CASA, f"Prodotto {i}")
            giro()
        self.assertEqual(Prodotto.objects.filter(nome__startswith="Prodotto ").count(), 30)
        self.assertEqual(coda(CASA), set())

    # --- conflitti ---------------------------------------------------------

    def test_stesso_nome_dalle_due_parti_poi_unite(self):
        sagra = self.prodotto(SAGRA, "Pasta")
        casa = self.prodotto(CASA, "pasta")  # il nome è unico senza maiuscole
        operazione = self.carico(SAGRA, sagra, "5")
        giro()
        conflitto = Conflitto.objects.get(tabella="magazzino_prodotto")
        self.assertEqual((conflitto.chiave, conflitto.gemella), (str(sagra.pk), str(casa.pk)))
        self.assertIn("stesso valore", conflitto.motivo)
        self.assertTrue(Conflitto.objects.using(CASA).filter(chiave=str(casa.pk)).exists())
        # La riga del carico intanto aspetta: il suo prodotto a casa non c'è.
        self.assertFalse(Movimento.objects.using(CASA).filter(operazione_id=operazione.pk).exists())
        # «Tieni quella di casa»: il carico passa al prodotto di casa.
        sincronia.unisci(conflitto, using=SAGRA)
        giro()
        giro()
        for using in (SAGRA, CASA):
            self.assertEqual(list(Prodotto.objects.using(using).filter(nome__iexact="pasta").values_list("pk", flat=True)), [casa.pk])
            self.assertEqual(Movimento.objects.using(using).get(operazione_id=operazione.pk).prodotto_id, casa.pk)
            self.assertFalse(Conflitto.objects.using(using).exists())
        self.assertEqual(coda(SAGRA) | coda(CASA), set())

    def test_tieni_questa_lo_chiede_all_altro(self):
        sagra = self.prodotto(SAGRA, "Pasta")
        casa = self.prodotto(CASA, "Pasta")
        giro()
        sincronia.chiedi_all_altro(Conflitto.objects.get(), using=SAGRA)
        giro()
        giro()
        for using in (SAGRA, CASA):
            self.assertEqual(list(Prodotto.objects.using(using).filter(nome="Pasta").values_list("pk", flat=True)), [sagra.pk])
        self.assertFalse(Prodotto.objects.using(CASA).filter(pk=casa.pk).exists())

    def test_rinominando_passa_da_solo(self):
        self.prodotto(SAGRA, "Pasta")
        casa = self.prodotto(CASA, "Pasta")
        giro()
        self.assertTrue(Conflitto.objects.exists())
        Prodotto.objects.using(CASA).filter(pk=casa.pk).update(nome="Pasta corta")
        giro()
        giro()
        self.assertEqual(Prodotto.objects.filter(nome__startswith="Pasta").count(), 2)
        self.assertFalse(Conflitto.objects.exists() or Conflitto.objects.using(CASA).exists())

    def test_cancellato_da_una_parte_e_usato_dall_altra_resta(self):
        p = self.prodotto(CASA, "Pomodori")
        giro()
        Prodotto.objects.using(CASA).filter(pk=p.pk).delete()
        operazione = self.carico(SAGRA, Prodotto.objects.get(pk=p.pk), "4")
        giro()
        giro()
        for using in (SAGRA, CASA):
            self.assertTrue(Prodotto.objects.using(using).filter(pk=p.pk).exists())
            self.assertTrue(Movimento.objects.using(using).filter(operazione_id=operazione.pk).exists())
        self.assertEqual(coda(SAGRA) | coda(CASA), set())

    # --- utenti ------------------------------------------------------------

    def test_utenti_e_password_dalle_due_parti(self):
        utenti = get_user_model().objects
        nuovo = utenti.db_manager(CASA).create_user("volontaria", password="prima-123")
        giro()
        self.assertTrue(utenti.get(username="volontaria").check_password("prima-123"))
        # La password cambia a casa; subito dopo lei entra in sagra (con la vecchia): last_login non vince.
        u = utenti.db_manager(CASA).get(pk=nuovo.pk)
        u.set_password("dopo-456")
        u.save(using=CASA)
        utenti.filter(pk=nuovo.pk).update(last_login=timezone.now() + timedelta(seconds=5))
        self.assertNotIn(("auth_user", str(nuovo.pk)), coda(SAGRA))  # un accesso da solo non parte
        giro()
        self.assertTrue(utenti.get(pk=nuovo.pk).check_password("dopo-456"))
        self.assertIsNone(utenti.db_manager(CASA).get(pk=nuovo.pk).last_login)  # ognuno ha il suo

    def test_utente_cancellato_con_le_sue_righe_locali(self):
        from django.contrib.admin.models import ADDITION, LogEntry

        nuovo = get_user_model().objects.create_user("di-passaggio", password="x")
        giro()
        LogEntry.objects.using(CASA).create(user_id=nuovo.pk, action_flag=ADDITION, object_repr="x")
        get_user_model().objects.filter(pk=nuovo.pk).delete()
        giro()
        self.assertFalse(get_user_model().objects.using(CASA).filter(pk=nuovo.pk).exists())

    # --- quando non si può -------------------------------------------------

    def test_versioni_diverse(self):
        vere = sincronia.migrazioni

        def a_casa_una_in_piu(using="default"):
            return vere(using) + ([["bar", "9999_futuro"]] if using == CASA else [])

        with mock.patch.object(sincronia, "migrazioni", a_casa_una_in_piu):
            with self.assertRaisesMessage(sincronia.SincroniaFerma, "aggiorna il server della sagra"):
                giro()

    def test_due_server_con_lo_stesso_nome(self):
        with mock.patch.dict(connections[CASA].settings_dict, {"SERVER_NOME": "sagra"}):
            with self.assertRaisesMessage(sincronia.SincroniaFerma, "SERVER_NOME=casa"):
                giro()


@skipUnless(CI_SONO_DUE_DATABASE and shutil.which("pg_dump"), "serve PostgreSQL (server di prova)")
@override_settings(COPIA_CHIAVE="chiave-di-prova", SERVER_NOME="casa")
class ApiSincroniaTest(TestCase):
    """L'API vera di casa, con una «sagra» finta che le manda un messaggio."""

    def setUp(self):
        cartella = tempfile.TemporaryDirectory()  # la pulizia dei file non deve toccare quelli veri
        self.addCleanup(cartella.cleanup)
        impostazioni = override_settings(MEDIA_ROOT=cartella.name)
        impostazioni.enable()
        self.addCleanup(impostazioni.disable)

    def manda(self, messaggio, chiave="chiave-di-prova"):
        return self.client.post(reverse("sistema:api_sincronia"), sincronia.scrivi_messaggio(messaggio),
                                content_type=sincronia.TIPO_MESSAGGIO, HTTP_AUTHORIZATION=f"Bearer {chiave}")

    def test_scambio_e_battito(self):
        messaggio = {**sincronia.messaggio_base(), "server": "sagra", "modifiche": [], "in_coda": 3}
        self.assertEqual(self.manda(messaggio, chiave="sbagliata").status_code, 401)
        risposta = self.manda(messaggio)
        self.assertEqual(risposta.status_code, 200)
        dati = sincronia.leggi_messaggio(risposta.content)
        self.assertEqual(dati["server"], "casa")
        battito = Battito.objects.get(nome=sincronia.NOME)
        self.assertEqual(battito.dettagli["in_coda_altro"], 3)
        self.assertIn("La sagra si è fatta sentire", battito.messaggio)

    def test_versione_diversa(self):
        messaggio = {**sincronia.messaggio_base(), "server": "sagra", "migrazioni": [["bar", "0001_iniziale"]]}
        risposta = self.manda(messaggio)
        self.assertEqual(risposta.status_code, 409)
        self.assertIn("aggiorna", risposta.json()["errori"][0])
