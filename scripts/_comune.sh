# Funzioni in comune agli script (si include, non si esegue).
#
# COMPOSE è il comando di Docker Compose dell'installazione su cui lavorare:
#   in locale (predefinito):  docker compose
#   in cloud:                 COMPOSE="docker compose -f docker-compose.yml -f docker-compose.cloud.yml"
#   server di prova:          COMPOSE="docker compose -f docker-compose.yml -f docker-compose.debug.yml"

set -euo pipefail
cd "$(dirname "$0")/.."
COMPOSE=${COMPOSE:-docker compose}

progetto() {
  $COMPOSE config 2>/dev/null | sed -n 's/^name: //p' | head -1
}

# Aspetta che l'app risponda a /salute/ (fino a 2 minuti). Restituisce 1 se non risponde.
attendi_app() {
  local i
  for i in $(seq 1 60); do
    if $COMPOSE exec -T web python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/salute/', timeout=3)" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

attendi_db() {
  local i
  for i in $(seq 1 60); do
    if $COMPOSE exec -T db sh -c 'pg_isready -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "Il database non risponde." >&2
  return 1
}

# Scrive quello che arriva in ingresso nel file indicato. La cartella backup/ la crea il
# servizio dei backup (Docker) come root: se non si può scrivere direttamente, si passa da un container.
scrivi_file() {
  local nome="$1" cartella
  cartella=$(dirname "$nome")
  mkdir -p "$cartella" 2>/dev/null || true
  if [ -w "$cartella" ]; then
    cat > "$nome.tmp" && mv "$nome.tmp" "$nome"
  else
    docker run --rm -i -v "$(cd "$cartella" && pwd):/cartella" postgres:17-alpine \
      sh -c "cat > '/cartella/$(basename "$nome").tmp' && mv '/cartella/$(basename "$nome").tmp' '/cartella/$(basename "$nome")'"
  fi
}

# Copia del database adesso, compressa (stesso formato dei backup automatici).
backup_database() {
  local nome="$1"
  $COMPOSE exec -T db sh -c 'pg_dump --no-owner --clean --if-exists -U "$POSTGRES_USER" -d "$POSTGRES_DB"' | gzip | scrivi_file "$nome"
  [ -s "$nome" ] || { echo "Backup non riuscito: $nome è vuoto." >&2; return 1; }
}

# Carica una copia del database (file .sql.gz) al posto di quello che c'è.
ripristina_database() {
  local file="$1"
  gunzip -c "$file" | $COMPOSE exec -T db sh -c 'psql -q -o /dev/null -v ON_ERROR_STOP=1 --single-transaction -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
}

conferma() {
  local risposta
  if [ "${SI:-}" = "1" ]; then return 0; fi
  read -r -p "$1 Scrivi «si» per continuare: " risposta
  [ "$risposta" = "si" ]
}
