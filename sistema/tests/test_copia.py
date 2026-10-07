import gzip
import io
import json
import tarfile
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from sistema import copia


def finto_database(destinazione):
    with gzip.GzipFile(fileobj=destinazione, mode="wb") as f:
        f.write(b"-- dump finto\n")


class ScriviCopiaTest(TestCase):
    def test_contenuto(self):
        with tempfile.TemporaryDirectory() as base:
            dati = Path(base) / "dati"
            (dati / "allegati" / "fatture").mkdir(parents=True)
            (dati / "allegati" / "fatture" / "f1.pdf").write_bytes(b"%PDF")
            (dati / "secret_key").write_text("segreta")
            buffer = io.BytesIO()
            with override_settings(BASE_DIR=Path(base)):
                copia.scrivi_copia(buffer, database=finto_database)
        buffer.seek(0)
        with tarfile.open(fileobj=buffer) as archivio:
            self.assertEqual(archivio.getnames(), ["LEGGIMI.txt", "info.json", "database.sql.gz", "dati.tar.gz"])
            self.assertEqual(gzip.decompress(archivio.extractfile("database.sql.gz").read()), b"-- dump finto\n")
            self.assertIn("scripts/importa-dati.sh", archivio.extractfile("LEGGIMI.txt").read().decode())
            info = json.loads(archivio.extractfile("info.json").read())
            with tarfile.open(fileobj=archivio.extractfile("dati.tar.gz")) as interni:
                nomi = interni.getnames()
        self.assertIn(["cassa", "0001_iniziale"], info["migrazioni"])
        self.assertEqual(len(info["codice"]), 8)
        self.assertIn("allegati/fatture/f1.pdf", nomi)
        self.assertNotIn("secret_key", nomi)  # la chiave segreta resta quella del server su cui si carica


class SoloPostgresTest(SimpleTestCase):
    def test_solo_con_postgresql(self):
        with override_settings(DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3"}}):
            with self.assertRaisesMessage(copia.CopiaNonRiuscita, "PostgreSQL"):
                copia.copia_database(io.BytesIO())


class PaginaCopiaTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        utenti = get_user_model().objects
        cls.utente = utenti.create_user("volontario", password="segreta-123")
        cls.staff = utenti.create_user("capo", password="segreta-123", is_staff=True)

    def test_solo_staff(self):
        self.client.force_login(self.utente)
        self.assertEqual(self.client.get(reverse("copia_dati")).status_code, 403)
        self.assertEqual(self.client.get(reverse("copia_dati_scarica")).status_code, 403)

    def test_scarica(self):
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("copia_dati")), "Scarica la copia")
        with mock.patch.object(copia, "copia_database", finto_database):
            risposta = self.client.get(reverse("copia_dati_scarica"))
        self.assertEqual(risposta.status_code, 200)
        self.assertIn('attachment; filename="copia-', risposta["Content-Disposition"])
        with tarfile.open(fileobj=io.BytesIO(b"".join(risposta.streaming_content))) as archivio:
            self.assertIn("database.sql.gz", archivio.getnames())

    def test_errore_spiegato(self):
        self.client.force_login(self.staff)
        guasto = mock.Mock(side_effect=copia.CopiaNonRiuscita("Copia del database non riuscita: disco pieno"))
        with mock.patch.object(copia, "copia_database", guasto):
            risposta = self.client.get(reverse("copia_dati_scarica"), follow=True)
        self.assertRedirects(risposta, reverse("copia_dati"))
        self.assertContains(risposta, "Copia del database non riuscita: disco pieno")
