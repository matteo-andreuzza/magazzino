# Magazzino e cassa della sagra

App web da usare col telefono per sapere cosa c'è in frighi, congelatori e dispensa,
cosa va in cucina a ogni serata o pranzo, cosa rientra e quanto si consuma.

Nella stessa app c'è la **cassa**: prende gli ordini all'ingresso (nome, tavolo, cosa si
mangia e si beve), stampa la comanda per la cucina e la copia per il bar, memorizza tutto
e a fine serata mostra incassi, venduto e consumi con i grafici (vedi [Cassa](#cassa)).
Cassa e magazzino usano lo stesso database: le serate, il menu e i prodotti sono gli stessi.

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

I prezzi non stanno nel menu ma nel listino della cassa (sotto).

## Cassa

In alto si passa da *Magazzino* a *Cassa*. Le pagine di configurazione (listino, impostazioni,
stampanti) le vedono solo gli utenti *staff*; tutto il resto chiunque abbia un utente.

### Prima della sagra

1. **Listino** (*Cassa → Listino*). Al primo avvio viene creato dai piatti del menu, **senza
   prezzi**: finché un articolo non ha il prezzo non compare in cassa. Scrivi i prezzi nella
   tabella e premi *Salva il listino*. Le **bevande** negli appunti non c'erano: aggiungile con
   *Nuovo articolo*, reparto **Bar**. Le categorie (colori e ordine in cassa) si cambiano da
   *Categorie*.
   - Ogni articolo ha un **reparto**: *Cucina* finisce sulla comanda della cucina, *Bar* sulla copia del bar.
   - *Piatto del menu* (facoltativo): se la serata ha scelto i suoi piatti, l'articolo compare in
     cassa solo nelle serate che lo servono (la paella solo la sera della paella). Serve anche a
     calcolare il consumo degli ingredienti per porzione.
   - I pranzi a menu fisso si possono vendere come un articolo unico (es. «Pranzo anniversari»).
2. **Stampanti** (*Cassa → Stampanti*): aggiungi le stampanti e fai una stampa di prova, poi in
   *Impostazioni* scegli dove escono le comande della cucina e la copia del bar (vedi sotto).
3. Crea la **serata** come per il magazzino (anche dalla pagina *Cassa*, se oggi non c'è).

### Durante la serata

*Cassa → Apri la cassa*. Sul PC o sul tablet della cassa:

1. Nome e tavolo del cliente (i coperti sono facoltativi).
2. Tocca gli articoli: ogni tocco ne aggiunge uno. `−`/`+` cambiano le quantità, *Nota* aggiunge
   una richiesta alla singola riga (es. «senza cipolla»: va sulla comanda). Da tastiera: F2 cerca,
   Invio aggiunge il primo articolo trovato.
3. Scegli il pagamento: con *Contanti* scrivi quanto ti danno (o tocca 10 €, 20 €…) e leggi il resto.
   *Omaggio / volontari* registra l'ordine senza contarlo negli incassi.
4. **Conferma e stampa**: l'ordine riceve un numero progressivo della serata e le comande escono.

L'ordine in corso resta salvato anche ricaricando la pagina. Se la rete cade proprio mentre si
conferma, basta premere di nuovo *Conferma*: lo stesso ordine non viene mai registrato due volte.
In basso ci sono gli ultimi ordini con lo stato della stampa.

- **Esauriti**: *Segna esauriti* e tocca l'articolo finito; tutte le casse lo vedono barrato.
- **Errori**: *Ordini* → apri l'ordine → *Annulla o correggi*. L'ordine resta nell'elenco come
  annullato e non conta negli incassi; se la comanda era già stata stampata, in cucina esce un
  biglietto **ANNULLATO**. *Annulla e correggi* riapre la cassa con lo stesso ordine da sistemare.
- **Ristampa** di comande e ricevuta dalla pagina dell'ordine.
- Da **terminata** la serata la cassa non registra più ordini (si riapre dal magazzino).

Le ricevute, se attivate, sono **non fiscali**.

### Stampanti e comande

Le comande escono su stampanti termiche ESC/POS da 80 o 58 mm (Epson TM e compatibili) in due modi:

- **Stampante di rete o collegata a un computer**: su un computer della sagra (anche un
  Raspberry Pi) gira il *programma di stampa*, che chiede le comande al server e le manda alle
  stampanti. Funziona anche con l'app in cloud e senza aprire porte nel router.
  Da *Cassa → Stampanti* si scarica già configurato; serve solo Python 3:

  ```sh
  python3 agente_stampa.py                  # su Windows basta un doppio clic
  python3 agente_stampa.py --prova 192.168.1.50   # prova diretta della stampante, senza server
  ```

  Indirizzi: `192.168.1.50` (stampante di rete, porta 9100), `/dev/usb/lp0` (USB su Linux),
  `\\localhost\Cucina` (stampante condivisa su Windows), `cups:Cucina` (coda CUPS).
  Deve restare acceso durante il servizio: se si ferma, in cassa compare un avviso rosso. Se una
  stampante è spenta o senza carta, la comanda viene ritentata da sola per 10 minuti; dopo si
  preme *Riprova* dall'ordine.
- **Dal browser della cassa**: una stampante collegata al PC della cassa. Per stampare senza la
  finestra di conferma avvia Chrome con `--kiosk-printing` e imposta la termica come predefinita.

La copia del bar può uscire al bar oppure alla cassa, se è il cliente a portarla al bar: basta
scegliere la stampante in *Impostazioni*.

### Funzioni smart

Spente di default: la cassa prende gli ordini, stampa e memorizza. Si accendono (e spengono)
quando si vuole da *Cassa → Impostazioni*:

- **Monitor cucina**: uno schermo in cucina (tablet o TV) con le comande *da fare*, *in
  preparazione* e *pronte*, i minuti di attesa e il totale di cosa c'è da preparare. Si tocca
  *Inizio*, *Pronta*, *Ritirata* (↩ per tornare indietro). Le comande di carta continuano a
  uscire. Il report mostra poi i tempi della cucina.
- **Cassa al bar**: al bar si paga direttamente, solo per gli articoli del bar, senza fare la coda
  all'ingresso. Stesso database e stessi numeri d'ordine.
- **Scarico dal magazzino**: per gli articoli collegati a un *prodotto del magazzino* (es. la
  Coca-Cola in lattine) la serata propone *Scarica il venduto*: quanto venduto, già diviso tra
  frighi e dispensa partendo da quello scelto in impostazioni (es. *Frigo bar*). Si controlla e si
  registra come «Venduto in cassa» e compare nei consumi. Si può fare più volte nella serata.
  Non collegare la merce che la cucina prende con *Metti a cucinare*, verrebbe contata due volte.

### Report e statistiche

- **Report della serata** (*Cassa → Report*): incassato, contanti e carta, ordini, coperti,
  scontrino medio, omaggi e annullati; ordini ogni quarto d'ora, articoli più venduti, venduto
  per articolo e per categoria; tempi della cucina; consumo degli ingredienti per porzione
  (quanto è uscito dal magazzino diviso per le porzioni vendute). Si scarica per Excel e si stampa.
- **Statistiche** di tutta la sagra: incassi per serata, venduto per articolo e serata, consumo
  per porzione. Utili per gli ordini della prossima sagra.

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

## In cloud

La stessa installazione gira su un server in cloud (una VPS con Docker), così report e
statistiche si consultano da ovunque. Serve un dominio che punti all'indirizzo del server, con
le porte 80 e 443 aperte. Nel file `.env` scrivi il dominio:

```sh
DOMINIO=sagra.esempio.it
```

e avvia con il file per il cloud, che aggiunge l'HTTPS automatico (Caddy, certificato Let's Encrypt):

```sh
docker compose -f docker-compose.yml -f docker-compose.cloud.yml up -d --build
```

Backup, Google Drive e comandi utili sono gli stessi (aggiungendo sempre i due `-f`).

**Attenzione alla connessione della sagra**: con l'app in cloud, se internet cade la cassa non
può registrare ordini finché non torna (le comande già registrate e non ancora stampate escono
appena torna). Serve una connessione affidabile, meglio con un router 4G/5G di riserva. Dove
internet è incerto conviene installare l'app su un computer della sagra, come descritto sopra:
funziona allo stesso modo, anche il programma di stampa.

Se invece pubblichi l'app dietro un tuo proxy HTTPS, aggiungi al file `.env`:

```sh
DJANGO_ALLOWED_HOSTS=magazzino.esempio.it
DJANGO_CSRF_TRUSTED_ORIGINS=https://magazzino.esempio.it
DJANGO_COOKIE_SICURI=1
DJANGO_DIETRO_PROXY_HTTPS=1
```

## Da decidere

Punti ancora aperti negli appunti, segnati nelle note dei piatti:

- nome del software (`APP_NOME` nel file `.env`, poi `docker compose up -d`);
- frittura: ci vanno anche le sardine?
- tagliata: con la polenta?
- paella: solo pesce o anche di carne? Le teste sono le stesse della frittura? Quante bustine di zafferano?
- pranzo paesano: antipasti uguali a quelli degli anniversari?
- dolce e caffè: ingredienti da inserire;
- cassa: prezzi del listino e bevande da inserire (*Cassa → Listino*).

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
.venv/bin/python manage.py test                   # SQLite: i test di concorrenza vengono saltati
docker compose exec web python manage.py test     # PostgreSQL: tutti i test
```

I test della cassa comprendono anche un giro completo del programma di stampa: ordine, server,
programma di stampa e una finta stampante di rete.
