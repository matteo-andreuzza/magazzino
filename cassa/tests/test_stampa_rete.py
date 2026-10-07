"""Stampa dal server: stampanti normali (IPP, fogli A4/A5) e termiche di rete.

Le stampanti sono finte: piccoli server nella stessa macchina dei test.
"""

import socket
import struct
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from PIL import features

from cassa import fogli, ipp, services, servizio_stampa, stampa
from cassa.models import AgenteStampa, Connessione, Stampa, Stampante, StatoStampa
from cassa.servizio_stampa import ServizioStampa

from .base import BaseCassa

COMANDA = [
    {"s": "centro", "t": "Sagra"},
    {"s": "titolo", "t": "CUCINA"},
    {"s": "evidenza", "t": "ASPORTO"},
    {"s": "grande", "t": "N. 57  TAV. 12"},
    {"s": "linea"},
    {"s": "voce", "q": 2, "t": "Grigliata mista", "alta": True},
    {"s": "nota", "t": "senza cipolla"},
    {"s": "coppia", "t": "TOTALE", "d": "34,50 €", "b": True},
]


# ---------------------------------------------------------------------------
# Stampanti finte
# ---------------------------------------------------------------------------


def _risoluzione(nome, *valori):
    dati = bytearray()
    for i, (x, y) in enumerate(valori):
        n = nome.encode() if i == 0 else b""
        dati += struct.pack(">BH", ipp.RESOLUTION, len(n)) + n + struct.pack(">HiiB", 9, x, y, 3)
    return bytes(dati)


class StampanteFinta:
    """Una stampante IPP: risponde a Get-Printer-Attributes e Print-Job e tiene i lavori ricevuti."""

    def __init__(self, *, formati=("image/pwg-raster",), percorso="/ipp/print", stato_stampa=0, motivi=("none",)):
        self.formati = formati
        self.percorso = percorso
        self.stato_stampa = stato_stampa
        self.motivi = motivi
        self.lavori = []  # (attributi, documento)
        finta = self

        class Gestore(BaseHTTPRequestHandler):
            def do_POST(self):
                corpo = self.rfile.read(int(self.headers["Content-Length"]))
                if self.path != finta.percorso:
                    self.send_response(404)
                    self.end_headers()
                    return
                risposta = finta.rispondi(corpo)
                self.send_response(200)
                self.send_header("Content-Type", "application/ipp")
                self.send_header("Content-Length", str(len(risposta)))
                self.end_headers()
                self.wfile.write(risposta)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Gestore)
        self.indirizzo = f"127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def chiudi(self):
        self.server.shutdown()
        self.server.server_close()

    def rispondi(self, corpo):
        operazione, attributi, documento = ipp.leggi_messaggio(corpo)
        id_richiesta = struct.unpack(">I", corpo[4:8])[0]
        testa = struct.pack(">BBHI", 2, 0, 0, id_richiesta) + bytes([ipp.OPERAZIONE])
        testa += ipp._attributo(ipp.CHARSET, "attributes-charset", "utf-8")
        testa += ipp._attributo(ipp.LANGUAGE, "attributes-natural-language", "it")
        if operazione == ipp.GET_PRINTER_ATTRIBUTES:
            gruppo = bytes([0x04])
            gruppo += ipp._attributo(ipp.NAME, "printer-make-and-model", "Laser Finta 3000")
            gruppo += ipp._attributo(ipp.ENUM, "printer-state", 3)
            gruppo += ipp._attributo(ipp.KEYWORD, "printer-state-reasons", *self.motivi)
            gruppo += ipp._attributo(ipp.MIME, "document-format-supported", "application/octet-stream", *self.formati)
            gruppo += _risoluzione("pwg-raster-document-resolution-supported", (300, 300), (600, 600))
            gruppo += ipp._attributo(ipp.KEYWORD, "pwg-raster-document-type-supported", "black_1", "sgray_8", "srgb_8")
            gruppo += ipp._attributo(ipp.KEYWORD, "urf-supported", "V1.4", "W8", "SRGB24", "RS300-600")
            gruppo += ipp._attributo(ipp.KEYWORD, "media-supported", "iso_a4_210x297mm", "iso_a5_148x210mm")
            gruppo += ipp._attributo(ipp.KEYWORD, "sides-supported", "one-sided")
            return testa + gruppo + bytes([ipp.FINE])
        self.lavori.append((attributi, documento))
        stato = self.stato_stampa
        risposta = bytearray(testa)
        risposta[2:4] = struct.pack(">H", stato)
        if stato < 0x0100:
            risposta += bytes([ipp.LAVORO]) + ipp._attributo(ipp.INTEGER, "job-id", len(self.lavori))
        return bytes(risposta) + bytes([ipp.FINE])


class TermicaFinta:
    """Una stampante termica di rete: accetta connessioni sulla sua porta e tiene quello che riceve."""

    def __init__(self):
        self.ricevuto = []
        self.presa = socket.create_server(("127.0.0.1", 0))
        self.indirizzo = f"127.0.0.1:{self.presa.getsockname()[1]}"
        threading.Thread(target=self._ascolta, daemon=True).start()

    def _ascolta(self):
        while True:
            try:
                collegamento, _ = self.presa.accept()
            except OSError:
                return
            with collegamento:
                dati = b""
                while pezzo := collegamento.recv(65536):
                    dati += pezzo
                if dati:  # il controllo periodico si collega e basta: non è una stampa
                    self.ricevuto.append(dati)

    def chiudi(self):
        self.presa.close()


def porta_chiusa():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"127.0.0.1:{s.getsockname()[1]}"


def decodifica_pwg(dati):
    """[(intestazione, pixel)] di un file PWG raster a 8 bit per pixel (per controllare la scrittura)."""
    assert dati[:4] == b"RaS2"
    pagine, i = [], 4
    while i < len(dati):
        h = dati[i:i + 1796]
        i += 1796
        larghezza, altezza = struct.unpack(">II", h[372:380])
        unita = struct.unpack(">I", h[388:392])[0] // 8
        pixel, y = bytearray(), 0
        while y < altezza:
            ripeti = dati[i] + 1
            i += 1
            riga = bytearray()
            while len(riga) < larghezza * unita:
                n = dati[i]
                i += 1
                if n < 128:
                    riga += dati[i:i + unita] * (n + 1)
                    i += unita
                else:
                    k = 257 - n
                    riga += dati[i:i + k * unita]
                    i += k * unita
            pixel += bytes(riga) * ripeti
            y += ripeti
        pagine.append((h, bytes(pixel)))
    return pagine


# ---------------------------------------------------------------------------
# Protocollo e fogli
# ---------------------------------------------------------------------------


class IppTest(SimpleTestCase):
    def test_messaggio_avanti_e_indietro(self):
        dati = ipp.richiesta(
            ipp.PRINT_JOB,
            [(ipp.CHARSET, "attributes-charset", "utf-8"), (ipp.KEYWORD, "requested-attributes", "a", "b")],
            [(ipp.INTEGER, "copies", 2), (ipp.BOOLEAN, "vero", True)],
        )
        codice, attributi, resto = ipp.leggi_messaggio(dati + b"DOCUMENTO")
        self.assertEqual(codice, ipp.PRINT_JOB)
        self.assertEqual(attributi["requested-attributes"], ["a", "b"])
        self.assertEqual((attributi["copies"], attributi["vero"]), ([2], [True]))
        self.assertEqual(resto, b"DOCUMENTO")

    def test_indirizzi(self):
        i = ipp.leggi_indirizzo(" 192.168.1.50 ")
        self.assertEqual((i.host, i.porta, i.percorso, i.tls, i.uri), ("192.168.1.50", 631, None, False, "ipp://192.168.1.50/ipp/print"))
        i = ipp.leggi_indirizzo("ipps://stampante.local:8443/ipp/print")
        self.assertEqual((i.host, i.porta, i.percorso, i.tls), ("stampante.local", 8443, "/ipp/print", True))
        self.assertEqual(ipp.leggi_indirizzo("192.168.1.50:8631").uri, "ipp://192.168.1.50:8631/ipp/print")
        for sbagliato in ("", "ftp://x", "ipp://:631", "192.168.1.50:porta"):
            with self.subTest(sbagliato), self.assertRaises(ipp.ErroreStampante):
                ipp.leggi_indirizzo(sbagliato)

    def test_scelta_del_formato(self):
        def capacita(**campi):
            return ipp.Capacita(indirizzo=ipp.leggi_indirizzo("x"), **campi)

        tutti = capacita(formati=list(ipp.FORMATI), tipi_pwg=["sgray_8"], risoluzioni_pwg=[(600, 600, 3), (300, 300, 3)])
        self.assertEqual(tutti.scegli(), ("image/pwg-raster", 300, "sgray_8"))
        # PWG senza i dati che servono: si passa al formato dopo.
        senza_tipo = capacita(formati=["image/pwg-raster", "image/urf"], urf=["W8", "RS600"])
        self.assertEqual(senza_tipo.scegli(), ("image/urf", 600, "W8"))
        self.assertEqual(capacita(formati=["image/urf"], urf=["SRGB24", "RS300-600"]).scegli(), ("image/urf", 300, "SRGB24"))
        self.assertEqual(capacita(formati=["application/pdf", "image/jpeg"]).scegli(), ("application/pdf", 300, ""))
        with self.assertRaisesMessage(ipp.ErroreStampante, "accetta: application/postscript"):
            capacita(formati=["application/octet-stream", "application/postscript"]).scegli()

    def test_problemi_in_italiano(self):
        c = ipp.Capacita(indirizzo=ipp.leggi_indirizzo("x"), stato=5, motivi=["media-empty-error", "toner-low-warning", "altro"])
        self.assertEqual(c.descrizione_stato, "ferma · carta finita, toner quasi finito")


class FogliTest(SimpleTestCase):
    def test_misure_e_solo_bianco_e_nero(self):
        a4 = fogli.pagine(COMANDA, carta="A4", dpi=300)
        a5 = fogli.pagine(COMANDA, carta="A5", dpi=300)
        self.assertEqual([p.size for p in a4], [(2480, 3508)])
        self.assertEqual([p.size for p in a5], [(1748, 2480)])
        self.assertEqual(set(a4[0].tobytes()), {0, 255})

    def test_biglietto_lungo_su_piu_pagine(self):
        lungo = COMANDA + [{"s": "voce", "q": 1, "t": f"Piatto {i}", "alta": True} for i in range(40)]
        pagine = fogli.pagine(lungo, carta="A5", dpi=100)
        self.assertGreater(len(pagine), 1)
        # In fondo a ogni pagina c'è il numero: nell'ultima riga utile c'è dell'inchiostro.
        larghezza, altezza, margine = fogli.misure("A5", 100)
        fondo = pagine[-1].crop((margine, altezza - margine - 15, larghezza - margine, altezza - margine))
        self.assertIn(0, fondo.tobytes())

    def test_pwg_raster(self):
        pagine = fogli.pagine(COMANDA, carta="A5", dpi=150)
        for tipo, unita in (("sgray_8", 1), ("srgb_8", 3)):
            with self.subTest(tipo):
                dati = fogli.pwg_raster(pagine, dpi=150, tipo=tipo, carta="A5")
                ((h, pixel),) = decodifica_pwg(dati)
                self.assertEqual(h[:9], b"PwgRaster")
                self.assertEqual(struct.unpack(">II", h[276:284]), (150, 150))
                self.assertEqual(struct.unpack(">II", h[352:360]), (420, 595))  # A5 in punti
                self.assertEqual(h[1732:1748], b"iso_a5_148x210mm")
                self.assertEqual(pixel, bytes(b for b in pagine[0].tobytes() for _ in range(unita)))

    def test_apple_raster_pdf_jpeg(self):
        urf = fogli.documenti(COMANDA, formato="image/urf", dpi=300, tipo="W8")[0]
        self.assertEqual(urf[:12], b"UNIRAST\x00\x00\x00\x00\x01")
        self.assertEqual(tuple(urf[12:15]), (8, 0, 1))  # 8 bit, grigio, solo fronte
        self.assertEqual(struct.unpack(">III", urf[24:36]), (2480, 3508, 300))
        pdf = fogli.documenti(COMANDA, formato="application/pdf", dpi=300)[0]
        self.assertTrue(pdf.startswith(b"%PDF"))
        if features.check("libtiff"):
            self.assertIn(b"CCITTFaxDecode", pdf)  # bianco e nero compresso: pochi KB
        lungo = COMANDA + [{"s": "voce", "q": 1, "t": f"Piatto {i}", "alta": True} for i in range(40)]
        self.assertEqual(len(fogli.documenti(lungo, formato="image/jpeg", dpi=100)), len(fogli.pagine(lungo, dpi=100)))


class StampanteFintaTest(SimpleTestCase):
    def setUp(self):
        self.finta = StampanteFinta()
        self.addCleanup(self.finta.chiudi)

    def test_interroga_e_stampa(self):
        capacita = ipp.interroga(self.finta.indirizzo)
        self.assertEqual(capacita.modello, "Laser Finta 3000")
        self.assertEqual(capacita.indirizzo.percorso, "/ipp/print")
        self.assertEqual(capacita.descrizione_stato, "pronta")
        documento = fogli.documenti(COMANDA, formato="image/pwg-raster", dpi=300, tipo="sgray_8")[0]
        self.assertEqual(ipp.stampa(capacita, documento, "image/pwg-raster", nome_lavoro="Comanda", media="iso_a4_210x297mm"), 1)
        ((attributi, ricevuto),) = self.finta.lavori
        self.assertEqual(ricevuto, documento)
        self.assertEqual(attributi["document-format"], ["image/pwg-raster"])
        self.assertEqual(attributi["media"], ["iso_a4_210x297mm"])
        self.assertEqual(attributi["printer-uri"], [f"ipp://{self.finta.indirizzo}/ipp/print"])

    def test_trova_la_coda_da_sola(self):
        self.finta.percorso = "/ipp/printer"
        self.assertEqual(ipp.interroga(self.finta.indirizzo).indirizzo.percorso, "/ipp/printer")
        with self.assertRaisesMessage(ipp.ErroreStampante, "nessuna coda IPP"):
            ipp.interroga(f"{self.finta.indirizzo}/ipp/sbagliata")

    def test_stampante_che_rifiuta(self):
        self.finta.stato_stampa = 0x0507
        capacita = ipp.interroga(self.finta.indirizzo)
        with self.assertRaisesMessage(ipp.ErroreStampante, "la stampante è occupata"):
            ipp.stampa(capacita, b"x", "image/pwg-raster", nome_lavoro="x")

    def test_stampante_spenta(self):
        indirizzo = porta_chiusa()
        with self.assertRaises(OSError) as errore:
            ipp.interroga(indirizzo, timeout=2)
        self.assertIn("rifiuta la connessione", ipp.spiega_errore_rete(errore.exception, indirizzo))


# ---------------------------------------------------------------------------
# Servizio di stampa
# ---------------------------------------------------------------------------


class ServizioDiProva(ServizioStampa):
    """Senza thread: nei test ogni stampante si serve subito, nella stessa transazione."""

    def __init__(self):
        self.righe = []
        super().__init__(scrivi=self.righe.append)

    def avvia(self, pk):
        self._servi(pk)

    def avvia_controllo(self, pk):
        self._controlla_stampante(pk)


class ServizioStampaTest(BaseCassa):
    def setUp(self):
        super().setUp()
        self.laser = StampanteFinta()
        self.termica = TermicaFinta()
        self.addCleanup(self.laser.chiudi)
        self.addCleanup(self.termica.chiudi)
        Stampante.objects.filter(pk=self.st_cucina.pk).update(connessione=Connessione.IPP, indirizzo=self.laser.indirizzo, carta="A5")
        Stampante.objects.filter(pk=self.st_bar.pk).update(indirizzo=self.termica.indirizzo)

    def test_stampa_su_laser_e_termica(self):
        ordine, _ = self.ordina([(self.grigliata, 1), (self.coca, 1)])
        servizio = ServizioDiProva()
        servizio.giro()
        self.assertEqual(
            set(ordine.stampe.values_list("reparto", "stato", "agente")),
            {("cucina", StatoStampa.STAMPATA, stampa.NOME_SERVER), ("bar", StatoStampa.STAMPATA, stampa.NOME_SERVER)},
        )
        ((attributi, documento),) = self.laser.lavori
        self.assertEqual(attributi["media"], ["iso_a5_148x210mm"])
        self.assertEqual(attributi["job-name"], ["Comanda cucina n. 1"])
        self.assertEqual(decodifica_pwg(documento)[0][0][1732:1748], b"iso_a5_148x210mm")
        (biglietto,) = self.termica.ricevuto
        self.assertTrue(biglietto.startswith(b"\x1b@"))
        self.assertIn(b"N. 1  TAV. 12", biglietto)
        self.assertIn("Stampato: Comanda cucina n. 1 su Cucina", servizio.righe)
        # Il servizio si fa vivo: in cassa niente avviso.
        self.assertTrue(AgenteStampa.objects.get(nome=stampa.NOME_SERVER).collegato)
        self.assertEqual(stampa.stato_stampa()["avviso"], "")

    def test_stampante_spenta_errore_e_pausa(self):
        Stampante.objects.filter(pk=self.st_cucina.pk).update(indirizzo=porta_chiusa())
        self.ordina([(self.grigliata, 1)])
        self.ordina([(self.paella, 1)])
        servizio = ServizioDiProva()
        servizio.giro()
        prima, seconda = Stampa.objects.order_by("pk")
        self.assertEqual(prima.stato, StatoStampa.ERRORE)
        self.assertIn("rifiuta la connessione", prima.errore)
        self.assertEqual(seconda.stato, StatoStampa.IN_CODA)  # la stampante si lascia in pace per un po'
        servizio.giro()
        self.assertEqual(Stampa.objects.get(pk=seconda.pk).stato, StatoStampa.IN_CODA)
        # Riaccesa (qui: indirizzo giusto), dopo la pausa escono tutte e due, in ordine.
        Stampante.objects.filter(pk=self.st_cucina.pk).update(indirizzo=self.laser.indirizzo)
        servizio.ferma_fino.clear()
        Stampa.objects.filter(pk=prima.pk).update(presa_il=timezone.now() - timedelta(seconds=20))
        servizio.giro()
        self.assertEqual(set(Stampa.objects.values_list("stato", flat=True)), {StatoStampa.STAMPATA})
        self.assertEqual([a["job-name"] for a, _ in self.laser.lavori], [["Comanda cucina n. 1"], ["Comanda cucina n. 2"]])

    def test_controllo_periodico_e_avviso_in_cassa(self):
        stampa.segna_contatto(stampa.NOME_SERVER)
        ServizioDiProva().giro()  # al primo giro controlla subito tutte le stampanti di rete
        laser = Stampante.objects.get(pk=self.st_cucina.pk)
        controllo = laser.controllo
        self.assertEqual((controllo.stato, controllo.messaggio), ("ok", "pronta"))
        self.assertEqual(controllo.dettagli, {"risponde": True, "modello": "Laser Finta 3000"})
        self.assertEqual(Stampante.objects.get(pk=self.st_bar.pk).controllo.stato, "ok")
        self.assertEqual(stampa.stato_stampa()["avviso"], "")
        # Carta finita: la cassa lo dice.
        self.laser.motivi = ("media-empty-error",)
        servizio_stampa.rileva(laser)
        self.assertIn("«Cucina»: carta finita", stampa.stato_stampa()["avviso"])
        # Spenta.
        Stampante.objects.filter(pk=self.st_bar.pk).update(indirizzo=porta_chiusa())
        servizio_stampa.rileva(Stampante.objects.get(pk=self.st_bar.pk))
        self.assertIn("la stampante «bar» non risponde", stampa.stato_stampa()["avviso"].lower())
        # Un controllo vecchio non conta più.
        from sistema.models import Battito

        Battito.objects.filter(nome__startswith="stampante-").update(ultimo_il=timezone.now() - timedelta(minutes=10))
        self.assertEqual(stampa.stato_stampa()["avviso"], "")

    def test_battito_nel_pannello_e_casa_che_non_stampa(self):
        from django.test import override_settings

        from sistema.models import Battito

        servizio = ServizioDiProva()
        servizio.giro()
        battito = Battito.objects.get(nome="stampa")
        self.assertEqual((battito.stato, battito.dettagli["versione"]), ("ok", servizio_stampa.VERSIONE))
        with override_settings(SERVER_NOME="casa"):
            servizio.riposa()
        self.assertEqual(Battito.objects.get(nome="stampa").stato, "spento")

    def test_errore_del_programma_va_nel_pannello(self):
        from unittest import mock

        from sistema.models import Errore

        self.ordina([(self.grigliata, 1)])
        with mock.patch.object(ServizioDiProva, "invia", side_effect=KeyError("colonne")):
            ServizioDiProva().giro()
        errore = Errore.objects.get()
        self.assertEqual((errore.origine, errore.tipo), ("stampa", "KeyError"))
        self.assertIn("Comanda cucina n. 1", errore.dove)

    def test_il_programma_di_stampa_non_riceve_le_laser(self):
        self.ordina([(self.grigliata, 1), (self.coca, 1)])
        lavori = stampa.prendi_lavori(agente="pc")
        self.assertEqual([l["connessione"] for l in lavori], [Connessione.RETE])
        self.assertIsNone(Stampa.objects.get(reparto="cucina").presa_il)

    def test_stato_stampa_per_tipo_di_stampante(self):
        avviso_server = "Il servizio di stampa non è attivo: le comande restano in coda. Avvisa chi gestisce il computer del programma."
        self.assertEqual(stampa.stato_stampa()["avviso"], avviso_server)
        # Per le termiche basterebbe il programma di stampa, ma per le laser serve il server.
        stampa.segna_contatto("pc")
        self.assertEqual(stampa.stato_stampa()["avviso"], avviso_server)
        stampa.segna_contatto(stampa.NOME_SERVER)
        self.assertEqual(stampa.stato_stampa()["avviso"], "")
        Stampante.objects.filter(pk=self.st_bar.pk).update(connessione=Connessione.COMPUTER, indirizzo="/dev/usb/lp0")
        AgenteStampa.objects.filter(nome="pc").update(ultimo_contatto=timezone.now() - timedelta(minutes=5))
        self.assertEqual(stampa.stato_stampa()["avviso"], "Il programma di stampa non è collegato: le comande restano in coda. Avvisa chi gestisce il computer delle stampanti.")
        Stampante.objects.update(attiva=False)
        self.assertEqual(stampa.stato_stampa()["avviso"], "")


class PaginaStampantiTest(BaseCassa):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)
        self.laser = StampanteFinta(motivi=("media-empty-error",))
        self.addCleanup(self.laser.chiudi)
        Stampante.objects.filter(pk=self.st_cucina.pk).update(connessione=Connessione.IPP, indirizzo=self.laser.indirizzo)

    def test_controlla(self):
        risposta = self.client.post(reverse("cassa:stampante_controlla", args=[self.st_cucina.pk]), follow=True)
        self.assertContains(risposta, "Laser Finta 3000 risponde: pronta · carta finita.")
        self.assertContains(risposta, "PWG-RASTER a 300 punti per pollice")
        Stampante.objects.filter(pk=self.st_cucina.pk).update(indirizzo=porta_chiusa())
        risposta = self.client.post(reverse("cassa:stampante_controlla", args=[self.st_cucina.pk]), follow=True)
        self.assertContains(risposta, "rifiuta la connessione")
        termica = TermicaFinta()
        self.addCleanup(termica.chiudi)
        Stampante.objects.filter(pk=self.st_bar.pk).update(indirizzo=termica.indirizzo)
        risposta = self.client.post(reverse("cassa:stampante_controlla", args=[self.st_bar.pk]), follow=True)
        self.assertContains(risposta, "risponde sulla porta")

    def test_stato_del_servizio(self):
        risposta = self.client.get(reverse("cassa:stampanti"))
        self.assertContains(risposta, "Il servizio di stampa del server non è acceso")
        stampa.segna_contatto(stampa.NOME_SERVER)
        risposta = self.client.get(reverse("cassa:stampanti"))
        self.assertContains(risposta, "servizio di stampa acceso")
        self.assertNotContains(risposta, "(server)")  # non compare tra i computer con il programma di stampa

    def test_nuova_stampante_normale(self):
        dati = {"nome": "Ufficio", "connessione": "ipp", "indirizzo": "192.168.1.70", "carta": "A5", "colonne": 42, "attiva": "on"}
        risposta = self.client.post(reverse("cassa:stampante_nuova"), dati)
        self.assertRedirects(risposta, reverse("cassa:stampanti"))
        self.assertEqual(Stampante.objects.get(nome="Ufficio").carta, "A5")
        dati.update(nome="Sbagliata", indirizzo="ftp://x")
        risposta = self.client.post(reverse("cassa:stampante_nuova"), dati)
        self.assertContains(risposta, "Scrivi l&#x27;indirizzo IP")

    def test_nome_riservato_al_server(self):
        risposta = self.client.post(
            reverse("cassa:api_agente_lavori"), {"agente": stampa.NOME_SERVER}, content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {services.impostazioni().token_agente}",
        )
        self.assertEqual(risposta.status_code, 400)


class ControllaSenzaReteTest(SimpleTestCase):
    def test_stampante_dal_browser(self):
        va_bene, testo = servizio_stampa.controlla(Stampante(nome="x", connessione=Connessione.BROWSER))
        self.assertFalse(va_bene)
        self.assertIn("stampa di prova", testo)
