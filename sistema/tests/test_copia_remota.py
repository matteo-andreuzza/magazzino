import gzip
import io
import tempfile
import urllib.error
from datetime import timedelta
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from sistema import carica, copia, copia_remota
from sistema.models import Battito

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


@override_settings(COPIA_CHIAVE=CHIAVE, COPIA_DI="http://sagra:8000/")
class SincronizzaTest(TestCase):
    def setUp(self):
        self.sagra = tempfile.TemporaryDirectory()
        self.casa = tempfile.TemporaryDirectory()
        self.addCleanup(self.sagra.cleanup)
        self.addCleanup(self.casa.cleanup)
        (Path(self.sagra.name) / "foto").mkdir()
        (Path(self.sagra.name) / "foto" / "birra.jpg").write_bytes(b"JPEG")
        (Path(self.casa.name) / "vecchia.jpg").write_bytes(b"via")  # alla sagra non c'è più
        self.instradatore = Instradatore(self.client)
        sagra = Path(self.sagra.name)
        for bersaglio, sostituto in (
            ("urllib.request.urlopen", self.instradatore),
            ("sistema.copia.copia_database", scrivi_dump),
            ("sistema.copia_remota.elenco_allegati", lambda: [["foto/birra.jpg", 4]]),
            ("sistema.copia_remota.file_allegato", lambda percorso: sagra / percorso),
        ):
            patcher = mock.patch(bersaglio, sostituto)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.carica = mock.patch("sistema.carica.carica_database", return_value="x").start()
        self.addCleanup(mock.patch.stopall)

    def test_copia_completa_poi_solo_quello_che_cambia(self):
        with override_settings(MEDIA_ROOT=Path(self.casa.name)):
            primo = copia_remota.sincronizza(scrivi=lambda *_: None)
            Battito.objects.create(nome="copia", ultimo_il=timezone.now(), dettagli=primo)
            secondo = copia_remota.sincronizza(scrivi=lambda *_: None)
        self.assertTrue(primo["cambiato"])
        self.assertFalse(secondo["cambiato"])  # stesso database: non si ricarica
        self.assertEqual(self.carica.call_count, 1)
        self.assertEqual((Path(self.casa.name) / "foto" / "birra.jpg").read_bytes(), b"JPEG")
        self.assertFalse((Path(self.casa.name) / "vecchia.jpg").exists())
        self.assertEqual((primo["allegati_scaricati"], primo["allegati_tolti"]), (1, 1))

    def test_mai_sovrascrivere_dati_piu_nuovi(self):
        with mock.patch("sistema.copia_remota.ultimo_dato", return_value=timezone.now()), \
                mock.patch("sistema.copia.ultimo_dato", return_value=timezone.now() - timedelta(days=1)):
            with self.assertRaisesMessage(copia_remota.DirezioneSbagliata, "mai su quello della sagra"):
                copia_remota.sincronizza(scrivi=lambda *_: None)
        self.carica.assert_not_called()

    def test_ciclo_una_volta(self):
        with override_settings(MEDIA_ROOT=Path(self.casa.name)):
            copia_remota.ciclo(ogni_minuti=5, scrivi=lambda *_: None, una_volta=True)
        battito = Battito.objects.get(nome="copia")
        self.assertEqual(battito.stato, "ok")
        self.assertIn("Copia aggiornata", battito.messaggio)

    def test_sagra_spenta(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timed out")):
            copia_remota.ciclo(ogni_minuti=5, scrivi=lambda *_: None, una_volta=True)
        battito = Battito.objects.get(nome="copia")
        self.assertEqual(battito.stato, "avviso")
        self.assertIn("non è raggiungibile", battito.messaggio)

    def test_versione_piu_nuova_alla_sagra(self):
        futuro = sorted(MigrationRecorder(connection).applied_migrations()) + [("bar", "9999_futuro")]
        with mock.patch("sistema.copia.migrazioni_applicate", return_value=futuro):
            copia_remota.ciclo(ogni_minuti=5, scrivi=lambda *_: None, una_volta=True)
        battito = Battito.objects.get(nome="copia")
        self.assertEqual(battito.stato, "errore")
        self.assertIn("Aggiorna prima questo server", battito.messaggio)
        self.carica.assert_not_called()
