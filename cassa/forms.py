from django import forms
from django.core.exceptions import ValidationError

from magazzino.forms import CampoQuantita
from magazzino.models import Piatto, Prodotto

from .formati import euro, leggi_euro
from .models import Articolo, Connessione, ImpostazioniCassa, Stampante


class CampoEuro(forms.Field):
    """Prezzo scritto all'italiana (12,50) o con il punto (12.50)."""

    widget = forms.TextInput(attrs={"inputmode": "decimal", "autocomplete": "off"})

    def to_python(self, value):
        try:
            return leggi_euro(value)
        except ValueError as e:
            raise ValidationError(str(e)) from e

    def prepare_value(self, value):
        if value is None or isinstance(value, str):
            return value or ""
        return euro(value, simbolo=False)


class ArticoloForm(forms.ModelForm):
    prezzo = CampoEuro(required=False, label="Prezzo (€)", help_text=Articolo._meta.get_field("prezzo").help_text)
    quantita_prodotto = CampoQuantita(
        label=Articolo._meta.get_field("quantita_prodotto").verbose_name.capitalize(),
        help_text=Articolo._meta.get_field("quantita_prodotto").help_text,
    )

    class Meta:
        model = Articolo
        fields = [
            "nome", "categoria", "reparto", "prezzo", "piatto", "prodotto", "quantita_prodotto",
            "ordine", "attivo", "esaurito", "note",
        ]
        widgets = {"reparto": forms.RadioSelect}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["piatto"].queryset = Piatto.objects.select_related("menu").order_by("menu__ordine", "ordine", "nome")
        self.fields["piatto"].label_from_instance = lambda p: f"{p.nome} ({p.menu.nome})"
        prodotti = Prodotto.objects.filter(attivo=True)
        if self.instance.prodotto_id:
            prodotti = prodotti | Prodotto.objects.filter(pk=self.instance.prodotto_id)
        self.fields["prodotto"].queryset = prodotti.distinct()
        if not self.instance.pk:
            self.initial.setdefault("quantita_prodotto", 1)

    def clean_quantita_prodotto(self):
        valore = self.cleaned_data.get("quantita_prodotto")
        if not valore:
            raise ValidationError("Scrivi una quantità maggiore di zero.")
        return valore


class ImpostazioniForm(forms.ModelForm):
    class Meta:
        model = ImpostazioniCassa
        fields = [
            "monitor_cucina", "cassa_bar", "scarico_magazzino",
            "intestazione", "stampante_cucina", "stampante_bar",
            "ricevuta_cliente", "stampante_ricevute", "messaggio_ricevuta",
            "stampante_cassa_bar", "stampa_comande_cassa_bar", "ubicazione_scarico",
        ]


class StampanteForm(forms.ModelForm):
    class Meta:
        model = Stampante
        fields = ["nome", "connessione", "indirizzo", "agente", "colonne", "accenti", "attiva"]
        widgets = {"connessione": forms.RadioSelect}

    def clean(self):
        dati = super().clean()
        if dati.get("connessione") == Connessione.BROWSER:
            dati["indirizzo"] = ""
            dati["agente"] = ""
        return dati
