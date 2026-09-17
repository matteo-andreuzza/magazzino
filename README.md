# Magazzino Sagra

App web da usare col telefono per sapere cosa c'è in frighi, congelatori e dispensa,
cosa va in cucina a ogni serata o pranzo, cosa rientra e quanto si consuma.

Le giacenze non si scrivono a mano: sono sempre la somma dei movimenti registrati
(carichi, prelievi, rientri, spostamenti, scarti, correzioni), quindi non possono
"disallinearsi". Se due persone prelevano lo stesso prodotto nello stesso istante,
l'app non lascia andare la giacenza sotto zero.

## Installazione

Serve un computer con Docker (e Docker Compose) collegato alla stessa rete Wi-Fi dei telefoni.

1. Copia il file delle impostazioni e modificalo:

   ```sh
   cp .env.example .env
   ```

   Cambia almeno `ADMIN_PASSWORD` e `POSTGRES_PASSWORD` (quest'ultima **prima** del primo
   avvio: cambiarla dopo non ha effetto). In `APP_NOME` va il nome mostrato in alto nell'app.

2. Avvia:

   ```sh
   docker compose up -d --build
   ```

3. Dai telefoni apri `http://INDIRIZZO-DEL-COMPUTER:8000` (l'indirizzo si trova con
   `hostname -I`) ed entra con l'utente amministratore.

4. Crea gli utenti per i volontari da **Amministrazione → Utenti → Aggiungi**
   (senza "staff": così non vedono l'amministrazione). Va bene anche un utente unico
   condiviso, ma con utenti diversi lo storico mostra chi ha registrato cosa.

Comandi utili:

| Cosa | Comando |
| --- | --- |
| Aggiornare dopo una modifica al codice | `docker compose up -d --build` (le migrazioni partono da sole) |
| Fermare | `docker compose down` |
| Vedere i log | `docker compose logs -f web` |

`docker compose down` non cancella i dati. **Non usare mai `docker compose down -v`**:
cancella il database.

## Uso durante la sagra

**Prima di iniziare**
- Controlla frighi e congelatori in *Amministrazione → Ubicazioni* e i prodotti in *Prodotti*.
  Per i prodotti importanti imposta la *scorta minima*: in home compaiono quelli sotto scorta.
- *Carica* → scegli il frigo → **Inventario iniziale** → scrivi le quantità di ciò che c'è già.

**Quando arriva merce**: *Carica* → scegli dove la metti → **Acquisto / consegna**.
In fondo al modulo si può allegare la fattura o la bolla: una foto scattata col telefono
o un PDF (anche più file, al massimo 20 MB l'uno). Se la fattura arriva dopo, apri il carico
da *Storico* e allegala lì. Gli allegati si aprono e si scaricano solo dall'app, dopo il login.

**A ogni serata o pranzo**
1. In home tocca *Crea la serata di oggi* (o *il pranzo*) e spunta i piatti serviti:
   nel modulo di prelievo verranno mostrati per primi i loro ingredienti.
   I due pranzi dello stesso giorno si distinguono col nome (es. «Pranzo anniversari»).
2. **Metti a cucinare**: per ogni frigo o congelatore scrivi quanto prendi.
   Si può tornare più volte durante il servizio: ogni invio si somma ai precedenti.
3. A fine servizio, **Rientro dalla cucina**: quello che non è stato usato e dove lo rimetti.
   La merce scongelata non va rimessa in congelatore, per questo l'app non lo propone.
4. Quando è tutto finito, **Termina la serata** (o *il pranzo*). La pagina di conferma
   mostra cosa è uscito per la cucina e non è rientrato: se qualcosa è avanzato registra
   prima il rientro, il resto viene contato come usato. Da terminata non si possono più
   registrare prelievi e rientri, né annullarli; se serve, *Riapri la serata* dalla sua pagina.

Dopo mezzanotte, fino alle 5, l'app considera ancora la serata del giorno prima.

**Altre operazioni**
- **Sposta**: per esempio dal congelatore al frigo per scongelare.
- **Scarto**: merce buttata; scrivi il motivo nelle note.
- **Correggi conteggio**: se i numeri non tornano, conta cosa c'è davvero e scrivi la quantità reale.
- **Errori**: *Storico* → apri l'operazione → annulla la riga sbagliata (o tutta l'operazione)
  e poi registra di nuovo quella giusta. Annullando tutto un carico si cancellano anche
  le sue fatture: se servono, scaricale prima.

**Consultazione**: *Giacenze* (cosa c'è e dove), *Frighi*, *Consumi* (usato per ogni servizio,
utile per gli ordini dell'anno dopo), *Storico* con filtri. Giacenze, consumi e storico si
possono stampare o scaricare in CSV da aprire con Excel.

## Menu e piatti

Menu, piatti, ingredienti, prodotti e ubicazioni vengono caricati al primo avvio dagli appunti
della sagra (`magazzino/migrations/0002_dati_iniziali.py`). Dopo si modificano da
*Amministrazione → Menu / Piatti*. Gli ingredienti non hanno quantità: servono a filtrare
il modulo *Metti a cucinare* e a mostrare nella pagina *Menu* quanto ce n'è in magazzino.

L'app gestisce solo il magazzino: prezzi e cassa non ci sono.

## Backup e ripristino

Il servizio `backup` ogni 30 minuti:
- salva una copia del database in `backup/magazzino-sagra-AAAAMMGG-HHMM.sql.gz`
  (tenute 10 giorni);
- copia le fatture nuove in `backup/magazzino-sagra-allegati/` (non si cancellano).

I nomi iniziano con il nome del progetto, così i backup di installazioni diverse sullo
stesso computer non si confondono. A fine giornata conviene copiare la cartella anche
su una chiavetta, oppure attivare la copia su Google Drive (sotto).

Per tornare a un backup del database (tutto quello registrato dopo quell'ora va perso):

```sh
docker compose stop web
gunzip -c backup/magazzino-sagra-AAAAMMGG-HHMM.sql.gz | docker compose exec -T db psql -q -v ON_ERROR_STOP=1 --single-transaction -U magazzino -d magazzino
docker compose start web
```

Il ripristino è tutto o niente: se si interrompe con un errore il database resta com'era.

Per rimettere le fatture (per esempio su un computer nuovo):

```sh
docker compose run --rm --no-deps --user root --entrypoint sh -v ./backup/magazzino-sagra-allegati:/ripristino:ro web \
  -c 'mkdir -p /app/dati/allegati && cp -a /ripristino/. /app/dati/allegati/ && chown -R app:app /app/dati/allegati'
```

## Backup su Google Drive

Il servizio `drive` copia su Google Drive tutto quello che il servizio `backup` mette in
`backup/`: database e fatture. Controlla ogni 10 minuti; se manca internet riprova da solo
e recupera i file rimasti indietro. Su Drive le copie del database restano 30 giorni
(`DRIVE_GIORNI` nel file `.env`), le fatture non si cancellano. La cartella si chiama
«Backup magazzino sagra» (`DRIVE_CARTELLA`).

L'app può vedere e modificare **solo i file che carica lei**: il resto del Drive non lo tocca.

Si configura una volta sola, dal computer dove gira l'app.

**1. Crea le credenziali su Google Cloud** (gratis, con l'account Google che riceverà i backup).
Il client condiviso di rclone viene dismesso nel 2026, quindi ne serve uno proprio.
1. Apri <https://console.cloud.google.com/> e crea un progetto, per esempio «Magazzino sagra».
2. *API e servizi → Libreria*: cerca **Google Drive API** e premi *Abilita*.
3. *Google Auth Platform* (o *Schermata consenso OAuth*): tipo **Esterno**, un nome qualsiasi
   e la tua email. Poi in *Pubblico* premi **Pubblica app**: se resta "in test" l'accesso
   scade dopo 7 giorni.
4. *Client → Crea client*: tipo **App desktop**. Copia l'**ID client** e il **client secret**.

**2. Collega Drive** (sostituisci ID e secret):

```sh
docker run --rm -it --network host -v "$PWD/rclone:/config/rclone" rclone/rclone:1.75.1 \
  config create drive drive scope=drive.file client_id=IL_TUO_ID client_secret=IL_TUO_SECRET
```

Alla domanda sul browser rispondi `y`, apri il link che compare, accedi con l'account Google
e autorizza (Google avvisa che l'app non è verificata: è la tua, puoi proseguire). Alla domanda
sui Drive condivisi rispondi `n`. L'accesso viene salvato nella cartella `rclone/`:
**non condividerla**, dà accesso ai backup.

**3. Riavvia e controlla:**

```sh
docker compose restart drive
docker compose logs -f drive
```

Deve comparire «Google Drive collegato» e poi l'elenco dei file copiati.

Per scollegare Drive cancella la cartella `rclone/` (`sudo rm -r rclone`, perché la crea Docker)
e togli l'accesso da <https://myaccount.google.com/permissions>.

## Accesso da fuori rete

Pensata per la rete locale, l'app accetta qualsiasi indirizzo. Se la pubblichi dietro un proxy
HTTPS aggiungi al file `.env`:

```sh
DJANGO_ALLOWED_HOSTS=magazzino.esempio.it
DJANGO_CSRF_TRUSTED_ORIGINS=https://magazzino.esempio.it
DJANGO_COOKIE_SICURI=1
```

## Da decidere

Punti ancora aperti negli appunti, segnati nelle note dei piatti:

- nome del software (`APP_NOME` nel file `.env`, poi `docker compose up -d`);
- frittura: ci vanno anche le sardine?
- tagliata: con la polenta?
- paella: solo pesce o anche di carne? Le teste sono le stesse della frittura? Quante bustine di zafferano?
- pranzo paesano: antipasti uguali a quelli degli anniversari?
- dolce e caffè: ingredienti da inserire.

## Sviluppo e debug

Senza Docker l'app usa SQLite (`dati/db.sqlite3`):

```sh
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser
DJANGO_DEBUG=1 .venv/bin/python manage.py runserver
```

Con VS Code (estensione Python) nel pannello *Esegui e debug* ci sono tre configurazioni:

| Configurazione | Cosa fa |
| --- | --- |
| **Magazzino: avvia con debugger (SQLite)** | applica le migrazioni e avvia l'app su <http://localhost:8001> con i breakpoint attivi |
| **Magazzino: test con debugger** | lancia i test (chiede quali) fermandosi sui breakpoint |
| **Magazzino: collega a Docker (debug)** | si collega all'app avviata con la configurazione Docker qui sotto |

Per provare con PostgreSQL, come in produzione:

```sh
docker compose -f docker-compose.yml -f docker-compose.debug.yml up --build
```

L'app è su <http://localhost:8002>, usa il codice della cartella e si ricarica a ogni modifica;
dopo una ricarica il debugger di VS Code va ricollegato. È un progetto separato
(`magazzino-debug`) con database e allegati suoi: i dati veri non vengono toccati.
Per cancellarlo: `docker compose -f docker-compose.yml -f docker-compose.debug.yml down -v`.

In debug l'app mostra le pagine di errore dettagliate e nel log i messaggi di livello DEBUG.
Con `DJANGO_LOG_SQL=1` stampa anche ogni query al database. **Non attivare mai
`DJANGO_DEBUG` sull'installazione vera**: le pagine di errore mostrano dati interni.

Test:

```sh
.venv/bin/python manage.py test                   # SQLite: i 2 test di concorrenza vengono saltati
docker compose exec web python manage.py test     # PostgreSQL: tutti i test
```
