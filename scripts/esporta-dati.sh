#!/usr/bin/env bash
# Prepara il trasloco su un altro server: un solo file con database, fatture e foto,
# chiave segreta e impostazioni (.env).
#
#   scripts/esporta-dati.sh            → trasloco-magazzino-sagra-AAAAMMGG-HHMM.tar
#
# Il file contiene le password del file .env e la chiave segreta: trattalo come una password.
source "$(dirname "$0")/_comune.sh"

PROGETTO=$(progetto)
NOME="trasloco-$PROGETTO-$(date +%Y%m%d-%H%M)"
TEMP=$(mktemp -d)
trap 'rm -rf "$TEMP"' EXIT

attendi_db
echo "Database…"
backup_database "$TEMP/database.sql.gz"
echo "Fatture, foto e chiave segreta…"
$COMPOSE run --rm --no-deps -T --entrypoint sh web -c 'tar -C /app/dati -czf - .' > "$TEMP/dati.tar.gz"
[ -f .env ] && cp .env "$TEMP/env"
[ -d rclone ] && tar -czf "$TEMP/rclone.tar.gz" rclone 2>/dev/null || true
cat > "$TEMP/LEGGIMI.txt" <<FINE
Trasloco di $PROGETTO del $(date '+%d/%m/%Y %H:%M').
Sul server nuovo: scarica il programma (git clone), metti questo file nella cartella e lancia
  scripts/importa-dati.sh $NOME.tar
FINE
tar -cf "$NOME.tar" -C "$TEMP" .
chmod 600 "$NOME.tar"
echo "Pronto: $NOME.tar ($(du -h "$NOME.tar" | cut -f1)). Copialo sul server nuovo (es. scp) e cancellalo da qui quando hai finito."
