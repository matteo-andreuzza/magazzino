"""Lo stato di tutto il sistema, per il pannello (/sistema/): server, container, stampanti,
dispositivi collegati, attività recenti, errori e versione.

Ogni servizio è un dizionario con:
- id, nome, cosa (cosa fa);
- stato: ok, avviso, errore, spento (non serve o non c'è) o sconosciuto (non si sa);
- testo: lo stato in una riga; avviato_il e ultimo_il (ISO) per «acceso da» e «ultimo segnale»;
- dettagli: [[etichetta, valore]]; aiuto: cosa controllare se non va.

Le informazioni sui container che non sono Django (backup, drive) arrivano dai file che
scrivono nella cartella dello stato (settings.CARTELLA_STATO) e dalla cartella dei backup.
"""

import os
import platform
import sys
import time
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import django
from django.conf import settings
from django.db import DatabaseError, connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Max
from django.utils import timezone

from . import sincronia as sincronia_
from .models import Battito, Conflitto, Dispositivo, Errore, Modifica
from .services import e_casa, impronta_codice

ATTIVO = timedelta(minutes=2)  # un dispositivo visto da meno di così è «attivo»
RECENTE = timedelta(hours=12)


def _iso(momento):
    return momento.isoformat() if momento else None


def _mb(byte):
    """Dimensione leggibile: «850 KB», «12,3 MB», «1,4 GB»."""
    byte = byte or 0
    for unita, divisore in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if byte >= divisore:
            return f"{byte / divisore:.1f} {unita}".replace(".", ",")
    return f"{byte} B"


# ---------------------------------------------------------------------------
# Computer
# ---------------------------------------------------------------------------


def _leggi(percorso):
    try:
        return Path(percorso).read_text()
    except OSError:
        return ""


def avvio_processo(pid):
    """Quando è partito il processo `pid` (da /proc, solo Linux)."""
    try:
        campi = _leggi(f"/proc/{pid}/stat").rsplit(")", 1)[1].split()
        inizio = int(campi[19]) / os.sysconf("SC_CLK_TCK")
        acceso = next(int(r.split()[1]) for r in _leggi("/proc/stat").splitlines() if r.startswith("btime"))
        return datetime.fromtimestamp(acceso + inizio, tz=UTC)
    except (IndexError, ValueError, StopIteration, OSError):
        return None


def server():
    """Il computer su cui gira il programma (in Docker: quello che ospita i container)."""
    dati = {"architettura": platform.machine(), "processori": os.cpu_count()}
    acceso = _leggi("/proc/uptime").split()
    if acceso:
        dati["acceso_da"] = float(acceso[0])
        dati["acceso_il"] = _iso(timezone.now() - timedelta(seconds=float(acceso[0])))
    carico = _leggi("/proc/loadavg").split()
    if carico:
        dati["carico"] = [float(c) for c in carico[:3]]
    memoria = {r.split(":")[0]: int(r.split()[1]) * 1024 for r in _leggi("/proc/meminfo").splitlines() if r.split()[1:]}
    if "MemTotal" in memoria:
        dati["memoria"] = {"totale": memoria["MemTotal"], "libera": memoria.get("MemAvailable", 0)}
    temperatura = _leggi("/sys/class/thermal/thermal_zone0/temp").strip()
    if temperatura.isdigit():
        dati["temperatura"] = int(temperatura) / 1000
    for nome, cartella in (("disco_backup", settings.CARTELLA_BACKUP), ("disco_dati", Path(settings.BASE_DIR) / "dati")):
        try:
            s = os.statvfs(cartella)
            dati[nome] = {"totale": s.f_blocks * s.f_frsize, "libero": s.f_bavail * s.f_frsize}
        except OSError:
            pass
    return dati


# ---------------------------------------------------------------------------
# Servizi (container)
# ---------------------------------------------------------------------------


def _servizio(id, nome, cosa, stato, testo, *, avviato_il=None, ultimo_il=None, dettagli=(), aiuto=()):
    return {
        "id": id, "nome": nome, "cosa": cosa, "stato": stato, "testo": testo,
        "avviato_il": _iso(avviato_il) if isinstance(avviato_il, datetime) else avviato_il,
        "ultimo_il": _iso(ultimo_il) if isinstance(ultimo_il, datetime) else ultimo_il,
        "dettagli": [[e, str(v)] for e, v in dettagli if v not in (None, "")],
        "aiuto": list(aiuto),
    }


def in_docker():
    return Path("/.dockerenv").exists()


def web(errori_recenti):
    avvio = avvio_processo(1 if in_docker() else os.getpid())
    stato, testo = "ok", "Risponde (questa pagina arriva da qui)."
    if errori_recenti:
        stato, testo = "avviso", f"Risponde, ma {errori_recenti} errori non ancora visti nell'ultima ora: vedi «Errori»."
    if settings.DEBUG:
        stato, testo = "avviso", "Modalità DEBUG accesa: va bene sul server di prova, non su quello vero."
    return _servizio(
        "web", "web", "L'app: pagine, cassa, API per il programma di stampa e per Claude (Django + gunicorn).",
        stato, testo, avviato_il=avvio, ultimo_il=timezone.now(),
        dettagli=[
            ["Python", platform.python_version()], ["Django", django.get_version()],
            ["Processo", "gunicorn" if "gunicorn" in " ".join(sys.argv) else Path(sys.argv[0]).name],
            ["Server", "di casa (sul web)" if e_casa() else "della sagra (rete locale)"],
        ],
        aiuto=[
            "Se le pagine non si aprono: docker compose ps (web deve essere «healthy»).",
            "Log: docker compose logs --tail=200 web. Riavvio: docker compose restart web.",
        ],
    )


def database():
    inizio = time.monotonic()
    try:
        with connection.cursor() as c:
            if connection.vendor == "postgresql":
                c.execute(
                    "SELECT pg_postmaster_start_time(), current_setting('server_version'), "
                    "pg_database_size(current_database()), "
                    "(SELECT count(*) FROM pg_stat_activity WHERE datname = current_database())"
                )
                avvio, versione, dimensione, collegamenti = c.fetchone()
            else:
                c.execute("SELECT 1")
                avvio, versione, dimensione, collegamenti = None, f"SQLite {connection.Database.sqlite_version}", None, None
    except DatabaseError as e:
        return _servizio("db", "db", "Il database PostgreSQL: tutti i dati.", "errore", f"Non risponde: {e}",
                         aiuto=["docker compose ps db; docker compose logs --tail=100 db", "Disco pieno? df -h"])
    risposta_ms = (time.monotonic() - inizio) * 1000
    return _servizio(
        "db", "db", "Il database PostgreSQL: tutti i dati (giacenze, ordini, fusti, utenti).",
        "ok" if risposta_ms < 500 else "avviso", f"Risponde in {risposta_ms:.0f} ms.",
        avviato_il=avvio, ultimo_il=timezone.now(),
        dettagli=[["Versione", versione], ["Dimensione", _mb(dimensione) if dimensione else ""],
                  ["Collegamenti aperti", collegamenti]],
        aiuto=["docker compose logs --tail=100 db", "Mai «docker compose down -v»: cancella il database."],
    )


def stampa():
    from cassa.models import AgenteStampa, Stampa, StatoStampa
    from cassa.stampa import NOME_SERVER

    if e_casa():
        return _servizio("stampa", "stampa", "Manda comande e ricevute alle stampanti di rete.", "spento",
                         "Spento: a casa non si stampa, le stampanti sono in sagra.")
    battito = Battito.objects.filter(nome="stampa").first()
    contatto = AgenteStampa.objects.filter(nome=NOME_SERVER).first()
    ultimo = battito.ultimo_il if battito else (contatto.ultimo_contatto if contatto else None)
    adesso = timezone.now()
    in_coda = Stampa.objects.filter(stato__in=[StatoStampa.IN_CODA, StatoStampa.IN_STAMPA]).count()
    errori = Stampa.objects.filter(stato=StatoStampa.ERRORE, creata_il__gte=adesso - timedelta(hours=6)).count()
    if ultimo is None:
        stato, testo = "sconosciuto", "Non si è mai fatto vivo: il servizio «stampa» non è mai partito."
    elif adesso - ultimo > timedelta(seconds=30):
        stato, testo = "errore", f"Fermo: ultimo segnale {timezone.localtime(ultimo):%d/%m %H:%M:%S}."
    elif errori:
        stato, testo = "avviso", f"Acceso; {errori} stampe in errore nelle ultime 6 ore."
    else:
        stato, testo = "ok", "Acceso: manda le stampe appena arrivano."
    dettagli = battito.dettagli if battito else {}
    return _servizio(
        "stampa", "stampa", "Manda comande e ricevute alle stampanti di rete (IPP e termiche).",
        stato, testo, avviato_il=battito.avviato_il if battito else None, ultimo_il=ultimo,
        dettagli=[["In coda o in stampa", in_coda], ["In errore (6 ore)", errori],
                  ["Stampate da quando è acceso", dettagli.get("stampate")], ["Versione", dettagli.get("versione")]],
        aiuto=["docker compose logs --tail=100 stampa", "Riavvio: docker compose restart stampa",
               "Una stampante: Cassa → Stampanti → Controlla."],
    )


def _stato_file(nome):
    """Il file chiave=valore che scrive un servizio shell (backup, drive), o None se non c'è."""
    testo = _leggi(Path(settings.CARTELLA_STATO) / nome)
    if not testo:
        return None
    return dict(r.split("=", 1) for r in testo.splitlines() if "=" in r)


def _data(testo):
    try:
        return datetime.fromisoformat(testo) if testo else None
    except ValueError:
        return None


def file_di_backup():
    cartella = Path(settings.CARTELLA_BACKUP)
    try:
        file = sorted(cartella.glob("*.sql.gz"), key=lambda f: f.stat().st_mtime, reverse=True)
    except OSError:
        return []
    return [
        {"nome": f.name, "dimensione": f.stat().st_size,
         "quando": _iso(datetime.fromtimestamp(f.stat().st_mtime, tz=UTC))}
        for f in file
    ]


def backup():
    stato_file = _stato_file("backup")
    elenco = file_di_backup()
    ultimo_file = _data(elenco[0]["quando"]) if elenco else None
    aiuto = ["docker compose logs --tail=50 backup", "Un backup adesso: scripts/backup-ora.sh",
             f"I file sono nella cartella backup/ del programma (cartella vista qui: {settings.CARTELLA_BACKUP})."]
    if stato_file is None and not elenco:
        return _servizio("backup", "backup", "Ogni tanto salva il database e le foto nella cartella backup/.",
                         "sconosciuto", "Non so niente: cartelle dei backup e dello stato non collegate a questo container.",
                         aiuto=aiuto)
    stato_file = stato_file or {}
    ultimo_ok = _data(stato_file.get("ultimo_ok")) or ultimo_file
    limite = timedelta(minutes=settings.BACKUP_OGNI_MINUTI * 2 + 5)
    errore = stato_file.get("ultimo_errore")
    if ultimo_ok is None:
        stato, testo = "errore", "Nessun backup riuscito."
    elif timezone.now() - ultimo_ok > limite:
        stato, testo = "errore", f"L'ultimo backup è del {timezone.localtime(ultimo_ok):%d/%m alle %H:%M}: il servizio è fermo?"
    elif errore and _data(stato_file.get("errore_il")) and _data(stato_file["errore_il"]) > ultimo_ok:
        stato, testo = "errore", f"L'ultimo tentativo è fallito: {errore}"
    else:
        stato, testo = "ok", f"Ultimo backup alle {timezone.localtime(ultimo_ok):%H:%M} del {timezone.localtime(ultimo_ok):%d/%m}."
    return _servizio(
        "backup", "backup", f"Ogni {settings.BACKUP_OGNI_MINUTI} minuti salva il database (e le foto nuove) nella cartella backup/.",
        stato, testo, avviato_il=_data(stato_file.get("avviato")), ultimo_il=ultimo_ok,
        dettagli=[["File di backup", len(elenco)], ["Spazio usato", _mb(sum(f["dimensione"] for f in elenco)) if elenco else ""],
                  ["Ultimo file", elenco[0]["nome"] if elenco else ""], ["Prossimo", stato_file.get("prossimo", "")]],
        aiuto=aiuto,
    )


def drive():
    stato_file = _stato_file("drive")
    aiuto = ["docker compose logs --tail=50 drive", "Configurarlo: README, «Backup su Google Drive».",
             "Senza internet riprova da solo ogni 10 minuti."]
    if stato_file is None:
        return _servizio("drive", "drive", "Copia la cartella backup/ su Google Drive quando c'è internet.",
                         "sconosciuto", "Non so niente: il servizio non ha mai scritto il suo stato.", aiuto=aiuto)
    situazione = stato_file.get("stato", "")
    ultimo_ok = _data(stato_file.get("ultimo_ok"))
    if situazione == "spento":
        stato, testo = "spento", "Non configurato: i backup restano solo sul computer (e sulla chiavetta)."
    elif situazione == "ok":
        stato, testo = "ok", "Collegato: i backup arrivano su Google Drive."
    else:
        quando = f" (ultima copia riuscita: {timezone.localtime(ultimo_ok):%d/%m %H:%M})" if ultimo_ok else ""
        stato, testo = "avviso", f"Copia su Google Drive non riuscita: manca internet?{quando}"
    return _servizio(
        "drive", "drive", "Copia la cartella backup/ su Google Drive quando c'è internet.", stato, testo,
        avviato_il=_data(stato_file.get("avviato")), ultimo_il=ultimo_ok,
        dettagli=[["Cartella su Drive", stato_file.get("cartella", "")], ["Ultimo tentativo", stato_file.get("tentativo_il", "")]],
        aiuto=aiuto,
    )


def _ora(testo):
    momento = _data(testo)
    return f"{timezone.localtime(momento):%d/%m %H:%M}" if momento else ""


def sincronia():
    """Il riquadro della sincronia: sulla sagra il servizio che chiama casa, a casa l'ultimo contatto."""
    cosa = ("Si scambia le modifiche con l'altro server: la sagra chiama casa ogni "
            f"{settings.SINCRONIA_OGNI_SECONDI:g} secondi quando c'è internet.")
    if not settings.COPIA_CHIAVE:
        return _servizio("sincronia", "sincronia", cosa, "spento", "Non configurata (COPIA_CHIAVE vuota): questo server lavora da solo.",
                         aiuto=["Manuale tecnico, «Sincronia con casa»."])
    battito = Battito.objects.filter(nome=sincronia_.NOME).first()
    d = battito.dettagli if battito else {}
    in_coda, conflitti = Modifica.objects.count(), Conflitto.objects.count()
    altri = d.get("conflitti_altro") or []
    dettagli = [
        ["Modifiche di qui in coda", in_coda], ["Conflitti di qui", conflitti],
        ["In coda dall'altra parte", d.get("in_coda_altro", "")], ["Conflitti dall'altra parte", len(altri)],
        ["Ultimo giro riuscito", _ora(d.get("ultimo_ok") or d.get("ultimo_contatto"))],
        ["Ultimo giro: righe mandate", d.get("inviate", "")], ["Ultimo giro: righe ricevute", d.get("ricevute", "")],
        ["Differenza tra gli orologi", f"{d['scarto_orologio']} s" if "scarto_orologio" in d else ""],
        ["Codice di qui", impronta_codice()],
    ]
    if settings.SINCRONIA_CON:
        aiuto = ["docker compose logs --tail=50 sincronia", "Serve internet; casa deve essere accesa e raggiungibile.",
                 "COPIA_CHIAVE uguale sui due server; SINCRONIA_CON = indirizzo di casa."]
        if battito is None:
            return _servizio("sincronia", "sincronia", cosa, "sconosciuto",
                             "Il servizio «sincronia» non è mai partito (COMPOSE_PROFILES=sincronia nel file .env).", aiuto=aiuto)
        vivo = battito.vivo(timedelta(minutes=2))
        return _servizio(
            "sincronia", "sincronia", cosa + f" Casa: {settings.SINCRONIA_CON}",
            battito.stato if vivo else "errore", battito.messaggio if vivo else "Il servizio «sincronia» è fermo.",
            avviato_il=battito.avviato_il, ultimo_il=battito.ultimo_il, dettagli=dettagli, aiuto=aiuto,
        )
    aiuto = ["La sagra chiama casa: guarda il pannello della sagra (docker compose logs sincronia, lì).",
             "Fuori dai giorni della sagra è normale che non si senta."]
    if not e_casa():
        return _servizio("sincronia", "sincronia", cosa, "avviso",
                         "Manca SINCRONIA_CON: questo server della sagra non chiama casa.", dettagli=dettagli, aiuto=aiuto)
    contatto = _data(d.get("ultimo_contatto"))
    if contatto is None:
        return _servizio("sincronia", "sincronia", cosa, "avviso", "Pronta: la sagra non si è ancora fatta sentire.",
                         dettagli=dettagli, aiuto=aiuto)
    recente = timezone.now() - contatto < timedelta(minutes=2)
    if battito.stato == "errore" and recente:
        stato, testo = "errore", battito.messaggio
    elif conflitti or altri:
        stato, testo = "avviso", "Ci sono conflitti: vedi «Sincronia» qui sotto."
    elif recente:
        stato, testo = "ok", f"La sagra si fa sentire (ultima volta alle {timezone.localtime(contatto):%H:%M})."
    else:
        stato, testo = "spento", f"La sagra non si sente dal {timezone.localtime(contatto):%d/%m alle %H:%M} (spenta o senza internet)."
    return _servizio("sincronia", "sincronia", cosa, stato, testo, ultimo_il=contatto, dettagli=dettagli, aiuto=aiuto)


def conflitti():
    """Per il pannello: le righe di qui che l'altro server non accetta, e quelle dell'altro che qui non passano."""
    battito = Battito.objects.filter(nome=sincronia_.NOME).first()
    d = battito.dettagli if battito else {}
    qui = [
        {"pk": c.pk, "descrizione": sincronia_.descrivi(c.tabella, c.chiave), "motivo": c.motivo, "volte": c.volte,
         "primo_il": c.primo_il, "doppione": bool(c.gemella and c.dati_gemella),
         "gemella": sincronia_.descrivi(c.tabella, c.gemella, dati=c.dati_gemella) if c.gemella else ""}
        for c in Conflitto.objects.all()[:30]
    ]
    return {"qui": qui, "altro": d.get("conflitti_altro") or [], "esiti": d.get("esiti_richieste") or [],
            "richieste": d.get("richieste") or []}


# ---------------------------------------------------------------------------
# Stampanti e dispositivi
# ---------------------------------------------------------------------------


def stampanti():
    from cassa.models import Connessione, Stampa, Stampante, StatoStampa

    from cassa.ipp import TESTI_BLOCCANTI

    adesso = timezone.now()
    controlli = {b.nome: b for b in Battito.objects.filter(nome__startswith="stampante-")}
    elenco = []
    for s in Stampante.objects.all():
        stampe = Stampa.objects.filter(stampante=s)
        ultima = stampe.filter(stato=StatoStampa.STAMPATA).aggregate(m=Max("stampata_il"))["m"]
        errori = stampe.filter(stato=StatoStampa.ERRORE, creata_il__gte=adesso - timedelta(hours=6)).count()
        in_coda = stampe.filter(stato__in=[StatoStampa.IN_CODA, StatoStampa.IN_STAMPA]).count()
        controllo = controlli.get(f"stampante-{s.pk}")
        recente = controllo and adesso - controllo.ultimo_il < timedelta(minutes=5)
        rilevato = controllo.messaggio if controllo else ""
        if not s.attiva:
            stato, testo = "spento", "Non attiva."
        elif s.connessione in (Connessione.BROWSER, Connessione.COMPUTER):
            stato, testo = ("avviso", f"{errori} stampe in errore.") if errori else ("ok", s.get_connessione_display())
        elif e_casa():
            stato, testo = "spento", "Da casa le stampanti della sagra non si controllano."
        elif not recente:
            stato, testo = "sconosciuto", "Non controllata di recente (il servizio di stampa la controlla ogni minuto)."
        elif controllo.stato == "errore":
            stato, testo = "errore", rilevato or "Non risponde."
        elif any(t in rilevato for t in TESTI_BLOCCANTI):
            stato, testo = "errore", rilevato
        elif errori or " · " in rilevato:
            stato, testo = "avviso", rilevato if " · " in rilevato else f"{errori} stampe in errore nelle ultime 6 ore."
        else:
            stato, testo = "ok", rilevato or "Risponde."
        elenco.append({
            "id": s.pk, "nome": s.nome, "tipo": s.get_connessione_display(), "connessione": s.connessione,
            "indirizzo": s.indirizzo, "stato": stato, "testo": testo,
            "modello": (controllo.dettagli.get("modello", "") if controllo else ""),
            "controllata_il": _iso(controllo.ultimo_il if controllo else None), "ultima_stampa": _iso(ultima),
            "in_coda": in_coda, "errori": errori,
        })
    return elenco


def dispositivi():
    from cassa.models import AgenteStampa
    from cassa.stampa import NOME_SERVER

    adesso = timezone.now()
    elenco = [
        {"id": f"d{d.pk}", "tipo": d.tipo, "ruolo": d.ruolo, "utente": d.utente, "ip": d.indirizzo_ip,
         "browser": d.browser, "pagina": d.ultima_pagina, "ultimo_il": _iso(d.ultimo_il),
         "attivo": adesso - d.ultimo_il < ATTIVO}
        for d in Dispositivo.objects.filter(ultimo_il__gte=adesso - RECENTE)[:30]
    ]
    for a in AgenteStampa.objects.exclude(nome=NOME_SERVER).filter(ultimo_contatto__gte=adesso - RECENTE):
        elenco.append({"id": f"a{a.pk}", "tipo": "agente", "ruolo": "Programma di stampa", "utente": a.nome,
                       "ip": a.indirizzo_ip, "browser": f"versione {a.versione}", "pagina": "",
                       "ultimo_il": _iso(a.ultimo_contatto), "attivo": a.collegato})
    return sorted(elenco, key=lambda d: (not d["attivo"], d["ruolo"], d["utente"] or ""))


# ---------------------------------------------------------------------------
# Programma: componenti, attività, errori, versione
# ---------------------------------------------------------------------------


def componenti():
    """Numeri per lo schema «dentro il programma»."""
    from assistente.models import ChiaveMCP
    from bar.models import Bevanda, Fusto, OperazioneBar
    from cassa.models import Ordine, Stampa, StatoStampa
    from magazzino.models import Operazione, Prodotto

    oggi = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "magazzino": {"prodotti": Prodotto.objects.count(), "operazioni_oggi": Operazione.objects.filter(creata_il__gte=oggi).count()},
        "bar": {"bevande": Bevanda.objects.count(), "fusti_attaccati": Fusto.objects.filter(finito_il__isnull=True).count(),
                "operazioni_oggi": OperazioneBar.objects.filter(creata_il__gte=oggi).count()},
        "cassa": {"ordini_oggi": Ordine.objects.filter(creato_il__gte=oggi, annullato_il__isnull=True).count()},
        "coda": {"in_coda": Stampa.objects.filter(stato=StatoStampa.IN_CODA).count(),
                 "errori": Stampa.objects.filter(stato=StatoStampa.ERRORE, creata_il__gte=timezone.now() - timedelta(hours=6)).count()},
        "assistente": {"chiavi": ChiaveMCP.objects.filter(revocata_il__isnull=True).count(),
                       "ultimo_uso": _iso(ChiaveMCP.objects.aggregate(m=Max("ultimo_uso"))["m"])},
        "sistema": {"errori_da_vedere": Errore.objects.filter(visto=False).count()},
    }


def attivita():
    """Quando è successa l'ultima volta ogni cosa importante."""
    from django.contrib.auth import get_user_model
    from django.db.migrations.recorder import MigrationRecorder

    from assistente.models import ChiaveMCP
    from bar.models import OperazioneBar, Pesata
    from cassa.models import Ordine, Stampa, StatoStampa
    from magazzino.models import Operazione

    def ultimo(modello, campo, **filtro):
        return modello.objects.filter(**filtro).aggregate(m=Max(campo))["m"]

    backup_file = file_di_backup()
    drive_file = _stato_file("drive") or {}
    remota = Battito.objects.filter(nome=sincronia_.NOME).first()
    righe = [
        ("Ultimo ordine in cassa", ultimo(Ordine, "creato_il")),
        ("Ultima stampa riuscita", ultimo(Stampa, "stampata_il", stato=StatoStampa.STAMPATA)),
        ("Ultima operazione di magazzino", ultimo(Operazione, "creata_il")),
        ("Ultima operazione del bar", ultimo(OperazioneBar, "creata_il")),
        ("Ultima pesata dei fusti", ultimo(Pesata, "registrata_il")),
        ("Ultimo backup", _data(backup_file[0]["quando"]) if backup_file else None),
        ("Ultima copia su Google Drive", _data(drive_file.get("ultimo_ok"))),
        ("Ultimo giro con l'altro server", _data((remota.dettagli.get("ultimo_ok") or remota.dettagli.get("ultimo_contatto")) if remota else None)),
        ("Ultimo uso di Claude", ultimo(ChiaveMCP, "ultimo_uso")),
        ("Ultimo accesso di un utente", ultimo(get_user_model(), "last_login")),
        ("Ultima migrazione applicata", ultimo(MigrationRecorder.Migration, "applied")),
        ("Avvio dell'app", avvio_processo(1 if in_docker() else os.getpid())),
    ]
    return [{"cosa": cosa, "quando": _iso(quando)} for cosa, quando in righe]


def errori():
    return [
        {"id": e.pk, "origine": e.origine, "tipo": e.tipo, "messaggio": e.messaggio[:500], "dove": e.dove,
         "utente": e.utente, "volte": e.volte, "primo_il": _iso(e.primo_il), "ultimo_il": _iso(e.ultimo_il),
         "visto": e.visto, "traccia": e.traccia}
        for e in Errore.objects.all()[:40]
    ]


def stampe_in_errore():
    from cassa.models import Stampa, StatoStampa

    return [
        {"id": s.pk, "cosa": s.descrizione, "stampante": s.stampante.nome, "errore": s.errore,
         "ordine": s.ordine.numero if s.ordine_id else None, "quando": _iso(s.creata_il)}
        for s in Stampa.objects.filter(stato=StatoStampa.ERRORE).select_related("stampante", "ordine").order_by("-creata_il")[:15]
    ]


@lru_cache(maxsize=1)
def _migrazioni_da_applicare(_minuto):
    executor = MigrationExecutor(connection)
    piano = executor.migration_plan(executor.loader.graph.leaf_nodes())
    return [f"{m.app_label}.{m.name}" for m, _ in piano]


def versione():
    from django.db.migrations.recorder import MigrationRecorder

    ultime = {}
    for app, nome in sorted(MigrationRecorder(connection).applied_migrations()):
        ultime[app] = nome
    try:
        mancanti = _migrazioni_da_applicare(int(time.time() // 60))
    except DatabaseError:
        mancanti = []
    return {
        "codice": impronta_codice(),
        "python": platform.python_version(),
        "django": django.get_version(),
        "debug": settings.DEBUG,
        "modalita": settings.SERVER_NOME,
        "migrazioni": [[app, nome] for app, nome in ultime.items() if app in {"magazzino", "bar", "cassa", "assistente", "sistema"}],
        "da_applicare": mancanti,
    }


def stato(request=None):
    """Tutto quello che mostra il pannello."""
    errori_recenti = Errore.objects.filter(visto=False, ultimo_il__gte=timezone.now() - timedelta(hours=1)).count()
    return {
        "generato_il": timezone.now().isoformat(),
        "programma": settings.APP_NOME,
        "indirizzo": request.get_host() if request else "",
        "modalita": settings.SERVER_NOME,
        "sincronia_con": settings.SINCRONIA_CON,
        "server": server(),
        "servizi": [web(errori_recenti), database(), stampa(), backup(), drive(), sincronia()],
        "stampanti": stampanti(),
        "dispositivi": dispositivi(),
        "componenti": componenti(),
        "attivita": attivita(),
        "errori": errori(),
        "stampe_in_errore": stampe_in_errore(),
        "versione": versione(),
        "backup": file_di_backup()[:8],
    }
