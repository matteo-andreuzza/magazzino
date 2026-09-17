#!/usr/bin/env python3
"""Programma di stampa della cassa della sagra.

Chiede al server le comande da stampare e le manda alle stampanti termiche
(comandi ESC/POS): stampanti di rete (indirizzo IP, porta 9100) oppure
collegate a questo computer. Deve restare acceso durante il servizio.

Serve solo Python 3.8 o più recente, nessun'altra installazione.

Uso:
    python3 agente_stampa.py
        (scaricato dall'app è già configurato; su Windows basta un doppio clic)
    python3 agente_stampa.py --server https://sagra.esempio.it/ --token CHIAVE --nome cassa
    python3 agente_stampa.py --prova 192.168.1.50
        stampa di prova diretta, senza server: per controllare che la stampante risponda
    python3 agente_stampa.py --prova /dev/usb/lp0
    python3 agente_stampa.py --prova cups:Cucina

La chiave si può passare anche con la variabile d'ambiente CASSA_TOKEN.
"""

import argparse
import base64
import json
import os
import platform
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

# Compilati dall'app quando si scarica il programma.
SERVER = ""
TOKEN = ""

VERSIONE = "1.0"
ATTESA = 2  # secondi tra una richiesta e l'altra quando non c'è niente da stampare
ATTESA_MASSIMA = 30  # se il server non risponde si riprova sempre più piano, fino a qui
PORTA_PREDEFINITA = 9100


def scrivi(messaggio):
    print(f"{datetime.now():%H:%M:%S}  {messaggio}", flush=True)


# ---------------------------------------------------------------------------
# Invio alle stampanti
# ---------------------------------------------------------------------------


def invia_rete(indirizzo, dati, timeout=10):
    host, porta = indirizzo, PORTA_PREDEFINITA
    if ":" in indirizzo:
        host, _, testo_porta = indirizzo.rpartition(":")
        porta = int(testo_porta)
    with socket.create_connection((host, porta), timeout=timeout) as connessione:
        connessione.sendall(dati)


def invia_computer(indirizzo, dati):
    if indirizzo.lower().startswith("cups:"):
        subprocess.run(
            ["lp", "-d", indirizzo[5:], "-o", "raw"], input=dati, check=True, capture_output=True, timeout=30
        )
        return
    # /dev/usb/lp0 su Linux, \\localhost\NomeCondivisione su Windows (stampante condivisa).
    with open(indirizzo, "wb") as stampante:
        stampante.write(dati)


def invia(connessione, indirizzo, dati):
    if connessione == "rete":
        invia_rete(indirizzo, dati)
    elif connessione == "computer":
        invia_computer(indirizzo, dati)
    else:
        raise ValueError(f"tipo di stampante sconosciuto: {connessione}")


def spiega_errore(errore, indirizzo):
    if isinstance(errore, ConnectionRefusedError):
        return f"La stampante {indirizzo} rifiuta la connessione: è accesa? L'indirizzo è giusto?"
    if isinstance(errore, (socket.timeout, TimeoutError)):
        return f"La stampante {indirizzo} non risponde: è accesa e collegata alla rete?"
    if isinstance(errore, socket.gaierror):
        return f"Indirizzo della stampante non valido: {indirizzo}"
    if isinstance(errore, FileNotFoundError):
        return f"Stampante non trovata su questo computer: {indirizzo}"
    if isinstance(errore, PermissionError):
        return f"Permesso negato per {indirizzo} (su Linux aggiungi l'utente al gruppo «lp»)."
    if isinstance(errore, subprocess.CalledProcessError):
        dettaglio = (errore.stderr or b"").decode(errors="replace").strip()
        return f"Errore di stampa con CUPS: {dettaglio or errore}"
    if isinstance(errore, OSError) and errore.errno is not None:
        return f"Errore con la stampante {indirizzo}: {errore.strerror or errore}"
    return f"Errore con la stampante {indirizzo}: {errore}"


def prova_diretta(indirizzo):
    """Stampa di prova senza passare dal server."""
    dati = (
        b"\x1b@\x1ba\x01\x1b!\x38PROVA\n\x1b!\x00"
        + f"Programma di stampa {VERSIONE}\n{datetime.now():%d/%m/%Y %H:%M}\n".encode("ascii")
        + b"Se leggi questo, la stampante funziona.\n\x1bd\x04\x1dVB\x00"
    )
    connessione = "computer" if ("/" in indirizzo or "\\" in indirizzo or indirizzo.lower().startswith("cups:")) else "rete"
    try:
        invia(connessione, indirizzo, dati)
    except Exception as e:  # noqa: BLE001 - qualsiasi errore va spiegato a chi prova
        scrivi(spiega_errore(e, indirizzo))
        return 1
    scrivi(f"Stampa di prova inviata a {indirizzo}.")
    return 0


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


class ChiaveNonValida(Exception):
    pass


class Server:
    def __init__(self, indirizzo, token, nome):
        self.indirizzo = indirizzo.rstrip("/") + "/"
        self.token = token
        self.nome = nome

    def chiama(self, percorso, dati):
        richiesta = urllib.request.Request(
            self.indirizzo + percorso,
            data=json.dumps(dati).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token}",
                "User-Agent": f"agente-stampa-sagra/{VERSIONE}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(richiesta, timeout=20) as risposta:
                return json.loads(risposta.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise ChiaveNonValida() from e
            raise

    def lavori(self):
        risposta = self.chiama(
            "cassa/api/agente/lavori/", {"agente": self.nome, "versione": VERSIONE, "massimo": 5}
        )
        return risposta.get("lavori", [])

    def esito(self, stampa_id, ok, errore=""):
        # Ritenta qualche volta: se l'esito si perde, la stampa verrebbe ripresa da un altro giro.
        for tentativo in range(3):
            try:
                self.chiama("cassa/api/agente/esito/", {"agente": self.nome, "id": stampa_id, "ok": ok, "errore": errore})
                return True
            except ChiaveNonValida:
                raise
            except (urllib.error.URLError, OSError, ValueError):
                time.sleep(1 + tentativo)
        return False


def gira(server, attesa=ATTESA, una_volta=False):
    """Ciclo principale. Restituisce solo con una_volta=True (per le prove)."""
    stampate = {}  # id -> quando: se il server ce la ripropone non si stampa due volte
    stato = ""
    pausa_errore = attesa
    while True:
        pausa = attesa
        try:
            lavori = server.lavori()
            if stato != "ok":
                scrivi(f"Collegato a {server.indirizzo} come «{server.nome}». In attesa di comande…")
                stato = "ok"
            pausa_errore = attesa
            for lavoro in lavori:
                descrizione = lavoro.get("descrizione", f"stampa {lavoro['id']}")
                if lavoro["id"] in stampate:
                    server.esito(lavoro["id"], True)
                    continue
                try:
                    invia(lavoro["connessione"], lavoro["indirizzo"], base64.b64decode(lavoro["dati"]))
                except Exception as e:  # noqa: BLE001 - l'errore va comunicato al server
                    messaggio = spiega_errore(e, lavoro["indirizzo"])
                    scrivi(f"NON stampato: {descrizione}. {messaggio}")
                    server.esito(lavoro["id"], False, messaggio)
                else:
                    stampate[lavoro["id"]] = time.monotonic()
                    scrivi(f"Stampato: {descrizione}")
                    server.esito(lavoro["id"], True)
            if lavori:
                pausa = 0.2
            # Si ricordano le stampe dell'ultima ora.
            limite = time.monotonic() - 3600
            stampate = {k: v for k, v in stampate.items() if v > limite}
        except ChiaveNonValida:
            if stato != "chiave":
                scrivi("Chiave non valida: riscarica il programma di stampa dall'app (Cassa → Stampanti).")
                stato = "chiave"
            pausa = ATTESA_MASSIMA
        except (urllib.error.URLError, OSError, ValueError) as e:
            if stato != "rete":
                motivo = getattr(e, "reason", None) or e
                scrivi(f"Server non raggiungibile ({motivo}). Riprovo da solo…")
                stato = "rete"
            pausa_errore = min(pausa_errore * 2, ATTESA_MASSIMA)
            pausa = pausa_errore
        if una_volta:
            return stampate
        time.sleep(pausa)


def main(argomenti=None):
    parser = argparse.ArgumentParser(description="Programma di stampa della cassa della sagra.")
    parser.add_argument("--server", default=os.environ.get("CASSA_SERVER") or SERVER, help="indirizzo dell'app")
    parser.add_argument("--token", default=os.environ.get("CASSA_TOKEN") or TOKEN, help="chiave (Cassa → Stampanti)")
    parser.add_argument("--nome", default=platform.node() or "computer", help="nome di questo computer")
    parser.add_argument("--attesa", type=float, default=ATTESA, help="secondi tra una richiesta e l'altra")
    parser.add_argument("--prova", metavar="INDIRIZZO", help="stampa di prova diretta su una stampante")
    opzioni = parser.parse_args(argomenti)

    if opzioni.prova:
        return prova_diretta(opzioni.prova)
    if not opzioni.server or not opzioni.token:
        scrivi("Manca l'indirizzo del server o la chiave: scarica il programma dall'app (Cassa → Stampanti).")
        if os.name == "nt":
            input("Premi Invio per chiudere…")
        return 2
    scrivi(f"Programma di stampa {VERSIONE}. Per fermarlo premi Ctrl+C.")
    try:
        gira(Server(opzioni.server, opzioni.token, opzioni.nome[:60]), attesa=max(opzioni.attesa, 0.5))
    except KeyboardInterrupt:
        scrivi("Fermato.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
