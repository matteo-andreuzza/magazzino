from django.contrib import admin

from .models import ChiaveMCP


@admin.register(ChiaveMCP)
class ChiaveMCPAdmin(admin.ModelAdmin):
    list_display = ["nome", "utente", "inizio", "scrittura", "creata_il", "ultimo_uso", "revocata_il"]
    readonly_fields = ["inizio", "creata_il", "ultimo_uso"]
    fields = ["nome", "utente", "inizio", "scrittura", "creata_il", "ultimo_uso", "revocata_il"]

    def has_add_permission(self, request):
        # Le chiavi si creano dalla pagina «Claude», che mostra la chiave una volta sola.
        return False
