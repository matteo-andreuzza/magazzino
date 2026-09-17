import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Crea l'utente amministratore da ADMIN_USERNAME/ADMIN_PASSWORD se non esiste già."

    def handle(self, *args, **options):
        username = os.environ.get("ADMIN_USERNAME", "").strip() or "admin"
        password = os.environ.get("ADMIN_PASSWORD", "")
        User = get_user_model()
        if User.objects.filter(username=username).exists():
            self.stdout.write(f"Utente «{username}» già presente.")
            return
        if not password:
            self.stdout.write(self.style.WARNING(
                "ADMIN_PASSWORD non impostata: nessun amministratore creato. "
                "Impostala nel file .env oppure esegui: docker compose exec web python manage.py createsuperuser"
            ))
            return
        User.objects.create_superuser(username=username, password=password)
        self.stdout.write(self.style.SUCCESS(f"Creato l'amministratore «{username}»."))
