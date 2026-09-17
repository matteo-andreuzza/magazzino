from django.contrib import admin

from .models import Allegato, Ingrediente, Menu, Movimento, Operazione, Piatto, Prodotto, Servizio, Ubicazione


@admin.register(Ubicazione)
class UbicazioneAdmin(admin.ModelAdmin):
    list_display = ["nome", "tipo", "ordine", "attiva"]
    list_editable = ["ordine", "attiva"]


@admin.register(Prodotto)
class ProdottoAdmin(admin.ModelAdmin):
    list_display = ["nome", "categoria", "unita", "scorta_minima", "attivo"]
    list_filter = ["categoria", "attivo"]
    search_fields = ["nome"]

    def get_readonly_fields(self, request, obj=None):
        # Cambiare unità dopo aver registrato movimenti falserebbe le giacenze.
        if obj is not None and obj.movimenti.exists():
            return ["unita"]
        return []


class PiattoInline(admin.TabularInline):
    model = Piatto
    extra = 0
    show_change_link = True


@admin.register(Menu)
class MenuAdmin(admin.ModelAdmin):
    list_display = ["nome", "ordine"]
    inlines = [PiattoInline]


class IngredienteInline(admin.TabularInline):
    model = Ingrediente
    extra = 1
    autocomplete_fields = ["prodotto"]


@admin.register(Piatto)
class PiattoAdmin(admin.ModelAdmin):
    list_display = ["nome", "menu", "ordine"]
    list_filter = ["menu"]
    search_fields = ["nome"]
    inlines = [IngredienteInline]


@admin.register(Servizio)
class ServizioAdmin(admin.ModelAdmin):
    list_display = ["__str__", "data", "tipo", "nome", "chiuso_il"]
    list_filter = ["tipo"]
    filter_horizontal = ["piatti"]
    # Si termina e si riapre dall'app, che blocca le registrazioni in corso.
    readonly_fields = ["chiuso_il", "chiuso_da"]


class MovimentoInline(admin.TabularInline):
    model = Movimento
    extra = 0
    can_delete = False
    fields = ["prodotto", "ubicazione", "quantita"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class AllegatoInline(admin.TabularInline):
    model = Allegato
    extra = 0
    can_delete = False
    fields = ["nome", "tipo_contenuto", "dimensione", "caricato_il", "utente"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Operazione)
class OperazioneAdmin(admin.ModelAdmin):
    """Solo consultazione: i movimenti si registrano e si annullano dall'app,
    che controlla che le giacenze restino corrette."""

    list_display = ["creata_il", "tipo", "servizio", "utente", "note"]
    list_filter = ["tipo", "servizio"]
    date_hierarchy = "creata_il"
    fields = ["tipo", "servizio", "utente", "creata_il", "note"]
    readonly_fields = fields
    inlines = [MovimentoInline, AllegatoInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
