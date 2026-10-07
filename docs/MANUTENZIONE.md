# Manuale tecnico

Per chi installa, aggiorna e mantiene il programma: è la **guida del programmatore**. Si legge
anche dentro l'app, in fondo al **pannello del sistema** (*Magazzino → Sistema*, pagina
`/sistema/`, solo staff), sotto lo schema animato e lo stato di tutto. Chi usa il programma alla
sagra ha la **guida per i volontari** (il pulsante **?** in alto, pagina `/guida/`), stampabile.

- [Com'è fatto](#comè-fatto)
- [Pannello del sistema](#pannello-del-sistema)
- [Installare il programma alla sagra](#installare-il-programma-alla-sagra)
- [Stampa](#stampa)
- [Sincronia con casa](#sincronia-con-casa)
- [Variabili d'ambiente](#variabili-dambiente)
- [Comandi di tutti i giorni](#comandi-di-tutti-i-giorni)
- [Aggiornare senza rompere i dati](#aggiornare-senza-rompere-i-dati)
- [Backup](#backup)
- [Spostare l'app su un nuovo server](#spostare-lapp-su-un-nuovo-server)
- [Kubernetes](#kubernetes)
- [Monitor cucina](#monitor-cucina)
- [Sviluppo](#sviluppo)
- [Problemi comuni](#problemi-comuni)

## Com'è fatto

Un'applicazione **Django** con **PostgreSQL**, in **Docker Compose**, su un computer della sagra
collegato alla rete locale (switch) insieme alle stampanti e al Wi-Fi. Telefoni, tablet e
computer della cassa sono solo browser: non c'è niente da installare su di loro.

Lo stesso programma gira anche sul **server di casa** (es. un Raspberry, sempre acceso e
pubblicato sul web): lì si lavora tutto l'anno, per esempio si registrano i carichi dopo la spesa.
I due server si scambiano le modifiche quando la sagra ha internet (vedi
[Sincronia con casa](#sincronia-con-casa)); le casse parlano sempre e solo con il server della sagra.

```
 telefoni, tablet,          ┌──────────────── server della sagra (Docker Compose) ───────────────┐
 computer della cassa  ──── │  web (Django) ── db (PostgreSQL) ── stampa ──────────────────────── │ ── stampanti di rete
 (browser, Wi-Fi)    HTTP   │       │               │    │                            IPP · 9100   │    (laser, getto, termiche)
                            │   Claude (MCP)     backup   sincronia                               │
                            │                       │         │                                   │
                            │                     drive ──────┼──── internet ──── Google Drive     │
                            └───────────────────────────────── └─ HTTPS/Tailscale ── server di casa ┘
                                                                                     (sul web, tutto l'anno)
```

### Container

| Servizio | Cosa fa |
| --- | --- |
| `web` | L'app (gunicorn, file statici serviti da whitenoise). All'avvio applica da sola le migrazioni del database e crea l'amministratore (`entrypoint.sh`). Vede in sola lettura la cartella `backup/` e lo stato degli altri servizi, per il pannello. |
| `db` | PostgreSQL 17, dati nel volume `db_dati`. |
| `stampa` | Il servizio di stampa: manda comande e ricevute alle stampanti di rete e le controlla ogni minuto (`manage.py servizio_stampa`). Stessa immagine di `web`. |
| `backup` | Ogni 30 minuti salva il database (e fatture e foto nuove) nella cartella `backup/`; scrive il suo stato nel volume `stato`. |
| `drive` | Se configurato, copia `backup/` su Google Drive; scrive il suo stato nel volume `stato`. |
| `sincronia` | Solo sulla **sagra** (profilo `sincronia`): ogni 30 secondi manda a casa le modifiche di qui e prende quelle di casa (`manage.py sincronia`). Vedi [Sincronia con casa](#sincronia-con-casa). |
| `caddy` | Solo in cloud (`docker-compose.cloud.yml`): HTTPS automatico. |

Volumi: `db_dati` (database), `app_dati` (`/app/dati`: fatture, foto, chiave segreta generata al
primo avvio), `stato` (stato di backup e drive per il pannello). La cartella `backup/` è sul
computer, accanto al programma.

### App Django e file principali

| App | Cosa contiene | File da conoscere |
| --- | --- | --- |
| `magazzino` | Prodotti, frighi e congelatori (ubicazioni), operazioni e movimenti, serate (`Servizio`), menu e piatti, allegati e foto, la guida per i volontari, la copia per la chiavetta. | `services.py` (tutte le scritture), `quantita.py`, `views/`, `templates/magazzino/guida.html` |
| `bar` | Bevande, carichi, fusti: spinatrici, spine, pesate, litri usciti. Le bevande vendute in cassa escono da sole: si calcolano dalle righe degli ordini. | `services.py`, `views/`, `static/bar/` |
| `cassa` | Listino, ordini (anche da asporto), comande, stampe, monitor cucina, report e statistiche. | `services.py` (ordini), `stampa.py` (contenuti e coda), `servizio_stampa.py`, `fogli.py`, `ipp.py`, `views/`, `static/cassa/pos.js`, `agente/agente_stampa.py` |
| `assistente` | Il server MCP per Claude (`/mcp/` via HTTP, `manage.py server_mcp` via stdin) e le chiavi. | `protocollo.py`, `strumenti.py`, `strumenti_bar.py`, `views.py` |
| `sistema` | Salute del sistema: segnali di vita dei servizi, errori ed eccezioni, dispositivi collegati, pannello (`/sistema/`), sincronia con l'altro server, copie dei dati (chiavetta, riallineamento). | `monitor.py`, `middleware.py`, `errori.py`, `sincronia.py`, `copia.py`, `carica.py`, `copia_remota.py`, `views.py`, `static/sistema/pannello.js` |

Configurazione: `config/settings.py` (tutto da variabili d'ambiente, vedi
[Variabili d'ambiente](#variabili-dambiente)), `config/urls.py`. Docker: `Dockerfile`,
`entrypoint.sh`, `docker-compose.yml` (+ `.debug.yml` per il server di prova, `.cloud.yml` per il
cloud). Script per la manutenzione: `scripts/`. Esempi per Kubernetes: `deploy/kubernetes/`.

### Come si passano il lavoro

- **Un ordine**: `pos.js` (la cassa nel browser) manda l'ordine in JSON a `/cassa/serate/N/ordina/`
  con un token, così un ordine ripetuto per una rete incerta non si registra due volte.
  `cassa.services.crea_ordine` in una sola transazione numera l'ordine, salva le righe, crea le
  comande e mette in coda le stampe (`cassa.stampa.accoda_ordine`, tabella `Stampa`, con il
  contenuto già pronto). Il servizio `stampa` le prende dalla coda e le manda alle stampanti; l'esito
  torna sulla riga della `Stampa`. Ogni pochi secondi `pos.js` chiede `/cassa/serate/N/stato/`
  (listino cambiato, stampe in errore, stampanti che non rispondono).
- **Il bar**: le bevande vendute non si scaricano a mano: `bar.services` le calcola dalle righe
  degli ordini collegate a una bevanda. I fusti si registrano con le pesate.
- **Il magazzino**: ogni registrazione è un'`Operazione` con i suoi `Movimento`; le giacenze sono
  sempre la somma dei movimenti, mai un numero salvato.
- **Claude**: `POST /mcp/` con una chiave personale → `assistente.protocollo` → strumenti →
  gli stessi `services.py` delle pagine (quindi gli stessi controlli).
- **Il pannello**: il middleware di `sistema` segna i dispositivi collegati e gli errori delle
  pagine; i servizi Django (`stampa`, `copia`) scrivono il loro segnale di vita (`Battito`);
  `backup` e `drive` scrivono file nel volume `stato`; `sistema.monitor.stato()` mette insieme
  tutto per `/sistema/` e `/sistema/stato.json`.

### Regole del codice

- La logica sta nei `services.py`: le viste e il server MCP li chiamano, così i controlli sono gli
  stessi ovunque.
- Tutto in italiano, anche i nomi nel codice.
- Una migrazione già applicata sull'installazione vera non si modifica: se ne scrive una nuova
  (vedi [Aggiornare senza rompere i dati](#aggiornare-senza-rompere-i-dati)).
- Le tabelle che riguardano solo il server su cui stanno (sessioni, segnali di vita del
  programma di stampa, tabelle `sistema_*`) non vanno nelle copie verso altri server
  (`sistema/copia.py`, `TABELLE_LOCALI`): non devono avere chiavi esterne verso le altre tabelle,
  e quello che cambia da solo ogni pochi secondi va lì, non nelle tabelle dei dati.
- Tutte le altre tabelle **si sincronizzano da sole** con l'altro server (un trigger per tabella,
  messo da `migrate`): una tabella nuova non ha bisogno di niente. Una colonna che cambia a ogni
  uso (come `last_login`) va in `COLONNE_LOCALI` (`sistema/sincronia.py`), se no fa «vincere» tutta
  la riga. Le migrazioni di dati non vanno in coda: ogni server applica le sue, e la sagra dopo
  l'aggiornamento si riallinea da casa.

## Pannello del sistema

*Magazzino → Sistema* (`/sistema/`, solo utenti staff). Si aggiorna da solo ogni 5 secondi.

- **In alto**: in una riga, se va tutto bene o cosa guardare.
- **Schema: chi parla con chi**: dispositivi collegati (a sinistra), rete della sagra, il server
  con i suoi container, le stampanti, internet, Google Drive e il server di casa. I puntini che
  scorrono sono i dati che passano: pagine (azzurro, andata e ritorno), database e copie (viola),
  stampe (rosa). Il colore del riquadro è la salute: verde a posto, giallo da guardare, rosso
  guasto, grigio spento o non serve. Toccando un riquadro, a destra: cos'è, da quanto è acceso,
  l'ultimo segnale, i dettagli e **cosa fare se non va**.
- **Dentro il programma**: i pezzi del codice (cassa nel browser, Django, servizi delle app, coda
  delle stampe, database, servizio di stampa, copie) e come si passano il lavoro, con i numeri di
  oggi. Toccando un pezzo si vede in che file sta e cosa fa.
- **Servizi**: una scheda per container, con acceso da, ultimo segnale, dettagli e cosa fare.
  Sopra, il computer: da quanto è acceso, carico, memoria, temperatura, disco.
- **Stampanti**: stato rilevato dal controllo di ogni minuto (risponde, modello, carta e toner),
  ultima stampa, stampe in coda e in errore.
- **Dispositivi collegati**: chi usa l'app (con cosa sta facendo: cassa, monitor cucina, bar…),
  Claude e i computer con il programma di stampa.
- **Ultime attività**: ultimo ordine, ultima stampa, ultimo backup, ultima copia su Drive e
  remota, ultimo uso di Claude, ultimo accesso, ultima migrazione, avvio dell'app.
- **Errori ed eccezioni**: le pagine andate in errore e gli errori dei servizi, con il punto del
  codice (file e riga), l'utente, quante volte e la **traccia completa**. Quelli uguali si
  contano invece di ripetersi. *Segna tutti come visti* / *Togli quelli visti*.
- **Versione e backup**: impronta del codice (due server con la stessa impronta hanno lo stesso
  codice), Python, Django, ultima migrazione per app, **migrazioni non ancora applicate**, ultimi
  file di backup.
- **Guida del programmatore**: questo file.

**Da dove vengono i dati** (per capire cosa vuol dire «non si sa»):

| Parte | Fonte |
| --- | --- |
| web | il processo che risponde (`/proc`), impostazioni |
| db | una query a PostgreSQL (`pg_postmaster_start_time`, dimensione, collegamenti) |
| stampa, copia | segnale di vita nella tabella `sistema_battito`, scritto ogni pochi secondi |
| backup, drive | file `chiave=valore` in `/stato` (volume `stato`), scritti dai loro script; elenco di `backup/` |
| stampanti | controllo del servizio di stampa ogni minuto (battiti `stampante-N`) e tabella `Stampa` |
| dispositivi | middleware (`sistema_dispositivo`), al massimo una scrittura ogni 20 secondi per dispositivo |
| errori | segnale `got_request_exception` di Django e `registra_errore()` nei servizi (`sistema_errore`) |
| computer | `/proc/uptime`, `/proc/loadavg`, `/proc/meminfo`, temperatura in `/sys/class/thermal`, spazio su disco |

**Se qualcosa si rompe**:

1. Apri il pannello. La riga in alto dice cosa non va; nello schema il riquadro rosso.
2. Tocca il riquadro rosso: *Se non va* dice cosa controllare e quale comando lanciare.
3. In *Errori* c'è la traccia completa delle eccezioni, con file e riga: il punto da cui partire.
4. In *Ultime attività* si vede cosa si è fermato e da quando.
5. Se il pannello non si apre, è fermo `web` (o il computer): `docker compose ps`, poi
   `docker compose logs --tail=200 web`.

`/sistema/stato.json` dà gli stessi dati in JSON (con il login da staff): utile anche per Claude o
per uno script di controllo.

## Installare il programma alla sagra

L'app gira su un computer della sagra (va bene un mini PC), collegato con il cavo allo **switch**
della rete locale insieme alle stampanti e al punto di accesso Wi-Fi dei telefoni. Tutto resta
nella rete della sagra: internet serve solo per installare (immagini Docker e pacchetti), per
mandare i backup su Google Drive e per scambiare le modifiche con il server di casa.

```
             switch della sagra
     ┌──────────┬──────┴─────┬──────────────┐
  server     stampante    stampante     Wi-Fi ── telefoni e tablet
 (questo)    cucina       cassa                  (browser: http://192.168.1.10:8000)
```

1. **Docker**. Linux (consigliato, es. Debian o Ubuntu):
   `curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker $USER` (poi esci e
   rientra). Lo script attiva Docker all'avvio del computer (`systemctl is-enabled docker`).
   Windows: Docker Desktop (con WSL 2), impostazione *Start Docker Desktop when you sign in*;
   Docker Desktop parte solo quando si entra nell'utente, quindi accesso automatico. Quando
   Windows chiede se far passare la porta 8000 nel firewall, consenti le reti private.
2. **Il programma**: `git clone INDIRIZZO-DEL-REPOSITORY magazzino && cd magazzino` (o la cartella
   copiata da una chiavetta).
3. **Impostazioni**: `cp .env.example .env`; cambia `ADMIN_PASSWORD`, `POSTGRES_PASSWORD` (prima
   del primo avvio: dopo non ha effetto) e `APP_NOME`. Per la sincronia con casa anche
   `COPIA_CHIAVE`, `SINCRONIA_CON` e `COMPOSE_PROFILES=sincronia` (vedi [Sincronia con casa](#sincronia-con-casa)).
4. **Avvio**: `docker compose up -d --build` (la prima volta con internet: scarica le immagini e
   costruisce quella dell'app). `docker compose ps` deve dire `healthy` per `web` e `stampa`.
5. **Indirizzo fisso** per il server e per le stampanti, es. 192.168.1.10 il server, .50 e .51
   le stampanti. Sul server mettilo **nelle impostazioni di rete del server stesso** (IPv4 manuale:
   indirizzo, maschera 255.255.255.0, gateway e DNS = il router): quando si dà la corrente il
   router può accendersi dopo il server, e così l'indirizzo c'è subito. Per le stampanti va bene
   anche la prenotazione nel DHCP del router. Se nella rete non c'è un router (solo lo switch)
   nessuno dà gli indirizzi: vanno messi a mano su tutti, server, stampanti, punto di accesso e
   telefoni, oppure serve un router come DHCP.
6. **Accensione quando arriva la corrente**: nel BIOS/UEFI del server (all'avvio *Canc*, *F2* o
   *F10*) cerca l'opzione che dice cosa fare quando torna la corrente, di solito in *Power* o
   *Advanced → Power Management*: *Restore on AC Power Loss*, *After Power Failure*, *AC Recovery*,
   *AC Back*, *Power On after Power Fail*. Mettila su **Power On** (non *Last State*: se il PC era
   stato spento con il pulsante resterebbe spento). Così basta dare la corrente alla sagra: si
   accendono router, server e casse, e il programma parte da solo.
7. **Prova di accensione**: stacca la spina del server e ridagli corrente, senza toccare
   altro. Dopo un minuto o due `http://192.168.1.10:8000/salute/` da un altro computer risponde
   `ok`; da un telefono sul Wi-Fi della sagra si apre `http://192.168.1.10:8000`. Nel pannello del
   sistema tutto verde (Google Drive grigio se non è configurato).
8. **Il programma della cassa** sui computer delle casse: un file sul desktop, senza installare
   niente (`scripts/cassa/`, istruzioni in `LEGGIMI.txt`). *Avvia cassa.bat* (Windows) o
   *avvia-cassa.sh* con *cassa-sagra.desktop* (Linux): aspetta che il server risponda a
   `/salute/` (anche se il PC della cassa si è acceso prima del server) e poi apre la cassa in una
   finestra pulita di Chrome o Edge (`--app`, senza barra degli indirizzi). Nel file va scritto
   solo l'indirizzo del server; se il server è lo stesso PC, `localhost:8000`.

Tutti i servizi hanno `restart: always`: ripartono da soli all'accensione e dopo un errore, anche
se prima qualcuno li aveva fermati con `docker compose stop` (`docker compose down` invece li
toglie: si rimettono con `docker compose up -d`). Su Linux Docker parte con il sistema, senza bisogno
di entrare nell'utente; su Windows Docker Desktop parte solo dopo l'accesso, quindi serve l'accesso
automatico. Il computer non deve andare in sospensione durante la sagra (impostazioni di risparmio
energetico). Casse e stampa non aspettano internet: la sincronia con casa riprova ogni 30 secondi
finché il router non è collegato, e una stampante che si accende dopo il server va bene lo stesso
(ogni stampa si riprova per 10 minuti, le stampanti si controllano ogni minuto).

L'immagine va costruita prima, con internet: alla sagra `docker compose up -d` usa quella che
c'è. Prima di portare il computer alla sagra quindi fai lì gli aggiornamenti
([Aggiornare senza rompere i dati](#aggiornare-senza-rompere-i-dati)).

## Stampa

Quando la cassa conferma un ordine, nella stessa transazione si creano le stampe (`Stampa`, una
per comanda o ricevuta, con il contenuto già pronto: vedi `cassa/stampa.py`). Chi le stampa:

| Stampante (*Cassa → Stampanti*) | Chi la serve | Come |
| --- | --- | --- |
| Normale di rete (laser, getto d'inchiostro) | servizio `stampa` | IPP sulla porta 631, fogli A4 o A5 |
| Termica di rete | servizio `stampa` o programma di stampa | ESC/POS sulla porta 9100 |
| Collegata con il cavo a un altro computer | programma di stampa su quel computer | ESC/POS (USB, condivisione Windows, CUPS) |
| Dal browser della cassa | il browser che ha fatto l'ordine | finestra di stampa (o `--kiosk-printing`) |

**Cosa esce e dove** (*Cassa → Impostazioni*; `cassa/stampa.py`, `_destinazioni`). Alla sagra:

| Documento | Stampante | Impostazione |
| --- | --- | --- |
| Comanda della cucina | Cucina | *Stampante cucina* |
| Comanda del bar (ordini dell'ingresso) | Cassa: la porta il cliente al bar | *Stampante comanda bar* |
| Ricevuta per il cliente | Cassa | *Ricevuta per il cliente* + *stampante ricevute* |
| Ordini della cassa al bar | nessuna: non stampa | *Stampante della cassa al bar* vuota |

Se la comanda del bar esce sulla stessa stampante delle ricevute (`porta_il_cliente`), sopra c'è
scritto «Da presentare al bar». Se manca una di queste stampanti, la pagina *Cassa* lo dice
prima di aprire.

**Servizio `stampa`** (`cassa/servizio_stampa.py`): ogni mezzo secondo guarda la coda; per ogni
stampante con qualcosa da stampare apre un thread che stampa una stampa alla volta, in ordine.
Ogni 5 secondi segna che è acceso (`AgenteStampa` «(server)» per la cassa, `Battito` «stampa»
per il pannello): se tace per 30 secondi, in cassa compare l'avviso rosso. Ogni minuto
**controlla le stampanti** di rete libere (IPP: stato, modello, carta e toner; termiche: se
rispondono sulla porta) e salva il risultato nei battiti `stampante-N`: se una stampante non
risponde o non può stampare (carta finita, inceppata, sportello aperto…) lo dice anche la cassa.
Il controllo di salute di Docker guarda il file `/tmp/servizio-stampa-vivo`. Sul server di casa
(`SERVER_NOME=casa`) il servizio resta acceso ma non stampa, e da casa non si mettono in coda
stampe: ristampe e stampe di prova dicono di farle dalla sagra.

**Stampanti normali** (`cassa/ipp.py`, `cassa/fogli.py`): niente driver né CUPS. Il servizio
chiede alla stampante cosa sa fare (*Get-Printer-Attributes*, ricordato per un minuto) e prepara
la pagina come immagine in bianco e nero, con le stesse righe dei biglietti termici ingrandite a
tutta la larghezza del foglio (carattere DejaVu Sans Mono, pacchetto `fonts-dejavu-core`).
Poi la manda (*Print-Job*) nel primo formato che la stampante accetta, in quest'ordine:
PWG raster (IPP Everywhere, Mopria), Apple raster/URF (AirPrint), PDF, JPEG. La coda IPP si
trova da sola (`/ipp/print`, `/ipp/printer`, `/ipp`, `/`); se serve, nell'indirizzo si scrive
quella completa, es. `ipp://192.168.1.50/ipp/print` o `ipps://…`. Una comanda lunga va su più
pagine, con numero d'ordine e pagina in fondo.

**Errori**: se una stampante non risponde, la stampa va in errore con il motivo (visibile in
*Cassa → Stampanti*, nell'ordine e nel pannello), la stampante si lascia stare 15 secondi e poi
si riprova, da soli per 10 minuti; le stampe successive restano in coda ed escono in ordine. Una
stampa presa da qualcuno che poi sparisce torna in coda dopo un minuto. Un errore del programma
(non della stampante) finisce anche in *Errori* nel pannello, con la traccia.

**Rete**: il container raggiunge le stampanti della rete locale attraverso la rete di Docker
(NAT), senza configurare niente. Non funziona se la rete della sagra usa gli stessi indirizzi
delle reti di Docker (`172.17.0.0/16` e seguenti): in quel caso cambia gli indirizzi della rete
della sagra o le reti di Docker (`default-address-pools` in `/etc/docker/daemon.json`).

```sh
docker compose logs -f stampa      # «Stampato: Comanda cucina n. 12 su Cucina», oppure il motivo dell'errore
docker compose exec web python manage.py shell -c "from cassa import ipp; c = ipp.interroga('192.168.1.50'); print(c.modello, c.formati, c.scegli())"
```

**Provare senza stampanti**: CUPS ha una stampante IPP finta, `ippeveprinter`, che riceve i
lavori come una vera e li tiene in una cartella:

```sh
ippeveprinter -p 8631 -f image/pwg-raster,image/urf -n localhost -k -d /tmp/stampe "Prova"
```

Su Linux `-n localhost` evita l'errore sull'indirizzo IPv6. Gira sull'host, quindi va bene con
l'app avviata con `runserver` (dai container l'host spesso è chiuso dal firewall).
`/usr/lib/cups/command/ippeveps` converte un lavoro ricevuto in PostScript (e se il raster è
sbagliato dà errore). I test automatici usano finte stampanti in Python
(`cassa/tests/test_stampa_rete.py`).

## Sincronia con casa

Due server con lo stesso programma, **e si lavora su tutti e due**:

- **casa** (es. un Raspberry, sempre acceso, pubblicato sul web con Tailscale Funnel): è il
  programma che si usa tutto l'anno e da fuori. Per esempio, dopo la spesa si registrano i carichi
  dal telefono, si creano i prodotti nuovi, si cambia il listino;
- **sagra** (il computer nella rete locale, con casse e stampanti): le casse parlano solo con lui,
  quindi alla sagra tutto funziona anche senza internet.

Quando la sagra ha internet, ogni 30 secondi **manda a casa le righe cambiate e prende quelle di
casa**. All'accensione lo fa subito: il magazzino in sagra è aggiornato con i carichi fatti da casa.
Senza internet le modifiche aspettano in coda, da tutte e due le parti, e partono appena torna.
**Utenti e password sono gli stessi** sui due server: un volontario nuovo o una password cambiata
valgono anche dall'altra parte al giro successivo. A casa non si stampa: le stampanti sono in sagra.

### Come funziona

- **La coda** (`sistema/sincronia.py`): ogni tabella da sincronizzare ha un trigger di PostgreSQL
  che annota la riga cambiata in `sistema_modifica` (una voce per riga, con un numero che sale e
  l'ora). Così si vede tutto: pagine, admin, Claude, comandi, script. I trigger e le sequenze li
  rimette a posto ogni `migrate` (segnale `post_migrate`, cioè a ogni avvio di `web`) e ogni
  caricamento completo. I trigger ci sono solo con `COPIA_CHIAVE` impostata: un server da solo non
  accumula una coda che nessuno prende.
- **Chi chiama: la sagra** (`SINCRONIA_CON`, servizio `sincronia`). Manda a casa le righe in coda,
  lette come sono in quel momento, e le conferme di quelle ricevute; casa applica e risponde con le
  sue (`POST /sistema/api/sincronia/`, JSON compresso, con `COPIA_CHIAVE`). Casa non chiama mai:
  alla sagra basta poter uscire su internet. Un giro fa più messaggi, finché le code non si svuotano.
- **Conferme**: una voce esce dalla coda solo quando l'altro conferma quel numero. Se la riga è
  cambiata mentre viaggiava, il numero è salito e riparte. Applicare due volte la stessa riga non
  fa danni (upsert), quindi un messaggio perso per strada non crea doppioni.
- **Applicare**: upsert (`jsonb_populate_record … ON CONFLICT (id) DO UPDATE`) o cancellazione, una
  riga per volta in un savepoint, con i padri prima delle figlie (chiavi esterne dal catalogo).
  Una riga che non passa non blocca le altre: torna indietro a chi l'ha mandata, che la tiene in
  coda e la mostra nel pannello come **conflitto**.
- **Vince l'ultima modifica**: se la stessa riga è cambiata da tutte e due le parti prima di
  arrivare, vince quella con l'ora più recente (a parità la sagra). Vale per la riga intera: se
  mentre non c'era internet a casa si cambia il prezzo di un articolo e in sagra il suo nome, resta
  solo la modifica più recente. Carichi, prelievi, ordini e pesate invece non si perdono mai:
  sono righe nuove, e le giacenze sono la somma dei movimenti.
- **Colonne di un solo server** (`COLONNE_LOCALI`): `last_login` degli utenti e `ultimo_uso` delle
  chiavi di Claude cambiano a ogni uso. Da sole non fanno partire la riga e non sovrascrivono quelle
  dell'altro: un accesso in sagra non «vince» su una password cambiata a casa.
- **Gli id non si scontrano**: le sequenze vanno a passi di 2, **casa usa gli id dispari, la sagra
  i pari**. Due righe nuove create insieme dalle due parti non prendono mai lo stesso numero.
- **Cosa non si sincronizza**: le tabelle di un solo server (`TABELLE_LOCALI` in `sistema/copia.py`:
  sessioni, pannello del sistema, programmi di stampa) e quelle che ognuno si crea da sé con le
  migrazioni (`ESCLUSE_DALLA_SINCRONIA`: tipi di contenuto, permessi, registro dell'admin). Quando
  arriva la cancellazione di un utente, le sue righe in queste tabelle si tolgono come farebbe Django.
- **File** (fatture, foto): ognuno chiede all'altro quelli che le sue righe usano e non ha. Un file
  che nessuna riga usa più da un giorno si toglie (es. la foto di un prodotto cancellato dall'altra parte).
- **Versioni**: si sincronizza solo tra programmi con le **stesse migrazioni**; se sono diverse il
  giro si ferma e il pannello dice chi aggiornare, mentre ognuno lavora per conto suo. Le righe
  create dalle migrazioni non vanno in coda: ogni server applica le sue. Per questo, dopo un
  aggiornamento con migrazioni, la sagra si riallinea da casa (lo fa `scripts/aggiorna.sh`).
- **Una cancellazione di una riga che dall'altra parte serve ancora** (casa cancella un prodotto,
  intanto la sagra lo carica) non si fa: la riga si rimanda e torna anche dove era stata cancellata.
  Così non si perde il carico.

**Perché non la replica di PostgreSQL**: la replica logica nei due sensi (PostgreSQL 16+) vuole i
due database collegati direttamente, non copia le migrazioni e si ferma al primo conflitto finché
qualcuno non interviene a mano sul database. Con una sagra che ha internet a tratti e due server
che si aggiornano in momenti diversi è più fragile della coda dell'app, che ha il controllo delle
versioni e mostra i conflitti nel pannello.

### Conflitti

Nel pannello, sezione *Sincronia*: le righe di qui che l'altro server non accetta (e un riassunto di
quelle dell'altro che qui non passano). Si riprovano a ogni giro.

| Cosa dice | Perché | Cosa fare |
| --- | --- | --- |
| «C'è già una riga con lo stesso valore» | Lo stesso nome creato dalle due parti prima che si sentissero (es. il prodotto «Pasta»). | **Tieni quella di casa/della sagra**: le due diventano una, e quello che usava l'altra passa a lei. Oppure **Tieni questa** (lo fa l'altro server al giro dopo), oppure rinomina una delle due: al giro dopo passa da sola. |
| «Manca una riga collegata» | Di solito la riga collegata arriva al giro dopo, e allora passa da sola. | Niente; se resta, guarda se la riga collegata è stata cancellata dall'altra parte. |
| «Non la cancello: qui la usano ancora altre righe» | La cancellazione arriva prima delle righe che la usavano. | Niente: passa quando arrivano anche le loro cancellazioni. |

Se i due server si sono separati davvero (es. un ripristino da backup su uno dei due), **Riallinea
da casa** (pannello della sagra, o `manage.py sincronia --riallinea`): i dati della sagra diventano
una copia di quelli di casa. Prima salva una copia di sicurezza della sagra (in `backup/`). Le
modifiche della sagra non ancora arrivate a casa si perdono: se ce ne sono si ferma, salvo `--forza`.
**Dopo un ripristino da backup** su uno dei due server, riallinea la sagra da casa (o, se il
ripristino era a casa, rimetti a casa i dati della sagra con la chiavetta e poi riallinea la sagra).

### Messa in funzione

1. **Una chiave** lunga e casuale, la stessa per i due server:
   `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.
2. **La sagra con il programma aggiornato** (`scripts/aggiorna.sh`). Nel suo `.env`:
   `SERVER_NOME=sagra` e `COPIA_CHIAVE=la-chiave`, poi `docker compose up -d`: da qui in poi i
   trigger annotano ogni modifica (la sagra usa gli id pari).
3. **Casa**: Docker e il programma alla stessa versione. Nel `.env`:

   ```sh
   SERVER_NOME=casa
   COPIA_CHIAVE=la-stessa-chiave
   DJANGO_CSRF_TRUSTED_ORIGINS=https://raspberry.tuo-tailnet.ts.net   # se pubblicato con Funnel
   ```

   `docker compose up -d --build`, poi **i dati della sagra a casa**, una volta: con i due computer
   nella stessa rete (o la sagra su Tailscale)
   `docker compose exec web python manage.py sincronia --riallinea --da http://192.168.1.10:8000/`,
   oppure con la chiavetta (*Copia dati* in sagra, `scripts/importa-dati.sh` a casa). Google Drive a
   casa: non configurarlo, oppure con un'altra cartella (`DRIVE_CARTELLA`).
4. **Casa sul web**: `sudo tailscale funnel --bg 8000` pubblica l'app su
   `https://raspberry.tuo-tailnet.ts.net` (da mettere in `DJANGO_CSRF_TRUSTED_ORIGINS`, poi
   `docker compose up -d`). La pagina di accesso è su internet: password vere per tutti gli utenti.
5. **La sagra chiama casa**: nel `.env` della sagra

   ```sh
   SINCRONIA_CON=https://raspberry.tuo-tailnet.ts.net/   # oppure http://100.x.y.z:8000/ con Tailscale
   COMPOSE_PROFILES=sincronia                             # accende il servizio «sincronia»
   ```

   e `docker compose up -d`. Entro un minuto il pannello della sagra mostra «sincronia» verde e
   quello di casa «la sagra si fa sentire». Con l'indirizzo Funnel alla sagra non serve Tailscale:
   basta internet; anche l'API `/sistema/api/…` di casa però è su internet, protetta solo dalla
   chiave. Con Tailscale anche sulla sagra (`curl -fsSL https://tailscale.com/install.sh | sh`,
   `sudo tailscale up`) e `SINCRONIA_CON=http://100.x.y.z:8000/` (l'IP Tailscale di casa) i due
   server si parlano solo dentro la tua rete Tailscale.

I container raggiungono gli indirizzi Tailscale (`100.x`) attraverso il computer; se usi un nome
(MagicDNS), prova prima che si risolva da un container.

### Comandi

| Cosa | Comando |
| --- | --- |
| Come va | il pannello (sezione *Sincronia*), o in sagra `docker compose logs -f sincronia` |
| Un giro adesso | pannello della sagra → *Sincronizza adesso*, o `docker compose exec sincronia python manage.py sincronia --una-volta` |
| La sagra riprende tutto da casa | pannello della sagra → *Riallinea da casa*, o `… sincronia --riallinea` (`--forza` se ha ancora modifiche in coda) |
| Copiare tutto da un altro server | `… sincronia --riallinea --da http://INDIRIZZO:8000/` |
| Quante modifiche aspettano | pannello, oppure `docker compose exec web python manage.py shell -c "from sistema.models import Modifica; print(Modifica.objects.count())"` |

## Variabili d'ambiente

Nel file `.env` (vedi `.env.example`); dopo una modifica `docker compose up -d`.

| Variabile | Cosa fa |
| --- | --- |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | Amministratore creato al primo avvio. |
| `APP_NOME` | Nome mostrato in alto e nei biglietti. |
| `PORTA` | Porta dell'app sul computer (8000). |
| `POSTGRES_PASSWORD` | Password del database, da decidere prima del primo avvio. |
| `DJANGO_SECRET_KEY` | Facoltativa: se vuota si genera e si conserva in `app_dati`. |
| `DJANGO_ALLOWED_HOSTS` | Nomi con cui si apre l'app (predefinito: tutti). |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Indirizzi HTTPS davanti all'app (es. Funnel): `https://nome.ts.net`. |
| `DJANGO_COOKIE_SICURI`, `DJANGO_DIETRO_PROXY_HTTPS` | Per l'app dietro HTTPS (cloud). |
| `DJANGO_DEBUG`, `DJANGO_LOG_LEVEL`, `DJANGO_LOG_SQL` | Solo per lo sviluppo e il server di prova. |
| `BACKUP_OGNI_MINUTI`, `BACKUP_GIORNI` | Backup automatici: ogni quanto e per quanti giorni (30, 10). |
| `DRIVE_CARTELLA`, `DRIVE_GIORNI` | Google Drive: cartella e giorni di conservazione. |
| `SERVER_NOME` | `sagra` (predefinito: rete locale, casse e stampanti) o `casa` (sul web; non stampa). Decide anche gli id: pari la sagra, dispari casa. |
| `COPIA_CHIAVE` | Sincronia e copie tra i due server: la chiave, uguale sulla sagra e a casa. Vuota: nessuno può sincronizzare. |
| `SINCRONIA_CON` | Solo sulla sagra: l'indirizzo di casa (Funnel `https://…ts.net/` o Tailscale `http://100.x.y.z:8000/`). |
| `SINCRONIA_OGNI_SECONDI` | Ogni quanto la sagra fa un giro con casa (30). |
| `COMPOSE_PROFILES` | `sincronia` sulla sagra, per accendere il servizio `sincronia`. |
| `DOMINIO` | Solo in cloud: il dominio per l'HTTPS. |
| `PORTA_DEBUG`, `PORTA_DEBUGPY` | Solo per il server di prova. |
| `CARTELLA_BACKUP`, `CARTELLA_STATO` | Dove il pannello trova backup e stato dei servizi (`/backup`, `/stato`). |

## Comandi di tutti i giorni

Nella cartella del programma (in cloud aggiungi sempre `-f docker-compose.yml -f docker-compose.cloud.yml`):

| Cosa | Comando |
| --- | --- |
| Come sta tutto | il pannello: *Magazzino → Sistema* (`/sistema/`) |
| Stato dei container | `docker compose ps` |
| Log dell'app | `docker compose logs -f web` |
| Log della stampa | `docker compose logs -f stampa` |
| Log di backup e Drive | `docker compose logs -f backup drive` |
| Log della sincronia (sulla sagra) | `docker compose logs -f sincronia` |
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
   (`http://localhost:8002`) e prova le cose cambiate: cassa, fusti, report, pannello del sistema.
2. **Con la sincronia, aggiorna prima il server di casa, poi la sagra**. Finché i due programmi
   non sono alla stessa versione la sincronia aspetta (ognuno lavora per conto suo, le modifiche
   restano in coda). Sulla sagra `scripts/aggiorna.sh`, se sono arrivate migrazioni, alla fine
   manda a casa quello che è in coda e poi si riallinea da casa: così le righe create dalle
   migrazioni sono le stesse, con gli stessi id, sui due server.
3. **Aggiorna** l'installazione vera, fuori dalle serate:

   ```sh
   scripts/aggiorna.sh               # oppure: scripts/aggiorna.sh --pull per scaricare prima il codice (git pull)
   ```

   Lo script fa un backup, costruisce l'immagine nuova (se fallisce, l'app vecchia resta accesa e
   non cambia niente), mostra le migrazioni che verranno applicate, chiede conferma, riavvia e
   controlla che l'app risponda. Nel pannello, *Versione*: niente «migrazioni non ancora applicate».
4. **Se qualcosa va storto**, lo script stampa i comandi per tornare indietro: si torna alla versione
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
- una tabella nuova che riguarda solo il server su cui sta va aggiunta a `TABELLE_LOCALI`
  (`sistema/copia.py`), senza chiavi esterne verso le altre;
- prima di installare: `python manage.py makemigrations --check` (niente migrazioni dimenticate),
  i test su PostgreSQL (vedi [Sviluppo](#sviluppo)) e `scripts/prova-aggiornamento.sh`.

## Backup

Il servizio `backup`, ogni 30 minuti:
- salva il database in `backup/magazzino-sagra-AAAAMMGG-HHMM.sql.gz`, tenuti 10 giorni;
- copia fatture e foto dei prodotti nuove in `backup/magazzino-sagra-allegati/` (non si cancellano);
- scrive com'è andata nel volume `stato` (ultimo backup riuscito, ultimo errore): il pannello
  diventa rosso se un backup fallisce o se l'ultimo è troppo vecchio.

Ogni quanto e per quanti giorni si cambiano nel file `.env`, poi `docker compose up -d`:

```sh
BACKUP_OGNI_MINUTI=15
BACKUP_GIORNI=30
```

Altre copie:
- **Google Drive**: vedi il README, «Backup su Google Drive» (copie del database tenute 30 giorni,
  `DRIVE_GIORNI`);
- **a mano**: `scripts/backup-ora.sh` (es. prima di un aggiornamento o a fine sagra);
- **chiavetta e server di casa**: vedi sotto, e la [sincronia con casa](#sincronia-con-casa).

### Copia su chiavetta e server di casa

A fine serata, dall'app: *Magazzino → Copia dati → Scarica la copia* (solo staff). Il file
`copia-NOME-AAAAMMGG-HHMM.tar` contiene il database (`pg_dump`, come i backup, senza le tabelle
locali), fatture e foto e `info.json` (da dove viene, quando, con quali migrazioni); non contiene
`.env` né la chiave segreta. Si salva sulla chiavetta dal browser.

A casa, nella cartella del programma sul proprio server (con l'app installata come sopra):

```sh
scripts/importa-dati.sh /run/media/$USER/CHIAVETTA/copia-magazzino-sagra-AAAAMMGG-HHMM.tar
```

Lo script prima controlla che la copia vada bene per la versione del programma di casa (se viene
da un programma più nuovo si ferma senza toccare niente: aggiorna casa e riprova), poi chiede
conferma (`SI=1` per non chiederla), ferma app e stampa, sostituisce database, fatture e foto con
quelli della copia (con lo stesso caricamento del riallineamento: `manage.py carica_copia`) e
riavvia. Con la [sincronia](#sincronia-con-casa) accesa non serve: la chiavetta resta la strada che
funziona sempre, per la prima messa in funzione o se la sagra resta senza internet per giorni.
Attenzione: caricarla sostituisce tutti i dati di casa, anche quelli registrati lì e non ancora
arrivati in sagra; dopo, riallinea la sagra da casa.

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

Lo script rimette `.env`, avvia il database, controlla la versione, carica i dati, rimette fatture
e foto e avvia l'app. Se il server nuovo è in cloud: scrivi il dominio nel `.env` (`DOMINIO=…`),
punta il dominio al server nuovo e lancia `docker compose -f docker-compose.yml -f docker-compose.cloud.yml up -d`.

Il file del trasloco contiene le password: copialo solo su canali sicuri e cancellalo quando hai
finito. Durante il trasloco non registrare niente sul server vecchio (o ripeti l'esportazione).

## Kubernetes

L'app è già quasi pronta per Kubernetes:

- tutta la configurazione arriva da **variabili d'ambiente** (vedi [Variabili d'ambiente](#variabili-dambiente));
- l'immagine è una sola (`Dockerfile`), gira come utente non root, serve da sola i file statici;
- c'è il controllo di salute **`/salute/`** (senza login, controlla anche il database) per le sonde;
- il database è esterno all'app (`POSTGRES_HOST`): va bene un PostgreSQL gestito;
- il programma di stampa parla con il server via HTTPS: niente da cambiare. Il servizio `stampa`
  non c'è: dal cloud le stampanti della sagra non si raggiungono.

Le cose da sapere:

- **file caricati** (fatture, foto): stanno in `/app/dati`, che deve essere un volume persistente.
  Con un volume `ReadWriteOnce` l'app va tenuta a **una copia** (come negli esempi); per averne di più
  serve un volume `ReadWriteMany` (o portare i file su un archivio di oggetti, es. S3, che oggi
  non c'è);
- **chiave segreta**: va data con `DJANGO_SECRET_KEY` (in un Secret); dopo un trasloco usa la
  stessa che c'era, altrimenti tutti devono rientrare;
- **migrazioni**: oggi partono all'avvio del container. Con una copia sola va bene; con più copie
  conviene un `Job` che lanci `python manage.py migrate` prima del rilascio;
- **backup**: un `CronJob` (esempio incluso) al posto del servizio `backup`;
- **pannello del sistema**: funziona, ma backup e Drive risultano «non si sa» (non c'è il volume
  `stato`); i dispositivi e gli errori sono per database, quindi vanno bene anche con più copie.

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
Nel pannello del sistema il monitor compare tra i dispositivi collegati.

## Sviluppo

```sh
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate && .venv/bin/python manage.py createsuperuser
DJANGO_DEBUG=1 .venv/bin/python manage.py runserver
```

- **Test**: `.venv/bin/python manage.py test` (SQLite: salta quelli di concorrenza e quelli che
  usano `pg_dump`/`psql`). Tutti, su PostgreSQL, nel server di prova:
  `docker compose -f docker-compose.yml -f docker-compose.debug.yml exec web python manage.py test`.
- **Server di prova**: `docker compose -f docker-compose.yml -f docker-compose.debug.yml up -d --build`
  (porta 8002, usa il codice della cartella e si ricarica da solo, database suo). Non lanciare mai
  `docker compose up` nella cartella per provare: quella è l'installazione vera.
- **Stampa in sviluppo**: con `runserver` senza Docker le stampe restano in coda; in un altro
  terminale `.venv/bin/python manage.py servizio_stampa` le manda alle stampanti (o a
  `ippeveprinter`, vedi [Stampa](#stampa)). Nel server di prova il servizio `stampa` c'è già, ma
  non si ricarica da solo: dopo una modifica `docker compose -f docker-compose.yml -f docker-compose.debug.yml restart stampa`.
- **Sincronia in prova**: il server di prova fa la sagra, un secondo progetto con un suo file di
  impostazioni fa casa (porta 8004). I test automatici della sincronia usano invece due database
  dello stesso PostgreSQL (`default` = sagra, `altro` = casa: `config/settings.py` aggiunge
  `altro` quando si lanciano i test), quindi girano solo nel server di prova.

  ```sh
  # casa.env: PORTA_DEBUG=8004, PORTA_DEBUGPY=5679, POSTGRES_PASSWORD, ADMIN_*, SERVER_NOME=casa, COPIA_CHIAVE
  docker compose -p magazzino-casa-prova --env-file casa.env -f docker-compose.yml -f docker-compose.debug.yml up -d web stampa
  docker network connect magazzino-debug_default magazzino-casa-prova-web-1   # la «sagra» vede «casa»
  # la sagra (server di prova), con la stessa chiave:
  COPIA_CHIAVE=… SINCRONIA_CON=http://magazzino-casa-prova-web-1:8000/ COMPOSE_PROFILES=sincronia \
    docker compose -f docker-compose.yml -f docker-compose.debug.yml up -d web stampa sincronia
  # senza internet: docker network disconnect magazzino-debug_default magazzino-casa-prova-web-1
  # per togliere casa: … -p magazzino-casa-prova … down -v   (solo quel progetto!)
  ```

- **Pannello del sistema**: i dati in `sistema/monitor.py`, il disegno in
  `sistema/static/sistema/pannello.js` (gli schemi si ricostruiscono solo quando cambiano forma).
  Un servizio nuovo: un `segnala("nome", …)` periodico e una funzione in `monitor.py`.
- **Errori**: un'eccezione non prevista in un servizio si registra con
  `sistema.errori.registra_errore("origine", e)`; quelle delle pagine si registrano da sole.
- **Listino**: i listini stanno in `cassa/listini/*.json`; si caricano con
  `python manage.py carica_listino FILE --prova` (poi senza `--prova`). Si può ripetere.
- **Mappa del codice**: in `graphify-out/` c'è il grafo del codice (`GRAPH_REPORT.md`, `graph.html`):
  serve a orientarsi e a Claude Code nelle sessioni successive (`/graphify query "…"`). Dopo modifiche
  grosse si aggiorna con `graphify update .`.
- **Claude (MCP)**: gli strumenti sono in `assistente/strumenti.py` e `assistente/strumenti_bar.py`;
  usano gli stessi servizi delle pagine.

## Problemi comuni

Prima di tutto: *Magazzino → Sistema*. Il riquadro rosso e la sua voce *Se non va* dicono quasi
sempre cosa fare; *Errori* ha la traccia completa.

| Problema | Cosa fare |
| --- | --- |
| In cassa compare l'avviso rosso «il servizio di stampa non è attivo» | `docker compose ps`: `stampa` deve essere *healthy*; `docker compose logs --tail=50 stampa` dice cosa succede; `docker compose up -d` lo riavvia. |
| In cassa: «la stampante … non risponde» o «carta finita» | Accesa, cavo nello switch, carta e toner. Il controllo è ogni minuto: l'avviso sparisce da solo. *Cassa → Stampanti → Controlla* per provare subito. |
| Avviso rosso «il programma di stampa non è collegato» | Serve solo per stampanti collegate a un computer (o in cloud): su quel computer avvia `agente_stampa.py`. |
| Una comanda non esce | *Cassa → Stampanti → Controlla*: dice se la stampante risponde e se mancano carta o toner. Indirizzo IP giusto? Ping dal server (`ping 192.168.1.50`). Le stampe si ritentano da sole per 10 minuti, poi *Riprova* dall'ordine. |
| *Controlla* dice «non risponde» ma la stampante è accesa | Server e stampante nella stessa rete (stessi primi tre numeri dell'indirizzo)? Rete della sagra con indirizzi 172.17–172.31: vedi [Stampa](#stampa), «Rete». |
| «nessuno dei formati che il programma sa preparare» | Stampante senza AirPrint/Mopria: installala sul computer della cassa e scegli *Dal browser della cassa*. |
| L'app non si apre dal telefono | Stesso Wi-Fi della sagra; indirizzo con la porta (`http://IP:8000`); `docker compose ps` dice che `web` è *healthy*. |
| «Errore del server» | Pannello → *Errori* (traccia completa), oppure `docker compose logs --tail=200 web`. |
| Pannello: backup rosso | `docker compose logs --tail=50 backup`: di solito disco pieno o database fermo. |
| Pannello: «migrazioni non ancora applicate» | Il codice è più nuovo del database: `docker compose restart web` (le applica all'avvio) o `scripts/aggiorna.sh`. |
| Sincronia: «… ha il programma più aggiornato di …: aggiorna …» | Aggiorna quello indicato (prima casa, poi la sagra: `scripts/aggiorna.sh --pull`). Intanto ognuno lavora per conto suo e le modifiche restano in coda: partono da sole dopo. |
| Sagra: «l'altro server non è raggiungibile» | Normale senza internet: le modifiche aspettano in coda. Altrimenti casa è accesa? `SINCRONIA_CON` giusto? Con Tailscale: `tailscale status` sui due computer. |
| Sincronia: «l'altro server rifiuta la richiesta» | `COPIA_CHIAVE` diversa sui due server, o manca su uno dei due. |
| «Tutti e due i server si chiamano sagra» | Nel `.env` di casa manca `SERVER_NOME=casa` (poi `docker compose up -d`). |
| Sincronia: conflitti | Pannello → *Sincronia*: per i doppioni «Tieni quella di casa/della sagra» o rinomina; gli altri di solito passano da soli al giro dopo. Vedi [Conflitti](#conflitti). |
| Sincronia: «gli orologi dei due server sono distanti» | Vince la modifica più recente: gli orologi devono essere giusti. Su Linux `timedatectl` (deve dire *System clock synchronized: yes*). |
| Striscia in sagra «non ho ancora ricevuto le modifiche fatte da casa» | Dopo l'accensione la sagra non è ancora riuscita a sentire casa (internet?). Le cose registrate da casa arrivano appena si collega. |
| Dato la corrente ma il programma non risponde | Il server si è acceso? Se no: BIOS, «accendi quando torna la corrente» (vedi [Installare](#installare-il-programma-alla-sagra)). Se sì, dopo 2 minuti `docker compose ps` (su Windows: si è entrati nell'utente?). |
| Disco pieno | I backup locali si tengono `BACKUP_GIORNI` giorni; le foto in `backup/…-allegati` non si cancellano da sole. `docker system prune` toglie le immagini vecchie (non i volumi). |
| Ho perso la password dell'amministratore | `docker compose exec web python manage.py changepassword admin` |
