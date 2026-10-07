#!/usr/bin/env bash
# Torna a un backup del database: tutto quello registrato dopo quell'ora va perso.
#   scripts/ripristina.sh backup/magazzino-sagra-20261003-2330.sql.gz
# Il ripristino è tutto o niente: se si interrompe con un errore il database resta com'era.
source "$(dirname "$0")/_comune.sh"

FILE="${1:?Indica il file di backup, es. backup/magazzino-sagra-AAAAMMGG-HHMM.sql.gz}"
[ -f "$FILE" ] || { echo "Non trovo $FILE" >&2; exit 1; }
echo "Installazione: $(progetto). Backup da ripristinare: $FILE"
conferma "Tutto quello registrato dopo questo backup andrà perso." || { echo "Annullato."; exit 1; }

# Per sicurezza, prima una copia di com'è adesso.
ADESSO="backup/$(progetto)-prima-del-ripristino-$(date +%Y%m%d-%H%M%S).sql.gz"
backup_database "$ADESSO"
echo "Copia di com'era: $ADESSO"

$COMPOSE stop web stampa
ripristina_database "$FILE"
$COMPOSE up -d
attendi_app && echo "Fatto: il database è quello di $FILE." || { echo "L'app non risponde: guarda «$COMPOSE logs web»." >&2; exit 1; }
