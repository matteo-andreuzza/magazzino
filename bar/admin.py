from django.contrib import admin

from .models import Bevanda, ContaFusti, Fusto, MovimentoBar, OperazioneBar, Pesata, Spina, Spinatrice, TipoFusto


class TipoFustoInline(admin.TabularInline):
    model = TipoFusto
    extra = 0


class SpinaInline(admin.TabularInline):
    model = Spina
    extra = 0


@admin.register(Spinatrice)
class SpinatriceAdmin(admin.ModelAdmin):
    list_display = ["nome", "ordine", "attiva"]
    inlines = [SpinaInline]


@admin.register(Bevanda)
class BevandaAdmin(admin.ModelAdmin):
    list_display = ["nome", "tipo", "ordine", "attiva"]
    list_editable = ["ordine", "attiva"]
    inlines = [TipoFustoInline]


class MovimentoBarInline(admin.TabularInline):
    model = MovimentoBar
    extra = 0
    readonly_fields = ["bevanda", "tipo_fusto", "quantita"]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(OperazioneBar)
class OperazioneBarAdmin(admin.ModelAdmin):
    list_display = ["creata_il", "tipo", "utente", "note"]
    list_filter = ["tipo"]
    inlines = [MovimentoBarInline]
    readonly_fields = ["tipo", "utente", "creata_il"]


class PesataInline(admin.TabularInline):
    model = Pesata
    extra = 0
    readonly_fields = ["fusto", "peso", "vuoto", "misurata"]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ContaFusti)
class ContaFustiAdmin(admin.ModelAdmin):
    list_display = ["servizio", "creata_il", "utente"]
    inlines = [PesataInline]
    readonly_fields = ["servizio", "utente", "creata_il"]


@admin.register(Fusto)
class FustoAdmin(admin.ModelAdmin):
    list_display = ["__str__", "tipo", "aperto_con", "finito_con"]
    list_filter = ["tipo"]
    readonly_fields = ["tipo", "aperto_con", "finito_con"]
