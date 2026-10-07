"""Stampanti normali di rete (laser o a getto d'inchiostro) con il protocollo IPP.

IPP è il modo standard di stampare in rete: è quello di AirPrint (iPhone, Mac) e di
Mopria (Android), e lo capiscono quasi tutte le stampanti di rete degli ultimi dieci
anni. Si parla con la stampante in HTTP sulla porta 631, senza driver: le si chiede
cosa sa stampare (Get-Printer-Attributes) e le si manda la pagina in uno dei formati
che accetta (Print-Job). Le pagine le prepara fogli.py.

Solo libreria standard: niente CUPS né driver da installare.
"""

import http.client
import socket
import ssl
import struct
from dataclasses import dataclass, field
from itertools import count
from urllib.parse import urlsplit

PORTA = 631
# Dove le stampanti ricevono i lavori: il primo è quello di IPP Everywhere / AirPrint.
PERCORSI = ("/ipp/print", "/ipp/printer", "/ipp", "/")
TIMEOUT = 10

PRINT_JOB = 0x0002
GET_PRINTER_ATTRIBUTES = 0x000B

# Tag dei gruppi di attributi
OPERAZIONE, LAVORO, FINE = 0x01, 0x02, 0x03
# Tag dei valori
INTEGER, BOOLEAN, ENUM = 0x21, 0x22, 0x23
RESOLUTION = 0x32
NAME, KEYWORD, URI, CHARSET, LANGUAGE, MIME = 0x42, 0x44, 0x45, 0x47, 0x48, 0x49

# Formati delle pagine, dal preferito. PWG raster e Apple raster (URF) sono quelli che le
# stampanti AirPrint e Mopria devono saper stampare; PDF e JPEG vanno bene come riserva.
FORMATI = ("image/pwg-raster", "image/urf", "application/pdf", "image/jpeg")

STATI_STAMPANTE = {3: "pronta", 4: "sta stampando", 5: "ferma"}

# printer-state-reasons che vale la pena dire a chi è alla cassa.
MOTIVI = {
    "media-empty": "carta finita",
    "media-needed": "manca la carta",
    "media-jam": "carta inceppata",
    "toner-empty": "toner finito",
    "toner-low": "toner quasi finito",
    "marker-supply-empty": "inchiostro o toner finito",
    "marker-supply-low": "inchiostro o toner quasi finito",
    "door-open": "sportello aperto",
    "cover-open": "coperchio aperto",
    "input-tray-missing": "manca il cassetto della carta",
    "output-area-full": "vassoio di uscita pieno",
    "paused": "in pausa",
    "offline": "non in linea",
    "shutdown": "in spegnimento",
}

# Quelli per cui la stampante non stampa: si avvisa anche in cassa.
BLOCCANTI = {
    "media-empty", "media-needed", "media-jam", "toner-empty", "marker-supply-empty", "door-open", "cover-open",
    "input-tray-missing", "output-area-full", "paused", "offline",
}
TESTI_BLOCCANTI = {MOTIVI[m] for m in BLOCCANTI}

STATI_IPP = {
    0x0400: "richiesta non valida",
    0x0401: "la stampante rifiuta la stampa (accesso vietato)",
    0x0402: "la stampante chiede una password: toglila dalle impostazioni della stampante",
    0x0403: "la stampante rifiuta la stampa (accesso vietato)",
    0x0406: "indirizzo della coda di stampa sbagliato",
    0x040A: "formato non accettato dalla stampante",
    0x040B: "valori non accettati dalla stampante",
    0x0500: "errore interno della stampante",
    0x0503: "versione di IPP non supportata",
    0x0505: "la stampante non accetta lavori in questo momento",
    0x0506: "la stampante non accetta lavori (è in pausa o in errore?)",
    0x0507: "la stampante è occupata",
}

_richieste = count(1)


class ErroreStampante(Exception):
    """Errore da mostrare così com'è a chi usa la cassa."""


class _NonQui(Exception):
    """La stampante non riceve a questo percorso: si prova il prossimo."""


class _VuoleTLS(Exception):
    pass


# ---------------------------------------------------------------------------
# Indirizzo
# ---------------------------------------------------------------------------


@dataclass
class Indirizzo:
    host: str
    porta: int = PORTA
    percorso: str | None = None  # None: da scoprire
    tls: bool = False

    @property
    def uri(self):
        host = f"[{self.host}]" if ":" in self.host else self.host
        porta = "" if self.porta == PORTA else f":{self.porta}"
        return f"{'ipps' if self.tls else 'ipp'}://{host}{porta}{self.percorso or PERCORSI[0]}"


def leggi_indirizzo(testo):
    """«192.168.1.50», «192.168.1.50:631», «ipp://192.168.1.50/ipp/print», «ipps://…», «http://…:631/…»."""
    testo = (testo or "").strip()
    if not testo:
        raise ErroreStampante("Manca l'indirizzo della stampante.")
    if "://" not in testo:
        testo = "ipp://" + testo
    pezzi = urlsplit(testo)
    if pezzi.scheme not in ("ipp", "ipps", "http", "https") or not pezzi.hostname:
        raise ErroreStampante(f"Indirizzo della stampante non valido: {testo}")
    try:
        porta = pezzi.port or (443 if pezzi.scheme == "https" else PORTA)
    except ValueError:
        raise ErroreStampante(f"Porta non valida nell'indirizzo {testo}") from None
    return Indirizzo(
        host=pezzi.hostname, porta=porta, percorso=pezzi.path or None, tls=pezzi.scheme in ("ipps", "https")
    )


# ---------------------------------------------------------------------------
# Codifica dei messaggi
# ---------------------------------------------------------------------------


def _attributo(tag, nome, *valori):
    dati = bytearray()
    for i, valore in enumerate(valori):
        n = nome.encode("ascii") if i == 0 else b""
        if tag in (INTEGER, ENUM):
            v = struct.pack(">i", valore)
        elif tag == BOOLEAN:
            v = b"\x01" if valore else b"\x00"
        else:
            v = str(valore).encode("utf-8")
        dati += struct.pack(">BH", tag, len(n)) + n + struct.pack(">H", len(v)) + v
    return bytes(dati)


def richiesta(operazione, attributi_operazione, attributi_lavoro=(), *, versione=(2, 0)):
    """Messaggio IPP (senza il documento, che va subito dopo)."""
    dati = bytearray(struct.pack(">BBHI", *versione, operazione, next(_richieste)))
    dati.append(OPERAZIONE)
    dati += b"".join(_attributo(*a) for a in attributi_operazione)
    if attributi_lavoro:
        dati.append(LAVORO)
        dati += b"".join(_attributo(*a) for a in attributi_lavoro)
    dati.append(FINE)
    return bytes(dati)


def _valore(tag, dati):
    if tag in (INTEGER, ENUM) and len(dati) == 4:
        return struct.unpack(">i", dati)[0]
    if tag == BOOLEAN and len(dati) == 1:
        return dati != b"\x00"
    if tag == RESOLUTION and len(dati) == 9:
        x, y, unita = struct.unpack(">iib", dati)
        return (x, y, unita)  # unità 3 = punti per pollice
    if 0x40 <= tag <= 0x4A:
        return dati.decode("utf-8", "replace")
    if tag < 0x20:  # valori «fuori banda»: non supportato, sconosciuto, nessun valore
        return None
    return dati


def leggi_messaggio(dati):
    """(codice, {nome: [valori]}, resto) di un messaggio IPP. Il codice è lo stato in una
    risposta, l'operazione in una richiesta; il resto è il documento che segue gli attributi.
    Gli attributi di tutti i gruppi finiscono nello stesso dizionario: per quello che serve
    qui non si sovrappongono."""
    if len(dati) < 8:
        raise ErroreStampante("Risposta della stampante incompleta.")
    codice = struct.unpack(">H", dati[2:4])[0]
    attributi, nome, i = {}, None, 8
    try:
        while i < len(dati):
            tag = dati[i]
            i += 1
            if tag == FINE:
                break
            if tag < 0x10:  # inizio di un gruppo
                nome = None
                continue
            lunghezza_nome = struct.unpack(">H", dati[i:i + 2])[0]
            i += 2
            if lunghezza_nome:
                nome = dati[i:i + lunghezza_nome].decode("ascii", "replace")
                i += lunghezza_nome
            lunghezza = struct.unpack(">H", dati[i:i + 2])[0]
            i += 2
            valore = dati[i:i + lunghezza]
            i += lunghezza
            if nome is not None:
                # Nelle collezioni (es. media-col) i membri si accodano al nome della collezione:
                # qui non si usano, basta non perdere il filo.
                attributi.setdefault(nome, []).append(_valore(tag, valore))
    except struct.error:
        raise ErroreStampante("Risposta della stampante non valida.") from None
    return codice, attributi, dati[i:]


def leggi_risposta(dati):
    """(stato, attributi) di una risposta IPP."""
    stato, attributi, _ = leggi_messaggio(dati)
    return stato, attributi


# ---------------------------------------------------------------------------
# Trasporto
# ---------------------------------------------------------------------------


def _connessione(indirizzo, timeout):
    if indirizzo.tls:
        # Le stampanti hanno quasi sempre un certificato fatto da sé: nella rete della sagra va bene.
        contesto = ssl.create_default_context()
        contesto.check_hostname = False
        contesto.verify_mode = ssl.CERT_NONE
        return http.client.HTTPSConnection(indirizzo.host, indirizzo.porta, timeout=timeout, context=contesto)
    return http.client.HTTPConnection(indirizzo.host, indirizzo.porta, timeout=timeout)


def _posta(indirizzo, percorso, corpo, timeout):
    connessione = _connessione(indirizzo, timeout)
    try:
        connessione.request(
            "POST", percorso, body=corpo,
            headers={"Content-Type": "application/ipp", "Accept": "application/ipp"},
        )
        risposta = connessione.getresponse()
        dati = risposta.read()
    finally:
        connessione.close()
    if risposta.status == 426:
        raise _VuoleTLS()
    if risposta.status in (404, 405):
        raise _NonQui()
    if risposta.status in (401, 403):
        raise ErroreStampante(STATI_IPP[0x0402 if risposta.status == 401 else 0x0403])
    if risposta.status != 200:
        raise ErroreStampante(f"La stampante ha risposto con un errore (HTTP {risposta.status}).")
    return dati


def spiega_errore_rete(errore, indirizzo):
    if isinstance(errore, ErroreStampante):
        return str(errore)
    if isinstance(errore, ConnectionRefusedError):
        return f"La stampante {indirizzo} rifiuta la connessione: l'indirizzo è giusto? È una stampante di rete?"
    if isinstance(errore, (socket.timeout, TimeoutError)):
        return f"La stampante {indirizzo} non risponde: è accesa e collegata alla rete?"
    if isinstance(errore, socket.gaierror):
        return f"Indirizzo della stampante non valido: {indirizzo}"
    if isinstance(errore, OSError) and errore.errno is not None:
        return f"La stampante {indirizzo} non è raggiungibile ({errore.strerror or errore})."
    return f"Errore con la stampante {indirizzo}: {errore}"


def _chiama(indirizzo, operazione, attributi_operazione, attributi_lavoro=(), documento=b"", timeout=TIMEOUT):
    """Manda la richiesta e restituisce (stato, attributi). Se il percorso non è noto lo scopre
    (e lo lascia in `indirizzo`), se la stampante vuole HTTPS ci passa."""
    percorsi = [indirizzo.percorso] if indirizzo.percorso else list(PERCORSI)
    for tentativo in range(2):
        for percorso in percorsi:
            prova = Indirizzo(indirizzo.host, indirizzo.porta, percorso, indirizzo.tls)
            try:
                # IPP 2.0, oppure 1.1 per le stampanti più vecchie.
                for versione in ((2, 0), (1, 1)):
                    corpo = richiesta(
                        operazione,
                        [
                            (CHARSET, "attributes-charset", "utf-8"),
                            (LANGUAGE, "attributes-natural-language", "it"),
                            (URI, "printer-uri", prova.uri),
                            *attributi_operazione,
                        ],
                        attributi_lavoro,
                        versione=versione,
                    )
                    stato, attributi = leggi_risposta(_posta(prova, percorso, corpo + documento, timeout))
                    if stato != 0x0503:
                        break
            except _NonQui:
                continue
            except _VuoleTLS:
                if indirizzo.tls or tentativo:
                    raise ErroreStampante("La stampante chiede una connessione cifrata che non riesce ad aprire.") from None
                indirizzo.tls = True
                break
            except http.client.HTTPException as e:
                raise ErroreStampante(f"La stampante {indirizzo.host} ha risposto in modo strano ({e}).") from None
            if stato == 0x0406 and len(percorsi) > 1:
                continue
            indirizzo.percorso = percorso
            return stato, attributi
        else:
            raise ErroreStampante(
                f"Alla stampante {indirizzo.host} non risponde nessuna coda IPP: scrivi l'indirizzo completo, "
                "es. ipp://192.168.1.50/ipp/print (si legge nella pagina web della stampante)."
            )
    raise ErroreStampante("La stampante chiede una connessione cifrata che non riesce ad aprire.")


def _errore_ipp(stato):
    return STATI_IPP.get(stato) or f"la stampante ha rifiutato la stampa (codice IPP {stato:#06x})"


# ---------------------------------------------------------------------------
# Cosa sa fare la stampante
# ---------------------------------------------------------------------------


@dataclass
class Capacita:
    indirizzo: Indirizzo
    modello: str = ""
    stato: int = 3
    motivi: list = field(default_factory=list)
    formati: list = field(default_factory=list)
    risoluzioni_pwg: list = field(default_factory=list)
    tipi_pwg: list = field(default_factory=list)
    urf: list = field(default_factory=list)
    supportati: set = field(default_factory=set)  # attributi del lavoro che la stampante conosce

    @property
    def problemi(self):
        """Motivi da segnalare, in italiano (es. «carta finita»)."""
        testi = []
        for motivo in self.motivi:
            base = motivo.rsplit("-", 1)[0] if motivo.endswith(("-error", "-warning", "-report")) else motivo
            if base in MOTIVI and MOTIVI[base] not in testi:
                testi.append(MOTIVI[base])
        return testi

    @property
    def descrizione_stato(self):
        testo = STATI_STAMPANTE.get(self.stato, "stato sconosciuto")
        if self.problemi:
            testo += " · " + ", ".join(self.problemi)
        return testo

    def scegli(self):
        """(formato, punti per pollice, tipo di colore) con cui mandare le pagine."""
        for formato in FORMATI:
            if formato not in self.formati:
                continue
            if formato == "image/pwg-raster":
                tipo = next((t for t in ("sgray_8", "srgb_8") if t in self.tipi_pwg), None)
                dpi = _risoluzione([x for x, y, u in self.risoluzioni_pwg if x == y and u == 3])
                if tipo and dpi:
                    return formato, dpi, tipo
            elif formato == "image/urf":
                tipo = next((t for t in ("W8", "SRGB24") if t in self.urf), None)
                dpi = _risoluzione(_risoluzioni_urf(self.urf))
                if tipo and dpi:
                    return formato, dpi, tipo
            else:
                return formato, 300, ""
        elenco = ", ".join(f for f in self.formati if f != "application/octet-stream") or "nessuno"
        raise ErroreStampante(
            f"La stampante non accetta nessuno dei formati che il programma sa preparare (accetta: {elenco}). "
            "Scegli «Dal browser della cassa» per stamparci con il suo driver."
        )


def _risoluzione(disponibili):
    """300 punti per pollice se c'è (bastano per il testo e le pagine sono leggere), sennò la più vicina."""
    if not disponibili:
        return None
    return min(disponibili, key=lambda d: (abs(d - 300), d))


def _risoluzioni_urf(urf):
    for voce in urf:
        if voce.startswith("RS"):
            return [int(r) for r in voce[2:].split("-") if r.isdigit()]
    return []


def interroga(testo_indirizzo, timeout=TIMEOUT):
    """Chiede alla stampante cosa sa fare. Solleva ErroreStampante (o OSError) se non risponde."""
    indirizzo = leggi_indirizzo(testo_indirizzo)
    richiesti = [
        "printer-make-and-model", "printer-state", "printer-state-reasons", "document-format-supported",
        "pwg-raster-document-resolution-supported", "pwg-raster-document-type-supported", "urf-supported",
        "media-supported", "sides-supported", "print-scaling-supported",
    ]
    stato, attributi = _chiama(
        indirizzo, GET_PRINTER_ATTRIBUTES,
        [(NAME, "requesting-user-name", "cassa"), (KEYWORD, "requested-attributes", *richiesti)],
        timeout=timeout,
    )
    if stato >= 0x0100:
        raise ErroreStampante(f"La stampante non dice cosa sa fare: {_errore_ipp(stato)}.")

    def testi(nome):
        return [v for v in attributi.get(nome, []) if isinstance(v, str)]

    supportati = {nome for nome in ("media", "sides", "print-scaling") if attributi.get(f"{nome}-supported")}
    return Capacita(
        indirizzo=indirizzo,
        modello=next(iter(testi("printer-make-and-model")), ""),
        stato=next((v for v in attributi.get("printer-state", []) if isinstance(v, int)), 3),
        motivi=[m for m in testi("printer-state-reasons") if m != "none"],
        formati=[f.lower() for f in testi("document-format-supported")],
        risoluzioni_pwg=[v for v in attributi.get("pwg-raster-document-resolution-supported", []) if isinstance(v, tuple)],
        tipi_pwg=testi("pwg-raster-document-type-supported"),
        urf=testi("urf-supported"),
        supportati=supportati,
    )


def stampa(capacita, documento, formato, *, nome_lavoro, media="", timeout=TIMEOUT):
    """Manda un documento già pronto. Restituisce il numero del lavoro sulla stampante."""
    lavoro = []
    if "sides" in capacita.supportati:
        lavoro.append((KEYWORD, "sides", "one-sided"))
    if media and "media" in capacita.supportati:
        lavoro.append((KEYWORD, "media", media))
    if formato in ("image/jpeg", "application/pdf") and "print-scaling" in capacita.supportati:
        lavoro.append((KEYWORD, "print-scaling", "fit"))
    operazione = [
        (NAME, "requesting-user-name", "cassa"),
        (NAME, "job-name", nome_lavoro[:100] or "Stampa"),
        (MIME, "document-format", formato),
    ]
    stato, attributi = _chiama(capacita.indirizzo, PRINT_JOB, operazione, lavoro, documento, timeout=timeout)
    if stato >= 0x0100:
        raise ErroreStampante(f"Non stampato: {_errore_ipp(stato)}.")
    return next((v for v in attributi.get("job-id", []) if isinstance(v, int)), 0)
