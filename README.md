# Magazzino e cassa della sagra

Gestionale della sagra da usare col telefono, in una sola app web (Django + PostgreSQL in Docker):

- **magazzino**: cosa c'è in frighi, congelatori e dispensa, cosa va in cucina a ogni serata e cosa
  rientra. Le giacenze sono sempre la somma dei movimenti, quindi non si disallineano;
- **cassa**: ordini all'ingresso, comande per cucina e bar, ricevute; stampa da sola su stampanti di
  rete (laser, getto d'inchiostro, termiche), report e statistiche;
- **bar**: lattine, bottiglie e fusti alla spina, che escono da soli con le vendite; a fine serata
  si pesano i fusti sul disegno delle spinatrici;
- **pannello del sistema** (`/sistema/`, solo staff): schema animato di chi parla con chi, salute dei
  servizi, errori con la traccia, dispositivi collegati;
- **server MCP** per collegare Claude: legge giacenze e incassi, registra carichi e fusti.

Due server con lo stesso programma, **e si lavora su tutti e due**: quello **della sagra** (nella rete
locale con casse e stampanti, funziona anche senza internet) e quello **di casa** (es. un Raspberry,
sempre acceso e sul web). Quando la sagra ha internet si scambiano le modifiche ogni 30 secondi.

**Documentazione**

- **Guida per chi lo usa**: nell'app, pulsante **?** in alto (`/guida/`), stampabile.
- **Manuale tecnico**: [docs/MANUTENZIONE.md](docs/MANUTENZIONE.md), che si legge anche in fondo al
  pannello del sistema. C'è tutto: architettura, stampa, sincronia, aggiornamenti, backup e Google
  Drive, cloud, Claude (MCP), sviluppo, problemi comuni.

## Installare il server della sagra

Un computer (va bene un mini PC) con Linux, collegato con il cavo allo switch della sagra insieme
alle stampanti e al Wi-Fi. L'installazione va fatta **con internet**; alla sagra poi non serve.

```sh
# 1. Docker (poi esci e rientra nell'utente)
curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker $USER

# 2. Il programma
git clone https://github.com/matteo-andreuzza/magazzino.git && cd magazzino

# 3. Impostazioni: cambia ADMIN_PASSWORD, POSTGRES_PASSWORD (prima del primo avvio) e APP_NOME
cp .env.example .env && nano .env

# 4. Avvio (la prima volta qualche minuto)
docker compose up -d --build
docker compose ps                    # web e stampa devono dire «healthy»

# 5. Listino 2026 (facoltativo: prima --prova mostra cosa cambierebbe)
docker compose exec web python manage.py carica_listino cassa/listini/listino_2026.json --disattiva-altri
```

Poi, una volta sola:

- **indirizzo fisso** nelle impostazioni di rete del computer (es. `192.168.1.10`): i telefoni aprono
  `http://192.168.1.10:8000` ed entrano con l'utente `admin`;
- nel **BIOS**: *Restore on AC Power Loss* (o *After Power Failure*) su **Power On**. Così basta dare
  la corrente alla sagra: il computer si accende e il programma parte da solo, stampa e backup compresi;
- sui **computer delle casse** basta un file sul desktop: `scripts/cassa/` (istruzioni in `LEGGIMI.txt`);
- gli **utenti** dei volontari da *Amministrazione → Utenti* (senza «staff»).

Passo per passo, con stampanti e prova di accensione: [Installare il programma alla sagra](docs/MANUTENZIONE.md#installare-il-programma-alla-sagra).

## Installare il server di casa

Sempre acceso (es. un Raspberry con Raspberry Pi OS a 64 bit) e pubblicato sul web con
[Tailscale Funnel](https://tailscale.com/kb/1223/funnel). A casa non si stampa. Prima serve una
**chiave** lunga e casuale, la stessa per i due server:

```sh
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

**1. Sulla sagra** (con il programma aggiornato), nel `.env`:

```sh
SERVER_NOME=sagra
COPIA_CHIAVE=la-chiave
```

e `docker compose up -d`: da qui in poi la sagra annota ogni modifica.

**2. A casa**, stessa versione del programma della sagra:

```sh
curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker $USER   # poi esci e rientra
git clone https://github.com/matteo-andreuzza/magazzino.git && cd magazzino
cp .env.example .env && nano .env
```

Nel `.env` di casa, oltre a password e `APP_NOME`:

```sh
SERVER_NOME=casa
COPIA_CHIAVE=la-stessa-chiave
DJANGO_CSRF_TRUSTED_ORIGINS=https://raspberry.tuo-tailnet.ts.net
DJANGO_COOKIE_SICURI=1          # si entra solo dall'indirizzo https
DJANGO_DIETRO_PROXY_HTTPS=1
```

```sh
docker compose up -d --build

# i dati della sagra a casa, una volta (i due computer nella stessa rete, o la sagra su Tailscale)
docker compose exec web python manage.py sincronia --riallinea --da http://192.168.1.10:8000/
# oppure con la chiavetta: in sagra «Magazzino → Copia dati», a casa:
#   scripts/importa-dati.sh /percorso/copia-magazzino-sagra-AAAAMMGG-HHMM.tar

# casa sul web
curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up
sudo tailscale funnel --bg 8000      # pubblica https://raspberry.tuo-tailnet.ts.net
```

La pagina di accesso è su internet: password vere per tutti gli utenti. Google Drive a casa:
non configurarlo, oppure con un'altra cartella (`DRIVE_CARTELLA`).

**3. La sagra chiama casa**: nel `.env` della sagra aggiungi

```sh
SINCRONIA_CON=https://raspberry.tuo-tailnet.ts.net/
COMPOSE_PROFILES=sincronia           # accende il servizio «sincronia»
```

e `docker compose up -d`. Entro un minuto il pannello della sagra mostra «sincronia» verde.
Dettagli, conflitti e la variante solo dentro Tailscale: [Sincronia con casa](docs/MANUTENZIONE.md#sincronia-con-casa).

## Comandi utili

Nella cartella del programma:

| Cosa | Comando |
| --- | --- |
| Come sta tutto | pannello *Magazzino → Sistema* (`/sistema/`), oppure `docker compose ps` |
| Log | `docker compose logs -f web` (o `stampa`, `backup`, `drive`, `sincronia`) |
| Aggiornare (**prima casa, poi la sagra**) | `scripts/aggiorna.sh --pull` |
| Provare un aggiornamento sui dati veri | `scripts/backup-ora.sh && scripts/prova-aggiornamento.sh` |
| Backup adesso / tornare a un backup | `scripts/backup-ora.sh` / `scripts/ripristina.sh backup/FILE.sql.gz` |
| Un giro di sincronia adesso (sagra) | `docker compose exec sincronia python manage.py sincronia --una-volta` |
| Fermare / riavviare | `docker compose down` / `docker compose up -d` |

`docker compose down` non cancella i dati. **Mai `docker compose down -v`**: cancella il database.

## Sviluppo

```sh
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate && .venv/bin/python manage.py createsuperuser
DJANGO_DEBUG=1 .venv/bin/python manage.py runserver        # SQLite, http://localhost:8000
```

Con PostgreSQL c'è il **server di prova** (progetto separato `magazzino-debug`, porta 8002, database
suo). Nella cartella dell'installazione vera non lanciare `docker compose up` per provare.

```sh
docker compose -f docker-compose.yml -f docker-compose.debug.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.debug.yml exec web python manage.py test   # tutti i test
.venv/bin/python manage.py test       # SQLite: salta concorrenza e sincronia
```

Regole del codice, VS Code, sincronia in prova: [Sviluppo](docs/MANUTENZIONE.md#sviluppo).
