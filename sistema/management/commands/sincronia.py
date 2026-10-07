from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from sistema import sincronia


class Command(BaseCommand):
    help = (
        "Sincronia con l'altro server. Sulla sagra (SINCRONIA_CON, COPIA_CHIAVE) gira da sola nel "
        "servizio «sincronia»; a mano: --una-volta. --riallinea sostituisce i dati di qui con quelli "
        "dell'altro server (prima volta, dopo un aggiornamento)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--una-volta", action="store_true", help="un giro solo, poi esce")
        parser.add_argument("--riallinea", action="store_true", help="copia completa dall'altro server")
        parser.add_argument("--da", default="", help="con --riallinea: indirizzo dell'altro server (se no SINCRONIA_CON)")
        parser.add_argument("--forza", action="store_true",
                            help="con --riallinea: anche se qui ci sono modifiche non ancora mandate (si salva prima una copia)")
        parser.add_argument("--ogni", type=float, default=settings.SINCRONIA_OGNI_SECONDI, help="secondi tra un giro e l'altro")

    def handle(self, *args, una_volta, riallinea, da, forza, ogni, **opzioni):
        if not settings.COPIA_CHIAVE:
            raise CommandError("Serve COPIA_CHIAVE nel file .env (la stessa sui due server).")
        if riallinea:
            try:
                sincronia.riallinea(da=da, scrivi=self.stdout.write, forza=forza)
            except sincronia.CopiaNonRiuscita as e:
                raise CommandError(str(e)) from None
            return
        if not settings.SINCRONIA_CON:
            raise CommandError("Serve SINCRONIA_CON (l'indirizzo del server di casa) nel file .env della sagra: "
                               "è la sagra che chiama, casa risponde e basta.")
        if una_volta:
            try:
                esito = sincronia.giro(scrivi=self.stdout.write)
            except sincronia.CopiaNonRiuscita as e:
                raise CommandError(str(e)) from None
            self.stdout.write(sincronia._riassunto(esito))
            return
        self.stdout.write(f"Sincronia con {settings.SINCRONIA_CON}: ogni {ogni:g} secondi.")
        try:
            sincronia.ciclo(ogni_secondi=max(ogni, 5), scrivi=self.stdout.write)
        except KeyboardInterrupt:
            self.stdout.write("Sincronia fermata.")
