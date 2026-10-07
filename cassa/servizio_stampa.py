"""Servizio di stampa: il server manda da solo comande e ricevute alle stampanti di rete.

Gira come servizio «stampa» di Docker Compose (`manage.py servizio_stampa`) e parte
insieme all'app: sui computer e sui tablet della cassa non c'è niente da installare
né da avviare. Serve che il server sia nella rete della sagra, con le stampanti;
con l'app in cloud le stampanti non si raggiungono e serve il programma di stampa
(agente/agente_stampa.py) su un computer della sagra.

- stampanti normali (laser o a getto d'inchiostro): IPP, pagine A4 o A5 (ipp.py, fogli.py);
- stampanti termiche di rete: comandi ESC/POS sulla porta 9100.

Ogni stampante con qualcosa da stampare ha un suo thread: una stampante spenta non
ferma le altre e le stampe di una stessa stampante escono in ordine.
"""

import http.client
import socket
import threading
import time
from pathlib import Path

from django.db import DatabaseError, InterfaceError, close_old_connections, connection
from django.utils import timezone

from sistema.errori import registra_errore
from sistema.models import Battito
from sistema.services import e_casa, segnala

from . import fogli, ipp, stampa
from .models import CONNESSIONI_SERVER, Connessione, Stampante

VERSIONE = "1.0"
PORTA_TERMICHE = 9100
BATTITO = 5  # secondi tra un «sono acceso» e l'altro (in cassa l'avviso compare dopo 30)
DURATA_CAPACITA = 60  # secondi per cui si ricorda cosa sa fare una stampante
CONTROLLO = 60  # ogni quanti secondi si chiede a ogni stampante di rete se risponde e come sta
NOME = stampa.NOME_SERVER
# Errori che vengono dalla stampante o dalla rete (spenta, senza carta…): non sono errori del programma.
ERRORI_DI_STAMPANTE = (OSError, ipp.ErroreStampante, http.client.HTTPException)


def invia_termica(indirizzo, dati, timeout=10):
    host, porta = indirizzo, PORTA_TERMICHE
    if ":" in indirizzo:
        host, _, testo_porta = indirizzo.rpartition(":")
        porta = int(testo_porta)
    with socket.create_connection((host, porta), timeout=timeout) as collegamento:
        collegamento.sendall(dati)


def _rileva(stampante, timeout):
    """(risponde, stato in breve, modello, capacità IPP o None); None se il server non la controlla."""
    try:
        if stampante.connessione == Connessione.IPP:
            capacita = ipp.interroga(stampante.indirizzo, timeout=timeout)
            return True, capacita.descrizione_stato, capacita.modello, capacita
        if stampante.connessione == Connessione.RETE:
            host, _, porta = stampante.indirizzo.rpartition(":") if ":" in stampante.indirizzo else (stampante.indirizzo, "", "")
            with socket.create_connection((host, int(porta or PORTA_TERMICHE)), timeout=timeout):
                pass
            return True, f"risponde sulla porta {porta or PORTA_TERMICHE}", "", None
    except Exception as e:  # noqa: BLE001 - qualsiasi errore va spiegato
        return False, ipp.spiega_errore_rete(e, stampante.indirizzo), "", None
    return None


def salva_rilevazione(pk, risponde, testo, modello=""):
    """Il risultato del controllo, per il pannello e per gli avvisi in cassa (battito «stampante-N»)."""
    precedente = Battito.objects.filter(nome=f"stampante-{pk}").values_list("dettagli", flat=True).first() or {}
    segnala(f"stampante-{pk}", stato="ok" if risponde else "errore", messaggio=testo,
            dettagli={"risponde": risponde, "modello": modello or precedente.get("modello", "")})


def rileva(stampante, timeout=5):
    """Chiede alla stampante come sta e lo salva (per il pannello e per gli avvisi in cassa)."""
    risultato = _rileva(stampante, timeout)
    if risultato is not None and stampante.pk:
        salva_rilevazione(stampante.pk, *risultato[:3])
    return risultato


def controlla(stampante, timeout=5):
    """Per il pulsante «Controlla»: (va_bene, testo da mostrare)."""
    risultato = rileva(stampante, timeout=timeout)
    if risultato is None:
        return False, "Questa stampante non la controlla il server: fai una stampa di prova."
    risponde, testo, modello, capacita = risultato
    if not risponde:
        return False, testo
    if capacita is None:
        return True, f"La stampante {stampante.indirizzo} {testo}."
    try:
        formato, dpi, _ = capacita.scegli()
    except ipp.ErroreStampante as e:
        return False, str(e)
    return True, (
        f"{modello or 'Stampante'} risponde: {testo}. "
        f"Le pagine partono come {formato.split('/')[-1].upper()} a {dpi} punti per pollice ({capacita.indirizzo.uri})."
    )


class ServizioStampa:
    def __init__(self, *, attesa=0.5, scrivi=None, file_vivo=None):
        self.attesa = attesa
        self.scrivi = scrivi or (lambda testo: print(f"{timezone.localtime():%H:%M:%S}  {testo}", flush=True))
        self.file_vivo = Path(file_vivo) if file_vivo else None
        self.lavoratori = {}  # pk stampante -> thread
        self.ferma_fino = {}  # pk stampante -> time.monotonic(): dopo un errore si aspetta un po'
        self.capacita = {}  # indirizzo -> (ipp.Capacita, scadenza)
        self.controlli = {}  # pk stampante -> thread del controllo periodico
        self.ultimo_battito = None
        self.ultimo_controllo = None
        self.avviato_il = timezone.now()
        self.stampate = 0

    # -- invio -----------------------------------------------------------------

    def _capacita(self, indirizzo):
        ricordata = self.capacita.get(indirizzo)
        if ricordata and ricordata[1] > time.monotonic():
            return ricordata[0]
        capacita = ipp.interroga(indirizzo)
        self.capacita[indirizzo] = (capacita, time.monotonic() + DURATA_CAPACITA)
        return capacita

    def invia(self, lavoro):
        stampante = lavoro.stampante
        if stampante.connessione == Connessione.RETE:
            invia_termica(stampante.indirizzo, stampa.escpos(lavoro.contenuto, colonne=stampante.colonne, accenti=stampante.accenti))
            return
        if stampante.connessione != Connessione.IPP:
            raise ValueError(f"il server non stampa su «{stampante.get_connessione_display()}»")
        capacita = self._capacita(stampante.indirizzo)
        formato, dpi, tipo = capacita.scegli()
        documenti = fogli.documenti(
            lavoro.contenuto, formato=formato, dpi=dpi, tipo=tipo, carta=stampante.carta, colonne=stampante.colonne
        )
        media = fogli.CARTE[stampante.carta][2]
        for documento in documenti:
            ipp.stampa(capacita, documento, formato, nome_lavoro=self._nome_lavoro(lavoro), media=media)

    @staticmethod
    def _nome_lavoro(lavoro):
        nome = lavoro.descrizione
        if lavoro.ordine_id:
            nome += f" n. {lavoro.ordine.numero}"
        return nome

    # -- ciclo -------------------------------------------------------------------

    def _servi(self, pk):
        """Stampa quello che c'è per una stampante, una stampa alla volta, poi finisce."""
        while True:
            lavori = stampa.assegna(agente=NOME, connessioni=CONNESSIONI_SERVER, stampante=pk, massimo=1)
            if not lavori:
                return
            lavoro = lavori[0]
            cosa = f"{self._nome_lavoro(lavoro)} su {lavoro.stampante}"
            try:
                self.invia(lavoro)
            except Exception as e:  # noqa: BLE001 - l'errore si registra sulla stampa
                messaggio = ipp.spiega_errore_rete(e, lavoro.stampante.indirizzo)
                stampa.registra_esito(agente=NOME, stampa_id=lavoro.pk, ok=False, errore=messaggio)
                self.scrivi(f"NON stampato: {cosa}. {messaggio}")
                if isinstance(e, ERRORI_DI_STAMPANTE):
                    salva_rilevazione(pk, False, messaggio)
                else:
                    registra_errore("stampa", e, dove=cosa)  # un errore del programma: va nel pannello
                # La stampante si lascia in pace per un po'; le stampe restano in coda e si ritentano.
                self.capacita.pop(lavoro.stampante.indirizzo, None)
                self.ferma_fino[pk] = time.monotonic() + stampa.ATTESA_RIPROVA.total_seconds()
                return
            stampa.registra_esito(agente=NOME, stampa_id=lavoro.pk, ok=True)
            self.stampate += 1
            self.scrivi(f"Stampato: {cosa}")

    def _thread(self, pk):
        try:
            self._servi(pk)
        except (DatabaseError, InterfaceError) as e:
            self.scrivi(f"Database non raggiungibile ({e}): riprovo.")
        finally:
            connection.close()  # ogni thread ha la sua connessione al database

    def avvia(self, pk):
        thread = threading.Thread(target=self._thread, args=(pk,), name=f"stampante-{pk}", daemon=True)
        self.lavoratori[pk] = thread
        thread.start()

    def _controlla_stampante(self, pk):
        stampante = Stampante.objects.filter(pk=pk).first()
        if stampante is not None:
            rileva(stampante)

    def _thread_controllo(self, pk):
        try:
            self._controlla_stampante(pk)
        except (DatabaseError, InterfaceError):
            pass
        finally:
            connection.close()

    def avvia_controllo(self, pk):
        thread = threading.Thread(target=self._thread_controllo, args=(pk,), name=f"controllo-{pk}", daemon=True)
        self.controlli[pk] = thread
        thread.start()

    def controlla_stampanti(self, occupate):
        """Ogni minuto chiede alle stampanti di rete libere se rispondono (ognuna nel suo thread)."""
        for pk in Stampante.objects.filter(attiva=True, connessione__in=CONNESSIONI_SERVER).values_list("pk", flat=True):
            if pk in occupate or (pk in self.controlli and self.controlli[pk].is_alive()):
                continue
            self.avvia_controllo(pk)

    def batti(self, *, stato="ok", messaggio="Acceso."):
        stampa.segna_contatto(NOME, versione=VERSIONE)
        segnala("stampa", stato=stato, messaggio=messaggio, avviato_il=self.avviato_il,
                dettagli={"versione": VERSIONE, "stampate": self.stampate})
        if self.file_vivo:
            self.file_vivo.touch()

    def giro(self):
        """Un giro del ciclo principale: «sono acceso», un thread per ogni stampante con lavoro
        e, ogni minuto, il controllo delle stampanti."""
        adesso = time.monotonic()
        if self.ultimo_battito is None or adesso - self.ultimo_battito >= BATTITO:
            self.batti()
            self.ultimo_battito = adesso
        occupate = {pk for pk, thread in self.lavoratori.items() if thread.is_alive()}
        ferme = {pk for pk, fino in self.ferma_fino.items() if fino > adesso}
        for pk in sorted(stampa.stampanti_da_servire(agente=NOME, connessioni=CONNESSIONI_SERVER) - occupate - ferme):
            self.avvia(pk)
        if self.ultimo_controllo is None or adesso - self.ultimo_controllo >= CONTROLLO:
            self.ultimo_controllo = adesso
            self.controlla_stampanti(occupate | set(self.lavoratori))

    def riposa(self):
        """A casa non si stampa (le stampanti sono in sagra): il servizio dice solo che è acceso, e spento di proposito."""
        segnala("stampa", stato="spento", messaggio="Server di casa: qui non si stampa, le stampanti sono in sagra.",
                avviato_il=self.avviato_il)
        if self.file_vivo:
            self.file_vivo.touch()

    def gira(self):
        """Ciclo principale: non finisce mai (si ferma con Ctrl+C o con lo stop del container)."""
        a_casa = e_casa()
        if a_casa:
            self.scrivi("Server di casa (SERVER_NOME=casa): il servizio di stampa resta fermo.")
        else:
            self.scrivi(f"Servizio di stampa {VERSIONE} acceso: manda le stampe alle stampanti di rete.")
        guasto = False
        while True:
            try:
                close_old_connections()
                if a_casa:
                    self.riposa()
                    time.sleep(20)
                    continue
                self.giro()
                if guasto:
                    self.scrivi("Database di nuovo raggiungibile.")
                    guasto = False
            except (DatabaseError, InterfaceError) as e:
                # Database spento o non ancora pronto (es. migrazioni in corso all'avvio): si aspetta.
                if not guasto:
                    self.scrivi(f"Database non raggiungibile ({str(e).strip().splitlines()[0] if str(e).strip() else e}): riprovo da solo.")
                    guasto = True
                connection.close()
                time.sleep(5)
                continue
            except Exception as e:  # noqa: BLE001 - un errore del programma non deve fermare la stampa
                registra_errore("stampa", e, dove="ciclo del servizio di stampa")
                self.scrivi(f"Errore imprevisto: {e!r}. Vedi il pannello del sistema; riprovo.")
                time.sleep(5)
                continue
            time.sleep(self.attesa)
