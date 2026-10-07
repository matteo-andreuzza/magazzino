"""Copia remota: un altro server (es. il Raspberry di casa) tiene una copia aggiornata della sagra.

Come funziona:
- la sagra è il server «vero»: lì si lavora, anche senza internet;
- il server di casa ha COPIA_DI=indirizzo della sagra (di solito attraverso Tailscale) e la
  stessa COPIA_CHIAVE: ogni pochi minuti, quando la sagra è raggiungibile, le chiede la copia
  del database e gli allegati nuovi e li carica (vedi carica.py). È di sola lettura;
- se la copia arriva da un programma più aggiornato, il server di casa non la carica e lo dice:
  si aggiorna prima casa, poi la sagra.

La sagra risponde solo a chi ha la chiave (API in views.py); con COPIA_CHIAVE vuota non
risponde a nessuno.
"""

import json
import os
from datetime import datetime
import secrets
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from django.conf import settings
from django.db import DatabaseError, connection
from django.utils import timezone

from . import carica
from .copia import CopiaNonRiuscita, ultimo_dato
from .errori import registra_errore
from .models import Battito
from .services import segnala

NOME = "copia"  # battito del server di casa
NOME_INVIATA = "copia-inviata"  # battito della sagra: l'ultima volta che qualcuno ha preso la copia
TIMEOUT = 30


# ---------------------------------------------------------------------------
# Lato sagra: chi chiede la copia
# ---------------------------------------------------------------------------


def chiave_valida(request):
    chiave = settings.COPIA_CHIAVE
    tipo, _, ricevuta = request.headers.get("Authorization", "").partition(" ")
    return bool(chiave) and tipo.lower() == "bearer" and secrets.compare_digest(ricevuta.strip().encode(), chiave.encode())


def elenco_allegati():
    """[[percorso, dimensione]] dei file caricati (fatture, foto)."""
    radice = Path(settings.MEDIA_ROOT)
    if not radice.is_dir():
        return []
    return sorted(
        [str(f.relative_to(radice)), f.stat().st_size]
        for f in radice.rglob("*")
        if f.is_file() and not f.name.endswith(".tmp")
    )


def file_allegato(percorso):
    """Il file richiesto, solo se è davvero dentro la cartella degli allegati."""
    radice = Path(settings.MEDIA_ROOT).resolve()
    file = (radice / percorso).resolve()
    if radice not in file.parents or not file.is_file():
        return None
    return file


# ---------------------------------------------------------------------------
# Lato casa: prendere la copia
# ---------------------------------------------------------------------------


class SagraNonRaggiungibile(CopiaNonRiuscita):
    pass


def _richiesta(via, **parametri):
    indirizzo = settings.COPIA_DI.rstrip("/") + "/" + via
    if parametri:
        indirizzo += "?" + urllib.parse.urlencode(parametri)
    return urllib.request.Request(
        indirizzo, headers={"Authorization": f"Bearer {settings.COPIA_CHIAVE}", "User-Agent": "copia-remota-sagra"}
    )


def _apri(via, **parametri):
    try:
        return urllib.request.urlopen(_richiesta(via, **parametri), timeout=TIMEOUT)
    except urllib.error.HTTPError as e:
        if e.code in (401, 404):
            raise CopiaNonRiuscita(
                "La sagra rifiuta la richiesta: COPIA_CHIAVE deve essere uguale nel file .env dei due server "
                "(e la sagra deve avere il programma aggiornato)."
            ) from None
        raise CopiaNonRiuscita(f"La sagra ha risposto con un errore (HTTP {e.code}).") from None
    except (urllib.error.URLError, OSError) as e:
        motivo = getattr(e, "reason", None) or e
        raise SagraNonRaggiungibile(f"La sagra non è raggiungibile ({motivo}): spenta o senza internet?") from None


def _scarica(via, destinazione, **parametri):
    with _apri(via, **parametri) as risposta, open(destinazione, "wb") as f:
        shutil.copyfileobj(risposta, f)
    return os.path.getsize(destinazione)


def _allegati(remoti, scrivi):
    """Scarica gli allegati nuovi o cambiati e toglie quelli che alla sagra non ci sono più."""
    radice = Path(settings.MEDIA_ROOT)
    radice.mkdir(parents=True, exist_ok=True)
    voluti = {}
    for percorso, dimensione in remoti:
        destinazione = (radice / percorso).resolve()
        if radice.resolve() not in destinazione.parents:
            continue
        voluti[destinazione] = dimensione
    scaricati = 0
    for destinazione, dimensione in voluti.items():
        if destinazione.is_file() and destinazione.stat().st_size == dimensione:
            continue
        destinazione.parent.mkdir(parents=True, exist_ok=True)
        temporaneo = destinazione.with_name(destinazione.name + ".tmp")
        _scarica("sistema/api/copia/allegato/", temporaneo, percorso=str(destinazione.relative_to(radice.resolve())))
        temporaneo.replace(destinazione)
        scaricati += 1
    tolti = 0
    for file in list(radice.rglob("*")):
        if file.is_file() and file.resolve() not in voluti:
            file.unlink()
            tolti += 1
    if scaricati or tolti:
        scrivi(f"Allegati: {scaricati} scaricati, {tolti} tolti.")
    return scaricati, tolti


class DirezioneSbagliata(CopiaNonRiuscita):
    pass


def controlla_direzione(ultimo_della_sagra):
    """Mai sovrascrivere dati più nuovi di quelli della copia: vorrebbe dire che COPIA_DI è
    impostato sul server sbagliato (per esempio su quello della sagra, che punta a casa)."""
    qui = ultimo_dato()
    sagra = datetime.fromisoformat(ultimo_della_sagra) if ultimo_della_sagra else None
    if qui and (sagra is None or qui > sagra):
        raise DirezioneSbagliata(
            f"Questo server ha dati più recenti della copia (ultimo dato qui: {timezone.localtime(qui):%d/%m %H:%M}): "
            "non la carico per non perderli. COPIA_DI va impostato solo sul server di casa, mai su quello della sagra."
        )


def sincronizza(*, scrivi=print, forza=False, sovrascrivi=False):
    """Una copia: database (se è cambiato) e allegati. Restituisce i dettagli per il pannello.

    forza: ricarica il database anche se non è cambiato; sovrascrivi: anche se qui ci sono
    dati più recenti (solo a mano, da riga di comando: es. per ripartire da una sagra nuova).
    """
    inizio = time.monotonic()
    with _apri("sistema/api/copia/") as risposta:
        stato = json.loads(risposta.read().decode("utf-8"))
    carica.controlla_versione(stato["migrazioni"])
    if not sovrascrivi:
        controlla_direzione(stato.get("ultimo_dato"))
    precedente = (Battito.objects.filter(nome=NOME).values_list("dettagli", flat=True).first() or {})
    with tempfile.TemporaryDirectory() as cartella:
        dump = Path(cartella) / "database.sql.gz"
        dimensione = _scarica("sistema/api/copia/database/", dump)
        impronta = carica.leggi_dump(dump).impronta
        cambiato = forza or impronta != precedente.get("impronta_dati")
        if cambiato:
            carica.carica_database(dump, scrivi=scrivi)
            scrivi(f"Database caricato ({dimensione // 1024} KB).")
    scaricati, tolti = _allegati(stato.get("allegati", []), scrivi)
    adesso = timezone.now().isoformat()
    return {
        "impronta_dati": impronta,
        "copia_il": adesso if cambiato else precedente.get("copia_il", adesso),
        "controllata_il": adesso,
        "programma_sagra": stato.get("programma", ""),
        "codice_sagra": stato.get("codice", ""),
        "dimensione": dimensione,
        "allegati": len(stato.get("allegati", [])),
        "allegati_scaricati": scaricati,
        "allegati_tolti": tolti,
        "durata": round(time.monotonic() - inizio, 1),
        "cambiato": cambiato,
    }


def ciclo(*, ogni_minuti, scrivi=print, una_volta=False):
    """Il servizio «copia» del server di casa: prova ogni `ogni_minuti`, o subito se lo si chiede dal pannello."""
    avviato = timezone.now()
    # All'avvio il database può non essere pronto (migrazioni in corso nel container «web»).
    while True:
        try:
            segnala(NOME, stato="avviso", messaggio="Avviato: prima copia in corso…", avviato_il=avviato)
            break
        except DatabaseError:
            scrivi("Database non ancora pronto (migrazioni in corso?): aspetto.")
            connection.close()
            time.sleep(5)
    while True:
        dettagli = Battito.objects.filter(nome=NOME).values_list("dettagli", flat=True).first() or {}
        try:
            nuovi = sincronizza(scrivi=scrivi, forza=dettagli.get("forza", False))
            quando = timezone.localtime(datetime.fromisoformat(nuovi["copia_il"]))
            segnala(NOME, stato="ok", messaggio=f"Copia aggiornata: dati della sagra delle {quando:%H:%M} del {quando:%d/%m}.", dettagli=nuovi)
        except carica.VersioneIncompatibile as e:
            segnala(NOME, stato="errore", messaggio=str(e), dettagli={**dettagli, "forza": False, "controllata_il": timezone.now().isoformat()})
            scrivi(str(e))
        except SagraNonRaggiungibile as e:
            segnala(NOME, stato="avviso", messaggio=str(e), dettagli={**dettagli, "forza": False, "controllata_il": timezone.now().isoformat()})
            scrivi(str(e))
        except CopiaNonRiuscita as e:
            segnala(NOME, stato="errore", messaggio=str(e), dettagli={**dettagli, "forza": False})
            scrivi(str(e))
        except DatabaseError as e:
            scrivi(f"Database non raggiungibile ({e}): riprovo tra poco.")
            connection.close()
            time.sleep(10)
            continue
        except Exception as e:  # noqa: BLE001 - un errore imprevisto non deve fermare il servizio
            registra_errore("copia", e, dove="copia remota")
            segnala(NOME, stato="errore", messaggio=f"Errore imprevisto: {e}"[:300], dettagli={**dettagli, "forza": False})
            scrivi(f"Errore imprevisto: {e!r}")
        if una_volta:
            return
        # Si aspetta, ma ogni 10 secondi si guarda se dal pannello hanno chiesto «Copia adesso».
        fine = time.monotonic() + ogni_minuti * 60
        while time.monotonic() < fine:
            time.sleep(10)
            dettagli = Battito.objects.filter(nome=NOME).values_list("dettagli", flat=True).first() or {}
            Battito.objects.filter(nome=NOME).update(ultimo_il=timezone.now())
            if dettagli.get("adesso"):
                Battito.objects.filter(nome=NOME).update(dettagli={**dettagli, "adesso": False})
                break
