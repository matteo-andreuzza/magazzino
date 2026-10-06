"""Server MCP: protocollo, chiavi e strumenti, come li usa Claude Code."""

import io
import json
from decimal import Decimal as D

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from assistente.models import ChiaveMCP
from bar.models import MovimentoBar
from bar.tests.base import DatiBar
from magazzino import services as magazzino_services
from magazzino.models import Movimento, Prodotto, Ubicazione


class BaseMCP(DatiBar, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.crea_dati()
        cls.lettura, cls.testo_lettura = ChiaveMCP.crea(nome="Sola lettura", utente=cls.staff)
        cls.scrittura, cls.testo_scrittura = ChiaveMCP.crea(nome="Registra", utente=cls.staff, scrittura=True)

    def manda(self, messaggio, chiave=None, **intestazioni):
        chiave = self.testo_lettura if chiave is None else chiave
        if chiave:
            intestazioni["HTTP_AUTHORIZATION"] = f"Bearer {chiave}"
        return self.client.post(reverse("mcp"), json.dumps(messaggio), content_type="application/json", **intestazioni)

    def chiama(self, nome, argomenti=None, chiave=None):
        risposta = self.manda({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                               "params": {"name": nome, "arguments": argomenti or {}}}, chiave)
        self.assertEqual(risposta.status_code, 200)
        risultato = risposta.json()["result"]
        testo = risultato["content"][0]["text"]
        return risultato["isError"], (testo if risultato["isError"] else json.loads(testo))


class ProtocolloTest(BaseMCP):
    def test_inizio_della_conversazione(self):
        risposta = self.manda({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "claude-code", "version": "2"}}})
        dati = risposta.json()["result"]
        self.assertEqual(dati["protocolVersion"], "2025-06-18")
        self.assertIn("tools", dati["capabilities"])
        self.assertIn("panoramica", dati["instructions"])
        # Versione sconosciuta: il server propone la sua.
        dati = self.manda({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}}).json()
        self.assertEqual(dati["result"]["protocolVersion"], "2025-11-25")
        self.assertEqual(self.manda({"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code, 202)
        self.assertEqual(self.manda({"jsonrpc": "2.0", "id": 2, "method": "ping"}).json()["result"], {})

    def test_errori_di_protocollo(self):
        risposta = self.client.post(reverse("mcp"), "{non json", content_type="application/json",
                                    HTTP_AUTHORIZATION=f"Bearer {self.testo_lettura}")
        self.assertEqual(risposta.json()["error"]["code"], -32700)
        self.assertEqual(self.manda({"jsonrpc": "2.0", "id": 3, "method": "boh"}).json()["error"]["code"], -32601)
        dati = self.manda({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "boh"}}).json()
        self.assertEqual(dati["error"]["code"], -32602)
        self.assertEqual(self.client.get(reverse("mcp"), HTTP_AUTHORIZATION=f"Bearer {self.testo_lettura}").status_code, 405)
        self.assertEqual(self.manda({"jsonrpc": "2.0", "id": 5, "method": "ping"}, HTTP_MCP_PROTOCOL_VERSION="1999-01-01").status_code, 400)
        # Le versioni vecchie mandavano più messaggi insieme.
        dati = self.manda([{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "method": "notifications/initialized"}]).json()
        self.assertEqual(dati, [{"jsonrpc": "2.0", "id": 1, "result": {}}])

    def test_chiavi(self):
        self.assertEqual(self.manda({"jsonrpc": "2.0", "id": 1, "method": "ping"}, chiave="").status_code, 401)
        risposta = self.manda({"jsonrpc": "2.0", "id": 1, "method": "ping"}, chiave="sagra_sbagliata")
        self.assertEqual(risposta.status_code, 401)
        self.assertIn("Bearer", risposta["WWW-Authenticate"])
        # Da un sito estraneo (browser) no, anche con la chiave giusta.
        self.assertEqual(self.manda({"jsonrpc": "2.0", "id": 1, "method": "ping"}, HTTP_ORIGIN="http://cattivo.example").status_code, 403)
        # Revocata non funziona più.
        self.client.force_login(self.staff)
        self.client.post(reverse("assistente:chiave_revoca", args=[self.lettura.pk]))
        self.assertEqual(self.manda({"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code, 401)
        # Si salva solo l'impronta.
        self.assertNotIn(self.testo_scrittura, json.dumps(list(ChiaveMCP.objects.values()), default=str), "la chiave non va salvata in chiaro")
        self.scrittura.refresh_from_db()
        self.assertIsNone(self.scrittura.ultimo_uso)
        self.manda({"jsonrpc": "2.0", "id": 1, "method": "ping"}, chiave=self.testo_scrittura)
        self.scrittura.refresh_from_db()
        self.assertIsNotNone(self.scrittura.ultimo_uso)

    def test_pagina_delle_chiavi(self):
        self.client.force_login(self.utente)
        self.assertEqual(self.client.get(reverse("assistente:chiavi")).status_code, 403)
        self.client.force_login(self.staff)
        risposta = self.client.post(reverse("assistente:chiavi"), {"nome": "Portatile", "scrittura": "on"})
        testo = risposta.context["nuova"]["testo"]
        self.assertContains(risposta, f"claude mcp add --transport http magazzino-sagra http://testserver/mcp/ --header \"Authorization: Bearer {testo}\"")
        self.assertTrue(ChiaveMCP.trova(testo).scrittura)
        self.assertNotContains(self.client.get(reverse("assistente:chiavi")), testo)

    def test_il_server_via_stdio(self):
        righe = "\n".join(json.dumps(m) for m in [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "ubicazioni", "arguments": {}}},
        ]) + "\n"
        ingresso, uscita = io.StringIO(righe), io.StringIO()
        import sys
        vecchio = sys.stdin
        sys.stdin = ingresso
        try:
            call_command("server_mcp", utente=self.staff.username, stdout=uscita)
        finally:
            sys.stdin = vecchio
        risposte = [json.loads(r) for r in uscita.getvalue().splitlines()]
        self.assertEqual([r["id"] for r in risposte], [1, 2])
        self.assertFalse(risposte[1]["result"]["isError"])


class StrumentiTest(BaseMCP):
    def test_sola_lettura(self):
        nomi = [t["name"] for t in self.manda({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()["result"]["tools"]]
        self.assertIn("panoramica", nomi)
        self.assertNotIn("registra_carico", nomi)
        errore, testo = self.chiama("registra_carico", {"ubicazione": "1", "tipo": "acquisto", "voci": [{"prodotto": 1, "quantita": 1}]})
        self.assertTrue(errore)
        self.assertIn("sola lettura", testo)
        self.assertFalse(Movimento.objects.exists())
        tutti = self.manda({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, chiave=self.testo_scrittura).json()["result"]["tools"]
        scrivono = {t["name"] for t in tutti if not t["annotations"]["readOnlyHint"]}
        self.assertEqual(scrivono, {
            "registra_carico", "crea_prodotto", "preleva_per_cucina", "rientro_dalla_cucina", "carico_bar",
            "registra_fusti_serata", "annulla_fusti_serata", "tipo_di_fusto",
        })

    def test_letture(self):
        self.inventario((self.f30, "6"), (self.coca_b, "48"))
        self.attacca(self.f30)
        self.ordina([(self.birra_piccola, 10), (self.coca, 2)])
        for nome, argomenti in [
            ("panoramica", {}), ("cerca_prodotti", {"testo": "pat"}), ("giacenze", {}), ("ubicazioni", {}), ("menu", {}),
            ("serate", {}), ("serata", {"serata": self.serata.pk}), ("serata", {"serata": "2026-09-18"}), ("storico", {}),
            ("bar", {}), ("fusti_serata", {"serata": self.serata.pk}), ("consumi_bar", {}), ("listino", {}), ("statistiche_cassa", {}),
            ("prodotto", {"prodotto": Prodotto.objects.first().nome}),
        ]:
            with self.subTest(strumento=nome):
                errore, dati = self.chiama(nome, argomenti)
                self.assertFalse(errore, dati)
        _, dati = self.chiama("bar")
        self.assertEqual(dati["spinatrici"][0]["spine"][0]["attaccato"]["litri_rimasti"], 30)
        self.assertEqual(next(b for b in dati["bevande"] if b["nome"] == "Coca-Cola")["giacenza"], 46)
        _, dati = self.chiama("serata", {"serata": self.serata.pk})
        self.assertEqual(dati["cassa"]["totali"]["ordini"], 1)
        self.assertEqual(next(b for b in dati["bar"]["bevande"] if b["bevanda"] == "Birra bionda")["venduto"], 3.3)
        errore, testo = self.chiama("prodotto", {"prodotto": "nessun prodotto così"})
        self.assertTrue(errore)
        self.assertIn("Non trovo il prodotto", testo)

    def test_registrare(self):
        ubicazione = Ubicazione.objects.filter(attiva=True).first()
        patate = Prodotto.objects.get(nome="Patate")
        errore, dati = self.chiama("registra_carico", {
            "ubicazione": ubicazione.nome, "tipo": "acquisto", "voci": [{"prodotto": "patate", "quantita": "12,5"}],
        }, chiave=self.testo_scrittura)
        self.assertFalse(errore, dati)
        movimento = Movimento.objects.get()
        self.assertEqual((movimento.prodotto, movimento.quantita, movimento.operazione.utente), (patate, D("12.5"), self.staff))
        errore, testo = self.chiama("registra_carico", {
            "ubicazione": ubicazione.nome, "tipo": "acquisto", "voci": [{"prodotto": "patate", "quantita": -1}],
        }, chiave=self.testo_scrittura)
        self.assertTrue(errore)
        self.assertIn("maggiore di zero", testo)

        errore, dati = self.chiama("crea_prodotto", {"nome": "Radicchio", "categoria": "verdure", "unita": "kg"}, chiave=self.testo_scrittura)
        self.assertFalse(errore, dati)
        errore, testo = self.chiama("crea_prodotto", {"nome": "radicchio", "categoria": "verdure", "unita": "kg"}, chiave=self.testo_scrittura)
        self.assertTrue(errore)
        self.assertIn("Esiste già", testo)

        errore, dati = self.chiama("carico_bar", {"tipo": "carico", "voci": [
            {"cosa": "30 litri", "quantita": 2}, {"cosa": "Coca-Cola", "quantita": 24},
        ]}, chiave=self.testo_scrittura)
        self.assertFalse(errore, dati)
        self.assertEqual(MovimentoBar.objects.count(), 2)
        errore, testo = self.chiama("carico_bar", {"tipo": "carico", "voci": [{"cosa": "litri", "quantita": 1}]}, chiave=self.testo_scrittura)
        self.assertTrue(errore)
        self.assertIn("più tipi di fusto", testo)
        errore, dati = self.chiama("carico_bar", {"tipo": "carico", "voci": [
            {"cosa": "50 litri", "bevanda": "birra rossa", "quantita": 3},
        ]}, chiave=self.testo_scrittura)
        self.assertFalse(errore, dati)
        self.assertEqual(dati["righe"][0]["cosa"], "Fusto da 50 litri (Birra rossa)")

    def test_prelievi_e_rientri_della_cucina(self):
        patate = Prodotto.objects.get(nome="Patate")
        frigo, dispensa = Ubicazione.objects.get(nome="Frigo 1"), Ubicazione.objects.get(nome="Dispensa")
        magazzino_carico = {"tipo": "inventario", "voci": [{"prodotto": "Patate", "quantita": 10}]}
        self.chiama("registra_carico", {"ubicazione": "Frigo 1", **magazzino_carico}, chiave=self.testo_scrittura)
        # In un posto solo: non serve dire da dove.
        errore, dati = self.chiama("preleva_per_cucina", {"serata": self.serata.pk, "voci": [{"prodotto": "patate", "quantita": 4}]},
                                   chiave=self.testo_scrittura)
        self.assertFalse(errore, dati)
        self.assertEqual(dati["righe"][0]["ubicazione"], "Frigo 1")
        # In due posti: chiede da dove.
        self.chiama("registra_carico", {"ubicazione": "Dispensa", **magazzino_carico}, chiave=self.testo_scrittura)
        errore, testo = self.chiama("preleva_per_cucina", {"serata": self.serata.pk, "voci": [{"prodotto": "patate", "quantita": 1}]},
                                    chiave=self.testo_scrittura)
        self.assertTrue(errore)
        self.assertIn("c'è in più posti", testo)
        # Più di quello che c'è: rifiutato.
        errore, testo = self.chiama("preleva_per_cucina", {"serata": self.serata.pk, "voci": [
            {"prodotto": "patate", "quantita": 30, "ubicazione": "Dispensa"}]}, chiave=self.testo_scrittura)
        self.assertTrue(errore)
        self.assertIn("Non ho registrato niente", testo)
        errore, dati = self.chiama("rientro_dalla_cucina", {"serata": self.serata.pk, "voci": [
            {"prodotto": "patate", "quantita": 1.5, "ubicazione": "Frigo 1"}]}, chiave=self.testo_scrittura)
        self.assertFalse(errore, dati)
        self.assertEqual(magazzino_services.giacenze(prodotti=[patate.pk])[(patate.pk, frigo.pk)], D("7.5"))
        self.assertEqual(magazzino_services.giacenze(prodotti=[patate.pk])[(patate.pk, dispensa.pk)], D("10"))
