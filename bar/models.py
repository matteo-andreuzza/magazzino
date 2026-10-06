"""Magazzino del bar: bevande e fusti (birra e vino alla spina).

È tenuto a parte dal magazzino della cucina perché funziona in un altro modo:

* le giacenze di lattine e bottiglie sono la somma dei carichi e delle
  correzioni MENO quanto venduto in cassa (calcolato dagli ordini, vedi
  services.py): non c'è niente da scaricare a mano e la cassa non viene mai
  bloccata da un conteggio sbagliato;
* birra e vino alla spina si contano in fusti. Al bancone ci sono le
  spinatrici (una per la birra, una per il vino), ognuna con le sue spine
  (bionda e rossa, bianco e rosso): a ogni spina è attaccato un fusto alla
  volta. Sull'app si fa quello che si fa al bancone: si attacca un fusto
  pieno, si sposta tra i consumati quello finito; a fine serata si pesa
  quello rimasto attaccato. Dal peso si ricavano i litri usciti, da
  confrontare con quelli venduti in cassa.
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone

from magazzino.models import Servizio
from magazzino.quantita import formatta_quantita

# Un litro di birra pesa poco più di un chilo, uno di vino poco meno.
DENSITA_BIRRA = Decimal("1.01")
DENSITA_VINO = Decimal("0.99")


class TipoBevanda(models.TextChoices):
    LATTINE = "lattina", "Lattine"
    BOTTIGLIE = "bottiglia", "Bottiglie"
    LITRI = "litri", "Sfusa a litri (senza fusti da pesare)"
    FUSTI = "fusti", "Alla spina da fusti (birra, vino)"


class ColoreBevanda(models.TextChoices):
    """Il colore nei disegni: dentro i fusti, nel bicchiere sotto la spina."""

    BIONDA = "bionda", "Birra bionda"
    ROSSA = "rossa", "Birra rossa (ambrata)"
    SCURA = "scura", "Birra scura"
    BIANCO = "bianco", "Vino bianco"
    ROSATO = "rosato", "Vino rosato"
    ROSSO = "rosso", "Vino rosso"


class Bevanda(models.Model):
    nome = models.CharField(max_length=60)
    tipo = models.CharField(
        "come si conta", max_length=10, choices=TipoBevanda.choices,
        help_text="La birra si conta in fusti (pieni, attaccati, finiti); le altre in lattine, bottiglie o litri.",
    )
    ordine = models.PositiveIntegerField(default=0, help_text="Ordine negli elenchi.")
    attiva = models.BooleanField(default=True, help_text="Le bevande non attive non compaiono nei moduli.")
    note = models.CharField(max_length=200, blank=True)
    # Solo per quelle alla spina.
    densita = models.DecimalField(
        "peso di un litro (kg)", max_digits=4, decimal_places=3, default=DENSITA_BIRRA,
        validators=[MinValueValidator(Decimal("0.5"))],
        help_text="Solo alla spina: serve a ricavare i litri dal peso dei fusti. Birra circa 1,01; vino circa 0,99.",
    )
    colore = models.CharField(
        "colore nel disegno", max_length=10, choices=ColoreBevanda.choices, default=ColoreBevanda.BIONDA,
        help_text="Solo alla spina: il colore della bevanda nei fusti e nel bicchiere disegnati.",
    )

    class Meta:
        ordering = ["ordine", Lower("nome")]
        verbose_name = "bevanda"
        verbose_name_plural = "bevande"
        constraints = [
            models.UniqueConstraint(
                Lower("nome"), name="bevanda_nome_unico",
                violation_error_message="Esiste già una bevanda con questo nome.",
            ),
        ]

    def __str__(self):
        return self.nome

    @property
    def unita(self):
        """Unità delle giacenze e del venduto (per la birra: i litri venduti)."""
        if self.tipo in (TipoBevanda.LITRI, TipoBevanda.FUSTI):
            return "l"
        return self.tipo

    @property
    def come_si_vende(self):
        """Per gli elenchi a tendina: «in lattine», «in litri»…"""
        return "in litri" if self.unita == "l" else f"in {self.get_tipo_display().lower()}"

    @property
    def a_fusti(self):
        return self.tipo == TipoBevanda.FUSTI

    @property
    def vino(self):
        return self.colore in (ColoreBevanda.BIANCO, ColoreBevanda.ROSATO, ColoreBevanda.ROSSO)

    def formatta(self, valore):
        return formatta_quantita(valore, self.unita)


class ColoreFusto(models.TextChoices):
    """Com'è fatto fuori: aiuta a riconoscerlo al bancone («il largo argento», «l'alto nero»)."""

    ACCIAIO = "acciaio", "Acciaio (argento)"
    NERO = "nero", "Nero"
    AZZURRO = "azzurro", "Azzurro"
    BLU = "blu", "Blu"
    ROSSO = "rosso", "Rosso"
    VERDE = "verde", "Verde"
    GIALLO = "giallo", "Giallo"
    BIANCO = "bianco", "Bianco"


class FormaFusto(models.TextChoices):
    ALTO = "alto", "Alto"
    LARGO = "largo", "Basso e largo"
    BASSO = "basso", "Piccolo"


class TipoFusto(models.Model):
    """Un tipo di fusto di una bevanda: quanti litri contiene e quanto pesa da vuoto (tara).

    La tara dipende da com'è fatto il fusto (alto nero, largo argento…), quindi
    ogni tipo ha la sua. Se lo stesso fusto si usa per due bevande (la bionda e
    la rossa nei fusti da 50 litri) ci sono due tipi con lo stesso nome: salvando
    la tara di uno si può aggiornare anche l'altro.
    """

    bevanda = models.ForeignKey(
        Bevanda, on_delete=models.PROTECT, related_name="tipi_fusto", limit_choices_to={"tipo": TipoBevanda.FUSTI},
    )
    nome = models.CharField(max_length=40, help_text="Come lo chiamate al bancone: «Fusto da 50 litri», «Largo argento», «Alto nero».")
    litri = models.DecimalField(
        max_digits=6, decimal_places=2, validators=[MinValueValidator(Decimal("1"))],
        help_text="Quanto contiene un fusto pieno.",
    )
    tara = models.DecimalField(
        "peso da vuoto (kg)", max_digits=6, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Peso di un fusto vuoto (tara): serve a calcolare quanto resta in quelli pesati.",
    )
    # Messa a un valore simbolico finché l'amministrazione non scrive quella vera:
    # le pagine del bar la segnalano e i controlli sui pesi avvisano senza bloccare.
    tara_provvisoria = models.BooleanField(default=False, editable=False)
    colore = models.CharField(max_length=10, choices=ColoreFusto.choices, default=ColoreFusto.ACCIAIO)
    forma = models.CharField(max_length=10, choices=FormaFusto.choices, default=FormaFusto.ALTO)
    ordine = models.PositiveIntegerField(default=0)
    attivo = models.BooleanField(default=True, help_text="I tipi non attivi non compaiono (se non ce ne sono di pieni o attaccati).")

    class Meta:
        ordering = ["bevanda__ordine", "bevanda__nome", "ordine", "litri", "nome"]
        verbose_name = "tipo di fusto"
        verbose_name_plural = "tipi di fusto"
        constraints = [
            models.UniqueConstraint(
                "bevanda", Lower("nome"), name="tipo_fusto_nome_unico_per_bevanda",
                violation_error_message="Questa bevanda ha già un tipo di fusto con questo nome.",
            ),
        ]

    def __str__(self):
        return self.nome

    @property
    def nome_completo(self):
        """«Fusto da 50 litri (Birra bionda)»: per gli elenchi con più bevande."""
        return f"{self.nome} ({self.bevanda.nome})"

    # Stessa interfaccia di Bevanda, per i moduli di carico e correzione.
    unita = "fusto"

    def formatta(self, valore):
        return formatta_quantita(valore, self.unita)

    @property
    def peso_pieno(self):
        if self.tara is None:
            return None
        return self.tara + self.litri * self.bevanda.densita

    @property
    def grande(self):
        return self.forma != FormaFusto.BASSO


class Spinatrice(models.Model):
    """Una spinatrice del bancone (es. quella della birra e quella del vino), con le sue spine."""

    nome = models.CharField(max_length=40, help_text="Es. «Birra» o «Vino».")
    ordine = models.PositiveIntegerField(default=0)
    attiva = models.BooleanField(default=True, help_text="Le spinatrici non attive non compaiono (se non hanno fusti attaccati).")

    class Meta:
        ordering = ["ordine", "pk"]
        verbose_name = "spinatrice"
        verbose_name_plural = "spinatrici"
        constraints = [
            models.UniqueConstraint(
                Lower("nome"), name="spinatrice_nome_unico",
                violation_error_message="Esiste già una spinatrice con questo nome.",
            ),
        ]

    def __str__(self):
        return self.nome


class Spina(models.Model):
    """Una spina (rubinetto) di una spinatrice: ci si attacca un fusto alla volta."""

    spinatrice = models.ForeignKey(Spinatrice, on_delete=models.PROTECT, related_name="spine")
    nome = models.CharField(max_length=40, help_text="Es. «Bionda», «Rossa», «Bianco», «Rosso».")
    bevanda = models.ForeignKey(
        Bevanda, on_delete=models.PROTECT, null=True, blank=True, related_name="spine",
        limit_choices_to={"tipo": TipoBevanda.FUSTI},
        help_text="Cosa esce da questa spina: si possono attaccare solo i suoi fusti. Vuoto = qualsiasi.",
    )
    ordine = models.PositiveIntegerField(default=0)
    attiva = models.BooleanField(default=True, help_text="Le spine non attive non compaiono (se non hanno un fusto attaccato).")

    class Meta:
        ordering = ["spinatrice__ordine", "spinatrice_id", "ordine", "pk"]
        verbose_name = "spina"
        verbose_name_plural = "spine"
        constraints = [
            models.UniqueConstraint(
                Lower("nome"), name="spina_nome_unico", violation_error_message="Esiste già una spina con questo nome.",
            ),
        ]

    def __str__(self):
        return self.nome


# ---------------------------------------------------------------------------
# Carichi e correzioni
# ---------------------------------------------------------------------------


class TipoOperazioneBar(models.TextChoices):
    INVENTARIO = "inventario", "Inventario iniziale"
    CARICO = "carico", "Consegna / acquisto"
    RETTIFICA = "rettifica", "Correzione conteggio"


class OperazioneBar(models.Model):
    tipo = models.CharField(max_length=15, choices=TipoOperazioneBar.choices)
    utente = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    creata_il = models.DateTimeField(default=timezone.now, db_index=True)
    note = models.CharField(max_length=250, blank=True)
    # Impedisce di registrare due volte lo stesso modulo (doppio tocco sul pulsante).
    token = models.UUIDField(null=True, blank=True, unique=True, editable=False)

    class Meta:
        ordering = ["-creata_il", "-pk"]
        verbose_name = "operazione del bar"
        verbose_name_plural = "operazioni del bar"

    def __str__(self):
        return f"{self.get_tipo_display()} del {timezone.localtime(self.creata_il):%d/%m/%Y %H:%M}"


class MovimentoBar(models.Model):
    """Variazione di giacenza: di una bevanda (lattine, bottiglie, litri) o dei fusti pieni di un tipo."""

    operazione = models.ForeignKey(OperazioneBar, on_delete=models.CASCADE, related_name="movimenti")
    bevanda = models.ForeignKey(Bevanda, on_delete=models.PROTECT, null=True, blank=True, related_name="movimenti")
    tipo_fusto = models.ForeignKey(TipoFusto, on_delete=models.PROTECT, null=True, blank=True, related_name="movimenti")
    quantita = models.DecimalField(max_digits=12, decimal_places=3)

    class Meta:
        ordering = ["pk"]
        verbose_name = "movimento del bar"
        verbose_name_plural = "movimenti del bar"
        constraints = [
            models.CheckConstraint(condition=~Q(quantita=0), name="movimento_bar_quantita_non_zero"),
            models.CheckConstraint(
                condition=Q(bevanda__isnull=False, tipo_fusto__isnull=True) | Q(bevanda__isnull=True, tipo_fusto__isnull=False),
                name="movimento_bar_una_cosa",
            ),
        ]

    @property
    def cosa(self):
        return self.bevanda or self.tipo_fusto


# ---------------------------------------------------------------------------
# Fusti: spine, consumati e pesate
# ---------------------------------------------------------------------------


class ContaFusti(models.Model):
    """I fusti di una serata: quelli attaccati, finiti e pesati in quella serata.

    Si crea da sola alla prima operazione sui fusti della serata.
    """

    servizio = models.OneToOneField(Servizio, on_delete=models.PROTECT, related_name="conta_fusti")
    utente = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    creata_il = models.DateTimeField(default=timezone.now)
    note = models.CharField(max_length=250, blank=True)
    token = models.UUIDField(null=True, blank=True, unique=True, editable=False)

    class Meta:
        ordering = ["-pk"]
        verbose_name = "fusti della serata"
        verbose_name_plural = "fusti delle serate"

    def __str__(self):
        return f"Fusti: {self.servizio}"


class Fusto(models.Model):
    """Un fusto attaccato alla spina (tolto dai pieni), poi finito (tra i consumati)."""

    tipo = models.ForeignKey(TipoFusto, on_delete=models.PROTECT, related_name="fusti")
    spina = models.ForeignKey(Spina, on_delete=models.PROTECT, null=True, blank=True, related_name="fusti")
    aperto_con = models.ForeignKey(ContaFusti, on_delete=models.CASCADE, related_name="fusti_aperti")
    attaccato_il = models.DateTimeField(default=timezone.now)
    # Rimettendolo alla spina (errore), il fusto torna attaccato.
    finito_con = models.ForeignKey(
        ContaFusti, on_delete=models.SET_NULL, null=True, blank=True, related_name="fusti_finiti"
    )
    finito_il = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["pk"]
        verbose_name = "fusto"
        verbose_name_plural = "fusti"
        constraints = [
            # Alla spinatrice c'è un fusto attaccato alla volta.
            models.UniqueConstraint(
                fields=["spina"], condition=Q(finito_con__isnull=True), name="un_fusto_attaccato_per_spina",
            ),
            models.CheckConstraint(
                condition=Q(finito_con__isnull=False) | Q(spina__isnull=False), name="fusto_attaccato_a_una_spina",
            ),
        ]

    def __str__(self):
        return f"{self.tipo} n. {self.pk}"


class Pesata(models.Model):
    """Peso di un fusto in una serata. Da finito è il peso da vuoto.

    Un fusto finito senza pesarlo da vuoto (misurata=False) vale la tara del
    suo tipo di fusto di adesso: se l'amministrazione la corregge, si
    correggono anche i conti delle serate già passate.
    """

    conta = models.ForeignKey(ContaFusti, on_delete=models.CASCADE, related_name="pesate")
    fusto = models.ForeignKey(Fusto, on_delete=models.CASCADE, related_name="pesate")
    peso = models.DecimalField("peso (kg)", max_digits=6, decimal_places=2, validators=[MinValueValidator(Decimal("0"))])
    vuoto = models.BooleanField(default=False)
    # Da vuoto: True se il peso è stato misurato, False se è la tara del tipo di fusto.
    misurata = models.BooleanField(default=True)
    registrata_il = models.DateTimeField(default=timezone.now)
    utente = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["pk"]
        verbose_name = "pesata"
        verbose_name_plural = "pesate"
        constraints = [
            models.UniqueConstraint(fields=["conta", "fusto"], name="pesata_unica_per_conta"),
        ]
