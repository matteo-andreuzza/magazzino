#!/bin/sh
# Avvia la cassa della sagra: aspetta che il server risponda, poi apre l'app in una finestra pulita.
# Una sola cosa da cambiare: l'indirizzo del computer del programma (se è questo stesso PC: localhost:8000).
SERVER="${SERVER:-192.168.1.10:8000}"
PAGINA="cassa/"

risponde() {
  if command -v curl >/dev/null 2>&1; then
    curl -s -f -o /dev/null -m 3 "http://$SERVER/salute/"
  else
    wget -q -O /dev/null -T 3 "http://$SERVER/salute/"
  fi
}

echo "Aspetto il server della sagra (http://$SERVER/)…"
until risponde; do sleep 2; done
for browser in google-chrome google-chrome-stable chromium chromium-browser microsoft-edge brave-browser; do
  if command -v "$browser" >/dev/null 2>&1; then
    exec "$browser" --app="http://$SERVER/$PAGINA" --start-maximized
  fi
done
exec xdg-open "http://$SERVER/$PAGINA"
