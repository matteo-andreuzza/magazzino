from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from cassa import services
from cassa.models import Comanda, Ordine, Reparto, StatoComanda
from cassa.views.monitor import dati_monitor

from .base import BaseCassa


class MonitorTest(BaseCassa):
    def setUp(self):
        super().setUp()
        imp = services.impostazioni()
        imp.monitor_cucina = True
        services.salva_impostazioni(imp)
        self.client.force_login(self.utente)

    def comanda(self, ordine):
        return Comanda.objects.get(ordine=ordine, reparto=Reparto.CUCINA)

    def test_colonne_e_totali(self):
        nuova, _ = self.ordina([(self.grigliata, 2, "senza sale"), (self.coca, 1)], nome="Rossi", tavolo="4")
        in_prep, _ = self.ordina([(self.grigliata, 1), (self.patatine, 3)])
        pronta, _ = self.ordina([(self.pollo, 1)])
        ritirata, _ = self.ordina([(self.pollo, 1)])
        services.cambia_stato_comanda(comanda=self.comanda(in_prep), azione="inizia")
        services.cambia_stato_comanda(comanda=self.comanda(pronta), azione="pronta")
        services.cambia_stato_comanda(comanda=self.comanda(ritirata), azione="pronta")
        services.cambia_stato_comanda(comanda=self.comanda(ritirata), azione="ritira")
        dati = dati_monitor(self.serata)
        per_numero = {c["numero"]: c for c in dati["comande"]}
        self.assertEqual(set(per_numero), {1, 2, 3})
        self.assertEqual(per_numero[1]["stato"], StatoComanda.NUOVA)
        self.assertEqual(per_numero[1]["righe"], [{"q": 2, "t": "Grigliata mista", "n": "senza sale"}])  # niente bevande
        self.assertEqual(per_numero[2]["stato"], StatoComanda.IN_PREPARAZIONE)
        self.assertEqual(per_numero[3]["stato"], StatoComanda.PRONTA)
        self.assertEqual(dati["daPreparare"], [
            {"t": "Grigliata mista", "q": 3}, {"t": "Patatine fritte", "q": 3},
        ])

    def test_annullate_e_vecchie(self):
        annullato, _ = self.ordina([(self.grigliata, 1)])
        services.annulla_ordine(ordine=annullato, utente=None)
        pronta, _ = self.ordina([(self.pollo, 1)])
        services.cambia_stato_comanda(comanda=self.comanda(pronta), azione="pronta")
        dati = dati_monitor(self.serata)
        self.assertEqual([(c["numero"], c["annullato"]) for c in dati["comande"]], [(1, True), (2, False)])
        self.assertEqual(dati["daPreparare"], [])
        vecchio = timezone.now() - timedelta(hours=1)
        Ordine.objects.filter(pk=annullato.pk).update(annullato_il=vecchio)
        Comanda.objects.filter(ordine=pronta).update(pronta_il=vecchio)
        self.assertEqual(dati_monitor(self.serata)["comande"], [])

    def test_solo_comande_da_quando_e_acceso(self):
        prima, _ = self.ordina([(self.grigliata, 1)])
        Ordine.objects.filter(pk=prima.pk).update(creato_il=timezone.now() - timedelta(hours=2))
        dopo, _ = self.ordina([(self.grigliata, 1)])
        self.assertEqual([c["numero"] for c in dati_monitor(self.serata)["comande"]], [dopo.numero])

    def test_viste(self):
        ordine, _ = self.ordina([(self.grigliata, 1)])
        self.assertContains(self.client.get(reverse("cassa:monitor", args=[self.serata.pk])), "Monitor cucina")
        dati = self.client.get(reverse("cassa:monitor_dati", args=[self.serata.pk])).json()
        self.assertEqual(len(dati["comande"]), 1)
        comanda = self.comanda(ordine)
        risposta = self.client.post(reverse("cassa:comanda_azione", args=[comanda.pk, "inizia"]))
        self.assertEqual(risposta.json(), {"ok": True, "stato": StatoComanda.IN_PREPARAZIONE})
        self.assertEqual(self.client.post(reverse("cassa:comanda_azione", args=[comanda.pk, "brucia"])).status_code, 400)
        self.assertEqual(self.client.get(reverse("cassa:comanda_azione", args=[comanda.pk, "inizia"])).status_code, 405)
