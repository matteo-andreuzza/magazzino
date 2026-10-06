from django.contrib import admin

from .models import AgenteStampa, Articolo, Categoria, Comanda, Ordine, RigaOrdine, Stampa, Stampante


class ArticoloInline(admin.TabularInline):
    model = Articolo
    extra = 0
    fields = ["nome", "reparto", "prezzo", "ordine", "attivo", "esaurito"]
    show_change_link = True


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    list_display = ["nome", "colore", "ordine"]
    list_editable = ["colore", "ordine"]
    inlines = [ArticoloInline]


@admin.register(Articolo)
class ArticoloAdmin(admin.ModelAdmin):
    list_display = ["nome", "categoria", "reparto", "prezzo", "attivo", "esaurito"]
    list_editable = ["prezzo", "attivo", "esaurito"]
    list_filter = ["categoria", "reparto", "attivo", "esaurito"]
    search_fields = ["nome"]
    autocomplete_fields = ["piatto"]


class RigaOrdineInline(admin.TabularInline):
    model = RigaOrdine
    extra = 0
    can_delete = False
    fields = ["descrizione", "reparto", "quantita", "prezzo", "note"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Ordine)
class OrdineAdmin(admin.ModelAdmin):
    """Solo consultazione: gli ordini si registrano e si annullano dalla cassa."""

    list_display = ["numero", "servizio", "postazione", "tavolo", "nome", "totale", "pagamento", "creato_il", "annullato_il"]
    list_filter = ["servizio", "postazione", "pagamento"]
    search_fields = ["nome", "tavolo"]
    date_hierarchy = "creato_il"
    fields = [
        "servizio", "numero", "postazione", "nome", "tavolo", "coperti", "note", "pagamento", "totale",
        "creato_il", "utente", "annullato_il", "annullato_da", "motivo_annullamento",
    ]
    readonly_fields = fields
    inlines = [RigaOrdineInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Stampante)
class StampanteAdmin(admin.ModelAdmin):
    list_display = ["nome", "connessione", "indirizzo", "agente", "attiva"]


@admin.register(Stampa)
class StampaAdmin(admin.ModelAdmin):
    list_display = ["creata_il", "documento", "reparto", "stampante", "ordine", "stato", "tentativi", "errore"]
    list_filter = ["stato", "documento", "stampante"]
    readonly_fields = [f.name for f in Stampa._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(Comanda)
class ComandaAdmin(admin.ModelAdmin):
    list_display = ["ordine", "reparto", "stato", "iniziata_il", "pronta_il", "ritirata_il"]
    list_filter = ["reparto", "stato"]
    readonly_fields = ["ordine", "reparto", "stato", "iniziata_il", "pronta_il", "ritirata_il"]

    def has_add_permission(self, request):
        return False


@admin.register(AgenteStampa)
class AgenteStampaAdmin(admin.ModelAdmin):
    list_display = ["nome", "ultimo_contatto", "indirizzo_ip", "versione"]
    readonly_fields = ["nome", "ultimo_contatto", "indirizzo_ip", "versione"]
