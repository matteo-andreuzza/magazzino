#!/usr/bin/env bash
# Prova il codice della cartella sui dati veri, senza toccarli: carica l'ultimo backup
# dell'installazione vera nel server di prova (progetto magazzino-debug, porta 8002),
# applica le migrazioni del codice nuovo e controlla che l'app risponda.
#
#   scripts/prova-aggiornamento.sh                         l'ultimo backup in backup/
#   scripts/prova-aggiornamento.sh backup/FILE.sql.gz      quello indicato
#
# I dati del server di prova vengono sostituiti dalla copia di quelli veri.
source "$(dirname "$0")/_comune.sh"

VERO=${VERO:-magazzino-sagra}
COMPOSE=${PROVA:-docker compose -f docker-compose.yml -f docker-compose.debug.yml}
FILE="${1:-$(ls -t backup/"$VERO"-*.sql.gz 2>/dev/null | head -1 || true)}"
[ -n "$FILE" ] && [ -f "$FILE" ] || { echo "Non trovo un backup di $VERO in backup/ (fanne uno con scripts/backup-ora.sh)." >&2; exit 1; }
echo "Server di prova: $(progetto). Dati: $FILE"
conferma "I dati del server di prova verranno sostituiti." || { echo "Annullato."; exit 1; }

$COMPOSE up -d db
attendi_db
$COMPOSE stop web >/dev/null 2>&1 || true
$COMPOSE exec -T db sh -c 'psql -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"'
ripristina_database "$FILE"
echo "Dati caricati. Avvio il server di prova con il codice nuovo (le migrazioni partono da sole)…"
$COMPOSE up -d web
if attendi_app; then
  $COMPOSE exec -T web python manage.py check
  echo "Tutto a posto: prova il programma sul server di prova (porta 8002) prima di aggiornare quello vero."
else
  echo "Il server di prova non risponde: guarda «$COMPOSE logs --tail=100 web». Non aggiornare quello vero finché non è risolto." >&2
  exit 1
fi
