import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.utils import OperationalError


class Command(BaseCommand):
    help = "Aspetta che il database sia raggiungibile."

    def add_arguments(self, parser):
        parser.add_argument("--secondi", type=int, default=60)

    def handle(self, *args, **options):
        scadenza = time.monotonic() + options["secondi"]
        while True:
            try:
                connection.ensure_connection()
                return
            except OperationalError as e:
                if time.monotonic() > scadenza:
                    raise CommandError(f"Database non raggiungibile: {e}") from e
                self.stdout.write("In attesa del database…")
                time.sleep(1)
