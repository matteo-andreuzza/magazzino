# Magazzino e cassa della sagra

App web da usare col telefono per sapere cosa c'è in frighi, congelatori e dispensa,
cosa va in cucina a ogni serata o pranzo, cosa rientra e quanto si consuma.

Nella stessa app c'è la **cassa**: prende gli ordini all'ingresso (nome, tavolo, cosa si
mangia e si beve), stampa la comanda per la cucina e la copia per il bar, memorizza tutto
e a fine serata mostra incassi, venduto e consumi con i grafici (vedi [Cassa](#cassa)).
E c'è il **magazzino del bar**: lattine, bottiglie e i fusti di birra e vino alla spina, che escono
da soli con le vendite della cassa; a fine serata si segna sul disegno delle due spinatrici cosa è
successo (fusti cambiati, finiti) e si pesano quelli attaccati, oppure lo si racconta a Claude (vedi
[Bar](#bar)).

L'app ha anche la **modalità scura** (pulsante con la luna in alto: la scelta resta sul
telefono; finché non si sceglie segue il tema del telefono) e un **server MCP** per
collegare Claude Code (vedi [Collegare Claude](#collegare-claude-mcp)).
Cassa, bar e magazzino usano lo stesso database: le serate sono le stesse.

**Come si usa**: nell'app c'è la **Guida** (il pulsante **?** in alto), passo per passo e stampabile:
prima della sagra, ogni serata, cassa, bar, pesata dei fusti, domande frequenti.
**Per chi la mantiene** (aggiornamenti sicuri, backup, trasloco su un altro server, Kubernetes):
[docs/MANUTENZIONE.md](docs/MANUTENZIONE.md).

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
| Aggiornare dopo una modifica al codice | `scripts/aggiorna.sh` (backup, controlli, migrazioni: vedi [docs/MANUTENZIONE.md](docs/MANUTENZIONE.md)) |
| Backup adesso / tornare a un backup | `scripts/backup-ora.sh` / `scripts/ripristina.sh backup/FILE.sql.gz` |
| Fermare | `docker compose down` |
| Vedere i log | `docker compose logs -f web` |

`docker compose down` non cancella i dati. **Non usare mai `docker compose down -v`**:
cancella il database.

## Uso durante la sagra

**Prima di iniziare**
- Controlla frighi e congelatori in *Amministrazione → Ubicazioni* e i prodotti in *Prodotti*.
  Per i prodotti importanti imposta la *scorta minima*: in home compaiono quelli sotto scorta.
- **Foto dei prodotti** (facoltative): *Prodotti* → *modifica* → *Foto*, anche scattandola col
  telefono. Si vede negli elenchi, nella scheda del prodotto e nei moduli di carico e prelievo:
  aiuta a riconoscere la merce in frigo. Viene rimpicciolita (già nel telefono, così si carica
  in fretta anche col Wi-Fi lento), girata per il verso giusto e salvata senza dati nascosti
  (niente posizione GPS). Come le fatture, si vede solo dall'app dopo il login.
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
4. A fine serata **Chiudi la cassa** (dalla cassa dell'ingresso) e **pesa i fusti** di birra
   rimasti attaccati (dal bar, vedi [Bar](#bar)). Il riepilogo che si apre chiudendo la cassa
   elenca i passi che mancano.
5. Quando è tutto finito, **Termina la serata** (o *il pranzo*). La pagina di conferma
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

## Bar

In alto si passa da *Magazzino* a *Bar*. Il magazzino del bar è separato da quello della cucina:
contiene le bevande in **lattine** e **bottiglie** e quelle **alla spina** (birra bionda e rossa,
vino bianco e rosso), che si contano in **fusti**.

Al bancone ci sono due **spinatrici**: *Birra* con le spine *Bionda* e *Rossa*, *Vino* con le spine
*Bianco* e *Rosso*. A ogni spina è attaccato un fusto alla volta, e solo della sua bevanda (un fusto
di rossa non si può attaccare per sbaglio alla spina della bionda). Ogni bevanda ha i suoi **tipi di
fusto**, chiamati come al bancone: *Fusto da 50 litri* per la birra, *Largo argento*, *Alto nero*,
*Azzurro* per il vino. Ogni tipo ha i suoi litri e la sua **tara** (il peso da vuoto), più colore e
forma per riconoscerlo nel disegno. Tutto si cambia da *Bar → Bevande e fusti*.

**Prima della sagra**
1. *Bevande e fusti* (amministrazione): per ogni tipo di fusto scrivi **litri** e **tara**. Al primo
   avvio ci sono valori simbolici segnati come *provvisori*: finché restano così le pagine del bar lo
   ricordano, i litri calcolati non sono giusti e i controlli sui pesi avvisano senza bloccare.
   Correggendoli si correggono anche le serate già passate. Se lo stesso fusto si usa per due bevande
   (il 50 litri della bionda e della rossa), salvando uno si possono aggiornare anche gli altri con lo
   stesso nome. Lì si aggiungono anche spinatrici, spine e tipi di fusto nuovi.
2. *Cassa → Listino*: in ogni articolo del bar scegli la **bevanda** e quanta ne esce per pezzo
   venduto: 1 lattina, 0,33 l il bicchiere di birra, 1,5 l la caraffa, 0,1 l l'ombra, 0,5 l la
   caraffa di vino… Il listino mostra in arancione gli articoli del bar non collegati. Caffè, liquori
   e spritz non si collegano. (Il listino 2026 si carica già fatto, vedi [Cassa](#cassa).)
3. *Bar → Carica → Inventario iniziale*: quante lattine, bottiglie e **fusti pieni** di ogni tipo ci
   sono. Quando arriva merce: *Carica → Consegna*.

**Durante la serata** le bevande non vanno toccate: ogni ordine (all'ingresso o alla cassa del bar,
anche omaggio) le toglie dal magazzino del bar; un ordine annullato le rimette. Se le giacenze vanno
sotto zero la pagina del bar lo segnala (manca un carico), ma la cassa non si blocca.

**A fine serata: Bar → Fusti.** La pagina è disegnata come il bancone: a sinistra i **fusti nuovi**
(pieni, in magazzino), al centro le **due spinatrici** con i fusti attaccati (si vede quanto c'è
ancora dentro, col colore della bevanda), a destra i **fusti finiti** da inizio sagra, serata per
serata. Si rifà sull'app quello che è successo al bancone. Per esempio, se stasera alla bionda il
fusto di ieri è finito, ne è stato attaccato un altro, finito anche quello, e poi un terzo:

1. trascina il fusto di ieri tra i **finiti**;
2. trascina un fusto nuovo sulla spina; quando è finito trascina anche lui tra i finiti;
3. trascina un altro fusto nuovo sulla spina (oppure trascina direttamente il nuovo su quello
   attaccato: l'app chiede se quello attaccato è finito e fa tutte e due le cose);
4. **pesa** i fusti rimasti attaccati: toccane uno e scrivi il peso, oppure *Pesata di fine serata* e
   scrivi tutti i pesi insieme. Se un fusto stasera non è stato usato, *Non usato* ripete il peso di
   prima. Mentre scrivi l'app mostra quanto resta e quanto è uscito stasera.

Col dito: **tenere premuto** un attimo il fusto e poi trascinarlo (vicino al bordo dello schermo la
pagina scorre da sola). Toccandolo e basta si apre la finestra con le stesse scelte, utile sui telefoni
piccoli. Gli errori si correggono subito: un fusto finito per sbaglio si trascina di nuovo sulla sua
spina, uno attaccato per sbaglio stasera si rimette tra i nuovi dalla sua finestra, e *Rifai da capo la
serata* annulla tutto quello registrato stasera sui fusti.

Dal calo di peso l'app calcola i litri usciti (un litro di birra pesa circa 1,01 kg, uno di vino 0,99;
un fusto finito arriva alla tara, uno nuovo parte da pieno) e li confronta con quelli venduti in cassa:
la differenza è schiuma, prove e bicchieri non battuti. Pesare più della volta prima è sempre un errore
(o il fusto è stato cambiato o il peso è sbagliato) e l'app lo dice.

Le serate vanno in ordine: si modificano solo i fusti dell'ultima serata registrata, perché i conti di
ogni serata partono dal peso di quella prima. Anche da *terminata* la serata resta modificabile finché
non si registrano i fusti di quella dopo (le pesate si possono scrivere il giorno dopo). Se una serata
è stata dimenticata, la pagina lo segnala: per inserirla, *Rifai da capo* la serata dopo, registra quella
dimenticata e poi di nuovo quella dopo.

**La cassa del bar** (se attivata in *Cassa → Impostazioni*) si apre da *Bar → Oggi*, non dalla
pagina della cassa: è color ambra, si chiama *Cassa BAR* e ha il boccale, mentre quella
dell'ingresso è verde e si chiama *Cassa ingresso*; anche l'icona nella scheda del browser è
diversa. Dalla cassa del bar non si può chiudere la cassa (si chiude dall'ingresso, per tutti) e
*Ordini del bar* mostra solo i suoi ordini.

*Bar → Oggi* mostra per ogni bevanda alla spina quanta ce n'è ancora (fusti pieni più quello attaccato),
quanta ne esce in media a serata e **per quante serate basta**: serve a sapere quando ordinare altri
fusti. *Consumi* mostra per ogni serata lattine, bottiglie e litri venduti (e quelli usciti dai fusti);
*Correggi* serve se i conteggi non tornano (conta e scrivi la quantità vera); *Storico* elenca carichi
e correzioni, che si possono annullare, e i fusti di ogni serata.

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

1. **Listino** (*Cassa → Listino*). Il listino 2026 (foglio A1 cucina + bar) è già pronto da caricare,
   con prezzi, categorie, bevande del bar collegate e piatti della serata:

   ```sh
   docker compose exec web python manage.py carica_listino cassa/listini/listino_2026.json --prova
   docker compose exec web python manage.py carica_listino cassa/listini/listino_2026.json --disattiva-altri
   ```

   Con `--prova` mostra cosa cambierebbe senza salvare; si può ripetere (aggiorna invece di duplicare,
   e gli articoli nati dal menu vengono rinominati, così le statistiche restano). Con
   `--disattiva-altri` gli articoli che non sono nel listino escono dalla cassa (non si cancellano).
   Senza il comando, al primo avvio il listino viene creato dai piatti del menu, **senza
   prezzi**: finché un articolo non ha il prezzo non compare in cassa. Scrivi i prezzi nella
   tabella e premi *Salva il listino*. Le **bevande** negli appunti non c'erano: aggiungile con
   *Nuovo articolo*, reparto **Bar**, collegandole alla bevanda del magazzino del bar (vedi
   [Bar](#bar)). Le categorie (colori e ordine in cassa) si cambiano da *Categorie*.
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
   Invio aggiunge il primo articolo trovato, F9 (o Ctrl+Invio) conferma.
3. Scegli il pagamento: con *Contanti* scrivi quanto ti danno (o tocca 10 €, 20 €…) e leggi il resto.
   *Omaggio* registra l'ordine senza contarlo negli incassi; nome e tavolo non servono: se mancano
   diventano «Volontario», tavolo XXX.
4. **Conferma e stampa**: l'ordine riceve un numero progressivo della serata e le comande escono.

Totale, pagamento e *Conferma* restano sempre in vista anche su schermi bassi: si accorcia
l'elenco degli articoli scelti, che scorre.

L'ordine in corso resta salvato anche ricaricando la pagina. Se la rete cade proprio mentre si
conferma, basta premere di nuovo *Conferma*: lo stesso ordine non viene mai registrato due volte.
In basso ci sono gli ultimi ordini con lo stato della stampa.

- **Asporto**: il tasto *Asporto* accanto al tavolo. Il tavolo diventa **ASP**, niente coperti, e la
  comanda in cucina esce con la scritta **ASPORTO** grande, bianca su nero, in alto e in fondo (anche
  sul monitor cucina). Il report conta gli ordini da asporto.
- **Esauriti**: *Segna esauriti* e tocca l'articolo finito; tutte le casse lo vedono barrato.
- **Errori**: *Ordini* → apri l'ordine → *Annulla o correggi*. L'ordine resta nell'elenco come
  annullato e non conta negli incassi; se la comanda era già stata stampata, in cucina esce un
  biglietto **ANNULLATO**. *Annulla e correggi* riapre la cassa con lo stesso ordine da sistemare.
- **Ristampa** di comande e ricevuta dalla pagina dell'ordine.
- **Chiudi cassa** (in alto nella cassa) a fine serata: niente più ordini né annullamenti (anche
  alla cassa del bar), le comande già fatte escono lo stesso, e si apre il **riepilogo** con
  incassi, contanti da trovare nel cassetto, bevande vendute e i passi per finire la serata.
  Se arriva ancora qualcuno, *Riapri la cassa*. Il magazzino resta aperto per rientri e pesata dei fusti.
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
  all'ingresso. Stesso database e stessi numeri d'ordine. Si apre dalla sezione *Bar* e ha un
  altro colore, così non si confonde con quella dell'ingresso (vedi [Bar](#bar)).

### Report e statistiche

- **Report della serata** (*Cassa → Report*): incassato, contanti e carta, ordini, coperti,
  scontrino medio, omaggi e annullati; ordini ogni quarto d'ora, articoli più venduti, venduto
  per articolo e per categoria; bevande del bar (lattine, litri di vino e di birra, con la birra
  uscita dai fusti); tempi della cucina; consumo degli ingredienti per porzione
  (quanto è uscito dal magazzino diviso per le porzioni vendute). Si scarica per Excel e si stampa.
- **Statistiche** di tutta la sagra: incassi per serata, venduto per articolo e serata, consumo
  per porzione. Utili per gli ordini della prossima sagra.

## Backup e ripristino

Il servizio `backup` ogni 30 minuti (si cambia nel `.env`: `BACKUP_OGNI_MINUTI`, `BACKUP_GIORNI`):
- salva una copia del database in `backup/magazzino-sagra-AAAAMMGG-HHMM.sql.gz`
  (tenute 10 giorni);
- copia le fatture e le foto dei prodotti nuove in `backup/magazzino-sagra-allegati/` (non si cancellano).

I nomi iniziano con il nome del progetto, così i backup di installazioni diverse sullo
stesso computer non si confondono. A fine giornata conviene copiare la cartella anche
su una chiavetta, oppure attivare la copia su Google Drive (sotto).

Per tornare a un backup del database (tutto quello registrato dopo quell'ora va perso) c'è
`scripts/ripristina.sh backup/magazzino-sagra-AAAAMMGG-HHMM.sql.gz`, oppure a mano:

```sh
docker compose stop web
gunzip -c backup/magazzino-sagra-AAAAMMGG-HHMM.sql.gz | docker compose exec -T db psql -q -v ON_ERROR_STOP=1 --single-transaction -U magazzino -d magazzino
docker compose start web
```

Il ripristino è tutto o niente: se si interrompe con un errore il database resta com'era.

Per rimettere fatture e foto (per esempio su un computer nuovo):

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

Per spostare tutto su un altro server: `scripts/esporta-dati.sh` e `scripts/importa-dati.sh`
(vedi [docs/MANUTENZIONE.md](docs/MANUTENZIONE.md)).

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
- paella: le teste sono le stesse della frittura? Quante bustine di zafferano?
- pranzo paesano: antipasti uguali a quelli degli anniversari?
- dolce e caffè: ingredienti da inserire;
- dolce in fetta: nel listino 2026 è nel reparto *Bar* (esce sulla copia del bar), da spostare in *Cucina* se serve;
- bar: litri e tara (peso da vuoto) dei tipi di fusto, per ora provvisori (*Bar → Bevande e fusti*).

## Collegare Claude (MCP)

L'app ha un **server MCP**: Claude Code (o un altro programma che usa il Model Context Protocol)
può leggere giacenze, prodotti, serate, incassi, bar e fusti e, se la chiave lo permette,
registrare carichi del magazzino e del bar, prelievi e rientri della cucina, i fusti di fine serata
e creare prodotti. Tutto passa dagli stessi controlli dei moduli dell'app e risulta fatto dall'utente
che ha creato la chiave.

1. Da utente *staff*: *Magazzino → Claude* → *Nuova chiave* (spunta *Può registrare* solo se
   serve). La chiave si vede **una volta sola**.
2. Nel terminale dove usi Claude Code incolla il comando mostrato, per esempio:

   ```sh
   claude mcp add --transport http magazzino-sagra http://INDIRIZZO:8000/mcp/ \
     --header "Authorization: Bearer sagra_…"
   ```

3. In Claude Code, `/mcp` mostra se è collegato. Poi si chiede in italiano: «cosa è sotto scorta?»,
   «quanta birra è uscita ieri rispetto a quella venduta?», «registra 20 kg di patate in dispensa»,
   «stasera sono usciti 3 kg di gnocchi», «per quante serate basta la bionda?».

**Fine serata al bar raccontata a Claude.** Basta dirgli com'è andata, spina per spina, con le
parole di tutti i giorni:

```text
venerdì:   bionda in uso 20 kg, rossa 25 kg, bianco 7 kg, azzurro 22 kg (rosso)
sabato:    consumati 2 da 50 L di bionda (quello di ieri è stato cambiato), bionda 10 kg, rossa 15 kg;
           cambiati sia il rosso sia il bianco: bianco 26 largo argento, rosso 23,9 alto nero
domenica:  cambiata la bionda, nuova da 26 kg; rossa non cambiata 6,60; bianco 7,86; rosso 5,05
```

Claude guarda com'era la serata prima, fa una **prova** senza salvare e mostra una riga per spina
(«Bionda: a inizio serata il 50 litri di venerdì, 20 kg; finiti 2 fusti; adesso un 50 litri nuovo,
10 kg, restano ≈ 5 l; uscito stasera ≈ 110 l»), poi registra solo dopo la tua conferma. Se qualcosa
non torna non registra niente e chiede: un fusto «non cambiato» che pesa più di ieri, un tipo di fusto
diverso da quello attaccato («è attaccato un azzurro, non un alto nero: è stato cambiato?»), un tipo
che non esiste o che è ambiguo, una serata registrata fuori ordine. Avvisa (senza bloccare) se i fusti
pieni in magazzino non bastano (manca una consegna), se una serata prima ha venduto alla spina senza
fusti registrati, o se litri e tare sono ancora provvisori. Ripetere la stessa registrazione non fa
danni: la serata su quella spina si rifà da capo, quindi serve anche per correggere.

Sul computer dove girano i container si può anche fare a meno della chiave:

```sh
claude mcp add magazzino-sagra -- docker exec -i magazzino-sagra-web-1 python manage.py server_mcp --utente admin
```

(aggiungi `--scrittura` per permettere di registrare). Le chiavi si revocano dalla stessa pagina.
Senza HTTPS la chiave viaggia in chiaro: va bene nel Wi-Fi della sagra; in cloud l'app è già in HTTPS.

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

Per provare con PostgreSQL, come in produzione (è anche il **server di prova**, da lasciare acceso
per far provare l'app senza toccare i dati veri):

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
