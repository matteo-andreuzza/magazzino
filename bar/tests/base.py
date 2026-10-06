from datetime import date
from decimal import Decimal as D

from django.test import TestCase

from bar import services
from bar.models import Bevanda, Spina, TipoBevanda, TipoFusto, TipoOperazioneBar
from cassa.models import Articolo, Categoria, Reparto
from cassa.tests.base import DatiCassa
from magazzino.models import Servizio


class DatiBar(DatiCassa):
    """Listino della cassa con le bevande collegate al magazzino del bar (creato dalle migrazioni).

    Le migrazioni creano le due spinatrici (Birra: Bionda e Rossa; Vino: Bianco e
    Rosso) e i tipi di fusto con litri e tare provvisori. Qui i fusti da 15 e da
    30 litri della bionda hanno tare vere (5 e 10 kg): i conti dei test tornano
    tondi. Il vino sfuso a litri serve per le giacenze in litri.
    """

    @classmethod
    def crea_dati(cls):
        super().crea_dati()
        cls.coca_b = Bevanda.objects.get(nome="Coca-Cola")
        cls.vino = Bevanda.objects.create(nome="Vino sfuso", tipo=TipoBevanda.LITRI, ordine=20)
        cls.birra = Bevanda.objects.get(nome="Birra bionda")
        cls.rossa = Bevanda.objects.get(nome="Birra rossa")
        cls.bianco = Bevanda.objects.get(nome="Vino bianco")
        TipoFusto.objects.filter(litri=15, bevanda=cls.birra).update(tara=D("5"), attivo=True, tara_provvisoria=False)
        TipoFusto.objects.filter(litri=30, bevanda=cls.birra).update(tara=D("10"), attivo=True, tara_provvisoria=False)
        cls.f15 = TipoFusto.objects.get(litri=15)
        cls.f30 = TipoFusto.objects.get(litri=30)
        Articolo.objects.filter(pk=cls.coca.pk).update(bevanda=cls.coca_b, quantita_bevanda=D("1"))
        bevande = Categoria.objects.get(nome="Bevande")
        cls.birra_piccola = Articolo.objects.create(
            nome="Birra 0,33", categoria=bevande, reparto=Reparto.BAR, prezzo=D("3.50"), bevanda=cls.birra,
            quantita_bevanda=D("0.33"),
        )
        cls.caraffa = Articolo.objects.create(
            nome="Caraffa birra 1,5 l", categoria=bevande, reparto=Reparto.BAR, prezzo=D("14"), bevanda=cls.birra,
            quantita_bevanda=D("1.5"),
        )
        cls.ombra = Articolo.objects.create(
            nome="Ombra bianco", categoria=bevande, reparto=Reparto.BAR, prezzo=D("1"), bevanda=cls.vino,
            quantita_bevanda=D("0.1"),
        )
        cls.serata2 = Servizio.objects.create(data=date(2026, 9, 19))
        cls.serata3 = Servizio.objects.create(data=date(2026, 9, 20))
        cls.spina = Spina.objects.get(nome="Bionda")

    def inventario(self, *voci):
        return services.registra_carico(tipo=TipoOperazioneBar.INVENTARIO, voci=[(c, D(q)) for c, q in voci], utente=self.utente)

    # Le operazioni del bancone, come le fa la pagina dei fusti.
    def attacca(self, tipo, servizio=None, spina=None):
        return services.attacca(servizio=servizio or self.serata, spina=spina or self.spina, tipo=tipo, utente=self.utente)

    def finisci(self, fusto, servizio=None):
        return services.finisci(servizio=servizio or self.serata, fusto=fusto, utente=self.utente)

    def pesa(self, fusto, peso, servizio=None):
        return services.pesa(servizio=servizio or self.serata, fusto=fusto, peso=D(peso), utente=self.utente)

    def cambia(self, tipo, attuale, servizio=None, spina=None):
        return services.cambia(
            servizio=servizio or self.serata, spina=spina or self.spina, tipo=tipo, attuale=attuale, utente=self.utente,
        )

    def spillato(self, servizio):
        return services.arrotonda(services.spillato_per_servizio().get(servizio.pk, {}).get(self.birra.pk, D("0")))


class BaseBar(DatiBar, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.crea_dati()

    def setUp(self):
        self.imposta(stampante_cucina=self.st_cucina, stampante_bar=self.st_bar)
