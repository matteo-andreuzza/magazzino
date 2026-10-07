"""Sincronia tra la sagra e il server di casa: si lavora su tutti e due, le modifiche si scambiano.

Come funziona (vedi anche docs/MANUTENZIONE.md, «Sincronia con casa»):

- La coda: ogni tabella da sincronizzare ha un trigger di PostgreSQL che annota in
  sistema_modifica la riga cambiata: una voce per riga, con un numero che sale e l'ora. Così si
  vede tutto (pagine, admin, Claude, comandi). Restano fuori le tabelle di un solo server
  (TABELLE_LOCALI) e quelle che ognuno si crea da sé (ESCLUSE_DALLA_SINCRONIA).
- Il giro: la sagra (SINCRONIA_CON) chiama casa ogni 30 secondi. Manda le righe in coda, lette
  come sono adesso, e le conferme di quelle ricevute; casa applica e risponde con le sue. Casa non
  chiama mai: alla sagra basta poter uscire su internet. Una voce esce dalla coda solo quando
  l'altro conferma quel numero: se la riga è cambiata mentre viaggiava, riparte.
- Applicare: upsert o cancellazione, una riga alla volta (savepoint), le tabelle padri prima delle
  figlie. Se una riga è cambiata da tutte e due le parti vince la modifica più recente (a parità
  la sagra). Una riga che non passa (nome già usato, riga collegata che manca) torna indietro: chi
  l'ha mandata la tiene in coda e la vede nel pannello come conflitto. Una cancellazione di una
  riga che qui usano righe nuove non ancora partite non si fa: la riga si rimanda all'altro.
- Gli id non si scontrano: le sequenze vanno a passi di 2, casa usa i dispari e la sagra i pari.
- Si sincronizza solo tra programmi con le stesse migrazioni.
- I file (fatture, foto) seguono le righe: ognuno prende quelli che le sue righe usano e non ha.
"""

import gzip
import json
import os
import signal
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.db import DEFAULT_DB_ALIAS, DatabaseError, IntegrityError, close_old_connections, connection, connections, transaction
from django.db.migrations.recorder import MigrationRecorder
from django.db.models import F, FileField
from django.utils import timezone

from . import copia_remota
from .copia import ESCLUSE_DALLA_SINCRONIA, TABELLE_LOCALI, CopiaNonRiuscita, copia_database
from .errori import registra_errore
from .models import Battito, Conflitto, Modifica
from .services import segnala

NOME = "sincronia"  # battito, su tutti e due i server
PARITA = {"casa": 1, "sagra": 0}  # casa: id dispari; sagra: id pari
FUNZIONE = "sistema_registra_modifica"
SEQUENZA = "sistema_modifica_numero"
TRIGGER_RIGHE = "sistema_sincronia"  # inserimenti e cancellazioni
TRIGGER_MODIFICHE = "sistema_sincronia_modifica"  # aggiornamenti che cambiano davvero qualcosa
# Colonne che ogni server tiene per sé: da sole non fanno partire la riga e non si sovrascrivono.
# Sono quelle che cambiano a ogni uso: un accesso in sagra non deve «vincere» su una password
# cambiata a casa, né un uso di Claude su una chiave appena revocata dall'altra parte.
COLONNE_LOCALI = {"auth_user": ("last_login",), "assistente_chiavemcp": ("ultimo_uso",)}
PER_VOLTA = 500  # righe per messaggio
PASSI_MASSIMI = 50  # messaggi per giro
TIPO_MESSAGGIO = "application/x-sincronia+gzip"
BLOCCO = 72514017  # pg_advisory_xact_lock: uno scambio per volta
PULIZIA_FILE_OGNI = timedelta(hours=1)
FILE_ORFANO_DOPO = timedelta(days=1)
NOMI_LUNGHI = {"casa": "il server di casa", "sagra": "il server della sagra"}
MIGRAZIONE_SU = None  # il database che si sta migrando (lo usa l'instradatore dei test, con due database)


class SincroniaFerma(CopiaNonRiuscita):
    """Non si può sincronizzare: versioni diverse, server con lo stesso nome, manca PostgreSQL…"""


def nome_server(using=DEFAULT_DB_ALIAS):
    """«casa» o «sagra». Nei test il secondo database fa l'altro server (SERVER_NOME nelle sue impostazioni)."""
    return connections[using].settings_dict.get("SERVER_NOME") or settings.SERVER_NOME


def altro(nome):
    return "casa" if nome == "sagra" else "sagra"


def attiva(using=DEFAULT_DB_ALIAS):
    return connections[using].vendor == "postgresql"


def configurata():
    """La sincronia c'è solo con COPIA_CHIAVE: senza, niente trigger e niente coda (un server da solo
    non accumula modifiche che nessuno prende). Nei test con due database c'è sempre."""
    return bool(settings.COPIA_CHIAVE) or "altro" in settings.DATABASES


def _q(nome):
    return '"' + nome.replace('"', '""') + '"'


def locale(tabella):
    """True per le tabelle che non si sincronizzano."""
    for modello in TABELLE_LOCALI + ESCLUSE_DALLA_SINCRONIA:
        if tabella == modello or (modello.endswith("*") and tabella.startswith(modello[:-1])):
            return True
    return False


# ---------------------------------------------------------------------------
# Le tabelle, dal catalogo di PostgreSQL
# ---------------------------------------------------------------------------


@dataclass
class Tabella:
    nome: str
    chiave: str  # colonna della chiave primaria
    tipo: str  # tipo della chiave (es. bigint)
    identita: bool = False  # chiave GENERATED … AS IDENTITY (quelle di Django)
    colonne: list = field(default_factory=list)
    padri: set = field(default_factory=set)
    figlie: list = field(default_factory=list)  # [(tabella, colonna)] che puntano qui, anche non sincronizzate
    sequenza: str | None = None
    posto: int = 0  # ordine: i padri prima delle figlie

    @property
    def q(self):
        return "public." + _q(self.nome)

    @property
    def qk(self):
        return _q(self.chiave)


def schema(using=DEFAULT_DB_ALIAS):
    """{nome: Tabella} delle tabelle da sincronizzare (quelle con una chiave primaria di una colonna)."""
    with connections[using].cursor() as c:
        c.execute("""
            SELECT t.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attidentity <> ''
            FROM pg_index i
            JOIN pg_class t ON t.oid = i.indrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = i.indkey[0]
            WHERE i.indisprimary AND i.indnatts = 1 AND n.nspname = 'public' AND t.relkind = 'r'
        """)
        tabelle = {
            nome: Tabella(nome, chiave, tipo, identita)
            for nome, chiave, tipo, identita in c.fetchall() if not locale(nome)
        }
        c.execute("""
            SELECT t.relname, a.attname FROM pg_attribute a
            JOIN pg_class t ON t.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = 'public' AND t.relkind = 'r' AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY t.relname, a.attnum
        """)
        for nome, colonna in c.fetchall():
            if nome in tabelle:
                tabelle[nome].colonne.append(colonna)
        c.execute("""
            SELECT f.relname, a.attname, p.relname FROM pg_constraint k
            JOIN pg_class f ON f.oid = k.conrelid
            JOIN pg_class p ON p.oid = k.confrelid
            JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = k.conkey[1]
            WHERE k.contype = 'f' AND k.connamespace = 'public'::regnamespace
        """)
        for figlia, colonna, padre in c.fetchall():
            if padre in tabelle:
                tabelle[padre].figlie.append((figlia, colonna))
            if figlia in tabelle and padre != figlia:
                tabelle[figlia].padri.add(padre)
        for t in tabelle.values():
            c.execute("SELECT pg_get_serial_sequence(%s, %s)", [t.q, t.chiave])
            t.sequenza = c.fetchone()[0]

    visti, ordine = set(), []

    def visita(nome, strada):
        if nome in visti or nome in strada:
            return
        strada.add(nome)
        for padre in sorted(tabelle[nome].padri):
            if padre in tabelle:
                visita(padre, strada)
        strada.discard(nome)
        visti.add(nome)
        ordine.append(nome)

    for nome in sorted(tabelle):
        visita(nome, set())
    for posto, nome in enumerate(ordine):
        tabelle[nome].posto = posto
    return tabelle


# ---------------------------------------------------------------------------
# Trigger e sequenze: si rifanno dopo ogni migrate e ogni caricamento (non fa danni)
# ---------------------------------------------------------------------------

FUNZIONE_SQL = f"""
CREATE OR REPLACE FUNCTION public.{FUNZIONE}() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- «si» mentre si applicano le righe dell'altro server, «migrazione» durante migrate.
    IF coalesce(current_setting('sistema.applica', true), '') <> '' THEN
        RETURN NULL;
    END IF;
    INSERT INTO public.sistema_modifica (tabella, chiave, numero, quando)
    VALUES (
        TG_TABLE_NAME,
        CASE WHEN TG_OP = 'DELETE' THEN to_jsonb(OLD) ->> TG_ARGV[0] ELSE to_jsonb(NEW) ->> TG_ARGV[0] END,
        nextval('public.{SEQUENZA}'),
        clock_timestamp()
    )
    ON CONFLICT (tabella, chiave) DO UPDATE SET numero = EXCLUDED.numero, quando = EXCLUDED.quando;
    RETURN NULL;
END
$$
"""


def _quando_cambia(tabella):
    locali = COLONNE_LOCALI.get(tabella, ())
    if not locali:
        return "OLD.* IS DISTINCT FROM NEW.*"
    elenco = ", ".join("'" + c + "'" for c in locali)
    return f"(to_jsonb(OLD) - ARRAY[{elenco}]) IS DISTINCT FROM (to_jsonb(NEW) - ARRAY[{elenco}])"


def prepara(using=DEFAULT_DB_ALIAS):
    """Trigger sulle tabelle da sincronizzare (solo se la sincronia è configurata), via da quelle che
    non lo sono più, sequenze pari o dispari (sempre: così si può accendere la sincronia dopo)."""
    if not attiva(using):
        return
    tabelle = schema(using)
    accesa = configurata()
    with connections[using].cursor() as c:
        c.execute(f"CREATE SEQUENCE IF NOT EXISTS public.{SEQUENZA}")
        c.execute(FUNZIONE_SQL)
        for t in tabelle.values() if accesa else ():
            argomento = "'" + t.chiave.replace("'", "''") + "'"
            c.execute(
                f"CREATE OR REPLACE TRIGGER {TRIGGER_RIGHE} AFTER INSERT OR DELETE ON {t.q} "
                f"FOR EACH ROW EXECUTE FUNCTION public.{FUNZIONE}({argomento})"
            )
            c.execute(
                f"CREATE OR REPLACE TRIGGER {TRIGGER_MODIFICHE} AFTER UPDATE ON {t.q} "
                f"FOR EACH ROW WHEN ({_quando_cambia(t.nome)}) EXECUTE FUNCTION public.{FUNZIONE}({argomento})"
            )
        c.execute(
            "SELECT t.relname, g.tgname FROM pg_trigger g JOIN pg_class t ON t.oid = g.tgrelid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace WHERE n.nspname = 'public' AND g.tgname IN (%s, %s)",
            [TRIGGER_RIGHE, TRIGGER_MODIFICHE],
        )
        for nome, trigger in c.fetchall():
            if nome not in tabelle or not accesa:
                c.execute(f"DROP TRIGGER {_q(trigger)} ON public.{_q(nome)}")
        if not accesa:
            c.execute("DELETE FROM public.sistema_modifica")
        _sequenze(c, tabelle.values(), PARITA[nome_server(using)])


def _sequenze(cursore, tabelle, parita):
    """Ogni sequenza a passi di 2, sul numero giusto (pari o dispari), oltre l'id più alto che c'è."""
    for t in tabelle:
        if not t.sequenza:
            continue
        cursore.execute(
            "SELECT s.seqincrement, pg_sequence_last_value(s.seqrelid), s.seqstart FROM pg_sequence s "
            "WHERE s.seqrelid = %s::regclass", [t.sequenza],
        )
        riga = cursore.fetchone()
        if riga is None:
            continue
        passo, ultimo, inizio = riga
        cursore.execute(f"SELECT max({t.qk}) FROM {t.q}")
        massimo = cursore.fetchone()[0] or 0
        prossimo_ora = ultimo + passo if ultimo is not None else inizio
        if passo == 2 and prossimo_ora % 2 == parita and prossimo_ora > massimo:
            continue
        prossimo = max(massimo, ultimo or 0) + 1
        if prossimo % 2 != parita:
            prossimo += 1
        if t.identita:
            cursore.execute(f"ALTER TABLE {t.q} ALTER COLUMN {t.qk} SET INCREMENT BY 2 RESTART WITH {int(prossimo)}")
        else:
            cursore.execute(f"ALTER SEQUENCE {t.sequenza} INCREMENT BY 2 RESTART WITH {int(prossimo)}")


def prima_di_migrate(sender, using=DEFAULT_DB_ALIAS, **kwargs):
    """Le righe create o cambiate dalle migrazioni non vanno in coda: ognuno applica le sue
    (e la sagra, appena aggiornata, si riallinea da casa)."""
    global MIGRAZIONE_SU
    MIGRAZIONE_SU = using
    if attiva(using):
        with connections[using].cursor() as c:
            c.execute("SELECT set_config('sistema.applica', 'migrazione', false)")


def dopo_migrate(sender, using=DEFAULT_DB_ALIAS, **kwargs):
    global MIGRAZIONE_SU
    MIGRAZIONE_SU = None
    if attiva(using):
        with connections[using].cursor() as c:
            c.execute("SELECT set_config('sistema.applica', '', false)")
        prepara(using)


# ---------------------------------------------------------------------------
# Messaggi tra i due server
# ---------------------------------------------------------------------------


def leggi_messaggio(dati):
    return json.loads(gzip.decompress(dati).decode("utf-8"))


def scrivi_messaggio(messaggio):
    return gzip.compress(json.dumps(messaggio, separators=(",", ":"), default=str).encode("utf-8"))


def migrazioni(using=DEFAULT_DB_ALIAS):
    """Le migrazioni applicate, senza quelle dell'app sistema (le sue tabelle sono di un solo server)."""
    return sorted([a, n] for a, n in MigrationRecorder(connections[using]).applied_migrations() if a != "sistema")


def _nome_lungo(nome, maiuscola=False):
    testo = NOMI_LUNGHI.get(nome, nome)
    return testo[0].upper() + testo[1:] if maiuscola else testo


def controlla_altro(messaggio, using=DEFAULT_DB_ALIAS):
    """Il messaggio viene dall'altro server e il suo programma è alla stessa versione di questo."""
    qui = nome_server(using)
    suo = messaggio.get("server")
    if suo == qui:
        raise SincroniaFerma(
            f"Tutti e due i server si chiamano «{qui}»: nel file .env del server di casa ci vuole SERVER_NOME=casa, "
            "in quello della sagra SERVER_NOME=sagra."
        )
    if suo != altro(qui):
        raise SincroniaFerma(f"Messaggio da un server sconosciuto («{suo}»).")
    mie = {tuple(m) for m in migrazioni(using)}
    sue = {tuple(m) for m in messaggio.get("migrazioni") or []}
    if mie == sue:
        return
    attesa = " Intanto ognuno lavora per conto suo: le modifiche aspettano in coda e partono dopo l'aggiornamento."
    if sue < mie or mie < sue:
        nuovo, vecchio = (qui, suo) if sue < mie else (suo, qui)
        raise SincroniaFerma(
            f"{_nome_lungo(nuovo, True)} ha il programma più aggiornato di {_nome_lungo(vecchio)}: "
            f"aggiorna {_nome_lungo(vecchio)} (scripts/aggiorna.sh)." + attesa
        )
    raise SincroniaFerma("I due server hanno versioni diverse del programma: aggiornali tutti e due alla stessa." + attesa)


def _blocca(using):
    with connections[using].cursor() as c:
        c.execute("SELECT pg_advisory_xact_lock(%s)", [BLOCCO])


# ---------------------------------------------------------------------------
# Mandare: la coda
# ---------------------------------------------------------------------------


@dataclass
class Uscita:
    modifiche: list
    altre: bool  # in coda ce ne sono altre oltre a queste
    in_coda: int


def da_inviare(*, tabelle, using=DEFAULT_DB_ALIAS, escludi=(), limite=None):
    """Le prime righe in coda, com'è ciascuna adesso (None se nel frattempo è stata cancellata)."""
    limite = limite or PER_VOLTA
    escludi = {tuple(e) for e in escludi}
    voci, sparite = [], set()
    coda = Modifica.objects.using(using).order_by("numero").values_list("tabella", "chiave", "numero", "quando")
    in_coda = coda.count()
    for t, k, n, q in coda.iterator(chunk_size=1000):
        if (t, k) in escludi:
            continue
        if t not in tabelle:
            sparite.add(t)
            continue
        voci.append((t, k, n, q))
        if len(voci) > limite:
            break
    altre = len(voci) > limite
    voci = voci[:limite]
    if sparite:  # tabelle che non si sincronizzano più
        Modifica.objects.using(using).filter(tabella__in=sparite).delete()
    per_tabella = defaultdict(list)
    for t, k, _, _ in voci:
        per_tabella[t].append(k)
    righe = {}
    with connections[using].cursor() as c:
        for nome, chiavi in per_tabella.items():
            t = tabelle[nome]
            c.execute(
                f"SELECT x.{t.qk}::text, to_jsonb(x)::text FROM {t.q} AS x WHERE x.{t.qk} = ANY(%s::{t.tipo}[])",
                [chiavi],
            )
            for chiave, riga in c.fetchall():
                righe[(nome, chiave)] = riga
    modifiche = [{"t": t, "k": k, "n": n, "q": q.isoformat(), "r": righe.get((t, k))} for t, k, n, q in voci]
    return Uscita(modifiche, altre, in_coda)


def conferma(conferme, *, using=DEFAULT_DB_ALIAS):
    """L'altro ha ricevuto queste righe: escono dalla coda (solo se nel frattempo non sono cambiate)."""
    if not conferme:
        return 0
    tabelle, chiavi, numeri = (list(x) for x in zip(*conferme))
    with connections[using].cursor() as c:
        c.execute(
            "DELETE FROM public.sistema_modifica m USING unnest(%s::text[], %s::text[], %s::bigint[]) AS u(t, k, n) "
            "WHERE m.tabella = u.t AND m.chiave = u.k AND m.numero = u.n",
            [tabelle, chiavi, numeri],
        )
        return c.rowcount


def _tocca(cursore, tabella, chiave):
    """Rimette in coda una riga di qui, come se fosse appena cambiata: vincerà dall'altra parte."""
    cursore.execute(
        f"INSERT INTO public.sistema_modifica (tabella, chiave, numero, quando) "
        f"VALUES (%s, %s, nextval('public.{SEQUENZA}'), clock_timestamp()) "
        "ON CONFLICT (tabella, chiave) DO UPDATE SET numero = EXCLUDED.numero, quando = EXCLUDED.quando",
        [tabella, chiave],
    )


# ---------------------------------------------------------------------------
# Ricevere: applicare le righe dell'altro
# ---------------------------------------------------------------------------


@dataclass
class Esito:
    conferme: list = field(default_factory=list)  # [[tabella, chiave, numero]]
    rifiutate: list = field(default_factory=list)  # [{t, k, n, motivo, gemella, riga_gemella}]
    applicate: int = 0
    tenute: int = 0  # qui c'era una modifica più recente: vince quella di qui
    rimandate: int = 0  # cancellazioni non fatte: qui la riga serve ancora


def _sql_upsert(t):
    colonne = ", ".join(_q(c) for c in t.colonne)
    aggiorna = [c for c in t.colonne if c != t.chiave and c not in COLONNE_LOCALI.get(t.nome, ())]
    sql = (
        f"INSERT INTO {t.q} ({colonne}) {'OVERRIDING SYSTEM VALUE ' if t.identita else ''}"
        f"SELECT {colonne} FROM jsonb_populate_record(NULL::{t.q}, %s::jsonb) ON CONFLICT ({t.qk}) DO "
    )
    if not aggiorna:
        return sql + "NOTHING"
    return sql + (
        f"UPDATE SET ({', '.join(_q(c) for c in aggiorna)}) = ROW({', '.join('EXCLUDED.' + _q(c) for c in aggiorna)})"
    )


def _togli_righe_non_sincronizzate(cursore, t, chiave, tabelle):
    """Prima di cancellare una riga: via le righe delle tabelle che non si sincronizzano e la usano
    (registro dell'admin, permessi di un utente), come farebbe Django (CASCADE)."""
    for figlia, colonna in t.figlie:
        if figlia not in tabelle and not figlia.startswith("sistema_"):
            cursore.execute(f"DELETE FROM public.{_q(figlia)} WHERE {_q(colonna)} = %s::{t.tipo}", [chiave])


def _usata_da_righe_in_coda(cursore, t, chiave, tabelle):
    """True se qui ci sono righe che usano questa e che l'altro server non ha ancora ricevuto."""
    for figlia, colonna in t.figlie:
        f = tabelle.get(figlia)
        if f is None:
            continue
        cursore.execute(
            f"SELECT 1 FROM {f.q} AS x WHERE x.{_q(colonna)} = %s::{t.tipo} AND EXISTS ("
            f"SELECT 1 FROM public.sistema_modifica m WHERE m.tabella = %s AND m.chiave = x.{f.qk}::text) LIMIT 1",
            [chiave, figlia],
        )
        if cursore.fetchone():
            return True
    return False


def _gemella(cursore, t, vincolo, riga, using):
    """La riga di qui che ha lo stesso valore unico (es. lo stesso nome) della riga arrivata."""
    try:
        with transaction.atomic(using=using):
            cursore.execute(
                "SELECT i.indnkeyatts, pg_get_expr(i.indpred, i.indrelid), i.indexrelid FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relname = %s", [vincolo],
            )
            dati = cursore.fetchone()
            if dati is None:
                return "", ""
            colonne, condizione, indice = dati
            espressioni = []
            for posto in range(1, colonne + 1):
                cursore.execute("SELECT pg_get_indexdef(%s, %s, true)", [indice, posto])
                espressioni.append(cursore.fetchone()[0])
            elenco = ", ".join(espressioni)
            sql = (
                f"SELECT x.{t.qk}::text, to_jsonb(x)::text FROM {t.q} AS x "
                f"WHERE ({elenco}) = (SELECT {elenco} FROM jsonb_populate_record(NULL::{t.q}, %s::jsonb) AS nuova) "
                f"AND x.{t.qk} <> (SELECT {t.qk} FROM jsonb_populate_record(NULL::{t.q}, %s::jsonb) AS nuova)"
            )
            if condizione:
                sql += f" AND ({condizione})"
            cursore.execute(sql + " LIMIT 1", [riga, riga])
            trovata = cursore.fetchone()
            return (trovata[0], trovata[1]) if trovata else ("", "")
    except DatabaseError:
        return "", ""


def _applica_una(cursore, m, t, tabelle, using):
    """None se la riga è andata, «rimandata» se una cancellazione non si fa, altrimenti
    (motivo, gemella, riga della gemella)."""
    try:
        with transaction.atomic(using=using):
            if m["r"] is None:
                _togli_righe_non_sincronizzate(cursore, t, m["k"], tabelle)
                cursore.execute(f"DELETE FROM {t.q} WHERE {t.qk} = %s::{t.tipo}", [m["k"]])
            else:
                cursore.execute(_sql_upsert(t), [m["r"]])
        return None
    except IntegrityError as e:
        causa = e.__cause__
        codice = getattr(causa, "sqlstate", None)
        diagnosi = getattr(causa, "diag", None)
        dettaglio = (getattr(diagnosi, "message_detail", None) or str(causa or e)).strip()
        if codice == "23503" and m["r"] is None:
            if _usata_da_righe_in_coda(cursore, t, m["k"], tabelle):
                _tocca(cursore, t.nome, m["k"])
                return "rimandata"
            return (f"Non la cancello: qui la usano ancora altre righe ({dettaglio}).", "", "")
        if codice == "23503":
            return (f"Manca una riga collegata ({dettaglio}).", "", "")
        if codice == "23505":
            gemella, riga = _gemella(cursore, t, getattr(diagnosi, "constraint_name", "") or "", m["r"], using)
            return (f"C'è già una riga con lo stesso valore ({dettaglio}).", gemella, riga)
        return (f"Non accettata dal database: {dettaglio}"[:400], "", "")
    except DatabaseError as e:
        return (f"Errore del database: {e}"[:400], "", "")


def applica(modifiche, *, tabelle, using=DEFAULT_DB_ALIAS):
    """Applica le righe arrivate dall'altro server. Le conferme dicono quali può togliere dalla coda."""
    esito = Esito()
    if not modifiche:
        return esito
    qui = nome_server(using)
    coppie = {(m["t"], m["k"]) for m in modifiche}
    locali = {
        (t, k): (n, q)
        for t, k, n, q in Modifica.objects.using(using)
        .filter(tabella__in={t for t, _ in coppie}).values_list("tabella", "chiave", "numero", "quando")
        if (t, k) in coppie
    }
    da_fare = []
    for m in modifiche:
        if m["t"] not in tabelle:
            esito.rifiutate.append({**_rifiuto(m), "motivo": "Tabella sconosciuta qui: i due programmi sono diversi?"})
            continue
        mia = locali.get((m["t"], m["k"]))
        if mia is not None:
            quando = datetime.fromisoformat(m["q"])
            if mia[1] > quando or (mia[1] == quando and qui == "sagra"):
                esito.conferme.append([m["t"], m["k"], m["n"]])
                esito.tenute += 1
                continue
        da_fare.append(m)
    inserite = sorted((m for m in da_fare if m["r"] is not None), key=lambda m: (tabelle[m["t"]].posto, m["n"]))
    cancellate = sorted((m for m in da_fare if m["r"] is None), key=lambda m: (-tabelle[m["t"]].posto, m["n"]))
    superate = []  # voci della coda di qui battute da quelle arrivate
    with transaction.atomic(using=using), connections[using].cursor() as c:
        c.execute("SELECT set_config('sistema.applica', 'si', true)")
        c.execute("SET CONSTRAINTS ALL IMMEDIATE")
        try:
            rimaste, fallite = inserite + cancellate, []
            for _ in range(2):  # al secondo passaggio passano quelle che aspettavano una riga arrivata dopo
                fallite = []
                for m in rimaste:
                    risultato = _applica_una(c, m, tabelle[m["t"]], tabelle, using)
                    if risultato is None:
                        esito.conferme.append([m["t"], m["k"], m["n"]])
                        esito.applicate += 1
                        if (m["t"], m["k"]) in locali:
                            superate.append([m["t"], m["k"], locali[(m["t"], m["k"])][0]])
                    elif risultato == "rimandata":
                        esito.conferme.append([m["t"], m["k"], m["n"]])
                        esito.rimandate += 1
                    else:
                        fallite.append((m, risultato))
                rimaste = [m for m, _ in fallite]
                if not rimaste:
                    break
            for m, (motivo, gemella, riga) in fallite:
                esito.rifiutate.append({**_rifiuto(m), "motivo": motivo, "gemella": gemella, "riga_gemella": riga})
        finally:
            if not connections[using].needs_rollback:
                c.execute("SET CONSTRAINTS ALL DEFERRED")
                c.execute("SELECT set_config('sistema.applica', '', true)")
        conferma(superate, using=using)
    return esito


def _rifiuto(m):
    return {"t": m["t"], "k": m["k"], "n": m["n"], "motivo": "", "gemella": "", "riga_gemella": ""}


def registra_rifiutate(rifiutate, *, using=DEFAULT_DB_ALIAS):
    """Le righe di qui che l'altro non ha accettato: restano in coda e si vedono nel pannello."""
    adesso = timezone.now()
    for r in rifiutate:
        campi = {"motivo": r.get("motivo") or "", "gemella": r.get("gemella") or "",
                 "dati_gemella": r.get("riga_gemella") or "", "ultimo_il": adesso}
        if not Conflitto.objects.using(using).filter(tabella=r["t"], chiave=r["k"]).update(volte=F("volte") + 1, **campi):
            Conflitto.objects.using(using).create(tabella=r["t"], chiave=r["k"], **campi)


def pulisci_conflitti(using=DEFAULT_DB_ALIAS):
    """Via i conflitti delle righe che non sono più in coda (sono passate, o non ci sono più)."""
    with connections[using].cursor() as c:
        c.execute(
            "DELETE FROM public.sistema_conflitto c WHERE NOT EXISTS ("
            "SELECT 1 FROM public.sistema_modifica m WHERE m.tabella = c.tabella AND m.chiave = c.chiave)"
        )


def elenco_conflitti(using=DEFAULT_DB_ALIAS, quanti=20):
    """Per il pannello dell'altro server: le righe di qui che là non passano."""
    return [
        {"t": c.tabella, "k": c.chiave, "motivo": c.motivo, "gemella": c.gemella, "volte": c.volte,
         "primo_il": c.primo_il.isoformat(), "descrizione": descrivi(c.tabella, c.chiave, using=using)}
        for c in Conflitto.objects.using(using)[:quanti]
    ]


def descrivi(tabella, chiave, *, dati=None, using=DEFAULT_DB_ALIAS):
    """«Prodotto «Pasta»» per una riga: dal modello Django, se c'è; altrimenti tabella e chiave."""
    modello = next((m for m in apps.get_models(include_auto_created=True) if m._meta.db_table == tabella), None)
    if modello is None:
        return f"{tabella} {chiave}"
    oggetto = None
    if dati:
        try:
            valori = json.loads(dati)
            oggetto = modello(**{f.attname: valori.get(f.column) for f in modello._meta.concrete_fields if f.column in valori})
        except (ValueError, TypeError):
            oggetto = None
    if oggetto is None:
        oggetto = modello.objects.using(using).filter(pk=chiave).first()
    nome = str(modello._meta.verbose_name)
    nome = nome[0].upper() + nome[1:]
    try:
        return f"{nome} «{oggetto}»" if oggetto is not None else f"{nome} {chiave} (non c'è più)"
    except Exception:  # noqa: BLE001 - __str__ di una riga a metà
        return f"{nome} {chiave}"


# ---------------------------------------------------------------------------
# Doppioni: unire due righe con lo stesso nome
# ---------------------------------------------------------------------------


def unisci(conflitto, *, using=DEFAULT_DB_ALIAS):
    """«Tieni quella dell'altro server»: la riga di qui doppia di una dell'altro server sparisce, e le
    righe di qui che la usavano passano a quella dell'altro (che entra qui com'è là)."""
    if not conflitto.gemella or not conflitto.dati_gemella:
        raise SincroniaFerma("Questo conflitto non è un doppione: non c'è niente da unire.")
    tabelle = schema(using)
    t = tabelle.get(conflitto.tabella)
    if t is None:
        raise SincroniaFerma("Tabella sconosciuta.")
    try:
        with transaction.atomic(using=using), connections[using].cursor() as c:
            c.execute("SET CONSTRAINTS ALL DEFERRED")
            for figlia, colonna in t.figlie:
                c.execute(
                    f"UPDATE public.{_q(figlia)} SET {_q(colonna)} = %s::{t.tipo} WHERE {_q(colonna)} = %s::{t.tipo}",
                    [conflitto.gemella, conflitto.chiave],
                )
            c.execute(f"DELETE FROM {t.q} WHERE {t.qk} = %s::{t.tipo}", [conflitto.chiave])
            # La gemella entra com'è dall'altra parte, senza rimetterla in coda: là c'è già.
            c.execute("SELECT set_config('sistema.applica', 'si', true)")
            c.execute(_sql_upsert(t), [conflitto.dati_gemella])
            c.execute("SELECT set_config('sistema.applica', '', true)")
            c.execute("SET CONSTRAINTS ALL IMMEDIATE")
            c.execute("SET CONSTRAINTS ALL DEFERRED")
            Conflitto.objects.using(using).filter(pk=conflitto.pk).delete()
    except IntegrityError as e:
        dettaglio = getattr(getattr(e.__cause__, "diag", None), "message_detail", None) or str(e)
        raise SincroniaFerma(f"Non riesco a unire le due righe: {dettaglio}. Sistemala a mano (rinomina o cancella una delle due).") from None


def chiedi_all_altro(conflitto, *, using=DEFAULT_DB_ALIAS):
    """«Tieni quella di qui»: al prossimo giro l'altro server unisce la sua riga doppia a questa."""
    _aggiungi_richieste([{"azione": "unisci", "t": conflitto.tabella, "k": conflitto.gemella, "gemella": conflitto.chiave}], using)


def _aggiungi_richieste(nuove, using=DEFAULT_DB_ALIAS):
    battito = Battito.objects.using(using).filter(nome=NOME).first()
    if battito is None:
        battito = Battito.objects.using(using).create(nome=NOME, ultimo_il=timezone.now(), stato="avviso")
    chiavi = {(r["t"], r["k"]) for r in nuove}
    richieste = [r for r in battito.dettagli.get("richieste", []) if (r.get("t"), r.get("k")) not in chiavi] + nuove
    battito.dettagli = {**battito.dettagli, "richieste": richieste}
    battito.save(update_fields=["dettagli"])


def prendi_richieste(using=DEFAULT_DB_ALIAS):
    """Le richieste per l'altro server, tolte dall'elenco (partono con il prossimo messaggio)."""
    battito = Battito.objects.using(using).filter(nome=NOME).first()
    if battito is None or not battito.dettagli.get("richieste"):
        return []
    richieste = battito.dettagli["richieste"]
    Battito.objects.using(using).filter(pk=battito.pk).update(dettagli={**battito.dettagli, "richieste": []})
    return richieste


def esegui_richieste(richieste, *, using=DEFAULT_DB_ALIAS):
    esiti = []
    for r in richieste:
        conflitto = Conflitto.objects.using(using).filter(tabella=r.get("t"), chiave=r.get("k")).first()
        if r.get("azione") != "unisci" or conflitto is None or conflitto.gemella != r.get("gemella"):
            esiti.append({**r, "ok": False, "messaggio": "Il doppione non c'è più."})
            continue
        try:
            unisci(conflitto, using=using)
            esiti.append({**r, "ok": True, "messaggio": "Unite."})
        except SincroniaFerma as e:
            esiti.append({**r, "ok": False, "messaggio": str(e)})
    return esiti


# ---------------------------------------------------------------------------
# File (fatture, foto): seguono le righe
# ---------------------------------------------------------------------------


def file_usati(using=DEFAULT_DB_ALIAS):
    """I percorsi dei file che le righe di qui usano."""
    usati = set()
    for modello in apps.get_models():
        campi = [f.attname for f in modello._meta.concrete_fields if isinstance(f, FileField)]
        if campi and not locale(modello._meta.db_table):
            for valori in modello.objects.using(using).values_list(*campi):
                usati.update(v for v in valori if v)
    return usati


def file_mancanti(using=DEFAULT_DB_ALIAS):
    radice = Path(settings.MEDIA_ROOT)
    return sorted(p for p in file_usati(using) if not (radice / p).is_file())


def pulisci_file(using=DEFAULT_DB_ALIAS):
    """Via i file che nessuna riga usa più da almeno un giorno (es. foto di un prodotto cancellato dall'altra parte).

    «Usati» sono quelli dei FileField dei modelli: un file degli allegati citato in un altro modo
    verrebbe tolto. Nei test MEDIA_ROOT va sempre spostata in una cartella temporanea.
    """
    radice = Path(settings.MEDIA_ROOT)
    if not radice.is_dir():
        return 0
    usati = file_usati(using)
    limite = time.time() - FILE_ORFANO_DOPO.total_seconds()
    tolti = 0
    for file in list(radice.rglob("*")):
        if file.is_file() and str(file.relative_to(radice)) not in usati and file.stat().st_mtime < limite:
            file.unlink(missing_ok=True)
            tolti += 1
    return tolti


def _pulizia_dovuta(dettagli):
    ultima = dettagli.get("pulizia_file_il")
    return not ultima or timezone.now() - datetime.fromisoformat(ultima) > PULIZIA_FILE_OGNI


def _scambia_file(base, mancanti_la, *, using, scrivi):
    """La sagra prende da casa i file che le mancano e le manda quelli che mancano a casa."""
    scaricati = mandati = 0
    for percorso in file_mancanti(using):
        destinazione = copia_remota.percorso_allegato(percorso)
        if destinazione is None:
            continue
        destinazione.parent.mkdir(parents=True, exist_ok=True)
        temporaneo = destinazione.with_name(destinazione.name + ".tmp")
        try:
            copia_remota.scarica(base, "sistema/api/copia/allegato/", temporaneo, percorso=percorso)
        except copia_remota.AltroNonRaggiungibile:
            temporaneo.unlink(missing_ok=True)
            raise
        except CopiaNonRiuscita:
            temporaneo.unlink(missing_ok=True)  # neanche là: arriverà con la riga giusta
            continue
        temporaneo.replace(destinazione)
        scaricati += 1
    for percorso in mancanti_la:
        file = copia_remota.file_allegato(percorso)
        if file is None:
            continue
        with copia_remota.apri(base, "sistema/api/sincronia/allegato/", dati=file.read_bytes(),
                               tipo="application/octet-stream", timeout=120, percorso=percorso):
            pass
        mandati += 1
    if scaricati or mandati:
        scrivi(f"File: {scaricati} presi da casa, {mandati} mandati a casa.")
    return scaricati, mandati


def ricevi_file(percorso, flusso):
    """Un file mandato dall'altro server: si salva solo se una riga di qui lo usa e manca."""
    destinazione = copia_remota.percorso_allegato(percorso)
    if destinazione is None or percorso not in set(file_mancanti()):
        return False
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    temporaneo = destinazione.with_name(destinazione.name + ".tmp")
    with temporaneo.open("wb") as f:
        while blocco := flusso.read(1024 * 1024):
            f.write(blocco)
    temporaneo.replace(destinazione)
    return True


# ---------------------------------------------------------------------------
# Lo scambio: lato casa (risponde) e lato sagra (chiama)
# ---------------------------------------------------------------------------


def messaggio_base(using=DEFAULT_DB_ALIAS):
    return {"server": nome_server(using), "migrazioni": migrazioni(using), "adesso": timezone.now().isoformat()}


def scambio(richiesta, *, using=DEFAULT_DB_ALIAS):
    """Casa riceve un messaggio della sagra e risponde con il suo. Restituisce (risposta, esito)."""
    if not attiva(using):
        raise SincroniaFerma("La sincronia funziona solo con PostgreSQL (l'installazione con Docker).")
    controlla_altro(richiesta, using)
    tabelle = schema(using)
    with transaction.atomic(using=using):
        _blocca(using)
        conferma(richiesta.get("conferme") or [], using=using)
        registra_rifiutate(richiesta.get("rifiutate") or [], using=using)
        esiti = esegui_richieste(richiesta.get("richieste") or [], using=using)
        esito = applica(richiesta.get("modifiche") or [], tabelle=tabelle, using=using)
        pulisci_conflitti(using)
    uscita = da_inviare(tabelle=tabelle, using=using, escludi=richiesta.get("escludi") or [])
    risposta = {
        **messaggio_base(using),
        "modifiche": uscita.modifiche, "altre": uscita.altre, "in_coda": uscita.in_coda,
        "conferme": esito.conferme, "rifiutate": esito.rifiutate,
        "conflitti": elenco_conflitti(using), "file_mancanti": file_mancanti(using),
        "richieste": prendi_richieste(using), "esiti_richieste": esiti,
    }
    return risposta, esito


def _chiama_http(base, richiesta):
    with copia_remota.apri(base, "sistema/api/sincronia/", dati=scrivi_messaggio(richiesta),
                           tipo=TIPO_MESSAGGIO, timeout=120) as risposta:
        return leggi_messaggio(risposta.read())


def giro(*, base=None, using=DEFAULT_DB_ALIAS, chiama=None, scrivi=print):
    """La sagra: uno scambio completo con casa (più messaggi, finché le code non sono passate)."""
    if not attiva(using):
        raise SincroniaFerma("La sincronia funziona solo con PostgreSQL (l'installazione con Docker).")
    base = base or settings.SINCRONIA_CON
    con_http = chiama is None
    chiama = chiama or (lambda richiesta: _chiama_http(base, richiesta))
    inizio = time.monotonic()
    tabelle = schema(using)
    conferme, rifiutate, richieste = [], [], prendi_richieste(using)
    mie_escluse, sue_escluse = set(), set()
    conti = Counter()
    risposta, ultimo = {}, False
    for _ in range(PASSI_MASSIMI):
        uscita = da_inviare(tabelle=tabelle, using=using, escludi=mie_escluse)
        richiesta = {
            **messaggio_base(using),
            "modifiche": uscita.modifiche, "altre": uscita.altre, "in_coda": uscita.in_coda,
            "conferme": conferme, "rifiutate": rifiutate, "escludi": sorted(sue_escluse),
            "conflitti": elenco_conflitti(using), "richieste": richieste,
        }
        partenza = time.time()
        try:
            risposta = chiama(richiesta)
        except BaseException:
            if richieste:  # non sono arrivate: ripartono al prossimo giro
                _aggiungi_richieste(richieste, using)
            raise
        arrivo = time.time()
        richieste = []
        controlla_altro(risposta, using)
        with transaction.atomic(using=using):
            _blocca(using)
            conferma(risposta.get("conferme") or [], using=using)
            registra_rifiutate(risposta.get("rifiutate") or [], using=using)
            esegui_richieste(risposta.get("richieste") or [], using=using)
            esito = applica(risposta.get("modifiche") or [], tabelle=tabelle, using=using)
            pulisci_conflitti(using)
        mie_escluse |= {(r["t"], r["k"]) for r in risposta.get("rifiutate") or []}
        sue_escluse |= {(r["t"], r["k"]) for r in esito.rifiutate}
        conti["inviate"] += len(uscita.modifiche)
        conti["ricevute"] += len(risposta.get("modifiche") or [])
        conti["applicate"] += esito.applicate
        conti["tenute"] += esito.tenute + esito.rimandate
        conti["rifiutate_qui"] += len(esito.rifiutate)
        conferme, rifiutate = esito.conferme, esito.rifiutate
        scarto = datetime.fromisoformat(risposta["adesso"]).timestamp() - (partenza + arrivo) / 2
        if ultimo:
            break
        if not uscita.altre and not risposta.get("altre"):
            if not conferme and not rifiutate:
                break
            ultimo = True  # un ultimo messaggio per consegnare le conferme
    scaricati, mandati = (0, 0)
    if con_http:
        scaricati, mandati = _scambia_file(base, risposta.get("file_mancanti") or [], using=using, scrivi=scrivi)
    in_coda = Modifica.objects.using(using).count()
    if conti["inviate"] or conti["ricevute"]:
        scrivi(f"Mandate {conti['inviate']} righe a casa, ricevute {conti['ricevute']}; in coda {in_coda}.")
    return {
        **conti, "in_coda": in_coda, "in_coda_altro": risposta.get("in_coda", 0),
        "conflitti": Conflitto.objects.using(using).count(), "conflitti_altro": risposta.get("conflitti") or [],
        "esiti_richieste": risposta.get("esiti_richieste") or [],
        "scarto_orologio": round(scarto, 1), "file_presi": scaricati, "file_mandati": mandati,
        "durata": round(time.monotonic() - inizio, 1),
    }


# ---------------------------------------------------------------------------
# Il servizio «sincronia» della sagra
# ---------------------------------------------------------------------------


def _dettagli(using=DEFAULT_DB_ALIAS):
    return Battito.objects.using(using).filter(nome=NOME).values_list("dettagli", flat=True).first() or {}


def _riassunto(esito):
    pezzi = []
    if esito["inviate"] or esito["ricevute"]:
        pezzi.append(f"{esito['inviate']} righe mandate, {esito['ricevute']} ricevute")
    if esito["conflitti"] or esito["conflitti_altro"]:
        pezzi.append(f"conflitti: {esito['conflitti']} qui, {len(esito['conflitti_altro'])} a casa (vedi il pannello)")
    if abs(esito["scarto_orologio"]) > 60:
        pezzi.append(f"gli orologi dei due server sono distanti {abs(esito['scarto_orologio']):.0f} secondi")
    ora = timezone.localtime()
    return f"Allineata con casa alle {ora:%H:%M}" + (": " + "; ".join(pezzi) if pezzi else ".")


def ciclo(*, ogni_secondi, scrivi=print, una_volta=False):
    """Il servizio «sincronia» della sagra: un giro subito, poi ogni `ogni_secondi` (o subito se lo
    chiede il pannello). Allo spegnimento del container (SIGTERM) fa un ultimo giro."""
    avviato = timezone.now()
    fermati = []
    if not una_volta:
        signal.signal(signal.SIGTERM, lambda *_: fermati.append(True))
    while True:  # all'avvio il database può non essere pronto (migrazioni in corso nel container «web»)
        try:
            segnala(NOME, stato="avviso", messaggio="Avviata: primo giro con casa in corso…", avviato_il=avviato,
                    dettagli={**_dettagli(), "primo_ok": False, "adesso": False, "riallinea": False})
            break
        except DatabaseError:
            scrivi("Database non ancora pronto (migrazioni in corso?): aspetto.")
            close_old_connections()
            time.sleep(5)
    while True:
        if not connection.in_atomic_block:  # nei test si è dentro la transazione del test: non si chiude
            close_old_connections()
        dettagli = _dettagli()
        try:
            if dettagli.get("riallinea"):
                segnala(NOME, stato="avviso", messaggio="Riallineamento da casa in corso…", dettagli={**dettagli, "riallinea": False})
                riallinea(scrivi=scrivi, forza=True)
                dettagli = _dettagli()
            esito = giro(scrivi=scrivi)
            if _pulizia_dovuta(dettagli):
                tolti = pulisci_file()
                if tolti:
                    scrivi(f"Tolti {tolti} file che nessuno usa più.")
                esito["pulizia_file_il"] = timezone.now().isoformat()
            problemi = esito["conflitti"] or esito["conflitti_altro"] or abs(esito["scarto_orologio"]) > 60
            segnala(NOME, stato="avviso" if problemi else "ok", messaggio=_riassunto(esito),
                    dettagli={**_dettagli(), **esito, "ultimo_ok": timezone.now().isoformat(), "primo_ok": True,
                              "ultimo_tentativo": timezone.now().isoformat()})
        except copia_remota.AltroNonRaggiungibile as e:
            segnala(NOME, stato="avviso", messaggio=f"{e} Le modifiche aspettano in coda.",
                    dettagli={**_dettagli(), "in_coda": Modifica.objects.count(), "ultimo_tentativo": timezone.now().isoformat()})
            scrivi(str(e))
        except CopiaNonRiuscita as e:
            segnala(NOME, stato="errore", messaggio=str(e)[:300],
                    dettagli={**_dettagli(), "in_coda": Modifica.objects.count(), "ultimo_tentativo": timezone.now().isoformat()})
            scrivi(str(e))
        except DatabaseError as e:
            scrivi(f"Database non raggiungibile ({e}): riprovo tra poco.")
            if una_volta:
                return
            close_old_connections()
            time.sleep(10)
            continue
        except Exception as e:  # noqa: BLE001 - un errore imprevisto non deve fermare il servizio
            registra_errore("sincronia", e, dove="sincronia con casa")
            segnala(NOME, stato="errore", messaggio=f"Errore imprevisto: {e}"[:300], dettagli=_dettagli())
            scrivi(f"Errore imprevisto: {e!r}")
        if una_volta or fermati:
            return
        # Si aspetta, ma ogni pochi secondi si guarda se dal pannello hanno chiesto «Sincronizza adesso».
        fine, passi = time.monotonic() + ogni_secondi, 0
        while time.monotonic() < fine and not fermati:
            time.sleep(1)
            passi += 1
            if passi % 5 == 0:
                dettagli = _dettagli()
                Battito.objects.filter(nome=NOME).update(ultimo_il=timezone.now())
                if dettagli.get("adesso") or dettagli.get("riallinea"):
                    Battito.objects.filter(nome=NOME).update(dettagli={**dettagli, "adesso": False})
                    break
        if fermati:
            scrivi("Spegnimento: un ultimo giro con casa.")
            una_volta = True


def chiedi_giro(using=DEFAULT_DB_ALIAS, *, riallinea_=False):
    """Dal pannello: il servizio fa un giro entro pochi secondi (o si riallinea da casa)."""
    battito = Battito.objects.using(using).filter(nome=NOME).first()
    if battito is None:
        return False
    chiave = "riallinea" if riallinea_ else "adesso"
    battito.dettagli = {**battito.dettagli, chiave: True}
    battito.save(update_fields=["dettagli"])
    return True


# ---------------------------------------------------------------------------
# Riallineare: copia completa dall'altro server (prima volta, dopo un aggiornamento)
# ---------------------------------------------------------------------------


def copia_di_sicurezza(nome, scrivi=print):
    """Salva il database di qui (senza tabelle locali) prima di sostituirlo."""
    cartella = Path(settings.CARTELLA_BACKUP)
    if not (cartella.is_dir() and os.access(cartella, os.W_OK)):
        cartella = Path(settings.BASE_DIR) / "dati" / "copie-di-sicurezza"
        cartella.mkdir(parents=True, exist_ok=True)
    file = cartella / f"{nome}-{timezone.localtime():%Y%m%d-%H%M%S}.sql.gz"
    with file.open("wb") as f:
        copia_database(f)
    scrivi(f"Copia di sicurezza dei dati di qui: {file}")
    return file


def riallinea(*, da=None, scrivi=print, forza=False):
    """Sostituisce i dati di qui con quelli dell'altro server (copia completa) e svuota la coda."""
    from . import carica

    da = (da or settings.SINCRONIA_CON).strip()
    if not da:
        raise SincroniaFerma("Manca l'indirizzo dell'altro server: SINCRONIA_CON nel file .env, oppure --da INDIRIZZO.")
    in_coda = Modifica.objects.count()
    if in_coda and not forza:
        raise SincroniaFerma(
            f"Qui ci sono {in_coda} modifiche che l'altro server non ha ancora: riallineando si perderebbero. "
            "Prima fai un giro (manage.py sincronia --una-volta); per riallineare lo stesso: --forza "
            "(prima si salva una copia di sicurezza di qui)."
        )
    with copia_remota.apri(da, "sistema/api/copia/") as risposta:
        stato = json.loads(risposta.read().decode("utf-8"))
    carica.controlla_versione(stato["migrazioni"])
    copia_di_sicurezza("prima-del-riallineamento", scrivi)
    with tempfile.TemporaryDirectory() as cartella:
        dump = Path(cartella) / "database.sql.gz"
        dimensione = copia_remota.scarica(da, "sistema/api/copia/database/", dump)
        carica.carica_database(dump, scrivi=scrivi)
    Modifica.objects.all().delete()
    Conflitto.objects.all().delete()
    prepara()
    presi = 0
    for percorso in file_mancanti():
        destinazione = copia_remota.percorso_allegato(percorso)
        if destinazione is None:
            continue
        destinazione.parent.mkdir(parents=True, exist_ok=True)
        try:
            copia_remota.scarica(da, "sistema/api/copia/allegato/", destinazione.with_name(destinazione.name + ".tmp"), percorso=percorso)
        except CopiaNonRiuscita:
            continue
        destinazione.with_name(destinazione.name + ".tmp").replace(destinazione)
        presi += 1
    scrivi(f"Riallineato da {da}: database ({dimensione // 1024} KB) e {presi} file.")
    return {"dimensione": dimensione, "file": presi, "programma": stato.get("programma", "")}
