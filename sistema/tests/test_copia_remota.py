import gzip
import io
import tempfile
import urllib.error
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from sistema import carica, copia, copia_remota, sincronia
from sistema.models import Battito, Modifica

CHIAVE = "chiave-di-prova-molto-lunga"


def scrivi_dump(destinazione, *, chiave="ABC", migrazioni=None, dati="riga\n"):
    """Un finto pg_dump con la tabella delle migrazioni (quella che guarda il controllo della versione)."""
    if migrazioni is None:
        migrazioni = sorted(MigrationRecorder(connection).applied_migrations())
    testo = [f"\\restrict {chiave}\n", "COPY public.django_migrations (id, app, name, applied) FROM stdin;\n"]
    testo += [f"{i}\t{app}\t{nome}\t2026-01-01 00:00:00+00\n" for i, (app, nome) in enumerate(migrazioni, 1)]
    testo += ["\\.\n", dati, f"\\unrestrict {chiave}\n"]
    with gzip.GzipFile(fileobj=destinazione, mode="wb", mtime=0) as f:
        f.write("".join(testo).encode())


def file_dump(cartella, nome, **opzioni):
    percorso = Path(cartella) / nome
    with percorso.open("wb") as f:
        scrivi_dump(f, **opzioni)
    return percorso


class LeggiDumpTest(TestCase):
    def test_migrazioni_e_impronta(self):
        with tempfile.TemporaryDirectory() as cartella:
            uno = file_dump(cartella, "1.sql.gz", chiave="AAA")
            due = file_dump(cartella, "2.sql.gz", chiave="BBB")
            diverso = file_dump(cartella, "3.sql.gz", dati="altra riga\n")
            dump = carica.leggi_dump(uno)
            self.assertIn(("cassa", "0001_iniziale"), dump.migrazioni)
            self.assertFalse(dump.contiene_locali)
            # La riga \restrict cambia a ogni copia: non conta.
            self.assertEqual(dump.impronta, carica.leggi_dump(due).impronta)
            self.assertNotEqual(dump.impronta, carica.leggi_dump(diverso).impronta)
            with (Path(cartella) / "vuoto.sql.gz").open("wb") as f:
                f.write(gzip.compress(b"SELECT 1;\n"))
            with self.assertRaisesMessage(copia.CopiaNonRiuscita, "non sembra una copia"):
                carica.leggi_dump(Path(cartella) / "vuoto.sql.gz")

    def test_tabelle_attese(self):
        tutte = sorted(MigrationRecorder(connection).applied_migrations())
        attese = carica.tabelle_attese(tutte)
        self.assertTrue({"cassa_ordine", "cassa_agentestampa", "auth_user", "auth_user_groups"} <= attese)
        prima_della_cassa = [m for m in tutte if m[0] != "cassa"]
        self.assertNotIn("cassa_ordine", carica.tabelle_attese(prima_della_cassa))

    def test_versione(self):
        carica.controlla_versione([("cassa", "0001_iniziale"), ("sistema", "9999_solo_di_qui")])
        with self.assertRaisesMessage(carica.VersioneIncompatibile, "cassa.9999_futuro"):
            carica.controlla_versione([("cassa", "0001_iniziale"), ("cassa", "9999_futuro")])


class Instradatore:
    """Al posto di internet: le richieste del server di casa arrivano al client di test (la «sagra»)."""

    def __init__(self, client):
        self.client = client
        self.richieste = []

    def __call__(self, richiesta, timeout=None):
        parti = urlsplit(richiesta.full_url)
        self.richieste.append(parti.path)
        risposta = self.client.get(parti.path + (f"?{parti.query}" if parti.query else ""),
                                   HTTP_AUTHORIZATION=richiesta.get_header("Authorization"))
        if risposta.status_code >= 400:
            raise urllib.error.HTTPError(richiesta.full_url, risposta.status_code, "errore", {}, None)
        return io.BytesIO(b"".join(risposta.streaming_content) if risposta.streaming else risposta.content)


@override_settings(COPIA_CHIAVE=CHIAVE)
class ApiCopiaTest(TestCase):
    def chiedi(self, nome, chiave=CHIAVE, **parametri):
        return self.client.get(reverse(nome), parametri, HTTP_AUTHORIZATION=f"Bearer {chiave}")

    def test_chiave(self):
        self.assertEqual(self.chiedi("sistema:api_copia", chiave="sbagliata").status_code, 401)
        self.assertEqual(self.client.get(reverse("sistema:api_copia")).status_code, 401)
        with override_settings(COPIA_CHIAVE=""):
            self.assertEqual(self.chiedi("sistema:api_copia").status_code, 404)
        with tempfile.TemporaryDirectory() as vuota, override_settings(MEDIA_ROOT=vuota):
            dati = self.chiedi("sistema:api_copia").json()
        self.assertIn(["cassa", "0001_iniziale"], dati["migrazioni"])
        self.assertEqual(dati["allegati"], [])

    def test_database_e_allegati(self):
        with tempfile.TemporaryDirectory() as allegati:
            (Path(allegati) / "fatture").mkdir()
            (Path(allegati) / "fatture" / "a.pdf").write_bytes(b"%PDF-1")
            with override_settings(MEDIA_ROOT=Path(allegati)), mock.patch.object(copia, "copia_database", scrivi_dump):
                self.assertEqual(self.chiedi("sistema:api_copia").json()["allegati"], [["fatture/a.pdf", 6]])
                risposta = self.chiedi("sistema:api_copia_database")
                self.assertTrue(gzip.decompress(b"".join(risposta.streaming_content)).startswith(b"\\restrict"))
                risposta = self.chiedi("sistema:api_copia_allegato", percorso="fatture/a.pdf")
                self.assertEqual(b"".join(risposta.streaming_content), b"%PDF-1")
                self.assertEqual(self.chiedi("sistema:api_copia_allegato", percorso="../../config/settings.py").status_code, 404)
        self.assertTrue(Battito.objects.filter(nome=copia_remota.NOME_INVIATA).exists())


@override_settings(COPIA_CHIAVE=CHIAVE, SINCRONIA_CON="http://casa:8000/")
class RiallineaTest(TestCase):
    """Riallinearsi dall'altro server: copia completa (qui finta: niente pg_dump e psql)."""

    def setUp(self):
        self.altro = tempfile.TemporaryDirectory()
        self.qui = tempfile.TemporaryDirectory()
        self.addCleanup(self.altro.cleanup)
        self.addCleanup(self.qui.cleanup)
        (Path(self.altro.name) / "foto").mkdir()
        (Path(self.altro.name) / "foto" / "birra.jpg").write_bytes(b"JPEG")
        self.instradatore = Instradatore(self.client)
        altro = Path(self.altro.name)
        for bersaglio, sostituto in (
            ("urllib.request.urlopen", self.instradatore),
            ("sistema.copia.copia_database", scrivi_dump),
            ("sistema.copia_remota.file_allegato", lambda percorso: altro / percorso),
            ("sistema.sincronia.copia_di_sicurezza", lambda *a, **k: None),
            ("sistema.sincronia.prepara", lambda *a, **k: None),
            ("sistema.sincronia.file_mancanti", lambda *a, **k: ["foto/birra.jpg"]),
        ):
            patcher = mock.patch(bersaglio, sostituto)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.carica = mock.patch("sistema.carica.carica_database", return_value="x").start()
        self.addCleanup(mock.patch.stopall)

    def test_copia_completa_e_file(self):
        Modifica.objects.create(tabella="magazzino_prodotto", chiave="2", numero=1, quando=timezone.now())
        with self.assertRaisesMessage(sincronia.SincroniaFerma, "si perderebbero"):
            sincronia.riallinea(scrivi=lambda *_: None)
        self.carica.assert_not_called()
        with override_settings(MEDIA_ROOT=Path(self.qui.name)):
            esito = sincronia.riallinea(scrivi=lambda *_: None, forza=True)
        self.assertEqual(self.carica.call_count, 1)
        self.assertEqual(esito["file"], 1)
        self.assertEqual((Path(self.qui.name) / "foto" / "birra.jpg").read_bytes(), b"JPEG")
        self.assertFalse(Modifica.objects.exists())  # la coda di prima non vale più

    def test_versione_piu_nuova_dall_altra_parte(self):
        futuro = sorted(MigrationRecorder(connection).applied_migrations()) + [("bar", "9999_futuro")]
        with mock.patch("sistema.copia.migrazioni_applicate", return_value=futuro):
            with self.assertRaisesMessage(carica.VersioneIncompatibile, "Aggiorna prima questo server"):
                sincronia.riallinea(scrivi=lambda *_: None)
        self.carica.assert_not_called()

    def test_senza_indirizzo(self):
        with override_settings(SINCRONIA_CON=""), self.assertRaisesMessage(sincronia.SincroniaFerma, "--da"):
            sincronia.riallinea(scrivi=lambda *_: None)


@override_settings(COPIA_CHIAVE=CHIAVE, SINCRONIA_CON="http://casa:8000/")
class CicloTest(TestCase):
    """Il servizio «sincronia» della sagra, con il giro finto."""

    def setUp(self):
        cartella = tempfile.TemporaryDirectory()  # la pulizia dei file non deve toccare quelli veri
        self.addCleanup(cartella.cleanup)
        impostazioni = override_settings(MEDIA_ROOT=cartella.name)
        impostazioni.enable()
        self.addCleanup(impostazioni.disable)

    def esito(self, **altro):
        return {"inviate": 2, "ricevute": 3, "applicate": 3, "tenute": 0, "rifiutate_qui": 0, "in_coda": 0,
                "in_coda_altro": 0, "conflitti": 0, "conflitti_altro": [], "esiti_richieste": [],
                "scarto_orologio": 0.4, "file_presi": 0, "file_mandati": 0, "durata": 0.2, **altro}

    def test_giro_riuscito(self):
        with mock.patch("sistema.sincronia.giro", return_value=self.esito()):
            sincronia.ciclo(ogni_secondi=30, scrivi=lambda *_: None, una_volta=True)
        battito = Battito.objects.get(nome=sincronia.NOME)
        self.assertEqual(battito.stato, "ok")
        self.assertIn("Allineata con casa", battito.messaggio)
        self.assertTrue(battito.dettagli["primo_ok"])

    def test_conflitti_e_orologi(self):
        with mock.patch("sistema.sincronia.giro", return_value=self.esito(conflitti=1, scarto_orologio=95.0)):
            sincronia.ciclo(ogni_secondi=30, scrivi=lambda *_: None, una_volta=True)
        battito = Battito.objects.get(nome=sincronia.NOME)
        self.assertEqual(battito.stato, "avviso")
        self.assertIn("orologi", battito.messaggio)

    def test_casa_non_raggiungibile(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timed out")), \
                mock.patch("sistema.sincronia.attiva", return_value=True), \
                mock.patch("sistema.sincronia.schema", return_value={}):
            sincronia.ciclo(ogni_secondi=30, scrivi=lambda *_: None, una_volta=True)
        battito = Battito.objects.get(nome=sincronia.NOME)
        self.assertEqual(battito.stato, "avviso")
        self.assertIn("non è raggiungibile", battito.messaggio)
        self.assertFalse(battito.dettagli["primo_ok"])

    def test_versioni_diverse(self):
        with mock.patch("sistema.sincronia.giro", side_effect=sincronia.SincroniaFerma("Aggiorna la sagra.")):
            sincronia.ciclo(ogni_secondi=30, scrivi=lambda *_: None, una_volta=True)
        battito = Battito.objects.get(nome=sincronia.NOME)
        self.assertEqual((battito.stato, battito.messaggio), ("errore", "Aggiorna la sagra."))
