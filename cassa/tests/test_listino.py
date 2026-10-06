"""Il listino 2026 caricato dal file (comando carica_listino)."""

import io
from decimal import Decimal as D

from django.core.management import call_command
from django.test import TestCase

from bar.models import Bevanda
from cassa.models import Articolo, Categoria

LISTINO = "cassa/listini/listino_2026.json"


class CaricaListinoTest(TestCase):
    def carica(self, *opzioni):
        uscita = io.StringIO()
        call_command("carica_listino", LISTINO, *opzioni, stdout=uscita)
        return uscita.getvalue()

    def test_prova_non_salva(self):
        prima = list(Articolo.objects.values_list("nome", "prezzo"))
        testo = self.carica("--prova")
        self.assertIn("PROVA", testo)
        self.assertEqual(list(Articolo.objects.values_list("nome", "prezzo")), prima)

    def test_carica_e_si_puo_ripetere(self):
        self.carica()
        # Gli articoli nati dal menu sono rinominati, non duplicati.
        self.assertFalse(Articolo.objects.filter(nome="Pennette al pomodoro").exists())
        pasta = Articolo.objects.get(nome="Pasta con pomodoro e basilico")
        self.assertEqual((pasta.prezzo, pasta.piatto.nome, pasta.reparto), (D("5.00"), "Gnocchi o pennette", "cucina"))
        birra = Articolo.objects.get(nome="Caraffa birra rossa 1,5 l")
        self.assertEqual((birra.bevanda.nome, birra.quantita_bevanda, birra.reparto), ("Birra rossa", D("1.5"), "bar"))
        self.assertEqual(Articolo.objects.get(nome="Ombra di Raboso").bevanda.nome, "Vino rosso")
        self.assertEqual(Articolo.objects.get(nome="Sangria caraffa").piatto.nome, "Sangria")
        self.assertTrue(Bevanda.objects.filter(nome="Acqua gassata").exists())
        self.assertFalse(Bevanda.objects.filter(nome="Acqua minerale").exists())  # diventata «Acqua naturale»
        self.assertTrue(Categoria.objects.filter(nome="Piatti della serata").exists())
        quanti = Articolo.objects.count()
        self.carica()
        self.assertEqual(Articolo.objects.count(), quanti)
        self.assertEqual(Articolo.objects.filter(attivo=True, prezzo__isnull=False).count(), 51)

    def test_disattiva_quelli_che_non_ci_sono(self):
        Articolo.objects.create(nome="Vecchio", categoria=Categoria.objects.first(), prezzo=D("2"))
        self.assertIn("Restano attivi anche: Vecchio", self.carica())
        self.assertIn("- Vecchio", self.carica("--disattiva-altri"))
        self.assertFalse(Articolo.objects.get(nome="Vecchio").attivo)
