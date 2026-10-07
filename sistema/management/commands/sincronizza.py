from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from sistema import copia_remota


class Command(BaseCommand):
    help = (
        "Server di casa: prende la copia dalla sagra (COPIA_DI, COPIA_CHIAVE) e la carica. "
        "In Docker gira da solo (servizio «copia»); a mano: --una-volta."
    )

    def add_arguments(self, parser):
        parser.add_argument("--una-volta", action="store_true", help="una copia sola, poi esce")
        parser.add_argument("--forza", action="store_true", help="ricarica il database anche se non è cambiato")
        parser.add_argument(
            "--sovrascrivi", action="store_true",
            help="carica anche se qui ci sono dati più recenti (es. per ripartire da una sagra nuova)",
        )
        parser.add_argument("--ogni", type=float, default=settings.COPIA_OGNI_MINUTI, help="minuti tra una copia e l'altra")

    def handle(self, *args, una_volta, forza, sovrascrivi, ogni, **opzioni):
        if not settings.COPIA_DI or not settings.COPIA_CHIAVE:
            raise CommandError("Servono COPIA_DI (indirizzo della sagra) e COPIA_CHIAVE nel file .env.")
        if una_volta or forza or sovrascrivi:
            try:
                dettagli = copia_remota.sincronizza(scrivi=self.stdout.write, forza=forza, sovrascrivi=sovrascrivi)
            except copia_remota.CopiaNonRiuscita as e:
                raise CommandError(str(e)) from None
            self.stdout.write(f"Fatto: database {'caricato' if dettagli['cambiato'] else 'già aggiornato'}, "
                              f"{dettagli['allegati']} allegati.")
            return
        self.stdout.write(f"Copia remota da {settings.COPIA_DI}: ogni {ogni:g} minuti.")
        try:
            copia_remota.ciclo(ogni_minuti=max(ogni, 0.5), scrivi=self.stdout.write)
        except KeyboardInterrupt:
            self.stdout.write("Copia remota fermata.")
