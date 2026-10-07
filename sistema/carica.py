"""Caricare su questo server una copia del database fatta altrove (chiavetta, copia remota, trasloco).

Passi, tutti in una sola transazione (se qualcosa va storto il database resta com'era):
1. controllo della versione: la copia non deve avere migrazioni che il codice di
   questo server non conosce (cioè il programma qui deve essere aggiornato almeno
   quanto quello che ha fatto la copia);
2. si tolgono le tabelle che la copia ricrea e quelle che la versione della copia non
   conosce (le rifanno le migrazioni di qui); restano quelle locali di questo server
   (sessioni, pannello del sistema: vedi copia.TABELLE_LOCALI) e quelle che chi ha fatto
   la copia considera locali (anche se qui il codice è un po' diverso);
3. si carica la copia;
4. si rimettono le migrazioni delle app locali com'erano qui.
Poi si applicano le migrazioni che questo server ha in più (se è più aggiornato).
"""

import gzip
import hashlib
import subprocess
import tempfile
from typing import NamedTuple

from django.core.management import call_command
from django.db import connection
from django.db.migrations.loader import MigrationLoader

from .copia import TABELLE_LOCALI, CopiaNonRiuscita, ambiente_postgres

APP_LOCALI = {"sistema"}
TABELLE_LOCALI_SQL = tuple(t for t in TABELLE_LOCALI if not t.endswith("*"))
PREFISSI_LOCALI = tuple(t.rstrip("*") for t in TABELLE_LOCALI if t.endswith("*"))


class VersioneIncompatibile(CopiaNonRiuscita):
    pass


def _righe(percorso_gz):
    with gzip.open(percorso_gz, "rt", encoding="utf-8", errors="replace") as f:
        yield from f


class Dump(NamedTuple):
    migrazioni: set  # {(app, nome)}
    contiene_locali: bool  # copia completa (trasloco, backup) con anche le tabelle locali
    impronta: str  # cambia solo se cambiano i dati
    tabelle: set  # le tabelle che la copia crea


def leggi_dump(percorso_gz):
    """Cosa c'è in un file .sql.gz fatto con pg_dump.

    L'impronta riguarda solo i dati: la riga \\restrict di pg_dump cambia a ogni copia.
    """
    migrazioni, tabelle, locali, dentro = set(), set(), False, False
    somma = hashlib.sha1()
    trovata = False
    for riga in _righe(percorso_gz):
        if not riga.startswith(("\\restrict", "\\unrestrict")):
            somma.update(riga.encode())
        if dentro:
            if riga.startswith("\\."):
                dentro = False
                continue
            campi = riga.rstrip("\n").split("\t")
            if len(campi) >= 3:
                migrazioni.add((campi[1], campi[2]))
        elif riga.startswith("COPY public.django_migrations "):
            dentro, trovata = True, True
        elif riga.startswith("CREATE TABLE public."):
            nome = riga[len("CREATE TABLE public."):].split(" ", 1)[0].strip('"')
            tabelle.add(nome)
            locali = locali or nome.startswith(PREFISSI_LOCALI)
    if not trovata:
        raise CopiaNonRiuscita("Il file non sembra una copia del database del programma (mancano le migrazioni).")
    return Dump(migrazioni, locali, somma.hexdigest(), tabelle)


def migrazioni_sconosciute(migrazioni):
    """Le migrazioni della copia che il codice di questo server non ha."""
    note = set(MigrationLoader(None, ignore_no_migrations=True).disk_migrations)
    return sorted(m for m in migrazioni if tuple(m) not in note and m[0] not in APP_LOCALI)


def controlla_versione(migrazioni):
    ignote = migrazioni_sconosciute(migrazioni)
    if ignote:
        elenco = ", ".join(f"{app}.{nome}" for app, nome in ignote[:6]) + (" …" if len(ignote) > 6 else "")
        raise VersioneIncompatibile(
            "La copia viene da un programma più aggiornato di questo (migrazioni sconosciute: "
            f"{elenco}). Aggiorna prima questo server (scripts/aggiorna.sh --pull), poi riprova."
        )


def _citazione(nome):
    return '"' + nome.replace('"', '""') + '"'


def _letterale(testo):
    return "'" + str(testo).replace("'", "''") + "'"


def tabelle_attese(migrazioni):
    """Le tabelle che esistono in un database con queste migrazioni applicate."""
    loader = MigrationLoader(None, ignore_no_migrations=True)
    nodi = [tuple(m) for m in migrazioni if tuple(m) in loader.graph.nodes]
    stato = loader.project_state(nodes=nodi, at_end=True)
    return {m._meta.db_table for m in stato.apps.get_models(include_auto_created=True)}


def _tabelle_da_togliere(dump):
    with connection.cursor() as cursore:
        cursore.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
        tabelle = [r[0] for r in cursore.fetchall()]
    attese = tabelle_attese(dump.migrazioni)
    return [
        t for t in tabelle
        if t not in TABELLE_LOCALI_SQL and not t.startswith(PREFISSI_LOCALI)
        and (t in dump.tabelle or t not in attese)
    ]


def _migrazioni_locali_qui():
    with connection.cursor() as cursore:
        if "django_migrations" not in connection.introspection.table_names(cursore):
            return []
        cursore.execute(
            "SELECT app, name, applied FROM django_migrations WHERE app = ANY(%s) ORDER BY id", [sorted(APP_LOCALI)]
        )
        return cursore.fetchall()


def carica_database(percorso_gz, *, scrivi=print, migra=True):
    """Sostituisce il database di questo server con la copia. Restituisce l'impronta dei dati."""
    ambiente = ambiente_postgres()
    dump = leggi_dump(percorso_gz)
    controlla_versione(dump.migrazioni)

    with tempfile.NamedTemporaryFile("w", suffix=".sql", encoding="utf-8") as sql:
        sql.write("SET client_min_messages = warning;\n")
        for tabella in _tabelle_da_togliere(dump):
            sql.write(f"DROP TABLE IF EXISTS public.{_citazione(tabella)} CASCADE;\n")
        for riga in _righe(percorso_gz):
            sql.write(riga)
        if not dump.contiene_locali:
            # Le tabelle locali sono rimaste quelle di qui: anche le loro migrazioni.
            app = ", ".join(_letterale(a) for a in sorted(APP_LOCALI))
            sql.write(f"\nDELETE FROM public.django_migrations WHERE app IN ({app});\n")
            for nome_app, nome, applicata in _migrazioni_locali_qui():
                sql.write(
                    "INSERT INTO public.django_migrations (app, name, applied) VALUES "
                    f"({_letterale(nome_app)}, {_letterale(nome)}, {_letterale(applicata.isoformat())});\n"
                )
        sql.flush()
        connection.close()  # psql lavora da solo: niente transazioni aperte di qui
        esito = subprocess.run(
            ["psql", "-q", "-o", "/dev/null", "-v", "ON_ERROR_STOP=1", "--single-transaction", "-f", sql.name],
            env=ambiente, capture_output=True, text=True,
        )
    if esito.returncode != 0:
        dettaglio = (esito.stderr or esito.stdout).strip().splitlines()
        raise CopiaNonRiuscita(f"Caricamento non riuscito, il database è rimasto com'era: {' '.join(dettaglio[-3:])[:400]}")
    if migra:
        scrivi("Copia caricata; applico le migrazioni di questo server…")
        call_command("migrate", interactive=False, verbosity=0)
    return dump.impronta
