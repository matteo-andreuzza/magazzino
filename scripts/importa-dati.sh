#!/usr/bin/env bash
# Seconda metà del trasloco: sul server nuovo, nella cartella del programma appena scaricato.
#
#   scripts/importa-dati.sh trasloco-magazzino-sagra-AAAAMMGG-HHMM.tar
#
# Crea l'installazione con i dati del file: database, fatture e foto, chiave segreta, .env.
# Si usa su un'installazione nuova (vuota): i dati che ci fossero verrebbero sostituiti.
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
conferma "Database, fatture e foto di questa installazione verranno sostituiti da quelli del trasloco." || { echo "Annullato."; exit 1; }

$COMPOSE up -d db
attendi_db
$COMPOSE stop web >/dev/null 2>&1 || true
echo "Database…"
ripristina_database "$TEMP/database.sql.gz"
echo "Fatture, foto e chiave segreta…"
$COMPOSE run --rm --no-deps -T --user root --entrypoint sh web \
  -c 'mkdir -p /app/dati && tar -C /app/dati -xzf - && chown -R app:app /app/dati' < "$TEMP/dati.tar.gz"
$COMPOSE up -d --build
if attendi_app; then
  echo "Trasloco finito: l'app risponde. Controlla l'indirizzo nel file .env (DOMINIO, se in cloud) e i backup."
else
  echo "L'app non risponde: guarda «$COMPOSE logs --tail=100 web»." >&2
  exit 1
fi
