"""Server MCP su stdin/stdout, per Claude Code sullo stesso computer dei container.

    claude mcp add magazzino-sagra -- docker exec -i magazzino-sagra-web-1 python manage.py server_mcp --utente admin

Ogni riga in ingresso è un messaggio JSON-RPC, ogni riga in uscita una risposta.
Su stdout non va scritto nient'altro: i messaggi di log vanno su stderr.
"""

import json
import sys

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from assistente.protocollo import gestisci_testo
from assistente.strumenti import Contesto


class Command(BaseCommand):
    help = "Server MCP su stdin/stdout (per collegare Claude Code)."

    def add_arguments(self, parser):
        parser.add_argument("--utente", required=True, help="Le operazioni risultano fatte da questo utente.")
        parser.add_argument("--scrittura", action="store_true", help="Permette anche di registrare (carichi, prodotti).")

    def handle(self, *args, utente, scrittura, **opzioni):
        u = get_user_model().objects.filter(username=utente, is_active=True).first()
        if u is None:
            raise CommandError(f"Utente «{utente}» inesistente o disattivato.")
        contesto = Contesto(utente=u, scrittura=scrittura)
        for riga in sys.stdin:
            riga = riga.strip()
            if not riga:
                continue
            risposta = gestisci_testo(riga, contesto)
            if risposta is not None:
                self.stdout.write(json.dumps(risposta, ensure_ascii=False))
                self.stdout.flush()
