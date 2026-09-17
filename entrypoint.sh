#!/bin/sh
set -e

# La chiave segreta, se non impostata in .env, viene generata al primo avvio
# e conservata nel volume (vedi config/settings.py).
python manage.py attendi_db
python manage.py migrate --noinput
python manage.py assicura_admin

exec "$@"
