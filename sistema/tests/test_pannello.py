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

    @override_settings(COPIA_CHIAVE="", COPIA_DI="")
    def test_stato_json(self):
        self.client.force_login(self.staff)
        self.client.get(reverse("sistema:pannello"))  # il dispositivo si segna a risposta finita
        dati = self.client.get(reverse("sistema:stato_json")).json()
        self.assertEqual([s["id"] for s in dati["servizi"]], ["web", "db", "stampa", "backup", "drive", "copia"])
        servizi = {s["id"]: s for s in dati["servizi"]}
        self.assertEqual(servizi["db"]["stato"], "ok")
        self.assertEqual(servizi["stampa"]["stato"], "sconosciuto")  # il servizio di stampa non è mai partito
        self.assertEqual(servizi["copia"]["stato"], "spento")  # copia remota non configurata
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


@override_settings(COPIA_DI="http://sagra:8000/")
class SolaLetturaTest(BaseSistema):
    def test_niente_modifiche_sulla_copia(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("giacenze")).status_code, 200)
        risposta = self.client.post(reverse("servizio_nuovo"), {"data": "2026-10-09"})
        self.assertContains(risposta, "copia di sola lettura", status_code=403)
        risposta = self.client.post("/cassa/serate/1/ordina/", "{}", content_type="application/json")
        self.assertEqual(risposta.status_code, 403)
        self.assertIn("sola lettura", risposta.json()["errori"][0])
        # Il pannello del sistema sì (scrive solo nelle sue tabelle), e si entra e si esce.
        self.client.post(reverse("sistema:dispositivi_dimentica"))
        self.client.post(reverse("logout"))
        risposta = self.client.post(reverse("login"), {"username": "capo", "password": "segreta-123"})
        self.assertEqual(risposta.status_code, 302)

    def test_striscia_in_cima(self):
        self.client.force_login(self.utente)
        self.assertContains(self.client.get(reverse("guida")), "Copia di sola lettura dei dati della sagra")
