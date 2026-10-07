#!/usr/bin/env bash
# Carica i dati di un file:
# - il trasloco fatto con scripts/esporta-dati.sh (sul server nuovo, nella cartella del programma
#   appena scaricato): database, fatture e foto, chiave segreta, .env;
# - la copia scaricata dall'app (Copia dati), per esempio sul server di casa a fine serata:
#   database, fatture e foto.
#
#   scripts/importa-dati.sh trasloco-magazzino-sagra-AAAAMMGG-HHMM.tar
#   scripts/importa-dati.sh /run/media/io/CHIAVETTA/copia-magazzino-sagra-AAAAMMGG-HHMM.tar
#
# I dati che ci sono in questa installazione vengono sostituiti da quelli del file.
source "$(dirname "$0")/_comune.sh"

ARCHIVIO="${1:?Indica il file del trasloco, es. trasloco-magazzino-sagra-AAAAMMGG-HHMM.tar}"
[ -f "$ARCHIVIO" ] || { echo "Non trovo $ARCHIVIO" >&2; exit 1; }
TEMP=$(mktemp -d)
trap 'rm -rf "$TEMP"' EXIT
tar -xf "$ARCHIVIO" -C "$TEMP"

if [ -f "$TEMP/env" ] && [ ! -f .env ]; then
  cp "$TEMP/env" .env && chmod 600 .env && echo "Impostazioni (.env) copiate."
fi
if [ -f "$TEMP/rclone.tar.gz" ] && [ ! -d rclone ]; then
  tar -xzf "$TEMP/rclone.tar.gz" && echo "Collegamento a Google Drive copiato."
fi
echo "Installazione: $(progetto)"
[ -f "$TEMP/LEGGIMI.txt" ] && head -1 "$TEMP/LEGGIMI.txt"
echo "Preparo il programma e controllo che la copia vada bene per questa versione…"
$COMPOSE build web >/dev/null
$COMPOSE up -d db
attendi_db
if ! $COMPOSE run --rm --no-deps -T --entrypoint python web manage.py carica_copia --solo-controllo - < "$TEMP/database.sql.gz"; then
  echo "Niente è stato cambiato." >&2
  exit 1
fi
conferma "Database, fatture e foto di questa installazione verranno sostituiti da quelli del file." || { echo "Annullato."; exit 1; }

$COMPOSE stop web stampa copia >/dev/null 2>&1 || true
echo "Database…"
# Stesso caricamento della copia remota: tiene le tabelle locali e applica le migrazioni di qui.
$COMPOSE run --rm --no-deps -T --entrypoint python web manage.py carica_copia - < "$TEMP/database.sql.gz"
echo "Fatture, foto e chiave segreta…"
$COMPOSE run --rm --no-deps -T --user root --entrypoint sh web \
  -c 'mkdir -p /app/dati && tar -C /app/dati -xzf - && chown -R app:app /app/dati' < "$TEMP/dati.tar.gz"
$COMPOSE up -d --build
if attendi_app; then
  echo "Fatto: l'app risponde con i dati di $(basename "$ARCHIVIO"). Dopo un trasloco controlla il file .env (DOMINIO, se in cloud) e i backup."
else
  echo "L'app non risponde: guarda «$COMPOSE logs --tail=100 web»." >&2
  exit 1
fi
