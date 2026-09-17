from decimal import Decimal

from django.test import SimpleTestCase

from magazzino.quantita import (
    QuantitaNonValida,
    formatta_numero,
    formatta_quantita,
    leggi_quantita,
    nome_unita,
)


class LeggiQuantitaTest(SimpleTestCase):
    def test_vuoto_restituisce_none(self):
        for testo in (None, "", "   "):
            self.assertIsNone(leggi_quantita(testo))

    def test_virgola_e_punto(self):
        self.assertEqual(leggi_quantita("2,5"), Decimal("2.5"))
        self.assertEqual(leggi_quantita("2.5"), Decimal("2.5"))
        self.assertEqual(leggi_quantita(" 12 "), Decimal("12"))
        self.assertEqual(leggi_quantita(",5"), Decimal("0.5"))
        self.assertEqual(leggi_quantita("3,"), Decimal("3"))
        self.assertEqual(leggi_quantita("0,125"), Decimal("0.125"))
        self.assertEqual(leggi_quantita("1 000"), Decimal("1000"))

    def test_zero(self):
        self.assertIsNone(leggi_quantita("0"))
        self.assertIsNone(leggi_quantita("0,0"))
        self.assertEqual(leggi_quantita("0", ammetti_zero=True), Decimal("0"))

    def test_testi_non_validi(self):
        for testo in ("abc", "2,5,3", "2..5", "1.000,5", "2kg", "--1", "1e3", "∞", ","):
            with self.subTest(testo=testo), self.assertRaises(QuantitaNonValida):
                leggi_quantita(testo)

    def test_negativo(self):
        with self.assertRaisesMessage(QuantitaNonValida, "negativa"):
            leggi_quantita("-2")

    def test_troppi_decimali(self):
        with self.assertRaisesMessage(QuantitaNonValida, "decimali"):
            leggi_quantita("1,2345")
        self.assertEqual(leggi_quantita("1,2340"), Decimal("1.234"))

    def test_troppo_grande(self):
        self.assertEqual(leggi_quantita("100000"), Decimal("100000"))
        with self.assertRaises(QuantitaNonValida):
            leggi_quantita("100000,001")


class FormattaTest(SimpleTestCase):
    def test_numeri(self):
        self.assertEqual(formatta_numero(Decimal("2.500")), "2,5")
        self.assertEqual(formatta_numero(Decimal("100.000")), "100")
        self.assertEqual(formatta_numero(Decimal("-3.250")), "-3,25")
        self.assertEqual(formatta_numero(Decimal("0.000")), "0")
        self.assertEqual(formatta_numero(Decimal("-0")), "0")
        self.assertEqual(formatta_numero(None), "")

    def test_unita_singolare_plurale(self):
        self.assertEqual(nome_unita("busta", Decimal("1")), "busta")
        self.assertEqual(nome_unita("busta", Decimal("2")), "buste")
        self.assertEqual(nome_unita("busta", Decimal("-1")), "busta")
        self.assertEqual(nome_unita("vaso"), "vasi")
        self.assertEqual(formatta_quantita(Decimal("1.000"), "vaso"), "1 vaso")
        self.assertEqual(formatta_quantita(Decimal("2.5"), "kg"), "2,5 kg")
