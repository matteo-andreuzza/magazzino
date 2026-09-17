import uuid

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Q
from django.db.models.functions import Lower
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import formats, timezone

from .quantita import ZERO, formatta_quantita


class TipoUbicazione(models.TextChoices):
    FRIGO = "frigo", "Frigo"
    CONGELATORE = "congelatore", "Congelatore"
    AMBIENTE = "ambiente", "Temperatura ambiente"


class Ubicazione(models.Model):
    nome = models.CharField(max_length=60)
    tipo = models.CharField(max_length=20, choices=TipoUbicazione.choices)
    ordine = models.PositiveIntegerField(default=0, help_text="Ordine di visualizzazione.")
    attiva = models.BooleanField(default=True, help_text="Le ubicazioni non attive non compaiono nei moduli di carico.")
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["ordine", "nome"]
        verbose_name = "ubicazione"
        verbose_name_plural = "ubicazioni"
        constraints = [
            models.UniqueConstraint(
                Lower("nome"),
                name="ubicazione_nome_unico",
                violation_error_message="Esiste già un'ubicazione con questo nome.",
            ),
        ]

    def __str__(self):
        return self.nome


class Categoria(models.TextChoices):
    CARNE = "carne", "Carne"
    PESCE = "pesce", "Pesce"
    PRONTI = "pronti", "Già pronti"
    PASTA_RISO = "pasta_riso", "Pasta, riso e polenta"
    VERDURE = "verdure", "Verdure e patate"
    LATTICINI = "latticini", "Formaggi e latticini"
    DISPENSA = "dispensa", "Condimenti e dispensa"
    PANE = "pane", "Pane"
    DOLCI = "dolci", "Dolci e caffè"
    BEVANDE = "bevande", "Bevande"
    ALTRO = "altro", "Altro"


class Unita(models.TextChoices):
    KG = "kg", "kg (chili)"
    LITRI = "l", "l (litri)"
    PEZZI = "pz", "pz (pezzi)"
    CONFEZIONI = "conf", "confezioni"
    BUSTE = "busta", "buste"
    VASI = "vaso", "vasi"
    VASCHETTE = "vaschetta", "vaschette"
    CARTONI = "cartone", "cartoni"
    BOTTIGLIE = "bottiglia", "bottiglie"
    LATTINE = "lattina", "lattine"
    MAZZI = "mazzo", "mazzi"


class Prodotto(models.Model):
    nome = models.CharField(max_length=100)
    categoria = models.CharField(max_length=20, choices=Categoria.choices, default=Categoria.ALTRO)
    unita = models.CharField(
        "unità di misura",
        max_length=12,
        choices=Unita.choices,
        help_text="Non si può cambiare dopo aver registrato dei movimenti.",
    )
    scorta_minima = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(ZERO)],
        help_text="Facoltativa: sotto questa quantità totale il prodotto viene segnalato.",
    )
    note = models.CharField(max_length=200, blank=True)
    attivo = models.BooleanField(default=True, help_text="I prodotti non attivi non compaiono nei moduli di carico.")

    class Meta:
        ordering = [Lower("nome")]
        verbose_name = "prodotto"
        verbose_name_plural = "prodotti"
        constraints = [
            models.UniqueConstraint(
                Lower("nome"),
                name="prodotto_nome_unico",
                violation_error_message="Esiste già un prodotto con questo nome.",
            ),
        ]

    def __str__(self):
        return self.nome

    def formatta(self, valore):
        return formatta_quantita(valore, self.unita)


class Menu(models.Model):
    nome = models.CharField(max_length=80, unique=True)
    note = models.TextField(blank=True)
    ordine = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["ordine", "nome"]
        verbose_name = "menu"
        verbose_name_plural = "menu"

    def __str__(self):
        return self.nome


class Piatto(models.Model):
    menu = models.ForeignKey(Menu, on_delete=models.CASCADE, related_name="piatti")
    nome = models.CharField(max_length=120)
    note = models.CharField(max_length=250, blank=True)
    ordine = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["menu__ordine", "ordine", "nome"]
        verbose_name = "piatto"
        verbose_name_plural = "piatti"
        constraints = [
            models.UniqueConstraint(
                fields=["menu", "nome"],
                name="piatto_unico_per_menu",
                violation_error_message="Questo piatto esiste già in questo menu.",
            ),
        ]

    def __str__(self):
        return f"{self.nome} ({self.menu})"


class Ingrediente(models.Model):
    piatto = models.ForeignKey(Piatto, on_delete=models.CASCADE, related_name="ingredienti")
    prodotto = models.ForeignKey(Prodotto, on_delete=models.PROTECT, related_name="ingredienti")
    note = models.CharField(max_length=120, blank=True)
    ordine = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["ordine", "pk"]
        verbose_name = "ingrediente"
        verbose_name_plural = "ingredienti"
        constraints = [
            models.UniqueConstraint(
                fields=["piatto", "prodotto"],
                name="ingrediente_unico_per_piatto",
                violation_error_message="Questo prodotto è già tra gli ingredienti del piatto.",
            ),
        ]

    def __str__(self):
        return f"{self.prodotto} in {self.piatto.nome}"


class TipoServizio(models.TextChoices):
    SERA = "sera", "Serata"
    PRANZO = "pranzo", "Pranzo"


class Servizio(models.Model):
    """Una serata (o un pranzo) della sagra."""

    data = models.DateField()
    tipo = models.CharField(max_length=10, choices=TipoServizio.choices, default=TipoServizio.SERA)
    nome = models.CharField(max_length=80, blank=True, help_text="Facoltativo, es. «Pranzo anniversari».")
    piatti = models.ManyToManyField(
        Piatto,
        blank=True,
        related_name="servizi",
        help_text="Piatti serviti: servono a mostrare per primi i loro ingredienti.",
    )
    note = models.TextField(blank=True)
    chiuso_il = models.DateTimeField(
        "terminato il", null=True, blank=True, help_text="Da terminato non si possono più registrare prelievi e rientri."
    )
    chiuso_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        verbose_name="terminato da",
    )

    class Meta:
        ordering = ["-data", "tipo", "nome"]
        verbose_name = "servizio (serata o pranzo)"
        verbose_name_plural = "servizi (serate e pranzi)"
        constraints = [
            models.UniqueConstraint(
                fields=["data", "tipo", "nome"],
                name="servizio_unico",
                violation_error_message="Esiste già un servizio con la stessa data, tipo e nome.",
            ),
        ]

    def __str__(self):
        giorno = formats.date_format(self.data, "l j F Y").lower()
        if self.nome:
            return f"{self.nome} – {giorno}"
        return f"{self.get_tipo_display()} di {giorno}"

    @property
    def chiuso(self):
        return self.chiuso_il is not None

    @property
    def tipo_con_articolo(self):
        return "il pranzo" if self.tipo == TipoServizio.PRANZO else "la serata"

    @property
    def desinenza(self):
        """Per concordare: «serata terminat-a», «pranzo terminat-o»."""
        return "o" if self.tipo == TipoServizio.PRANZO else "a"


class TipoOperazione(models.TextChoices):
    INVENTARIO = "inventario", "Inventario iniziale"
    ACQUISTO = "acquisto", "Acquisto / consegna"
    CUCINA = "cucina", "Messo a cucinare"
    RESO = "reso", "Rientrato dalla cucina"
    SPOSTAMENTO = "spostamento", "Spostamento"
    SCARTO = "scarto", "Scarto"
    RETTIFICA = "rettifica", "Correzione conteggio"
    VENDITA = "vendita", "Venduto in cassa"


TIPI_CON_SERVIZIO = [TipoOperazione.CUCINA, TipoOperazione.RESO, TipoOperazione.VENDITA]
TIPI_CON_ALLEGATI = [TipoOperazione.INVENTARIO, TipoOperazione.ACQUISTO]


class Operazione(models.Model):
    """Un gruppo di movimenti registrati insieme (un invio di modulo)."""

    tipo = models.CharField(max_length=20, choices=TipoOperazione.choices)
    servizio = models.ForeignKey(
        Servizio, on_delete=models.PROTECT, null=True, blank=True, related_name="operazioni"
    )
    utente = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    creata_il = models.DateTimeField(default=timezone.now, db_index=True)
    note = models.CharField(max_length=250, blank=True)
    # Impedisce di registrare due volte lo stesso modulo (doppio tocco sul pulsante).
    token = models.UUIDField(null=True, blank=True, unique=True, editable=False)

    class Meta:
        ordering = ["-creata_il", "-pk"]
        verbose_name = "operazione"
        verbose_name_plural = "operazioni"
        constraints = [
            models.CheckConstraint(
                condition=(Q(tipo__in=TIPI_CON_SERVIZIO) & Q(servizio__isnull=False))
                | (~Q(tipo__in=TIPI_CON_SERVIZIO) & Q(servizio__isnull=True)),
                name="operazione_servizio_coerente",
            ),
        ]

    def __str__(self):
        quando = formats.date_format(timezone.localtime(self.creata_il), "d/m/Y H:i")
        return f"{self.get_tipo_display()} del {quando}"

    @property
    def ammette_allegati(self):
        return self.tipo in TIPI_CON_ALLEGATI


class Movimento(models.Model):
    """Variazione di giacenza: positiva = entra, negativa = esce."""

    operazione = models.ForeignKey(Operazione, on_delete=models.CASCADE, related_name="movimenti")
    prodotto = models.ForeignKey(Prodotto, on_delete=models.PROTECT, related_name="movimenti")
    ubicazione = models.ForeignKey(Ubicazione, on_delete=models.PROTECT, related_name="movimenti")
    quantita = models.DecimalField(max_digits=12, decimal_places=3)

    class Meta:
        ordering = ["pk"]
        verbose_name = "movimento"
        verbose_name_plural = "movimenti"
        indexes = [models.Index(fields=["prodotto", "ubicazione"])]
        constraints = [
            models.CheckConstraint(condition=~Q(quantita=0), name="movimento_quantita_non_zero"),
        ]

    def __str__(self):
        return f"{self.prodotto}: {self.prodotto.formatta(self.quantita)} in {self.ubicazione}"


def percorso_allegato(allegato, nome_file):
    # Nome casuale: il nome originale resta solo in `nome`, così non si
    # sovrascrivono file con lo stesso nome e non si indovinano i percorsi.
    estensione = nome_file.rsplit(".", 1)[-1].lower() if "." in nome_file else "bin"
    adesso = timezone.localtime()
    return f"{adesso:%Y/%m}/{uuid.uuid4().hex}.{estensione}"


class Allegato(models.Model):
    """Fattura, bolla o foto allegata a un carico."""

    operazione = models.ForeignKey(Operazione, on_delete=models.CASCADE, related_name="allegati")
    file = models.FileField(upload_to=percorso_allegato, max_length=200)
    nome = models.CharField("nome del file", max_length=200)
    tipo_contenuto = models.CharField(max_length=50)
    dimensione = models.PositiveBigIntegerField()
    utente = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    caricato_il = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["caricato_il", "pk"]
        verbose_name = "allegato"
        verbose_name_plural = "allegati"

    def __str__(self):
        return self.nome

    @property
    def immagine(self):
        return self.tipo_contenuto in {"image/jpeg", "image/png", "image/webp"}


@receiver(post_delete, sender=Allegato)
def elimina_file_allegato(sender, instance, **kwargs):
    # Anche quando l'allegato sparisce insieme all'operazione annullata.
    # Il file si cancella solo se l'eliminazione va davvero a buon fine.
    nome, storage = instance.file.name, instance.file.storage
    if nome:
        transaction.on_commit(lambda: storage.delete(nome), using=kwargs.get("using"))
