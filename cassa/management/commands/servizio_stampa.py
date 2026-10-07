from django.core.management.base import BaseCommand

from cassa.servizio_stampa import ServizioStampa


class Command(BaseCommand):
    help = (
        "Manda comande e ricevute alle stampanti di rete della sagra. In Docker gira da solo "
        "(servizio «stampa»); a mano serve solo per provare."
    )

    def add_arguments(self, parser):
        parser.add_argument("--attesa", type=float, default=0.5, help="secondi tra un controllo della coda e l'altro")
        parser.add_argument(
            "--file-vivo", default="", help="file da toccare a ogni giro, per il controllo di salute di Docker"
        )

    def handle(self, *args, attesa, file_vivo, **opzioni):
        try:
            ServizioStampa(attesa=max(attesa, 0.1), file_vivo=file_vivo or None).gira()
        except KeyboardInterrupt:
            self.stdout.write("Servizio di stampa fermato.")
