from django import forms
from django.core.exceptions import ValidationError

from magazzino.forms import CampoQuantita
from bar.models import Bevanda
from magazzino.models import Piatto

from .formati import euro, leggi_euro
from .models import Articolo, Carta, Connessione, ImpostazioniCassa, Stampante


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
    quantita_bevanda = CampoQuantita(
        label=Articolo._meta.get_field("quantita_bevanda").verbose_name.capitalize(),
        help_text=Articolo._meta.get_field("quantita_bevanda").help_text,
    )

    class Meta:
        model = Articolo
        fields = [
            "nome", "categoria", "reparto", "prezzo", "piatto", "bevanda", "quantita_bevanda",
            "ordine", "attivo", "esaurito", "note",
        ]
        widgets = {"reparto": forms.RadioSelect}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["piatto"].queryset = Piatto.objects.select_related("menu").order_by("menu__ordine", "ordine", "nome")
        self.fields["piatto"].label_from_instance = lambda p: f"{p.nome} ({p.menu.nome})"
        bevande = Bevanda.objects.filter(attiva=True)
        if self.instance.bevanda_id:
            bevande = bevande | Bevanda.objects.filter(pk=self.instance.bevanda_id)
        self.fields["bevanda"].queryset = bevande.distinct()
        self.fields["bevanda"].label_from_instance = lambda b: f"{b.nome} ({b.come_si_vende})"
        if not self.instance.pk:
            self.initial.setdefault("quantita_bevanda", 1)

    def clean_quantita_bevanda(self):
        valore = self.cleaned_data.get("quantita_bevanda")
        if not valore:
            raise ValidationError("Scrivi una quantità maggiore di zero.")
        return valore


class ImpostazioniForm(forms.ModelForm):
    class Meta:
        model = ImpostazioniCassa
        fields = [
            "monitor_cucina", "cassa_bar",
            "intestazione", "stampante_cucina", "stampante_bar",
            "ricevuta_cliente", "stampante_ricevute", "messaggio_ricevuta",
            "stampante_cassa_bar", "stampa_comande_cassa_bar",
        ]


class StampanteForm(forms.ModelForm):
    class Meta:
        model = Stampante
        fields = ["nome", "connessione", "indirizzo", "carta", "colonne", "accenti", "agente", "attiva"]
        widgets = {"connessione": forms.RadioSelect}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["carta"].required = False  # serve solo alle stampanti normali

    def clean(self):
        dati = super().clean()
        dati["carta"] = dati.get("carta") or Carta.A4
        if dati.get("connessione") == Connessione.BROWSER:
            dati["indirizzo"] = ""
            dati["agente"] = ""
        return dati
