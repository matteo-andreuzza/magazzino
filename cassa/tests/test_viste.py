import json
import uuid
from decimal import Decimal as D

from django.urls import reverse
from django.utils import timezone

from cassa import services
from cassa.models import Articolo, Connessione, Ordine, Stampa, Stampante, StatoStampa
from magazzino.models import Servizio
from magazzino.views.comuni import giorno_servizio

from .base import BaseCassa


class AccessoTest(BaseCassa):
    def test_serve_il_login(self):
        for nome, args in [("cassa:inizio", []), ("cassa:cassa", [self.serata.pk]), ("cassa:report", [self.serata.pk])]:
            with self.subTest(nome=nome):
                risposta = self.client.get(reverse(nome, args=args))
                self.assertEqual(risposta.status_code, 302)
                self.assertTrue(risposta["Location"].startswith(reverse("login")))

    def test_configurazione_solo_staff(self):
        self.client.force_login(self.utente)
        for nome, args in [
            ("cassa:impostazioni", []), ("cassa:stampanti", []), ("cassa:stampante_nuova", []),
            ("cassa:articolo_nuovo", []), ("cassa:articolo_modifica", [self.coca.pk]),
        ]:
            with self.subTest(nome=nome):
                self.assertEqual(self.client.get(reverse(nome, args=args)).status_code, 403)
        risposta = self.client.post(reverse("cassa:listino"), {f"presente-{self.coca.pk}": "1", f"prezzo-{self.coca.pk}": "0"})
        self.assertEqual(risposta.status_code, 403)
        self.coca.refresh_from_db()
        self.assertEqual(self.coca.prezzo, D("2.50"))

    def test_funzioni_smart_spente(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("cassa:cassa_bar", args=[self.serata.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("cassa:monitor", args=[self.serata.pk])).status_code, 404)
        risposta = self.client.get(reverse("cassa:monitor_dati", args=[self.serata.pk]))
        self.assertEqual(risposta.status_code, 404)
        self.assertFalse(risposta.json()["ok"])
        risposta = self.client.get(reverse("cassa:inizio"))
        self.assertNotContains(risposta, reverse("cassa:monitor", args=[self.serata.pk]))


class PagineTest(BaseCassa):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)

    def test_tutte_le_pagine_si_aprono(self):
        self.imposta(monitor_cucina=True, cassa_bar=True, ricevuta_cliente=True, stampante_ricevute=self.st_bar)
        ordine, _ = self.ordina([(self.grigliata, 2, "senza sale"), (self.coca, 1)], coperti=3)
        self.ordina([(self.patatine, 1)], pagamento="omaggio")
        annullato, _ = self.ordina([(self.coca, 1)])
        services.annulla_ordine(ordine=annullato, utente=self.staff)
        Stampa.objects.filter(ordine=ordine).update(stato=StatoStampa.ERRORE, errore="carta finita")
        pagine = [
            ("cassa:inizio", []), ("cassa:cassa", [self.serata.pk]), ("cassa:cassa_bar", [self.serata.pk]),
            ("cassa:ordini", [self.serata.pk]), ("cassa:ordini_csv", [self.serata.pk]), ("cassa:ordine", [ordine.pk]),
            ("cassa:ordine", [annullato.pk]), ("cassa:ordine_annulla", [ordine.pk]), ("cassa:ordine_biglietti", [ordine.pk]),
            ("cassa:monitor", [self.serata.pk]), ("cassa:monitor_dati", [self.serata.pk]),
            ("cassa:report", [self.serata.pk]), ("cassa:report_csv", [self.serata.pk]),
            ("cassa:statistiche", []), ("cassa:statistiche_csv", []), ("cassa:listino", []),
            ("cassa:articolo_nuovo", []), ("cassa:articolo_modifica", [self.coca.pk]), ("cassa:impostazioni", []),
            ("cassa:stampanti", []), ("cassa:stampante_nuova", []), ("cassa:stampante_modifica", [self.st_bar.pk]),
            ("cassa:stato", [self.serata.pk]), ("cassa:stampe_browser", []),
            ("servizio_dettaglio", [self.serata.pk]), ("servizio_chiudi", [self.serata.pk]),
            ("home", []), ("riepilogo", []), ("cassa:chiudi", [self.serata.pk]),
            ("bar:inizio", []), ("bar:carico", []), ("bar:correggi", []), ("bar:consumi", []), ("bar:storico", []),
            ("bar:bevande", []), ("bar:conta_fusti", [self.serata.pk]),
        ]
        for nome, args in pagine:
            with self.subTest(pagina=nome, args=args):
                risposta = self.client.get(reverse(nome, args=args))
                self.assertEqual(risposta.status_code, 200, risposta.content[:300])
        self.assertContains(self.client.get(reverse("cassa:ordine", args=[ordine.pk])), "carta finita")

    def test_pagine_vuote(self):
        vuota = Servizio.objects.create(data=giorno_servizio())
        for nome, args in [("cassa:inizio", []), ("cassa:report", [vuota.pk]), ("cassa:statistiche", []), ("cassa:ordini", [vuota.pk])]:
            with self.subTest(pagina=nome):
                self.assertEqual(self.client.get(reverse(nome, args=args)).status_code, 200)

    def test_inizio_senza_serata_propone_di_crearla(self):
        risposta = self.client.get(reverse("cassa:inizio"))
        self.assertContains(risposta, "Crea la serata di oggi")
        self.assertContains(risposta, "articoli senza prezzo")

    def test_report_numeri(self):
        self.ordina([(self.grigliata, 2)], pagamento="carta")
        self.ordina([(self.coca, 2)])
        risposta = self.client.get(reverse("cassa:report", args=[self.serata.pk]))
        self.assertContains(risposta, "30,00 €")  # incassato
        self.assertContains(risposta, "25,00 €")  # carta
        risposta = self.client.get(reverse("cassa:report_csv", args=[self.serata.pk]))
        testo = risposta.content.decode("utf-8-sig")
        self.assertIn("Grigliata mista;Secondi;Cucina;2;0;25,00", testo)


class OrdinaJsonTest(BaseCassa):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.utente)

    def invia(self, **dati):
        corpo = {
            "token": str(uuid.uuid4()), "postazione": "ingresso", "nome": "Verdi", "tavolo": "7", "coperti": "2",
            "note": "", "pagamento": "contanti", "totale": 1500,
            "righe": [{"articolo": self.grigliata.pk, "quantita": 1, "note": ""}, {"articolo": self.coca.pk, "quantita": 1, "note": ""}],
        }
        corpo.update(dati)
        return self.client.post(reverse("cassa:ordina", args=[self.serata.pk]), json.dumps(corpo), content_type="application/json")

    def test_registra(self):
        risposta = self.invia()
        self.assertEqual(risposta.status_code, 200)
        dati = risposta.json()
        self.assertTrue(dati["ok"] and dati["nuovo"])
        self.assertEqual(dati["ordine"]["numero"], 1)
        self.assertEqual(dati["ordine"]["totaleTesto"], "15,00 €")
        self.assertEqual(dati["ordine"]["daStampare"], [])
        ordine = Ordine.objects.get()
        self.assertEqual((ordine.coperti, ordine.utente, ordine.tavolo), (2, self.utente, "7"))

    def test_stesso_token(self):
        token = str(uuid.uuid4())
        self.invia(token=token)
        dati = self.invia(token=token).json()
        self.assertTrue(dati["ok"])
        self.assertFalse(dati["nuovo"])
        self.assertEqual(Ordine.objects.count(), 1)

    def test_errori(self):
        risposta = self.invia(totale=1400)
        self.assertEqual(risposta.status_code, 409)
        self.assertTrue(risposta.json()["ricarica"])
        risposta = self.invia(tavolo="")
        self.assertEqual(risposta.json()["errori"], ["Manca il numero del tavolo."])
        for corpo in ({"token": "x"}, {"righe": [{"articolo": "a", "quantita": 1}]}, {"righe": "niente"}, {"postazione": "cucina"}, {"coperti": [1]}):
            with self.subTest(corpo=corpo):
                self.assertEqual(self.invia(**corpo).status_code, 400)
        risposta = self.client.post(reverse("cassa:ordina", args=[self.serata.pk]), "{", content_type="application/json")
        self.assertEqual(risposta.status_code, 400)
        self.assertFalse(Ordine.objects.exists())

    def test_stampante_dal_browser(self):
        Stampante.objects.filter(pk=self.st_cucina.pk).update(connessione=Connessione.BROWSER, indirizzo="")
        dati = self.invia().json()
        cucina = Stampa.objects.get(stampante=self.st_cucina)
        self.assertEqual(dati["ordine"]["daStampare"], [cucina.pk])

    def test_stato(self):
        self.invia()
        dati = self.client.get(reverse("cassa:stato", args=[self.serata.pk]) + "?postazione=ingresso").json()
        self.assertEqual(len(dati["ordini"]), 1)
        self.assertEqual(dati["ordini"][0]["stampa"], "coda")
        self.assertTrue(dati["stampa"]["avviso"])  # stampanti di rete ma nessun programma di stampa collegato
        self.assertFalse(dati["chiuso"])
        versione = dati["versione"]
        services.imposta_esaurito(articolo=self.coca, esaurito=True)
        self.assertNotEqual(self.client.get(reverse("cassa:stato", args=[self.serata.pk])).json()["versione"], versione)

    def test_esaurito(self):
        url = reverse("cassa:articolo_esaurito", args=[self.coca.pk])
        risposta = self.client.post(url, json.dumps({"esaurito": True}), content_type="application/json")
        self.assertEqual(risposta.json(), {"ok": True, "esaurito": True})
        self.assertTrue(Articolo.objects.get(pk=self.coca.pk).esaurito)
        self.assertEqual(self.client.post(url, json.dumps({"esaurito": "si"}), content_type="application/json").status_code, 400)

    def test_pagina_cassa(self):
        risposta = self.client.get(reverse("cassa:cassa", args=[self.serata.pk]))
        self.assertContains(risposta, "Grigliata mista")
        self.assertContains(risposta, "12,50 €")
        self.assertNotContains(risposta, "Funghi")  # senza prezzo
        dati = risposta.context["dati"]
        self.assertEqual(dati["articoli"][str(self.grigliata.pk)]["prezzo"], 1250)


class AnnullaViewTest(BaseCassa):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.utente)

    def test_annulla_e_correggi(self):
        ordine, _ = self.ordina([(self.grigliata, 2, "senza sale"), (self.funghi(), 1)])
        risposta = self.client.post(reverse("cassa:ordine_annulla", args=[ordine.pk]), {"motivo": "tavolo sbagliato", "rifai": "1"})
        destinazione = reverse("cassa:cassa", args=[self.serata.pk]) + f"?copia={ordine.pk}"
        self.assertRedirects(risposta, destinazione)
        ordine.refresh_from_db()
        self.assertTrue(ordine.annullato)
        Articolo.objects.filter(nome="Funghi").update(prezzo=None)
        copia = self.client.get(destinazione).context["dati"]["copia"]
        self.assertEqual(copia["righe"], [{"a": self.grigliata.pk, "q": 2, "n": "senza sale"}])
        self.assertEqual(copia["mancanti"], ["Funghi"])
        self.assertEqual((copia["nome"], copia["tavolo"]), ("Rossi", "12"))

    def funghi(self):
        Articolo.objects.filter(nome="Funghi").update(prezzo=D("4"))
        return Articolo.objects.get(nome="Funghi")

    def test_ristampa(self):
        ordine, _ = self.ordina([(self.grigliata, 1)])
        risposta = self.client.post(reverse("cassa:ordine_ristampa", args=[ordine.pk]), {"cosa": "cucina"})
        self.assertRedirects(risposta, reverse("cassa:ordine", args=[ordine.pk]))
        self.assertEqual(ordine.stampe.filter(copia=True).count(), 1)
        Stampante.objects.filter(pk=self.st_cucina.pk).update(connessione=Connessione.BROWSER, indirizzo="")
        risposta = self.client.post(reverse("cassa:ordine_ristampa", args=[ordine.pk]), {"cosa": "cucina"})
        self.assertTrue(risposta["Location"].startswith(reverse("cassa:stampe_browser") + "?id="))

    def test_riprova_stampa(self):
        ordine, _ = self.ordina([(self.grigliata, 1)])
        s = ordine.stampe.get()
        Stampa.objects.filter(pk=s.pk).update(stato=StatoStampa.ERRORE, errore="spenta")
        self.client.post(reverse("cassa:stampa_riprova", args=[s.pk]))
        s.refresh_from_db()
        self.assertEqual((s.stato, s.errore), (StatoStampa.IN_CODA, ""))


class GestioneTest(BaseCassa):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)

    def test_listino_prezzi_in_blocco(self):
        funghi = Articolo.objects.get(nome="Funghi")
        risposta = self.client.post(reverse("cassa:listino"), {
            f"presente-{funghi.pk}": "1", f"prezzo-{funghi.pk}": "4,50", f"attivo-{funghi.pk}": "1",
            f"presente-{self.coca.pk}": "1", f"prezzo-{self.coca.pk}": "3", f"esaurito-{self.coca.pk}": "1",
        })
        self.assertRedirects(risposta, reverse("cassa:listino"))
        funghi.refresh_from_db()
        self.coca.refresh_from_db()
        self.assertEqual((funghi.prezzo, funghi.attivo), (D("4.50"), True))
        self.assertEqual((self.coca.prezzo, self.coca.attivo, self.coca.esaurito), (D("3.00"), False, True))

    def test_listino_prezzo_non_valido_non_salva_niente(self):
        funghi = Articolo.objects.get(nome="Funghi")
        risposta = self.client.post(reverse("cassa:listino"), {
            f"presente-{funghi.pk}": "1", f"prezzo-{funghi.pk}": "4,50", f"attivo-{funghi.pk}": "1",
            f"presente-{self.coca.pk}": "1", f"prezzo-{self.coca.pk}": "tre", f"attivo-{self.coca.pk}": "1",
        })
        self.assertEqual(risposta.status_code, 200)
        self.assertContains(risposta, "Scrivi il prezzo in euro")
        funghi.refresh_from_db()
        self.assertIsNone(funghi.prezzo)

    def test_nuovo_articolo(self):
        risposta = self.client.post(reverse("cassa:articolo_nuovo"), {
            "nome": "Birra media", "categoria": self.coca.categoria_id, "reparto": "bar", "prezzo": "4,00",
            "quantita_bevanda": "1", "ordine": "0", "attivo": "on",
        })
        self.assertRedirects(risposta, reverse("cassa:listino"))
        self.assertEqual(Articolo.objects.get(nome="Birra media").prezzo, D("4.00"))
        risposta = self.client.post(reverse("cassa:articolo_nuovo"), {
            "nome": "birra MEDIA", "categoria": self.coca.categoria_id, "reparto": "bar", "prezzo": "4,001",
            "quantita_bevanda": "0", "ordine": "0",
        })
        self.assertContains(risposta, "Esiste già un articolo con questo nome.")
        self.assertContains(risposta, "al massimo due decimali")
        self.assertContains(risposta, "maggiore di zero")

    def test_impostazioni_e_stampanti(self):
        risposta = self.client.post(reverse("cassa:impostazioni"), {
            "monitor_cucina": "on", "stampante_cucina": self.st_cucina.pk, "messaggio_ricevuta": "Grazie",
        })
        self.assertRedirects(risposta, reverse("cassa:impostazioni"))
        imp = services.impostazioni()
        self.assertTrue(imp.monitor_cucina)
        self.assertIsNotNone(imp.monitor_cucina_dal)
        self.assertIsNone(imp.stampante_bar)
        risposta = self.client.post(reverse("cassa:stampante_nuova"), {"nome": "Cassa", "connessione": "rete", "indirizzo": "10.0.0.9:99999", "colonne": "42"})
        self.assertContains(risposta, "Scrivi l&#x27;indirizzo IP")
        risposta = self.client.post(reverse("cassa:stampante_nuova"), {"nome": "Cassa", "connessione": "browser", "indirizzo": "x", "colonne": "42", "attiva": "on"})
        self.assertRedirects(risposta, reverse("cassa:stampanti"))
        cassa = Stampante.objects.get(nome="Cassa")
        self.assertEqual(cassa.indirizzo, "")
        risposta = self.client.post(reverse("cassa:stampante_prova", args=[cassa.pk]))
        self.assertTrue(risposta["Location"].startswith(reverse("cassa:stampe_browser")))
        self.client.post(reverse("cassa:stampante_prova", args=[self.st_cucina.pk]))
        self.assertEqual(Stampa.objects.filter(documento="prova").count(), 2)


class MagazzinoTest(BaseCassa):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.utente)

    def test_serata_con_ordini_non_si_elimina(self):
        self.ordina([(self.coca, 1)])
        risposta = self.client.get(reverse("servizio_elimina", args=[self.serata.pk]))
        self.assertContains(risposta, "ordini registrati in cassa")
        self.client.post(reverse("servizio_elimina", args=[self.serata.pk]))
        self.assertTrue(Servizio.objects.filter(pk=self.serata.pk).exists())

    def test_nuova_serata_dalla_cassa(self):
        risposta = self.client.post(
            reverse("servizio_nuovo") + "?next=" + reverse("cassa:inizio"),
            {"data": "2026-09-25", "tipo": "sera", "nome": "", "note": "", "next": reverse("cassa:inizio")},
        )
        self.assertRedirects(risposta, reverse("cassa:inizio"))

    def test_serata_mostra_la_cassa(self):
        self.ordina([(self.coca, 1)])
        risposta = self.client.get(reverse("servizio_dettaglio", args=[self.serata.pk]))
        self.assertContains(risposta, "Report della cassa")

    def test_serata_terminata_blocca_la_cassa(self):
        Servizio.objects.filter(pk=self.serata.pk).update(chiuso_il=timezone.now())
        risposta = self.client.get(reverse("cassa:cassa", args=[self.serata.pk]))
        self.assertContains(risposta, "non si possono registrare ordini")
        self.assertTrue(self.client.get(reverse("cassa:stato", args=[self.serata.pk])).json()["chiuso"])
