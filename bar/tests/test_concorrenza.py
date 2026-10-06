"""Operazioni sui fusti nello stesso istante da due telefoni (solo con PostgreSQL)."""

import threading
from decimal import Decimal as D
from unittest import skipUnless

from django.db import connection
from django.test import TransactionTestCase

from bar import services
from bar.models import Fusto
from bar.services import ErroreBar

from .base import DatiBar


@skipUnless(connection.vendor == "postgresql", "I lock di riga si verificano solo con PostgreSQL")
class ConcorrenzaFustiTest(DatiBar, TransactionTestCase):
    serialized_rollback = True

    def setUp(self):
        self.crea_dati()
        self.inventario((self.f15, "4"), (self.f30, "6"))

    def _in_parallelo(self, funzioni):
        barriera = threading.Barrier(len(funzioni))
        esiti, blocco = [], threading.Lock()

        def esegui(funzione):
            try:
                barriera.wait()
                risultato = funzione()
            except ErroreBar as e:
                risultato = e
            finally:
                connection.close()
            with blocco:
                esiti.append(risultato)

        fili = [threading.Thread(target=esegui, args=(f,)) for f in funzioni]
        for f in fili:
            f.start()
        for f in fili:
            f.join(30)
        self.assertEqual(len(esiti), len(funzioni))
        return esiti

    def test_due_telefoni_attaccano_alla_stessa_spina(self):
        esiti = self._in_parallelo([lambda: self.attacca(self.f30), lambda: self.attacca(self.f15)])
        errori = [e for e in esiti if isinstance(e, ErroreBar)]
        self.assertEqual(len(errori), 1)
        self.assertIn("C'è già un fusto attaccato", errori[0].messaggi[0])
        self.assertEqual(Fusto.objects.count(), 1)

    def test_finito_mentre_un_altro_lo_pesa(self):
        fusto = self.attacca(self.f30)
        esiti = self._in_parallelo([lambda: self.finisci(fusto), lambda: self.pesa(fusto, "30.2")])
        fusto.refresh_from_db()
        if fusto.finito_con_id is None:
            self.fail("Il fusto doveva finire tra i consumati")
        errori = [e for e in esiti if isinstance(e, ErroreBar)]
        if errori:
            # Pesato dopo: rifiutato perché ormai è tra i consumati.
            self.assertIn("tra i finiti", errori[0].messaggi[0])
        # In ogni caso il fusto finito vale la tara: 30 litri.
        self.assertEqual(self.spillato(self.serata), D("30.0"))

    def test_prima_operazione_della_serata_da_due_telefoni(self):
        terza = services.Spina.objects.create(spinatrice=self.spina.spinatrice, nome="Terza", ordine=3)
        self._in_parallelo([lambda: self.attacca(self.f30), lambda: self.attacca(self.f15, spina=terza)])
        self.assertEqual(services.ContaFusti.objects.count(), 1)
        self.assertEqual(Fusto.objects.count(), 2)
