from django import forms
from django.core.exceptions import ValidationError

from .models import Piatto, Prodotto, Servizio
from .quantita import QuantitaNonValida, formatta_numero, leggi_quantita


class CampoQuantita(forms.Field):
    """Quantità scritta all'italiana (2,5) o con il punto (2.5)."""

    widget = forms.TextInput(attrs={"inputmode": "decimal", "autocomplete": "off"})

    def to_python(self, value):
        try:
            return leggi_quantita(value, ammetti_zero=True)
        except QuantitaNonValida as e:
            raise ValidationError(str(e)) from e

    def prepare_value(self, value):
        if isinstance(value, str):
            return value
        return formatta_numero(value)


class ProdottoForm(forms.ModelForm):
    scorta_minima = CampoQuantita(
        required=False,
        label="Scorta minima",
        help_text=Prodotto._meta.get_field("scorta_minima").help_text,
    )

    class Meta:
        model = Prodotto
        fields = ["nome", "categoria", "unita", "scorta_minima", "note", "attivo"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.unita_bloccata = bool(self.instance.pk and self.instance.movimenti.exists())
        if self.unita_bloccata:
            self.fields["unita"].disabled = True
            self.fields["unita"].help_text = (
                "Non modificabile: ci sono già movimenti registrati con questa unità."
            )


class DataInput(forms.DateInput):
    input_type = "date"

    def __init__(self, **kwargs):
        super().__init__(format="%Y-%m-%d", **kwargs)


class ServizioForm(forms.ModelForm):
    class Meta:
        model = Servizio
        fields = ["data", "tipo", "nome", "piatti", "note"]
        widgets = {
            "data": DataInput(),
            "tipo": forms.RadioSelect,
            "piatti": forms.CheckboxSelectMultiple,
            "note": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["piatti"].queryset = Piatto.objects.select_related("menu").order_by(
            "menu__ordine", "menu__nome", "menu_id", "ordine", "nome"
        )

    def gruppi_piatti(self):
        """[(menu, [(piatto, selezionato)])] per mostrare le caselle divise per menu."""
        valore = self["piatti"].value() or []
        selezionati = {str(v.pk if hasattr(v, "pk") else v) for v in valore}
        gruppi = []
        for piatto in self.fields["piatti"].queryset:
            if not gruppi or gruppi[-1][0].pk != piatto.menu_id:
                gruppi.append((piatto.menu, []))
            gruppi[-1][1].append((piatto, str(piatto.pk) in selezionati))
        return gruppi
