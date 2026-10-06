from django import forms
from django.core.exceptions import ValidationError

from magazzino.forms import CampoQuantita

from .models import Bevanda, Fusto, Spina, Spinatrice, TipoBevanda, TipoFusto


class BevandaForm(forms.ModelForm):
    densita = CampoQuantita(label="Peso di un litro (kg)", required=False, help_text=Bevanda._meta.get_field("densita").help_text)

    class Meta:
        model = Bevanda
        fields = ["nome", "tipo", "colore", "densita", "ordine", "attiva", "note"]
        widgets = {"tipo": forms.RadioSelect}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        b = self.instance
        # Cambiare come si conta dopo averla caricata o venduta falserebbe le giacenze.
        if b.pk and (b.movimenti.exists() or b.righe_vendute.exists() or b.tipi_fusto.exists()):
            self.fields["tipo"].disabled = True
            self.fields["tipo"].help_text = "Non modificabile: è già stata caricata, venduta o ha dei tipi di fusto."

    def clean_densita(self):
        valore = self.cleaned_data.get("densita")
        if valore is None:
            return Bevanda._meta.get_field("densita").default
        if not 0.5 <= valore <= 2:
            raise ValidationError("Un litro di birra pesa circa 1,01 kg, uno di vino circa 0,99.")
        return valore


class TipoFustoForm(forms.ModelForm):
    litri = CampoQuantita(label="Litri", help_text=TipoFusto._meta.get_field("litri").help_text)
    tara = CampoQuantita(
        label="Peso da vuoto (kg)", help_text=TipoFusto._meta.get_field("tara").help_text,
        error_messages={"required": "Scrivi quanto pesa un fusto vuoto, per esempio 12,5."},
    )
    confermati = forms.BooleanField(
        required=False, label="Litri e tara sono quelli veri",
        help_text="Spunta se i valori scritti sono già giusti: non saranno più provvisori. Cambiando litri o tara lo diventano da soli.",
    )
    anche_gli_altri = forms.BooleanField(
        required=False, initial=True, label="Stessi litri e tara anche per gli altri tipi con lo stesso nome",
    )

    class Meta:
        model = TipoFusto
        fields = ["bevanda", "nome", "litri", "tara", "colore", "forma", "ordine", "attivo"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["bevanda"].queryset = Bevanda.objects.filter(tipo=TipoBevanda.FUSTI)
        t = self.instance
        if t.pk and (t.fusti.exists() or t.movimenti.exists()):
            self.fields["litri"].help_text = (
                "Ci sono già fusti di questo tipo attaccati o finiti: cambiando i litri si ricalcolano anche le serate passate."
            )
            # I suoi fusti (e i litri usciti nelle serate passate) sono di quella bevanda.
            self.fields["bevanda"].disabled = True
            self.fields["bevanda"].help_text = "Non modificabile: ci sono già fusti di questo tipo caricati, attaccati o finiti."
        if t.tara_provvisoria:
            self.fields["tara"].help_text = (
                "Adesso litri e tara sono provvisori: scrivi quelli veri (diventano definitivi). "
                "Valgono anche per i conti delle serate già passate."
            )
        else:
            del self.fields["confermati"]
        self.gemelli = list(TipoFusto.objects.filter(nome__iexact=t.nome).exclude(pk=t.pk).select_related("bevanda")) if t.pk else []
        if self.gemelli:
            altri = ", ".join(g.bevanda.nome for g in self.gemelli)
            self.fields["anche_gli_altri"].help_text = f"Lo stesso fusto si usa anche per: {altri}."
        else:
            del self.fields["anche_gli_altri"]

    def clean_litri(self):
        valore = self.cleaned_data.get("litri")
        if not valore:
            raise ValidationError("Scrivi quanti litri contiene un fusto pieno.")
        return valore

    def clean_tara(self):
        valore = self.cleaned_data.get("tara")
        if not valore:
            raise ValidationError("Scrivi quanto pesa un fusto vuoto, per esempio 12,5.")
        return valore

    @property
    def litri_e_tara_scritti(self):
        """L'amministrazione ha scritto (o confermato) litri e tara: non sono più provvisori.

        Cambiando solo colore, forma o nome i valori simbolici restano provvisori.
        """
        return bool({"litri", "tara"} & set(self.changed_data)) or bool(self.cleaned_data.get("confermati"))

    def save(self, commit=True):
        scritti = not self.instance.pk or self.litri_e_tara_scritti
        if scritti:
            self.instance.tara_provvisoria = False
        tipo = super().save(commit)
        if commit and scritti and self.cleaned_data.get("anche_gli_altri"):
            TipoFusto.objects.filter(pk__in=[g.pk for g in self.gemelli]).update(
                litri=tipo.litri, tara=tipo.tara, tara_provvisoria=False,
            )
        return tipo


class SpinatriceForm(forms.ModelForm):
    class Meta:
        model = Spinatrice
        fields = ["nome", "ordine", "attiva"]

    def clean_attiva(self):
        attiva = self.cleaned_data.get("attiva")
        if not attiva and self.instance.pk and Fusto.objects.filter(spina__spinatrice=self.instance, finito_con__isnull=True).exists():
            raise ValidationError("Ci sono fusti attaccati alle sue spine: prima spostali tra i finiti.")
        return attiva


class SpinaForm(forms.ModelForm):
    class Meta:
        model = Spina
        fields = ["spinatrice", "nome", "bevanda", "ordine", "attiva"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["bevanda"].queryset = Bevanda.objects.filter(tipo=TipoBevanda.FUSTI)

    def _attaccato(self):
        if not self.instance.pk:
            return None
        return self.instance.fusti.filter(finito_con__isnull=True).select_related("tipo__bevanda").first()

    def clean_attiva(self):
        attiva = self.cleaned_data.get("attiva")
        if not attiva and self._attaccato() is not None:
            raise ValidationError("C'è un fusto attaccato: prima spostalo tra i finiti.")
        return attiva

    def clean_bevanda(self):
        bevanda = self.cleaned_data.get("bevanda")
        attaccato = self._attaccato()
        if bevanda is not None and attaccato is not None and attaccato.tipo.bevanda_id != bevanda.pk:
            raise ValidationError(f"Adesso c'è attaccato un fusto di {attaccato.tipo.bevanda.nome}: prima spostalo tra i finiti.")
        return bevanda
