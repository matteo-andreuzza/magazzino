"""Programma di stampa: invio alle stampanti e giro completo con il server."""

import importlib.util
import socket
import tempfile
import threading
from pathlib import Path
from unittest import mock

from django.test import LiveServerTestCase, SimpleTestCase

from cassa import services
from cassa.models import Connessione, Stampa, Stampante, StatoStampa

from .base import DatiCassa

PERCORSO = Path(__file__).resolve().parent.parent / "agente" / "agente_stampa.py"
spec = importlib.util.spec_from_file_location("agente_stampa", PERCORSO)
agente = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agente)


class StampanteFinta:
    """Server TCP che si comporta come una stampante di rete (porta 9100)."""

    def __init__(self):
        self.ricevuto = []
        self.presa = socket.socket()
        self.presa.bind(("127.0.0.1", 0))
        self.presa.listen()
        self.presa.settimeout(10)
        self.indirizzo = f"127.0.0.1:{self.presa.getsockname()[1]}"
        self.filo = threading.Thread(target=self._ascolta, daemon=True)
        self.filo.start()

    def _ascolta(self):
        while True:
            try:
                connessione, _ = self.presa.accept()
            except OSError:
                return
            with connessione:
                dati = b""
                while pezzo := connessione.recv(4096):
                    dati += pezzo
                self.ricevuto.append(dati)

    def chiudi(self):
        self.presa.close()


def porta_libera():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class InvioTest(SimpleTestCase):
    def test_rete(self):
        stampante = StampanteFinta()
        self.addCleanup(stampante.chiudi)
        agente.invia("rete", stampante.indirizzo, b"\x1b@ciao")
        stampante.filo.join(0.5)
        self.assertEqual(stampante.ricevuto, [b"\x1b@ciao"])

    def test_computer(self):
        with tempfile.TemporaryDirectory() as cartella:
            file = Path(cartella) / "lp0"
            agente.invia("computer", str(file), b"dati")
            self.assertEqual(file.read_bytes(), b"dati")

    def test_cups(self):
        with mock.patch.object(agente.subprocess, "run") as run:
            agente.invia("computer", "cups:Cucina", b"dati")
        self.assertEqual(run.call_args.args[0], ["lp", "-d", "Cucina", "-o", "raw"])
        self.assertEqual(run.call_args.kwargs["input"], b"dati")

    def test_errori_spiegati(self):
        indirizzo = f"127.0.0.1:{porta_libera()}"
        with self.assertRaises(ConnectionRefusedError) as e:
            agente.invia("rete", indirizzo, b"x")
        self.assertIn("rifiuta la connessione", agente.spiega_errore(e.exception, indirizzo))
        with self.assertRaises(FileNotFoundError) as e:
            agente.invia("computer", "/non/esiste/lp0", b"x")
        self.assertIn("non trovata", agente.spiega_errore(e.exception, "/non/esiste/lp0"))

    def test_prova_diretta_e_argomenti(self):
        with tempfile.TemporaryDirectory() as cartella, mock.patch("builtins.print"):
            file = Path(cartella) / "lp0"
            self.assertEqual(agente.main(["--prova", str(file)]), 0)
            self.assertIn(b"PROVA", file.read_bytes())
        with mock.patch.object(agente, "SERVER", ""), mock.patch.object(agente, "TOKEN", ""), \
                mock.patch.dict("os.environ", {}, clear=True), mock.patch("builtins.print"):
            self.assertEqual(agente.main([]), 2)


class GiroTest(SimpleTestCase):
    """Il ciclo principale con un server finto."""

    def server(self, lavori):
        finto = mock.Mock()
        finto.indirizzo = "http://sagra/"
        finto.nome = "pc"
        finto.lavori.return_value = lavori
        return finto

    def test_stampa_e_comunica_esito(self):
        finto = self.server([
            {"id": 1, "connessione": "rete", "indirizzo": "ok", "dati": "aGk=", "descrizione": "Comanda cucina"},
            {"id": 2, "connessione": "rete", "indirizzo": "rotta", "dati": "aGk=", "descrizione": "Comanda bar"},
        ])

        def invia(connessione, indirizzo, dati):
            if indirizzo == "rotta":
                raise ConnectionRefusedError()
            self.assertEqual(dati, b"hi")

        with mock.patch.object(agente, "invia", side_effect=invia), mock.patch("builtins.print"):
            stampate = agente.gira(finto, una_volta=True)
        self.assertEqual(set(stampate), {1})
        self.assertEqual(finto.esito.call_args_list[0].args, (1, True))
        self.assertEqual(finto.esito.call_args_list[1].args[:2], (2, False))
        self.assertIn("rifiuta la connessione", finto.esito.call_args_list[1].args[2])

    def test_server_irraggiungibile_non_si_ferma(self):
        finto = self.server([])
        finto.lavori.side_effect = OSError("rete assente")
        with mock.patch("builtins.print") as stampa:
            agente.gira(finto, una_volta=True)
        self.assertIn("Server non raggiungibile", stampa.call_args.args[0])
        finto.lavori.side_effect = agente.ChiaveNonValida()
        with mock.patch("builtins.print") as stampa:
            agente.gira(finto, una_volta=True)
        self.assertIn("Chiave non valida", stampa.call_args.args[0])


class GiroCompletoTest(DatiCassa, LiveServerTestCase):
    """Ordine in cassa -> programma di stampa -> stampante (finta) -> stampa segnata come stampata."""

    # I dati iniziali (menu, listino) vengono dalle migrazioni: vanno ripristinati dopo ogni test.
    serialized_rollback = True

    def setUp(self):
        self.crea_dati()
        self.stampante = StampanteFinta()
        self.addCleanup(self.stampante.chiudi)
        Stampante.objects.filter(pk=self.st_cucina.pk).update(indirizzo=self.stampante.indirizzo)
        Stampante.objects.filter(pk=self.st_bar.pk).update(connessione=Connessione.BROWSER, indirizzo="")
        self.imposta(stampante_cucina=Stampante.objects.get(pk=self.st_cucina.pk), stampante_bar=None)

    def test_comanda_stampata(self):
        ordine, _ = self.ordina([(self.grigliata, 2, "senza sale"), (self.coca, 1)])
        server = agente.Server(self.live_server_url, services.impostazioni().token_agente, "pc-test")
        with mock.patch("builtins.print"):
            agente.gira(server, una_volta=True)
        self.stampante.filo.join(0.5)
        self.assertEqual(len(self.stampante.ricevuto), 1)
        dati = self.stampante.ricevuto[0]
        self.assertTrue(dati.startswith(b"\x1b@"))
        self.assertIn(b"2 x Grigliata mista", dati)
        self.assertIn(b"> senza sale", dati)
        self.assertNotIn(b"Coca-Cola", dati)
        s = Stampa.objects.get(ordine=ordine)
        self.assertEqual((s.stato, s.agente), (StatoStampa.STAMPATA, "pc-test"))

    def test_chiave_sbagliata(self):
        server = agente.Server(self.live_server_url, "sbagliata", "pc-test")
        with self.assertRaises(agente.ChiaveNonValida):
            server.lavori()
