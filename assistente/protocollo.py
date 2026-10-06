"""Il protocollo MCP (Model Context Protocol): JSON-RPC 2.0 con i metodi che servono a Claude.

Lo stesso codice serve il collegamento via HTTP (views.mcp, per Claude Code
su un altro computer o con l'app in cloud) e quello via stdin/stdout
(comando server_mcp, per Claude Code sullo stesso computer dei container).

Il server non ha sessioni né notifiche: ogni richiesta è indipendente.
"""

import json
import logging

from django.conf import settings
from django.db import DatabaseError

from .strumenti import STRUMENTI, Contesto, ErroreStrumento

log = logging.getLogger(__name__)

VERSIONI = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]

ISTRUZIONI = """\
Gestionale della sagra: magazzino della cucina, magazzino del bar e cassa, sullo stesso database.

- Magazzino della cucina: prodotti (con unità: kg, l, pz, conf…) in ubicazioni (frighi, congelatori, dispense).
  Le giacenze sono la somma dei movimenti: carichi, prelievi per la cucina («Messo a cucinare»), rientri,
  spostamenti, scarti, correzioni. Non si può prendere più di quello che c'è.
- Le serate (o i pranzi) sono i giorni di servizio. Dopo mezzanotte, fino alle 5, vale la serata del giorno prima.
- Cassa: ordini con incassi (contanti, carta) e omaggi; il listino ha gli articoli con prezzo e reparto (cucina o bar).
- Bar: lattine e bottiglie escono da sole con le vendite della cassa. Birra e vino alla spina sono nei fusti: al
  bancone ci sono le spinatrici (Birra con le spine Bionda e Rossa, Vino con Bianco e Rosso) e a ogni spina è
  attaccato un fusto alla volta. Ogni bevanda ha i suoi tipi di fusto (es. «Fusto da 50 litri», «Largo argento»,
  «Alto nero», «Azzurro») con litri e tara (peso da vuoto) decisi dall'amministrazione. I litri usciti si
  ricavano dal peso: un fusto nuovo parte da pieno, uno finito arriva alla tara.

Fine serata al bar (strumenti fusti_serata e registra_fusti_serata). L'utente racconta, spina per spina, cosa è
successo durante la serata e quanto pesano i fusti attaccati adesso. Traduci così:
- «bionda in uso: 20 kg» / «rossa non cambiata, 6,60 kg» → finiti 0, peso_kg 20 (o 6.6);
- «cambiato la bionda, nuovo da 26 kg» → finiti 1, peso_kg 26 (tipo: lo stesso di prima, se non lo dice);
- «consumati 2 da 50 L bionda» (quello di ieri finito, un altro attaccato e finito, poi quello di adesso) →
  finiti 2, tipo «50 litri»; il peso è quello del fusto attaccato adesso;
- «bianco 26 largo argento», «rosso 23,9 alto nero» dopo «cambiato» → finiti 1, tipo «largo argento» / «alto nero»;
  il colore indica il tipo di fusto («azzurro 22 kg (rosso)» = spina Rosso, tipo Azzurro);
- alla prima serata (nessun fusto attaccato prima) «in uso: 20 kg» → finiti 0: si attacca un fusto nuovo e lo si pesa.
Prima guarda la serata con fusti_serata, poi chiama registra_fusti_serata con prova=true, mostra all'utente il
riepilogo (una riga per spina) e gli avvisi, e registra solo dopo la sua conferma. Se lo strumento rifiuta (pesa
più di ieri ma «non cambiato», tipo diverso da quello attaccato, tipo ambiguo) non indovinare: chiedi. Le serate
vanno registrate in ordine (venerdì, poi sabato, poi domenica): si modifica solo l'ultima con i fusti; per
correggerne una prima bisogna annullare quelle dopo (annulla_fusti_serata) e rifarle. Ripetere la stessa
registrazione non fa danni: corregge.

Comincia da «panoramica». Le quantità sono sempre nell'unità del prodotto. Gli strumenti che registrano
qualcosa (carichi, prelievi, fusti, prodotti nuovi) vanno usati solo dopo che l'utente ha confermato cosa
registrare; gli errori si correggono dall'app (Storico → annulla) o rifacendo la registrazione dei fusti.
Per carichi, prelievi e rientri passa sempre un id_registrazione tuo: se devi ripetere la chiamata (rete
caduta, risposta lenta) usa lo stesso, così non si registra due volte.
"""


class ErroreRichiesta(Exception):
    def __init__(self, codice, messaggio):
        super().__init__(messaggio)
        self.codice = codice
        self.messaggio = messaggio


def _risultato(id_richiesta, risultato):
    return {"jsonrpc": "2.0", "id": id_richiesta, "result": risultato}


def _errore(id_richiesta, codice, messaggio):
    return {"jsonrpc": "2.0", "id": id_richiesta, "error": {"code": codice, "message": messaggio}}


def _testo(dati, *, errore=False):
    return {
        "content": [{"type": "text", "text": dati if isinstance(dati, str) else json.dumps(dati, ensure_ascii=False, separators=(", ", ": "))}],
        "isError": errore,
    }


def inizializza(parametri):
    richiesta = (parametri or {}).get("protocolVersion")
    return {
        "protocolVersion": richiesta if richiesta in VERSIONI else VERSIONI[0],
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": "magazzino-sagra", "title": settings.APP_NOME, "version": "1.0"},
        "instructions": ISTRUZIONI,
    }


def elenca_strumenti(contesto):
    # Con una chiave di sola lettura gli strumenti che scrivono non si vedono neanche.
    return {"tools": [s.definizione() for s in STRUMENTI.values() if contesto.scrittura or not s.scrive]}


def chiama_strumento(parametri, contesto):
    parametri = parametri or {}
    nome = parametri.get("name")
    argomenti = parametri.get("arguments") or {}
    strumento = STRUMENTI.get(nome)
    if strumento is None:
        raise ErroreRichiesta(-32602, f"Strumento sconosciuto: {nome}")
    if not isinstance(argomenti, dict):
        raise ErroreRichiesta(-32602, "Gli argomenti devono essere un oggetto JSON.")
    if strumento.scrive and not contesto.scrittura:
        return _testo("Collegamento in sola lettura: per registrare serve una chiave con il permesso di scrittura "
                      "(si crea dall'app, Magazzino → Claude) oppure, via docker exec, l'opzione --scrittura.", errore=True)
    try:
        return _testo(strumento.funzione(argomenti, contesto))
    except ErroreStrumento as e:
        return _testo(str(e), errore=True)
    except (ValueError, TypeError) as e:
        return _testo(f"Argomenti non validi: {e}", errore=True)
    except DatabaseError:
        log.exception("Errore del database nello strumento MCP %s", nome)
        return _testo("Errore del database: riprova tra poco.", errore=True)


def gestisci(messaggio, contesto: Contesto):
    """Un messaggio JSON-RPC (già decodificato) -> risposta, oppure None per le notifiche."""
    if not isinstance(messaggio, dict) or messaggio.get("jsonrpc") != "2.0":
        return _errore(None, -32600, "Richiesta JSON-RPC non valida.")
    id_richiesta = messaggio.get("id")
    metodo = messaggio.get("method")
    if metodo is None:
        return None  # una risposta del client: qui non si fanno richieste al client, si ignora
    notifica = "id" not in messaggio
    try:
        if metodo == "initialize":
            risultato = inizializza(messaggio.get("params"))
        elif metodo == "ping":
            risultato = {}
        elif metodo == "tools/list":
            risultato = elenca_strumenti(contesto)
        elif metodo == "tools/call":
            risultato = chiama_strumento(messaggio.get("params"), contesto)
        elif metodo in ("resources/list", "resources/templates/list"):
            risultato = {"resources": []} if metodo == "resources/list" else {"resourceTemplates": []}
        elif metodo == "prompts/list":
            risultato = {"prompts": []}
        elif metodo.startswith("notifications/"):
            return None
        else:
            raise ErroreRichiesta(-32601, f"Metodo sconosciuto: {metodo}")
    except ErroreRichiesta as e:
        return None if notifica else _errore(id_richiesta, e.codice, e.messaggio)
    except Exception:
        log.exception("Errore inatteso nel server MCP (%s)", metodo)
        return None if notifica else _errore(id_richiesta, -32603, "Errore interno del server.")
    return None if notifica else _risultato(id_richiesta, risultato)


def gestisci_testo(testo, contesto):
    """Testo JSON (un messaggio o, per le versioni vecchie, un elenco) -> risposta da mandare o None."""
    try:
        dati = json.loads(testo)
    except (ValueError, UnicodeDecodeError):
        return _errore(None, -32700, "JSON non valido.")
    if isinstance(dati, list):
        if not dati:
            return _errore(None, -32600, "Elenco di richieste vuoto.")
        risposte = [r for r in (gestisci(m, contesto) for m in dati) if r is not None]
        return risposte or None
    return gestisci(dati, contesto)
