#!/usr/bin/env bash
# Aggiorna l'installazione al codice nuovo senza perdere i dati.
#
#   scripts/aggiorna.sh            usa il codice che c'è nella cartella
#   scripts/aggiorna.sh --pull     prima scarica gli aggiornamenti (git pull)
#
# 1. backup del database adesso;
# 2. costruisce la nuova immagine: se non riesce, l'app vecchia resta accesa e non cambia niente;
# 3. mostra le modifiche al database (migrazioni) che verranno fatte, e chiede conferma;
# 4. riavvia l'app (le migrazioni partono da sole) e controlla che risponda;
# 5. con la sincronia (vedi docs/MANUTENZIONE.md, «Sincronia con casa»): sulla sagra, se sono
#    cambiate le migrazioni, manda a casa quello che è in coda e si riallinea da casa (così le
#    righe create dalle migrazioni sono le stesse sui due server); a casa ricorda di aggiornare la sagra.
# Se qualcosa va storto stampa come tornare alla versione di prima.
# Con la sincronia si aggiorna PRIMA casa, POI la sagra.
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
PIANO=$($COMPOSE run --rm --no-deps --entrypoint python web manage.py migrate --plan)
echo "$PIANO"
conferma "Procedo con l'aggiornamento?" || { echo "Annullato: non è cambiato niente."; exit 1; }

echo "4/4 Riavvio…"
$COMPOSE up -d
if attendi_app; then
  echo "Aggiornato e funzionante."
  RUOLO=$($COMPOSE exec -T web sh -c 'echo "${SERVER_NOME:-sagra}|${SINCRONIA_CON:-}"' 2>/dev/null || echo "sagra|")
  if [ "${RUOLO%%|*}" = "casa" ]; then
    echo "Server di casa: ora aggiorna anche la sagra (scripts/aggiorna.sh lì). Finché i due programmi non sono"
    echo "alla stessa versione la sincronia aspetta: ognuno lavora per conto suo e le modifiche restano in coda."
  elif [ -n "${RUOLO#*|}" ] && ! echo "$PIANO" | grep -q "No planned migration operations"; then
    echo "5/5 Sincronia: mando a casa quello che è in coda e mi riallineo da casa…"
    $COMPOSE exec -T web python manage.py sincronia --una-volta || true
    if ! $COMPOSE exec -T web python manage.py sincronia --riallinea; then
      echo "Riallineamento non fatto (vedi sopra). Quando casa è aggiornata e raggiungibile e la coda è vuota:"
      echo "  pannello del sistema → Sincronia → Riallinea da casa"
    fi
  fi
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
