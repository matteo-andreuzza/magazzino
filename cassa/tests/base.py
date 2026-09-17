import uuid
from datetime import date
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase

from cassa import services
from cassa.models import Articolo, Categoria, Connessione, Pagamento, Postazione, Reparto, Stampante
from cassa.services import Voce
from magazzino.models import Servizio


class DatiCassa:
    """Dati comuni: listino con prezzi, due stampanti di rete, una serata."""

    @classmethod
    def crea_dati(cls):
        utenti = get_user_model().objects
        cls.utente = utenti.create_user("cassiere", password="segreta-123")
        cls.staff = utenti.create_user("capo", password="segreta-123", is_staff=True)
        cls.serata = Servizio.objects.create(data=date(2026, 9, 18))
        prezzi = {"Grigliata mista": "12.50", "Patatine fritte": "3.00", "Paella": "14.00", "Solo pollo": "9.00"}
        for nome, prezzo in prezzi.items():
            Articolo.objects.filter(nome=nome).update(prezzo=D(prezzo))
        cls.grigliata = Articolo.objects.get(nome="Grigliata mista")
        cls.patatine = Articolo.objects.get(nome="Patatine fritte")
        cls.paella = Articolo.objects.get(nome="Paella")
        cls.pollo = Articolo.objects.get(nome="Solo pollo")
        bevande = Categoria.objects.get(nome="Bevande")
        cls.coca = Articolo.objects.create(nome="Coca-Cola", categoria=bevande, reparto=Reparto.BAR, prezzo=D("2.50"))
        cls.acqua = Articolo.objects.create(nome="Acqua", categoria=bevande, reparto=Reparto.BAR, prezzo=D("1.00"))
        cls.st_cucina = Stampante.objects.create(nome="Cucina", connessione=Connessione.RETE, indirizzo="192.168.1.50")
        cls.st_bar = Stampante.objects.create(nome="Bar", connessione=Connessione.RETE, indirizzo="192.168.1.51")

    def imposta(self, **campi):
        imp = services.impostazioni()
        for nome, valore in campi.items():
            setattr(imp, nome, valore)
        imp.save()
        return imp

    def ordina(self, voci, **altro):
        dati = {
            "servizio": self.serata,
            "postazione": Postazione.INGRESSO,
            "voci": [voce(*v) for v in voci],
            "pagamento": Pagamento.CONTANTI,
            "utente": self.utente,
            "nome": "Rossi",
            "tavolo": "12",
        }
        dati.update(altro)
        return services.crea_ordine(**dati)


class BaseCassa(DatiCassa, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.crea_dati()

    def setUp(self):
        self.imposta(stampante_cucina=self.st_cucina, stampante_bar=self.st_bar)


def token():
    return uuid.uuid4()


def voce(articolo, quantita, note=""):
    return Voce(articolo_id=articolo.pk, quantita=quantita, note=note)
