# Contesto per una nuova chat

Da incollare (o far leggere) all'inizio di una nuova chat su questo progetto. Aggiornato all'8
ottobre 2026. Per i dettagli tecnici la fonte è `docs/MANUTENZIONE.md`; le regole del codice
sono in `CLAUDE.md`.

## Il progetto in breve

Gestionale della sagra di un'associazione (colore: verde chiaro), usato da volontari con
telefoni, tablet e computer. **Django + PostgreSQL in Docker Compose**, tutto in italiano
(anche i nomi nel codice). App:

- `magazzino`: frighi, congelatori e dispensa, operazioni e movimenti (le giacenze sono sempre la
  somma dei movimenti), serate (`Servizio`), menu e piatti, guida per i volontari (`/guida/`);
- `bar`: bevande, carichi, fusti con due spinatrici (birra: bionda/rossa; vino: bianco/rosso),
  pesate di fine serata; le bevande vendute in cassa escono da sole;
- `cassa`: listino, ordini (anche da asporto e omaggi), comande, stampa, monitor cucina, report;
- `assistente`: server MCP per Claude (`/mcp/`);
- `sistema`: pannello `/sistema/` (schema animato, salute, errori, dispositivi, guida del
  programmatore), sincronia nei due sensi con il server di casa, copie dei dati (chiavetta, riallineamento).

Container: `web`, `db`, `stampa` (servizio di stampa), `backup`, `drive` (Google Drive),
`sincronia` (solo sulla sagra, profilo `sincronia`), `caddy` (solo cloud). Tutti `restart: always`.

## Com'è la sagra (decisioni dell'utente)

- Un **PC server nella rete locale** della sagra (switch), con le stampanti e il Wi-Fi.
  Tutto deve funzionare **senza internet**; quando si dà la corrente si accendono router, PC e
  server e parte tutto da solo (BIOS «Power On after AC loss», `restart: always`, IP fisso sul
  server). Sui PC delle casse niente da installare: il file `scripts/cassa/Avvia cassa.bat` (o
  `avvia-cassa.sh`) aspetta il server e apre la cassa in una finestra `--app`.
- **Due stampanti normali di rete** (laser o getto d'inchiostro, IP fisso), stampate dal server
  con IPP (`cassa/ipp.py`, `cassa/fogli.py`, `cassa/servizio_stampa.py`), senza driver:
  - **Cucina**: comanda della cucina;
  - **Cassa** (ingresso): ricevuta del cliente + comanda del bar con «Da presentare al bar»
    (il cliente la porta al bar). Consigliata carta A5.
  - La **cassa del bar** non ha stampante e non stampa.
  - Impostazioni: stampante cucina = Cucina, stampante comanda bar = Cassa, ricevuta per il
    cliente spuntata con stampante ricevute = Cassa, stampante della cassa al bar vuota.
- **Più casse insieme**: ogni volontario entra col suo account; la cassa è legata alla serata
  (non c'è apertura per persona), «Chiudi la cassa» vale per tutte. Su comande e ricevuta esce
  **«Cassiere: nome account»**.
- **Backup**: ogni 30 minuti in `backup/` + Google Drive quando c'è internet; a fine serata
  **Copia dati** (`/copia-dati/`) su chiavetta, caricata a casa con `scripts/importa-dati.sh`.
- **Sincronia con casa** (`sistema/sincronia.py`, decisa l'8/10): il **Raspberry di casa** (Funnel,
  sempre acceso) è il programma sul web, **scrivibile in tutto** (es. carichi dopo la spesa); la
  sagra serve perché le casse non dipendano da internet. Coda di modifiche a livello di righe:
  trigger PostgreSQL → `sistema_modifica`; la **sagra chiama casa** ogni 30 s (`SINCRONIA_CON`,
  `COMPOSE_PROFILES=sincronia`), casa risponde (`SERVER_NOME=casa`); stessa `COPIA_CHIAVE`.
  Vince la modifica più recente (a parità la sagra); id pari alla sagra, dispari a casa; colonne
  locali `last_login` e `ultimo_uso`; conflitti nel pannello (doppioni: «Tieni quella di casa/della
  sagra» unisce le righe). Utenti e password uguali sui due server. A casa non si stampa.
  Stesse migrazioni richieste: **si aggiorna prima casa, poi la sagra** (che poi si riallinea da casa:
  lo fa `scripts/aggiorna.sh`). La vecchia copia di sola lettura (`COPIA_DI`) non c'è più.

## Dove siamo

- Repository `/home/matte/Documents/github/magazzino`, ramo **`cassa`** (ultimo commit `c911672`,
  «Implement system panel and remote copy API»); la sincronia con casa (8/10) è da committare.
- **Installazione vera** (progetto Docker `magazzino-sagra`, porta 8000) gira **da questa
  cartella** con il codice vecchio: il database ha bar `0002`, cassa `0003`, magazzino `0006`.
  **Non lanciare mai `docker compose up` qui per provare.** L'aggiornamento è una scelta
  dell'utente: `scripts/backup-ora.sh`, `scripts/prova-aggiornamento.sh`, poi `scripts/aggiorna.sh`
  (serve internet: l'immagine ora ha anche `fonts-dejavu-core`, `postgresql-client`, `Markdown`).
- **Server di prova** `magazzino-debug` su **porta 8002** (codice montato, database suo, dati di
  esempio; utenti di prova in `dati/server-di-prova.txt`), con servizio `stampa` e `COPIA_CHIAVE`.
- **Casa finta** `magazzino-casa-prova` su **porta 8004** (`SERVER_NOME=casa`, env in
  uno scratchpad: vedi «Sincronia in prova» nel manuale), sincronizzata con il server di prova
  (che ha il servizio `sincronia`); serve `docker network connect magazzino-debug_default
  magazzino-casa-prova-web-1`. Si toglie solo con
  `docker compose -p magazzino-casa-prova -f docker-compose.yml -f docker-compose.debug.yml down -v`.
- Grafo del codice (`graphify-out/`) aggiornato all'8/10, documenti compresi: 2.536 nodi,
  153 comunità. Nodi centrali: `Stampa`, `Stampante`, `Ordine`, `Servizio`, `Articolo`.
- Test: 391, tutti verdi su SQLite (`.venv/bin/python manage.py test`) e su PostgreSQL
  (`docker compose -f docker-compose.yml -f docker-compose.debug.yml exec web python manage.py test`).

## Ancora da fare o da provare

- Aggiornare l'installazione vera (vedi sopra) e configurare le due stampanti con i loro IP.
- Provare con **stampanti vere** (finora solo stampanti finte: `ippeveprinter` e finte in Python).
- Provare la sincronia con **Tailscale e Funnel veri** sul Raspberry (finora due progetti Docker:
  carichi da casa, ordini dalla sagra, rete staccata, doppioni, utenti: tutto provato).
- Prima messa in funzione vera: aggiornare la sagra, installare casa e riallinearla dalla sagra
  (`manage.py sincronia --riallinea --da …` o chiavetta), poi `SINCRONIA_CON` sulla sagra.
- Provare sul PC vero: BIOS «Power On», staccare la corrente, *Avvia cassa* sulle casse.
- Installazione su **Windows** documentata ma non provata (consigliato Linux).
- Tare dei fusti ancora **provvisorie**; listino 2026 da caricare sul sistema vero
  (`manage.py carica_listino cassa/listini/listino_2026.json`); «dolce in fetta» è nel reparto Bar.
- Proposti ma non fatti: totali per cassiere a fine serata (se ci sono due cassetti), una
  stampante per ogni cassa dell'ingresso.

## Regole per lavorarci

- La logica sta nei `services.py`; viste e strumenti MCP li chiamano.
- Le migrazioni già applicate sull'installazione vera non si modificano: se ne scrive una nuova.
- Tabelle che riguardano solo un server (sessioni, battiti, `sistema_*`, `cassa_agentestampa`) vanno
  in `TABELLE_LOCALI` (`sistema/copia.py`), senza chiavi esterne.
- Quando cambia qualcosa per chi usa il programma: aggiornare **`guida.html`** (volontari) e
  **`docs/MANUTENZIONE.md`** (programmatore, si legge anche in fondo a `/sistema/`).
- Prima di cercare nel codice: `graphify query "…"` (grafo in `graphify-out/`); dopo le modifiche
  `graphify update .`.
- Il Chrome dell'utente spesso è su un altro spazio di lavoro: per gli screenshot si usa
  `chromium --headless` su HTML salvato.

## File da leggere per primi

`CLAUDE.md`, `docs/MANUTENZIONE.md` (architettura, pannello, stampa, copia remota, variabili),
`cassa/services.py`, `cassa/stampa.py`, `cassa/servizio_stampa.py`, `sistema/monitor.py`,
`sistema/sincronia.py`, `sistema/carica.py`, `docker-compose.yml`, `graphify-out/GRAPH_REPORT.md`.
