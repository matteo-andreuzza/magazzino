"""Solo per i test della sincronia, che hanno due database (vedi config/settings.py).

Le migrazioni di dati (es. magazzino 0002) creano le righe con il database predefinito: mentre si
migra il secondo database finirebbero nel primo. Questo instradatore, durante un migrate, manda
letture e scritture al database che si sta migrando (sincronia.prima_di_migrate lo segna).
"""

from sistema import sincronia


class MigrazioneSulSuoDatabase:
    def db_for_read(self, model, **hints):
        return sincronia.MIGRAZIONE_SU

    def db_for_write(self, model, **hints):
        return sincronia.MIGRAZIONE_SU
