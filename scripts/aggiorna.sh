#!/usr/bin/env bash
# Aggiorna l'installazione al codice nuovo senza perdere i dati.
#
#   scripts/aggiorna.sh            usa il codice che c'è nella cartella
#   scripts/aggiorna.sh --pull     prima scarica gli aggiornamenti (git pull)
#
# 1. backup del database adesso;
# 2. costruisce la nuova immagine: se non riesce, l'app vecchia resta accesa e non cambia niente;
# 3. mostra le modifiche al database (migrazioni) che verranno fatte, e chiede conferma;
# 4. riavvia l'app (le migrazioni partono da sole) e controlla che risponda.
# Se qualcosa va storto stampa come tornare alla versione di prima.
# Consiglio: prima provalo con scripts/prova-aggiornamento.sh sui dati veri copiati nel server di prova.
source "$(dirname "$0")/_comune.sh"

PROGETTO=$(progetto)
echo "Installazione: $PROGETTO"
if [ "${1:-}" = "--pull" ]; then
  git pull --ff-only
fi
PRIMA=$(git rev-parse --short HEAD 2>/dev/null || echo "?")
[ -z "$(git status --porcelain 2>/dev/null)" ] || echo "Nota: nella cartella ci sono modifiche non salvate in git (verranno installate anche quelle)."

attendi_db
BACKUP="backup/$PROGETTO-prima-di-aggiornare-$(date +%Y%m%d-%H%M%S).sql.gz"
backup_database "$BACKUP"
echo "1/4 Backup salvato: $BACKUP"

echo "2/4 Costruisco la nuova versione…"
$COMPOSE build web

echo "3/4 Modifiche al database che verranno fatte:"
# Con l'entrypoint normale le migrazioni partirebbero subito: qui si guarda e basta.
$COMPOSE run --rm --no-deps --entrypoint python web manage.py migrate --plan
conferma "Procedo con l'aggiornamento?" || { echo "Annullato: non è cambiato niente."; exit 1; }

echo "4/4 Riavvio…"
$COMPOSE up -d
if attendi_app; then
  echo "Aggiornato e funzionante."
else
  cat >&2 <<FINE
L'app non risponde. Guarda cosa è successo:   $COMPOSE logs --tail=100 web
Per tornare alla versione di prima:
  git checkout $PRIMA
  scripts/ripristina.sh $BACKUP
  $COMPOSE up -d --build
FINE
  exit 1
fi
