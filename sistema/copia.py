"""Copie dei dati da portare su un altro server: chiavetta (Copia dati) e riallineamento
della sincronia (vedi sincronia.py).

Il database si copia con pg_dump, come i backup, ma senza le tabelle che riguardano solo
questo server (TABELLE_LOCALI: sessioni di chi è collegato, pannello del sistema): chi
riceve la copia tiene le sue.

La copia per la chiavetta è un file .tar con:
- LEGGIMI.txt: come caricarla;
- info.json: da dove viene, quando, con quale versione del programma (migrazioni);
- database.sql.gz: il database;
- dati.tar.gz: fatture e foto dei prodotti (senza la chiave segreta del server).
È compatibile con il trasloco (scripts/esporta-dati.sh) e si carica con scripts/importa-dati.sh.
"""

import gzip
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.db.models import Max
from django.db.migrations.recorder import MigrationRecorder
from django.utils import timezone
from django.utils.text import slugify

from .services import impronta_codice

# Tabelle di questo server che non si copiano (modelli di pg_dump): sessioni di chi è
# collegato, segnali di vita del programma di stampa, pannello del sistema. Cambiano da sole
# ogni pochi secondi e riguardano solo il server su cui stanno.
TABELLE_LOCALI = ["django_session", "cassa_agentestampa", "sistema_*"]
# Nelle copie sì, nella sincronia no: ogni server se le crea da solo con le migrazioni (tipi di
# contenuto e permessi, con id che possono essere diversi) e il registro dell'admin, che li usa.
ESCLUSE_DALLA_SINCRONIA = [
    "django_migrations", "django_content_type", "auth_permission", "auth_group_permissions",
    "auth_user_user_permissions", "django_admin_log",
]
# Nella cartella dei dati: questi non vanno nella copia.
ESCLUSI = {"secret_key"}


class CopiaNonRiuscita(Exception):
    pass


def nome_file(adesso=None):
    adesso = timezone.localtime(adesso)
    return f"copia-{slugify(settings.APP_NOME) or 'sagra'}-{adesso:%Y%m%d-%H%M}.tar"


def ambiente_postgres():
    """Variabili d'ambiente per pg_dump e psql, prese dalle impostazioni del database."""
    db = settings.DATABASES["default"]
    if "postgresql" not in db["ENGINE"]:
        raise CopiaNonRiuscita("Si può fare solo con l'installazione con PostgreSQL (quella con Docker).")
    if not shutil.which("pg_dump"):
        raise CopiaNonRiuscita("Manca pg_dump nell'immagine dell'app: aggiornala (docker compose up -d --build).")
    return {
        **os.environ,
        "PGHOST": db["HOST"],
        "PGPORT": str(db.get("PORT") or 5432),
        "PGUSER": db["USER"],
        "PGPASSWORD": db["PASSWORD"],
        "PGDATABASE": db["NAME"],
    }


def migrazioni_applicate():
    """[(app, nome)] delle migrazioni applicate a questo database: dicono la versione dei dati."""
    return sorted(MigrationRecorder(connection).applied_migrations())


def ultimo_dato():
    """Il momento dell'ultima cosa registrata (ordine, operazione di magazzino o del bar, pesata), o None."""
    from bar.models import OperazioneBar, Pesata
    from cassa.models import Ordine
    from magazzino.models import Operazione

    momenti = [
        Ordine.objects.aggregate(m=Max("creato_il"))["m"],
        Operazione.objects.aggregate(m=Max("creata_il"))["m"],
        OperazioneBar.objects.aggregate(m=Max("creata_il"))["m"],
        Pesata.objects.aggregate(m=Max("registrata_il"))["m"],
    ]
    momenti = [m for m in momenti if m]
    return max(momenti) if momenti else None


def info():
    ultimo = ultimo_dato()
    return {
        "programma": settings.APP_NOME,
        "creata_il": timezone.now().isoformat(),
        "codice": impronta_codice(),
        "ultimo_dato": ultimo.isoformat() if ultimo else None,
        "migrazioni": [list(m) for m in migrazioni_applicate()],
    }


def copia_database(destinazione):
    """Scrive in `destinazione` (file binario) il database compresso, senza le tabelle locali."""
    ambiente = ambiente_postgres()
    comando = ["pg_dump", "--no-owner", "--clean", "--if-exists"]
    comando += [f"--exclude-table={t}" for t in TABELLE_LOCALI]
    with tempfile.TemporaryFile() as errori, gzip.GzipFile(fileobj=destinazione, mode="wb", mtime=0) as compresso:
        processo = subprocess.Popen(comando, stdout=subprocess.PIPE, stderr=errori, env=ambiente)
        shutil.copyfileobj(processo.stdout, compresso)
        if processo.wait() != 0:
            errori.seek(0)
            dettaglio = errori.read().decode(errors="replace").strip()[:300]
            raise CopiaNonRiuscita(f"Copia del database non riuscita: {dettaglio or 'errore di pg_dump'}")


def scrivi_copia(file_tar, *, adesso=None, database=None):
    """Scrive la copia per la chiavetta nel file (aperto in scrittura binaria)."""
    database = database or copia_database
    adesso = timezone.localtime(adesso)
    cartella_dati = Path(settings.BASE_DIR) / "dati"
    with tempfile.TemporaryDirectory() as cartella:
        cartella = Path(cartella)
        with (cartella / "database.sql.gz").open("wb") as f:
            database(f)
        with tarfile.open(cartella / "dati.tar.gz", "w:gz") as dati:
            if cartella_dati.is_dir():
                for elemento in sorted(cartella_dati.iterdir()):
                    if elemento.name not in ESCLUSI:
                        dati.add(elemento, arcname=elemento.name)
        (cartella / "info.json").write_text(json.dumps(info(), indent=1), encoding="utf-8")
        (cartella / "LEGGIMI.txt").write_text(
            f"Copia dei dati di {settings.APP_NOME} del {adesso:%d/%m/%Y %H:%M}.\n\n"
            "Per caricarla su un altro server (per esempio quello di casa), nella cartella del programma:\n"
            "  scripts/importa-dati.sh QUESTO-FILE.tar\n"
            "I dati di quel server vengono sostituiti da quelli della copia. Il server che la riceve\n"
            "deve avere il programma aggiornato almeno quanto questo (se no lo script lo dice e si ferma).\n",
            encoding="utf-8",
        )
        with tarfile.open(fileobj=file_tar, mode="w") as copia:
            for nome in ("LEGGIMI.txt", "info.json", "database.sql.gz", "dati.tar.gz"):
                copia.add(cartella / nome, arcname=nome)
