"""Più telefoni che registrano nello stesso istante (solo con PostgreSQL)."""

import threading
import time
import uuid
from datetime import date
from decimal import Decimal as D
from unittest import mock, skipUnless

from django.db import connection
from django.test import TransactionTestCase

from magazzino import services
from magazzino.models import Operazione, Prodotto, Servizio, TipoOperazione, Ubicazione
from magazzino.services import ErroreMagazzino

_giacenze_originale = services.giacenze


def _giacenze_lenta(*args, **kwargs):
    # Allarga la finestra tra lettura e scrittura: senza lock i prelievi si sovrapporrebbero.
    risultato = _giacenze_originale(*args, **kwargs)
    time.sleep(0.05)
    return risultato


@skipUnless(connection.vendor == "postgresql", "I lock di riga si verificano solo con PostgreSQL")
class ConcorrenzaTest(TransactionTestCase):
    serialized_rollback = True

    def setUp(self):
        self.frigo = Ubicazione.objects.get(nome="Frigo 1")
        self.pollo = Prodotto.objects.get(nome="Pollo")
        self.serata = Servizio.objects.create(data=date(2026, 9, 18))
        services.registra_carico(
            tipo=TipoOperazione.ACQUISTO, ubicazione=self.frigo, quantita=[(self.pollo, D("10"))], utente=None
        )

    def _in_parallelo(self, funzione, n):
        barriera = threading.Barrier(n)
        esiti = []
        blocco = threading.Lock()

        def esegui():
            try:
                barriera.wait()
                risultato = funzione()
                with blocco:
                    esiti.append(risultato)
            except ErroreMagazzino:
                with blocco:
                    esiti.append("rifiutato")
            finally:
                connection.close()

        thread = [threading.Thread(target=esegui) for _ in range(n)]
        for t in thread:
            t.start()
        for t in thread:
            t.join(30)
        self.assertEqual(len(esiti), n)
        return esiti

    def test_prelievi_simultanei_non_vanno_sotto_zero(self):
        def preleva():
            services.registra_cucina(servizio=self.serata, prelievi=[(self.pollo, self.frigo, D("3"))], utente=None)
            return "ok"

        with mock.patch.object(services, "giacenze", _giacenze_lenta):
            esiti = self._in_parallelo(preleva, 8)
        self.assertEqual(esiti.count("ok"), 3)
        self.assertEqual(esiti.count("rifiutato"), 5)
        self.assertEqual(services.giacenze()[(self.pollo.pk, self.frigo.pk)], D("1"))

    def test_stesso_modulo_inviato_insieme_registrato_una_volta(self):
        token = uuid.uuid4()

        def invia():
            op, creata = services.registra_cucina(
                servizio=self.serata, prelievi=[(self.pollo, self.frigo, D("2"))], utente=None, token=token
            )
            return (op.pk, creata)

        with mock.patch.object(services, "giacenze", _giacenze_lenta):
            esiti = self._in_parallelo(invia, 4)
        self.assertEqual(len({pk for pk, _ in esiti}), 1)
        self.assertEqual(sum(1 for _, creata in esiti if creata), 1)
        self.assertEqual(Operazione.objects.filter(tipo=TipoOperazione.CUCINA).count(), 1)
        self.assertEqual(services.giacenze()[(self.pollo.pk, self.frigo.pk)], D("8"))
