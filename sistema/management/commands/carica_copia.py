import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from sistema import carica
from sistema.copia import CopiaNonRiuscita


class Command(BaseCommand):
    help = (
        "Sostituisce il database con una copia: file .tar (Copia dati, trasloco) o .sql.gz, oppure «-» "
        "per leggere il .sql.gz dall'ingresso. Controlla prima che la versione del programma vada bene."
    )

    def add_arguments(self, parser):
        parser.add_argument("file")
        parser.add_argument("--solo-controllo", action="store_true", help="controlla la versione e basta")

    def handle(self, *args, file, solo_controllo, **opzioni):
        with tempfile.TemporaryDirectory() as cartella:
            dump = Path(cartella) / "database.sql.gz"
            if file == "-":
                with dump.open("wb") as f:
                    shutil.copyfileobj(sys.stdin.buffer, f)
            elif file.endswith(".tar"):
                with tarfile.open(file) as archivio:
                    membro = next((m for m in archivio.getmembers() if Path(m.name).name == "database.sql.gz"), None)
                    if membro is None:
                        raise CommandError("Nel file non c'è database.sql.gz.")
                    with archivio.extractfile(membro) as origine, dump.open("wb") as f:
                        shutil.copyfileobj(origine, f)
            else:
                shutil.copyfile(file, dump)
            try:
                carica.controlla_versione(carica.leggi_dump(dump).migrazioni)
                if solo_controllo:
                    self.stdout.write("La versione della copia va bene per questo programma.")
                    return
                carica.carica_database(dump, scrivi=self.stdout.write)
            except CopiaNonRiuscita as e:
                raise CommandError(str(e)) from None
        self.stdout.write("Copia caricata.")
