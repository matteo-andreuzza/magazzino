from django.apps import AppConfig


class SistemaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "sistema"
    verbose_name = "Sistema: salute, errori e copia remota"

    def ready(self):
        from . import errori

        errori.collega_segnali()
