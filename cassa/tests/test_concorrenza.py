"""Più casse e più programmi di stampa nello stesso istante (solo con PostgreSQL)."""

import threading
import uuid
from unittest import skipUnless

from django.db import connection
from django.test import TransactionTestCase

from cassa import stampa
from cassa.models import Ordine, Stampa, StatoStampa
from cassa.services import ErroreCassa

from .base import DatiCassa


@skipUnless(connection.vendor == "postgresql", "I lock di riga si verificano solo con PostgreSQL")
class ConcorrenzaCassaTest(DatiCassa, TransactionTestCase):
    serialized_rollback = True

    def setUp(self):
        self.crea_dati()
        self.imposta(stampante_cucina=self.st_cucina, stampante_bar=self.st_bar)

    def _in_parallelo(self, funzione, n):
        barriera = threading.Barrier(n)
        esiti = []
        blocco = threading.Lock()

        def esegui(i):
            try:
                barriera.wait()
                risultato = funzione(i)
                with blocco:
                    esiti.append(risultato)
            except ErroreCassa as e:
                with blocco:
                    esiti.append(e)
            finally:
                connection.close()

        fili = [threading.Thread(target=esegui, args=(i,)) for i in range(n)]
        for f in fili:
            f.start()
        for f in fili:
            f.join(30)
        self.assertEqual(len(esiti), n)
        return esiti

    def test_numeri_senza_doppioni(self):
        esiti = self._in_parallelo(lambda i: self.ordina([(self.grigliata, 1), (self.coca, 1)])[0].numero, 10)
        self.assertEqual(sorted(esiti), list(range(1, 11)))
        self.assertEqual(Stampa.objects.count(), 20)

    def test_stesso_ordine_inviato_insieme(self):
        token = uuid.uuid4()
        esiti = self._in_parallelo(lambda i: self.ordina([(self.grigliata, 1)], token=token), 5)
        self.assertEqual(len({o.pk for o, _ in esiti}), 1)
        self.assertEqual(sum(1 for _, creato in esiti if creato), 1)
        self.assertEqual(Ordine.objects.count(), 1)

    def test_due_programmi_di_stampa_non_stampano_due_volte(self):
        for _ in range(6):
            self.ordina([(self.grigliata, 1), (self.coca, 1)])
        esiti = self._in_parallelo(lambda i: [l["id"] for l in stampa.prendi_lavori(agente=f"pc{i}", massimo=20)], 4)
        presi = [i for lavori in esiti for i in lavori]
        self.assertEqual(len(presi), 12)
        self.assertEqual(len(set(presi)), 12)
        self.assertEqual(Stampa.objects.filter(stato=StatoStampa.IN_STAMPA).count(), 12)
