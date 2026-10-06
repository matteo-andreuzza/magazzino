# Manuale tecnico

Per chi installa, aggiorna e mantiene il programma. Chi lo usa alla sagra ha la **guida dentro
l'app** (il pulsante **?** in alto, pagina `/guida/`), stampabile.

- [Com'è fatto](#comè-fatto)
- [Comandi di tutti i giorni](#comandi-di-tutti-i-giorni)
- [Aggiornare senza rompere i dati](#aggiornare-senza-rompere-i-dati)
- [Backup](#backup)
- [Spostare l'app su un nuovo server](#spostare-lapp-su-un-nuovo-server)
- [Kubernetes](#kubernetes)
- [Monitor cucina](#monitor-cucina)
- [Sviluppo](#sviluppo)
- [Problemi comuni](#problemi-comuni)

## Com'è fatto

Un'applicazione Django con PostgreSQL, in Docker Compose:

| Servizio | Cosa fa |
| --- | --- |
| `web` | L'app (gunicorn, file statici serviti da whitenoise). All'avvio applica da sola le migrazioni del database e crea l'amministratore (`entrypoint.sh`). |
| `db` | PostgreSQL 17, dati nel volume `db_dati`. |
| `backup` | Ogni 30 minuti copia il database (e fatture e foto nuove) nella cartella `backup/`. |
| `drive` | Se configurato, copia `backup/` su Google Drive. |
| `caddy` | Solo in cloud (`docker-compose.cloud.yml`): HTTPS automatico. |

Le app Django (cartelle del progetto):

| App | Cosa contiene |
| --- | --- |
| `magazzino` | Prodotti, frighi e congelatori (ubicazioni), movimenti, serate (`Servizio`), menu e piatti, la guida. |
| `cassa` | Listino, ordini (anche da asporto), comande, stampe (ESC/POS e browser), monitor cucina, report. |
| `bar` | Bevande, carichi, fusti: spinatrici, spine, pesate, litri usciti. |
| `assistente` | Il server MCP per Claude (`/mcp/` via HTTP, `manage.py server_mcp` via stdin). |

Regole del codice: la logica sta nei `services.py` (le viste e il server MCP li chiamano, così i
controlli sono gli stessi ovunque); le giacenze sono sempre la somma dei movimenti, mai un numero
salvato; tutto in italiano, anche i nomi nel codice. I file caricati (fatture, foto) stanno nel
volume `app_dati` (`/app/dati` nel container), insieme alla chiave segreta generata al primo avvio.

Il **programma di stampa** (`cassa/agente/agente_stampa.py`) gira su un computer della sagra,
chiede le stampe al server via HTTP(S) e le manda alle stampanti termiche: non servono porte aperte.

## Comandi di tutti i giorni

Nella cartella del programma (in cloud aggiungi sempre `-f docker-compose.yml -f docker-compose.cloud.yml`):

| Cosa | Comando |
| --- | --- |
| Stato dei servizi | `docker compose ps` |
| Log dell'app | `docker compose logs -f web` |
| Riavviare l'app | `docker compose restart web` |
| Fermare / riavviare tutto | `docker compose down` / `docker compose up -d` |
| Shell di Django | `docker compose exec web python manage.py shell` |
| Backup adesso | `scripts/backup-ora.sh` |
| Controllo di salute | `curl http://localhost:8000/salute/` (risponde `ok` se app e database funzionano) |

`docker compose down` non cancella i dati. **Mai `docker compose down -v`** sull'installazione vera:
cancella il database.

Tutti gli script in `scripts/` lavorano sull'installazione della cartella; per un'altra:
`COMPOSE="docker compose -f docker-compose.yml -f docker-compose.cloud.yml" scripts/…`.

## Aggiornare senza rompere i dati

I dati non stanno nell'immagine ma nei volumi: ricostruire l'app non li tocca. Quello che può
cambiarli sono le **migrazioni** (le modifiche alla struttura del database che arrivano con il
codice nuovo), applicate all'avvio. Per questo un aggiornamento si fa così:

1. **Prova il codice nuovo sui dati veri, senza toccarli**:

   ```sh
   scripts/backup-ora.sh             # una copia fresca dei dati veri
   scripts/prova-aggiornamento.sh    # la carica nel server di prova (porta 8002) e ci applica il codice nuovo
   ```

   Il server di prova (progetto `magazzino-debug`, database suo) prende la copia dei dati veri,
   applica le migrazioni del codice della cartella e controlla che l'app risponda. Aprilo
   (`http://localhost:8002`) e prova le cose cambiate: cassa, fusti, report.
2. **Aggiorna** l'installazione vera, fuori dalle serate:

   ```sh
   scripts/aggiorna.sh               # oppure: scripts/aggiorna.sh --pull per scaricare prima il codice (git pull)
   ```

   Lo script fa un backup, costruisce l'immagine nuova (se fallisce, l'app vecchia resta accesa e
   non cambia niente), mostra le migrazioni che verranno applicate, chiede conferma, riavvia e
   controlla che l'app risponda.
3. **Se qualcosa va storto**, lo script stampa i comandi per tornare indietro: si torna alla versione
   di codice precedente e al backup fatto un attimo prima:

   ```sh
   git checkout VERSIONE_DI_PRIMA
   scripts/ripristina.sh backup/magazzino-sagra-prima-di-aggiornare-AAAAMMGG-HHMMSS.sql.gz
   docker compose up -d --build
   ```

Durante la sagra conviene non aggiornare; se serve, farlo di giorno e provarlo prima come sopra.

**Regole per chi scrive il codice** (quelle che tengono al sicuro i dati):

- una migrazione già applicata sull'installazione vera **non si modifica più**: per cambiare
  qualcosa se ne scrive una nuova (`python manage.py makemigrations`);
- le modifiche ai dati (rinominare, convertire) vanno in migrazioni `RunPython` che saltano quello
  che è già stato cambiato a mano e non cancellano niente (vedi `bar/migrations/0007_spinatrici_dati.py`);
- un campo nuovo obbligatorio va aggiunto in tre passi: facoltativo, dati, obbligatorio
  (`bar` 0006 → 0007 → 0008);
- prima di installare: `python manage.py makemigrations --check` (niente migrazioni dimenticate),
  i test su PostgreSQL (vedi [Sviluppo](#sviluppo)) e `scripts/prova-aggiornamento.sh`.

## Backup

Il servizio `backup`, ogni 30 minuti:
- salva il database in `backup/magazzino-sagra-AAAAMMGG-HHMM.sql.gz`, tenuti 10 giorni;
- copia fatture e foto dei prodotti nuove in `backup/magazzino-sagra-allegati/` (non si cancellano).

Ogni quanto e per quanti giorni si cambiano nel file `.env`, poi `docker compose up -d`:

```sh
BACKUP_OGNI_MINUTI=15
BACKUP_GIORNI=30
```

Altre copie:
- **Google Drive**: vedi il README, «Backup su Google Drive» (copie del database tenute 30 giorni,
  `DRIVE_GIORNI`);
- **a mano**: `scripts/backup-ora.sh` (es. prima di un aggiornamento o a fine sagra);
- **chiavetta**: a fine giornata copia la cartella `backup/`.

**Ripristino** (tutto quello registrato dopo l'ora del backup va perso; prima lo script fa comunque
una copia di com'è adesso):

```sh
scripts/ripristina.sh backup/magazzino-sagra-AAAAMMGG-HHMM.sql.gz
```

Per rimettere fatture e foto su un computer nuovo vedi il README («Backup e ripristino») oppure il
[trasloco](#spostare-lapp-su-un-nuovo-server), che le porta con sé.

**Prova i backup** ogni tanto: `scripts/prova-aggiornamento.sh backup/FILE.sql.gz` carica un backup
nel server di prova; se l'app parte e i dati ci sono, il backup è buono.

## Spostare l'app su un nuovo server

Con due script si porta tutto: database, fatture e foto, chiave segreta, file `.env` (e il
collegamento a Google Drive, se c'è).

Sul server **vecchio**:

```sh
scripts/esporta-dati.sh           # crea trasloco-magazzino-sagra-AAAAMMGG-HHMM.tar
scp trasloco-magazzino-sagra-*.tar utente@server-nuovo:
```

Sul server **nuovo** (con Docker installato):

```sh
git clone INDIRIZZO-DEL-REPOSITORY magazzino && cd magazzino
mv ~/trasloco-magazzino-sagra-*.tar .
scripts/importa-dati.sh trasloco-magazzino-sagra-AAAAMMGG-HHMM.tar
```

Lo script rimette `.env`, avvia il database, carica i dati, rimette fatture e foto e avvia l'app.
Se il server nuovo è in cloud: scrivi il dominio nel `.env` (`DOMINIO=…`), punta il dominio al
server nuovo e lancia `docker compose -f docker-compose.yml -f docker-compose.cloud.yml up -d`.

Il file del trasloco contiene le password: copialo solo su canali sicuri e cancellalo quando hai
finito. Durante il trasloco non registrare niente sul server vecchio (o ripeti l'esportazione).

## Kubernetes

L'app è già quasi pronta per Kubernetes:

- tutta la configurazione arriva da **variabili d'ambiente** (vedi `config/settings.py` e `.env.example`);
- l'immagine è una sola (`Dockerfile`), gira come utente non root, serve da sola i file statici;
- c'è il controllo di salute **`/salute/`** (senza login, controlla anche il database) per le sonde;
- il database è esterno all'app (`POSTGRES_HOST`): va bene un PostgreSQL gestito;
- il programma di stampa parla con il server via HTTPS: niente da cambiare.

Le cose da sapere:

- **file caricati** (fatture, foto): stanno in `/app/dati`, che deve essere un volume persistente.
  Con un volume `ReadWriteOnce` l'app va tenuta a **una copia** (come negli esempi); per averne di più
  serve un volume `ReadWriteMany` (o portare i file su un archivio di oggetti, es. S3, che oggi
  non c'è);
- **chiave segreta**: va data con `DJANGO_SECRET_KEY` (in un Secret); dopo un trasloco usa la
  stessa che c'era, altrimenti tutti devono rientrare;
- **migrazioni**: oggi partono all'avvio del container. Con una copia sola va bene; con più copie
  conviene un `Job` che lanci `python manage.py migrate` prima del rilascio;
- **backup**: un `CronJob` (esempio incluso) al posto del servizio `backup`.

In `deploy/kubernetes/` c'è un esempio completo (namespace, segreti, impostazioni, PostgreSQL,
app con sonde e volume, ingress con HTTPS, backup):

```sh
docker build -t registro.esempio.it/sagra/magazzino:2026.10 . && docker push registro.esempio.it/sagra/magazzino:2026.10
cp deploy/kubernetes/10-segreti.esempio.yaml deploy/kubernetes/10-segreti.yaml   # e cambia i valori
# cambia dominio e immagine in 20-impostazioni.yaml, 40-app.yaml e 50-ingress.yaml
kubectl apply -k deploy/kubernetes
```

Per portarci i dati di un'installazione Docker: carica il dump del database
(`gunzip -c database.sql.gz | kubectl -n sagra exec -i sagra-db-0 -- psql -U magazzino -d magazzino`)
e il contenuto di `dati.tar.gz` del trasloco nel volume `sagra-allegati`
(`kubectl -n sagra exec -i deploy/sagra-web -- tar -C /app/dati -xzf - < dati.tar.gz`).

## Monitor cucina

Sì, c'è già: è una delle «funzioni smart», spente finché non servono.

1. *Cassa → Impostazioni → Monitor cucina* (staff) e salva.
2. Su un tablet o una TV in cucina, con il browser, entra con un utente e apri
   *Cassa → Oggi → Monitor cucina* della serata. Conviene lo schermo intero e lo spegnimento
   automatico dello schermo disattivato.
3. Le comande compaiono in *Da fare*; in cucina si tocca *Inizio*, *Pronta*, *Ritirata* (↩ per
   tornare indietro). Quelle da asporto hanno la scritta **ASPORTO**, quelle annullate sono barrate.
   I minuti di attesa diventano gialli dopo 10 e rossi dopo 20. Il report mostra poi i tempi.

Le comande di carta continuano a uscire: il monitor si aggiunge, non sostituisce le stampanti.

## Sviluppo

```sh
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate && .venv/bin/python manage.py createsuperuser
DJANGO_DEBUG=1 .venv/bin/python manage.py runserver
```

- **Test**: `.venv/bin/python manage.py test` (SQLite: salta quelli di concorrenza). Tutti, su
  PostgreSQL, nel server di prova: `docker compose -f docker-compose.yml -f docker-compose.debug.yml exec web python manage.py test`.
- **Server di prova**: `docker compose -f docker-compose.yml -f docker-compose.debug.yml up -d --build`
  (porta 8002, usa il codice della cartella e si ricarica da solo, database suo). Non lanciare mai
  `docker compose up` nella cartella per provare: quella è l'installazione vera.
- **Listino**: i listini stanno in `cassa/listini/*.json`; si caricano con
  `python manage.py carica_listino FILE --prova` (poi senza `--prova`). Si può ripetere.
- **Mappa del codice**: in `graphify-out/` c'è il grafo del codice (`GRAPH_REPORT.md`, `graph.html`):
  serve a orientarsi e a Claude Code nelle sessioni successive (`/graphify query "…"`). Dopo modifiche
  grosse si aggiorna con `/graphify . --update`.
- **Claude (MCP)**: gli strumenti sono in `assistente/strumenti.py` e `assistente/strumenti_bar.py`;
  usano gli stessi servizi delle pagine.

## Problemi comuni

| Problema | Cosa fare |
| --- | --- |
| In cassa compare l'avviso rosso «il programma di stampa non è collegato» | Sul computer delle stampanti avvia `agente_stampa.py`; controlla che arrivi al server (stesso Wi-Fi, o internet se in cloud). |
| Una comanda non esce | *Cassa → Stampanti*: stampa di prova; stampante accesa, carta, indirizzo IP giusto. Le stampe si ritentano da sole per 10 minuti, poi *Riprova* dall'ordine. |
| L'app non si apre dal telefono | Stesso Wi-Fi del computer; indirizzo con la porta (`http://IP:8000`); `docker compose ps` dice che `web` è *healthy*. |
| «Errore del server» | `docker compose logs --tail=200 web` mostra cosa è successo. |
| Disco pieno | I backup locali si tengono `BACKUP_GIORNI` giorni; le foto in `backup/…-allegati` non si cancellano da sole. `docker system prune` toglie le immagini vecchie (non i volumi). |
| Ho perso la password dell'amministratore | `docker compose exec web python manage.py changepassword admin` |
