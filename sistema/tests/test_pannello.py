import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from sistema import errori, monitor
from sistema.middleware import ruolo
from sistema.models import Battito, Dispositivo, Errore
from sistema.services import segnala


class BaseSistema(TestCase):
    @classmethod
    def setUpTestData(cls):
        utenti = get_user_model().objects
        cls.utente = utenti.create_user("volontario", password="segreta-123")
        cls.staff = utenti.create_user("capo", password="segreta-123", is_staff=True)


class PannelloTest(BaseSistema):
    def test_solo_staff(self):
        self.client.force_login(self.utente)
        self.assertEqual(self.client.get(reverse("sistema:pannello")).status_code, 403)
        self.assertEqual(self.client.get(reverse("sistema:stato_json")).status_code, 403)

    def test_pagina_con_schema_e_guida(self):
        self.client.force_login(self.staff)
        risposta = self.client.get(reverse("sistema:pannello"))
        self.assertContains(risposta, "Schema: chi parla con chi")
        self.assertContains(risposta, 'id="stato-iniziale"')
        # La guida del programmatore è docs/MANUTENZIONE.md, con l'indice.
        self.assertContains(risposta, "Guida del programmatore")
        self.assertContains(risposta, 'id="installare-il-programma-alla-sagra"')
        self.assertContains(risposta, 'class="toc"')

    @override_settings(COPIA_CHIAVE="", SINCRONIA_CON="")
    def test_stato_json(self):
        self.client.force_login(self.staff)
        self.client.get(reverse("sistema:pannello"))  # il dispositivo si segna a risposta finita
        dati = self.client.get(reverse("sistema:stato_json")).json()
        self.assertEqual([s["id"] for s in dati["servizi"]], ["web", "db", "stampa", "backup", "drive", "sincronia"])
        servizi = {s["id"]: s for s in dati["servizi"]}
        self.assertEqual(servizi["db"]["stato"], "ok")
        self.assertEqual(servizi["stampa"]["stato"], "sconosciuto")  # il servizio di stampa non è mai partito
        self.assertEqual(servizi["sincronia"]["stato"], "spento")  # sincronia non configurata
        self.assertEqual(dati["modalita"], "sagra")
        self.assertIn("migrazioni", dati["versione"])
        self.assertTrue(any(a["cosa"] == "Ultimo ordine in cassa" for a in dati["attivita"]))
        # Chi guarda il pannello è un dispositivo collegato.
        self.assertEqual(dati["dispositivi"][0]["ruolo"], "Pannello tecnico")

    def test_servizio_di_stampa_fermo(self):
        segnala("stampa", avviato_il=timezone.now() - timedelta(hours=2))
        self.assertEqual(monitor.stampa()["stato"], "ok")
        Battito.objects.filter(nome="stampa").update(ultimo_il=timezone.now() - timedelta(minutes=2))
        self.assertEqual(monitor.stampa()["stato"], "errore")

    def test_backup_e_drive_dai_file_di_stato(self):
        with tempfile.TemporaryDirectory() as cartella_backup, tempfile.TemporaryDirectory() as cartella_stato:
            (Path(cartella_backup) / "magazzino-sagra-20261007-2130.sql.gz").write_bytes(b"x" * 100)
            adesso = timezone.now().isoformat()
            (Path(cartella_stato) / "backup").write_text(f"avviato={adesso}\nultimo_ok={adesso}\n")
            (Path(cartella_stato) / "drive").write_text("stato=spento\n")
            with override_settings(CARTELLA_BACKUP=Path(cartella_backup), CARTELLA_STATO=Path(cartella_stato)):
                backup, drive = monitor.backup(), monitor.drive()
                self.assertEqual(monitor.file_di_backup()[0]["nome"], "magazzino-sagra-20261007-2130.sql.gz")
                # Un backup fallito dopo l'ultimo riuscito è un errore.
                (Path(cartella_stato) / "backup").write_text(
                    f"ultimo_ok={(timezone.now() - timedelta(minutes=40)).isoformat()}\n"
                    f"ultimo_errore=disco pieno\nerrore_il={adesso}\n"
                )
                fallito = monitor.backup()
        self.assertEqual(backup["stato"], "ok")
        self.assertEqual(drive["stato"], "spento")
        self.assertEqual(fallito["stato"], "errore")
        self.assertIn("disco pieno", fallito["testo"])

    def test_azioni(self):
        self.client.force_login(self.staff)
        Errore.objects.create(firma="a", origine="web", tipo="ValueError")
        self.client.post(reverse("sistema:errori_visti"))
        self.assertTrue(Errore.objects.get().visto)
        self.client.post(reverse("sistema:errori_svuota"))
        self.assertFalse(Errore.objects.exists())
        self.client.post(reverse("sistema:dispositivi_dimentica"))
        self.assertFalse(Dispositivo.objects.exists())


class ErroriTest(BaseSistema):
    def test_eccezione_di_una_pagina(self):
        self.client.force_login(self.utente)
        self.client.raise_request_exception = False
        with mock.patch("magazzino.views.guida.render", side_effect=RuntimeError("pagina rotta")):
            for _ in range(2):
                self.assertEqual(self.client.get(reverse("guida")).status_code, 500)
        errore = Errore.objects.get()
        self.assertEqual((errore.origine, errore.tipo, errore.volte), ("web", "RuntimeError", 2))
        self.assertEqual((errore.dove, errore.utente), ("GET /guida/", "volontario"))
        self.assertIn("pagina rotta", errore.traccia)

    def test_registra_errore_non_solleva(self):
        try:
            raise ValueError("x")
        except ValueError as e:
            errori.registra_errore("copia", e)
        self.assertEqual(Errore.objects.get().origine, "copia")
        with mock.patch.object(Errore.objects, "filter", side_effect=errori.DatabaseError("giù")):
            errori.registra_errore("copia", ValueError("y"))  # niente eccezione


class DispositiviTest(BaseSistema):
    def test_ruoli(self):
        fabbrica = RequestFactory()
        self.assertEqual(ruolo(fabbrica.get("/cassa/serate/3/")), "Cassa ingresso")
        self.assertEqual(ruolo(fabbrica.get("/cassa/serate/3/bar/")), "Cassa bar")
        self.assertEqual(ruolo(fabbrica.get("/cassa/serate/3/stato/", {"postazione": "bar"})), "Cassa bar")
        self.assertEqual(ruolo(fabbrica.get("/cassa/serate/3/monitor/dati/")), "Monitor cucina")
        self.assertEqual(ruolo(fabbrica.get("/bar/fusti/")), "Bar")
        self.assertEqual(ruolo(fabbrica.get("/mcp/")), "Claude (MCP)")
        self.assertEqual(ruolo(fabbrica.get("/giacenze/")), "Magazzino")

    def test_segnato_una_volta_ogni_tanto(self):
        self.client.force_login(self.utente)
        self.client.get(reverse("guida"), HTTP_USER_AGENT="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Mobile Safari/604.1")
        d = Dispositivo.objects.get()
        self.assertEqual((d.tipo, d.ruolo, d.utente), ("telefono", "Magazzino", "volontario"))
        self.assertEqual(Dispositivo.objects.count(), 1)


@override_settings(COPIA_CHIAVE="chiave", SERVER_NOME="casa", SINCRONIA_CON="")
class CasaTest(BaseSistema):
    """Il server di casa: si fa tutto come in sagra, tranne stampare."""

    def test_si_registra_ma_non_si_stampa(self):
        self.client.force_login(self.staff)
        risposta = self.client.post(reverse("servizio_nuovo"), {"data": "2026-10-09", "tipo": "sera"})
        self.assertEqual(risposta.status_code, 302)
        from cassa import stampa

        self.assertFalse(stampa.stampa_qui())

    def test_pannello_di_casa(self):
        self.client.force_login(self.staff)
        risposta = self.client.get(reverse("sistema:pannello"))
        self.assertContains(risposta, "server di casa")
        self.assertNotContains(risposta, f'action="{reverse("sistema:stampanti_controlla")}"')
        self.assertNotContains(risposta, 'name="riallinea"')  # si riallinea solo la sagra, da casa
        servizi = {s["id"]: s for s in monitor.stato()["servizi"]}
        self.assertEqual(servizi["stampa"]["stato"], "spento")
        self.assertIn("non si è ancora fatta sentire", servizi["sincronia"]["testo"])

    def test_striscia_se_la_sagra_non_si_sente(self):
        self.client.force_login(self.utente)
        segnala("sincronia", dettagli={"ultimo_contatto": (timezone.now() - timedelta(minutes=20)).isoformat()})
        self.assertContains(self.client.get(reverse("guida")), "La sagra non si sente dalle")
        Battito.objects.filter(nome="sincronia").update(dettagli={"ultimo_contatto": timezone.now().isoformat()})
        self.assertNotContains(self.client.get(reverse("guida")), "striscia-sincronia")


@override_settings(COPIA_CHIAVE="chiave", SERVER_NOME="sagra", SINCRONIA_CON="http://casa:8000/")
class SagraTest(BaseSistema):
    def test_striscia_finche_non_arriva_il_primo_giro(self):
        self.client.force_login(self.utente)
        self.assertNotContains(self.client.get(reverse("guida")), "striscia-sincronia")  # servizio mai partito
        segnala("sincronia", stato="avviso", dettagli={"primo_ok": False})
        self.assertContains(self.client.get(reverse("guida")), "Non ho ancora ricevuto le modifiche fatte da casa")
        segnala("sincronia", dettagli={"primo_ok": True})
        self.assertNotContains(self.client.get(reverse("guida")), "striscia-sincronia")

    def test_sincronizza_adesso_e_riallinea(self):
        self.client.force_login(self.staff)
        risposta = self.client.post(reverse("sistema:sincronia_adesso"), follow=True)
        self.assertContains(risposta, "non è acceso su questo server")
        segnala("sincronia", dettagli={})
        self.client.post(reverse("sistema:sincronia_adesso"))
        self.assertTrue(Battito.objects.get(nome="sincronia").dettagli["adesso"])
        risposta = self.client.post(reverse("sistema:sincronia_adesso"), {"riallinea": "1"}, follow=True)
        self.assertContains(risposta, "spunta la conferma")
        self.client.post(reverse("sistema:sincronia_adesso"), {"riallinea": "1", "conferma": "1"})
        self.assertTrue(Battito.objects.get(nome="sincronia").dettagli["riallinea"])

    def test_pannello_con_conflitti(self):
        from sistema.models import Conflitto

        self.client.force_login(self.staff)
        Conflitto.objects.create(tabella="magazzino_prodotto", chiave="2", motivo="C'è già una riga con lo stesso valore.",
                                 gemella="3", dati_gemella='{"id": 3, "nome": "Pasta", "unita": "pz"}')
        risposta = self.client.get(reverse("sistema:pannello"))
        self.assertContains(risposta, "Righe di qui che casa non accetta")
        self.assertContains(risposta, "Tieni quella di casa")
        self.assertContains(risposta, "Dall'altra parte: Prodotto «Pasta»")
        self.assertContains(risposta, 'name="riallinea"')
