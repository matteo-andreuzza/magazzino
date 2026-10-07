import base64
import json
from datetime import timedelta

from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from cassa import services, stampa
from cassa.models import AgenteStampa, Connessione, Reparto, Stampa, Stampante, StatoStampa

from .base import BaseCassa


class ImpaginazioneTest(SimpleTestCase):
    def test_voce_con_prezzo_va_a_capo(self):
        righe = stampa.impagina(
            [{"s": "voce", "q": 2, "t": "Gnocchi al ragù d'anatra con un nome lunghissimo", "p": "24,00"}], colonne=32
        )
        testi = [t for _, _, t in righe]
        self.assertEqual(testi[:2], ["2 x Gnocchi al ragù        24,00", "    d'anatra con un nome"])
        self.assertTrue(all(len(t) <= 32 for t in testi))

    def test_stili(self):
        righe = stampa.impagina([
            {"s": "titolo", "t": "CUCINA"},
            {"s": "nota", "t": "senza cipolla"},
            {"s": "coppia", "t": "TOTALE", "d": "34,50 €", "b": True},
            {"s": "linea"},
        ], colonne=42)
        self.assertEqual(righe[0], ({"b", "alto", "largo"}, "centro", "CUCINA"))
        self.assertEqual(righe[1][2], "    > senza cipolla")
        self.assertEqual(righe[2], ({"b"}, "sx", "TOTALE" + " " * 29 + "34,50 €"))
        self.assertEqual(righe[3][2], "-" * 42)

    def test_parola_troppo_lunga_spezzata(self):
        righe = stampa.impagina([{"s": "titolo", "t": "PRECIPITEVOLISSIMEVOLMENTE"}], colonne=32)
        self.assertEqual([t for _, _, t in righe], ["PRECIPITEVOLISSI", "MEVOLMENTE"])

    def test_senza_accenti(self):
        self.assertEqual(stampa.prepara_testo("Baccalà «buono» 3,00 € – ok", accenti=False), 'Baccala "buono" 3,00 EUR - ok')

    def test_escpos(self):
        dati = stampa.escpos([{"s": "testo", "t": "Ragù 2 €"}], colonne=42, accenti=True)
        self.assertTrue(dati.startswith(b"\x1b@\x1bt\x13"))
        self.assertIn("Ragù 2 €".encode("cp858"), dati)
        self.assertTrue(dati.endswith(b"\x1dVB\x00"))
        senza = stampa.escpos([{"s": "testo", "t": "Ragù"}], accenti=False)
        self.assertNotIn(b"\x1bt", senza)
        self.assertIn(b"Ragu\n", senza)


class CodaAgenteTest(BaseCassa):
    def test_prende_segna_e_non_ridà(self):
        ordine, _ = self.ordina([(self.grigliata, 1), (self.coca, 1)])
        lavori = stampa.prendi_lavori(agente="pc-cassa", ip="10.0.0.2", versione="1.0")
        self.assertEqual(len(lavori), 2)
        self.assertEqual({l["indirizzo"] for l in lavori}, {"192.168.1.50", "192.168.1.51"})
        dati = base64.b64decode(lavori[0]["dati"])
        self.assertTrue(dati.startswith(b"\x1b@"))
        self.assertIn(b"N. 1  TAV. 12", dati)
        self.assertEqual(set(ordine.stampe.values_list("stato", "agente", "tentativi")), {(StatoStampa.IN_STAMPA, "pc-cassa", 1)})
        self.assertEqual(stampa.prendi_lavori(agente="pc-cassa"), [])
        agente = AgenteStampa.objects.get(nome="pc-cassa")
        self.assertTrue(agente.collegato)
        self.assertEqual(agente.indirizzo_ip, "10.0.0.2")

    def test_esito(self):
        self.ordina([(self.grigliata, 1)])
        lavoro = stampa.prendi_lavori(agente="pc")[0]
        self.assertFalse(stampa.registra_esito(agente="altro", stampa_id=lavoro["id"], ok=True))
        self.assertTrue(stampa.registra_esito(agente="pc", stampa_id=lavoro["id"], ok=False, errore="carta finita"))
        s = Stampa.objects.get(pk=lavoro["id"])
        self.assertEqual((s.stato, s.errore), (StatoStampa.ERRORE, "carta finita"))
        # Si riprova da soli dopo qualche secondo.
        self.assertEqual(stampa.prendi_lavori(agente="pc"), [])
        Stampa.objects.filter(pk=s.pk).update(presa_il=timezone.now() - timedelta(seconds=20))
        self.assertEqual(len(stampa.prendi_lavori(agente="pc")), 1)
        self.assertTrue(stampa.registra_esito(agente="pc", stampa_id=s.pk, ok=True))
        s.refresh_from_db()
        self.assertEqual((s.stato, s.errore, s.tentativi), (StatoStampa.STAMPATA, "", 2))
        self.assertIsNotNone(s.stampata_il)

    def test_errori_vecchi_non_si_riprovano_da_soli(self):
        self.ordina([(self.grigliata, 1)])
        adesso = timezone.now()
        Stampa.objects.update(stato=StatoStampa.ERRORE, presa_il=adesso - timedelta(minutes=1), creata_il=adesso - timedelta(minutes=30))
        self.assertEqual(stampa.prendi_lavori(agente="pc"), [])
        stampa.riprova(Stampa.objects.get())
        self.assertEqual(len(stampa.prendi_lavori(agente="pc")), 1)

    def test_agente_che_non_risponde(self):
        self.ordina([(self.grigliata, 1)])
        stampa.prendi_lavori(agente="pc1")
        self.assertEqual(stampa.prendi_lavori(agente="pc2"), [])
        Stampa.objects.update(presa_il=timezone.now() - timedelta(seconds=90))
        self.assertEqual(len(stampa.prendi_lavori(agente="pc2")), 1)

    def test_stampanti_di_un_computer_e_dal_browser(self):
        Stampante.objects.filter(pk=self.st_cucina.pk).update(connessione=Connessione.COMPUTER, indirizzo="/dev/usb/lp0", agente="Cucina-PC")
        Stampante.objects.filter(pk=self.st_bar.pk).update(connessione=Connessione.BROWSER, indirizzo="")
        self.ordina([(self.grigliata, 1), (self.coca, 1)])
        self.assertEqual(stampa.prendi_lavori(agente="pc-cassa"), [])
        lavori = stampa.prendi_lavori(agente="cucina-pc")
        self.assertEqual([(l["connessione"], l["indirizzo"]) for l in lavori], [(Connessione.COMPUTER, "/dev/usb/lp0")])

    def test_stato_stampa(self):
        # Stampanti termiche di rete: le serve il servizio del server oppure il programma di stampa.
        self.assertEqual(stampa.stato_stampa()["avviso"], "Il servizio di stampa non è attivo: le comande restano in coda. Avvisa chi gestisce il computer del programma.")
        stampa.prendi_lavori(agente="pc")
        self.assertEqual(stampa.stato_stampa()["avviso"], "")
        AgenteStampa.objects.update(ultimo_contatto=timezone.now() - timedelta(minutes=5))
        self.assertNotEqual(stampa.stato_stampa()["avviso"], "")


class ApiAgenteTest(BaseCassa):
    def chiama(self, nome, dati, token=None):
        intestazioni = {}
        if token is not False:
            intestazioni["HTTP_AUTHORIZATION"] = f"Bearer {token or services.impostazioni().token_agente}"
        return self.client.post(reverse(nome), json.dumps(dati), content_type="application/json", **intestazioni)

    def test_senza_login_ma_con_chiave(self):
        self.ordina([(self.grigliata, 1)])
        risposta = self.chiama("cassa:api_agente_lavori", {"agente": "pc", "versione": "1.0"})
        self.assertEqual(risposta.status_code, 200)
        lavori = risposta.json()["lavori"]
        self.assertEqual(len(lavori), 1)
        risposta = self.chiama("cassa:api_agente_esito", {"agente": "pc", "id": lavori[0]["id"], "ok": True})
        self.assertEqual(risposta.json(), {"ok": True, "registrato": True})
        self.assertEqual(Stampa.objects.get().stato, StatoStampa.STAMPATA)

    def test_chiave_sbagliata_o_mancante(self):
        for token in (False, "sbagliata"):
            with self.subTest(token=token):
                risposta = self.chiama("cassa:api_agente_lavori", {"agente": "pc"}, token=token)
                self.assertEqual(risposta.status_code, 401)
        self.assertFalse(AgenteStampa.objects.exists())

    def test_nuova_chiave_invalida_la_vecchia(self):
        vecchia = services.impostazioni().token_agente
        services.rigenera_token_agente(services.impostazioni())
        self.assertEqual(self.chiama("cassa:api_agente_lavori", {"agente": "pc"}, token=vecchia).status_code, 401)
        self.assertEqual(self.chiama("cassa:api_agente_lavori", {"agente": "pc"}).status_code, 200)

    def test_richieste_non_valide(self):
        self.assertEqual(self.chiama("cassa:api_agente_lavori", {"agente": " "}).status_code, 400)
        self.assertEqual(self.chiama("cassa:api_agente_esito", {"agente": "pc", "id": "1", "ok": True}).status_code, 400)
        risposta = self.client.get(reverse("cassa:api_agente_lavori"))
        self.assertEqual(risposta.status_code, 405)


class StampaDalBrowserTest(BaseCassa):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.utente)
        Stampante.objects.filter(pk=self.st_cucina.pk).update(connessione=Connessione.BROWSER, indirizzo="")

    def test_pagina_e_segna_stampate(self):
        ordine, _ = self.ordina([(self.grigliata, 1), (self.coca, 1)])
        cucina = ordine.stampe.get(reparto=Reparto.CUCINA)
        bar = ordine.stampe.get(reparto=Reparto.BAR)
        risposta = self.client.get(reverse("cassa:stampe_browser") + f"?id={cucina.pk}&id={bar.pk}")
        self.assertContains(risposta, "Grigliata mista")
        self.assertContains(risposta, f'data-da-segnare="{cucina.pk}"')
        self.assertEqual(risposta["X-Frame-Options"], "SAMEORIGIN")  # la cassa la stampa da un riquadro
        risposta = self.client.post(
            reverse("cassa:stampe_browser_fatto"), json.dumps({"ids": [cucina.pk, bar.pk]}), content_type="application/json"
        )
        self.assertEqual(risposta.json()["segnate"], 1)  # quella di rete la stampa l'agente
        cucina.refresh_from_db()
        bar.refresh_from_db()
        self.assertEqual((cucina.stato, bar.stato), (StatoStampa.STAMPATA, StatoStampa.IN_CODA))

    def test_biglietti_di_emergenza(self):
        ordine, _ = self.ordina([(self.grigliata, 1), (self.coca, 1)])
        risposta = self.client.get(reverse("cassa:ordine_biglietti", args=[ordine.pk]))
        self.assertContains(risposta, "COPIA CUCINA")
        self.assertContains(risposta, "COPIA BAR")
        self.assertContains(risposta, "Ricevuta non fiscale")
        self.assertEqual(Stampa.objects.filter(stato=StatoStampa.STAMPATA).count(), 0)


class ScaricaAgenteTest(BaseCassa):
    def test_solo_staff_e_gia_configurato(self):
        self.client.force_login(self.utente)
        self.assertEqual(self.client.get(reverse("cassa:agente_scarica")).status_code, 403)
        self.client.force_login(self.staff)
        risposta = self.client.get(reverse("cassa:agente_scarica"))
        testo = risposta.content.decode()
        self.assertIn(f"TOKEN = '{services.impostazioni().token_agente}'", testo)
        self.assertIn("SERVER = 'http://testserver/'", testo)
        self.assertIn("attachment", risposta["Content-Disposition"])
