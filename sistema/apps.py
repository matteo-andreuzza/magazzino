from django.apps import AppConfig
from django.db.models.signals import post_migrate, pre_migrate


class SistemaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "sistema"
    verbose_name = "Sistema: salute, errori e sincronia"

    def ready(self):
        from . import errori, sincronia

        errori.collega_segnali()
        # Trigger della sincronia e sequenze pari/dispari, rifatti a ogni migrate.
        pre_migrate.connect(sincronia.prima_di_migrate, sender=self)
        post_migrate.connect(sincronia.dopo_migrate, sender=self)
