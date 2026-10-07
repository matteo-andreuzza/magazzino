"""Copia e caricamento veri, con pg_dump e psql: solo su PostgreSQL (server di prova)."""

import shutil
import tempfile
from pathlib import Path
from unittest import mock, skipUnless

from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.db import connection
from django.test import TransactionTestCase

from sistema import carica, copia
from sistema.models import Errore


@skipUnless(connection.vendor == "postgresql" and shutil.which("pg_dump"), "serve PostgreSQL con pg_dump e psql")
class CaricaSuPostgresTest(TransactionTestCase):
    serialized_rollback = True

    def test_copia_e_ricarica_tenendo_le_tabelle_locali(self):
        utenti = get_user_model().objects
        utenti.create_user("prima", password="x")
        with tempfile.TemporaryDirectory() as cartella:
            dump = Path(cartella) / "database.sql.gz"
            with dump.open("wb") as f:
                copia.copia_database(f)
            # Dopo la copia: un utente in più, uno in meno, e dati locali di questo server.
            utenti.create_user("dopo", password="x")
            utenti.filter(username="prima").delete()
            Errore.objects.create(firma="locale", origine="web", tipo="ValueError")
            sessione = SessionStore()
            sessione["chi"] = "io"
            sessione.create()
            carica.carica_database(dump, scrivi=lambda *_: None)
        self.assertTrue(utenti.filter(username="prima").exists())
        self.assertFalse(utenti.filter(username="dopo").exists())
        # Le tabelle di questo server sono rimaste com'erano.
        self.assertTrue(Errore.objects.filter(firma="locale").exists())
        self.assertEqual(SessionStore(session_key=sessione.session_key)["chi"], "io")

    def test_le_tabelle_locali_di_chi_fa_la_copia_restano(self):
        """Se chi fa la copia esclude una tabella che qui non è locale (codice un po' diverso),
        la tabella resta: non si toglie una tabella che la copia non ricrea."""
        from cassa.models import AgenteStampa
        from django.utils import timezone

        AgenteStampa.objects.create(nome="pc-cassa", ultimo_contatto=timezone.now())
        with tempfile.TemporaryDirectory() as cartella:
            dump = Path(cartella) / "database.sql.gz"
            with dump.open("wb") as f:
                copia.copia_database(f)
            with mock.patch.object(carica, "TABELLE_LOCALI_SQL", ("django_session",)):
                carica.carica_database(dump, scrivi=lambda *_: None)
        self.assertTrue(AgenteStampa.objects.filter(nome="pc-cassa").exists())
