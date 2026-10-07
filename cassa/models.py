"""Cassa della sagra: listino, ordini, comande e stampe.

Gira sullo stesso database del magazzino: gli ordini appartengono alle stesse
serate (Servizio), gli articoli del listino possono richiamare i piatti del
menu e le bevande del magazzino del bar (che si scaricano da sole vendendole).
"""

import secrets
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import formats, timezone

from bar.models import Bevanda
from magazzino.models import Piatto, Servizio


class Reparto(models.TextChoices):
    CUCINA = "cucina", "Cucina"
    BAR = "bar", "Bar"


class Colore(models.TextChoices):
    ROSSO = "rosso", "Rosso"
    ARANCIO = "arancio", "Arancione"
    GIALLO = "giallo", "Giallo"
    VERDE = "verde", "Verde"
    AZZURRO = "azzurro", "Azzurro"
    BLU = "blu", "Blu"
    VIOLA = "viola", "Viola"
    MARRONE = "marrone", "Marrone"
    GRIGIO = "grigio", "Grigio"


class Categoria(models.Model):
    """Gruppo di articoli in cassa (primi, secondi, bevande…)."""

    nome = models.CharField(max_length=40)
    colore = models.CharField(max_length=10, choices=Colore.choices, default=Colore.GRIGIO)
    ordine = models.PositiveIntegerField(default=0, help_text="Ordine in cassa.")

    class Meta:
        ordering = ["ordine", "nome"]
        verbose_name = "categoria del listino"
        verbose_name_plural = "categorie del listino"
        constraints = [
            models.UniqueConstraint(
                Lower("nome"), name="categoria_cassa_nome_unico",
                violation_error_message="Esiste già una categoria con questo nome.",
            ),
        ]

    def __str__(self):
        return self.nome


class Articolo(models.Model):
    """Voce del listino: quello che si vende in cassa."""

    nome = models.CharField(max_length=60, help_text="Così compare in cassa e sulle comande.")
    categoria = models.ForeignKey(Categoria, on_delete=models.PROTECT, related_name="articoli")
    reparto = models.CharField(
        max_length=10, choices=Reparto.choices, default=Reparto.CUCINA,
        help_text="Dove si prepara: decide su quale comanda viene stampato.",
    )
    prezzo = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal("0"))],
        help_text="In euro. Finché è vuoto l'articolo non compare in cassa.",
    )
    piatto = models.ForeignKey(
        Piatto, on_delete=models.SET_NULL, null=True, blank=True, related_name="articoli_cassa",
        verbose_name="piatto del menu",
        help_text="Facoltativo. Se la serata ha i suoi piatti, in cassa compare solo quando il piatto è servito; "
        "nelle statistiche serve a calcolare il consumo degli ingredienti per porzione.",
    )
    bevanda = models.ForeignKey(
        Bevanda, on_delete=models.SET_NULL, null=True, blank=True, related_name="articoli",
        verbose_name="bevanda del magazzino bar",
        help_text="Lascia vuoto per il cibo e per ciò che non si conta (caffè, liquori…).",
    )
    quantita_bevanda = models.DecimalField(
        "quantità per pezzo venduto", max_digits=8, decimal_places=3, default=Decimal("1"),
        validators=[MinValueValidator(Decimal("0.001"))],
        help_text="Es. 1 lattina; per birra e vino i litri: 0,33 il bicchiere, 1,5 la caraffa grande.",
    )
    ordine = models.PositiveIntegerField(default=0, help_text="Ordine dentro la categoria.")
    attivo = models.BooleanField(default=True, help_text="Gli articoli non attivi non compaiono in cassa.")
    esaurito = models.BooleanField(default=False, help_text="Si vede in cassa ma non si può vendere.")
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["categoria__ordine", "categoria__nome", "ordine", "nome"]
        verbose_name = "articolo"
        verbose_name_plural = "articoli (listino)"
        constraints = [
            models.UniqueConstraint(
                Lower("nome"), name="articolo_nome_unico",
                violation_error_message="Esiste già un articolo con questo nome.",
            ),
        ]

    def __str__(self):
        return self.nome

    @property
    def in_vendita(self):
        return self.attivo and self.prezzo is not None


class Postazione(models.TextChoices):
    INGRESSO = "ingresso", "Cassa all'ingresso"
    BAR = "bar", "Cassa del bar"


class Pagamento(models.TextChoices):
    CONTANTI = "contanti", "Contanti"
    CARTA = "carta", "Carta / bancomat"
    OMAGGIO = "omaggio", "Omaggio / volontari"


PAGAMENTI_INCASSATI = [Pagamento.CONTANTI, Pagamento.CARTA]


class Ordine(models.Model):
    servizio = models.ForeignKey(Servizio, on_delete=models.PROTECT, related_name="ordini")
    numero = models.PositiveIntegerField(help_text="Progressivo della serata.")
    postazione = models.CharField(max_length=10, choices=Postazione.choices, default=Postazione.INGRESSO)
    nome = models.CharField(max_length=40, blank=True)
    tavolo = models.CharField(max_length=10, blank=True)
    # Da portare via: niente tavolo (si scrive «ASP») né coperti, e la comanda lo dice in grande.
    asporto = models.BooleanField(default=False)
    coperti = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MaxValueValidator(500)])
    note = models.CharField(max_length=200, blank=True)
    pagamento = models.CharField(max_length=10, choices=Pagamento.choices)
    totale = models.DecimalField(max_digits=9, decimal_places=2)
    creato_il = models.DateTimeField(default=timezone.now, db_index=True)
    utente = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    annullato_il = models.DateTimeField(null=True, blank=True)
    annullato_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    motivo_annullamento = models.CharField(max_length=200, blank=True)
    # Impedisce di registrare due volte lo stesso ordine (doppio tocco, rete che cade).
    token = models.UUIDField(null=True, blank=True, unique=True, editable=False)

    class Meta:
        ordering = ["-creato_il", "-pk"]
        verbose_name = "ordine"
        verbose_name_plural = "ordini"
        constraints = [
            models.UniqueConstraint(fields=["servizio", "numero"], name="ordine_numero_unico_per_servizio"),
            models.CheckConstraint(condition=Q(totale__gte=0), name="ordine_totale_non_negativo"),
        ]

    def __str__(self):
        return f"Ordine n. {self.numero} ({self.servizio})"

    @property
    def annullato(self):
        return self.annullato_il is not None

    @property
    def incassato(self):
        return self.pagamento in PAGAMENTI_INCASSATI and not self.annullato

    @property
    def ora(self):
        return formats.time_format(timezone.localtime(self.creato_il), "H:i")


class RigaOrdine(models.Model):
    ordine = models.ForeignKey(Ordine, on_delete=models.CASCADE, related_name="righe")
    # Gli articoli già venduti non si cancellano (si disattivano): servono alle statistiche.
    articolo = models.ForeignKey(Articolo, on_delete=models.PROTECT, related_name="righe")
    # Nome, reparto e prezzo sono copiati al momento della vendita.
    descrizione = models.CharField(max_length=60)
    reparto = models.CharField(max_length=10, choices=Reparto.choices)
    prezzo = models.DecimalField("prezzo unitario", max_digits=7, decimal_places=2)
    quantita = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(999)])
    note = models.CharField(max_length=60, blank=True)
    # Anche la bevanda del bar e quanta ne esce per pezzo sono copiate: il venduto
    # del magazzino bar non cambia se poi si modifica l'articolo.
    bevanda = models.ForeignKey(Bevanda, on_delete=models.PROTECT, null=True, blank=True, related_name="righe_vendute")
    quantita_bevanda = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)

    class Meta:
        ordering = ["pk"]
        verbose_name = "riga dell'ordine"
        verbose_name_plural = "righe dell'ordine"
        constraints = [
            models.CheckConstraint(condition=Q(quantita__gt=0), name="riga_ordine_quantita_positiva"),
        ]

    def __str__(self):
        return f"{self.quantita} × {self.descrizione}"

    @property
    def importo(self):
        return self.prezzo * self.quantita


class StatoComanda(models.TextChoices):
    NUOVA = "nuova", "Da fare"
    IN_PREPARAZIONE = "preparazione", "In preparazione"
    PRONTA = "pronta", "Pronta"
    RITIRATA = "ritirata", "Ritirata"


class Comanda(models.Model):
    """La parte di un ordine che riguarda un reparto (cucina o bar)."""

    ordine = models.ForeignKey(Ordine, on_delete=models.CASCADE, related_name="comande")
    reparto = models.CharField(max_length=10, choices=Reparto.choices)
    stato = models.CharField(max_length=15, choices=StatoComanda.choices, default=StatoComanda.NUOVA)
    iniziata_il = models.DateTimeField(null=True, blank=True)
    pronta_il = models.DateTimeField(null=True, blank=True)
    ritirata_il = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["ordine__creato_il", "pk"]
        verbose_name = "comanda"
        verbose_name_plural = "comande"
        constraints = [
            models.UniqueConstraint(fields=["ordine", "reparto"], name="comanda_unica_per_reparto"),
        ]
        indexes = [models.Index(fields=["reparto", "stato"])]

    def __str__(self):
        return f"Comanda {self.get_reparto_display().lower()} dell'ordine n. {self.ordine.numero}"


class Connessione(models.TextChoices):
    IPP = "ipp", "Stampante normale di rete (laser o a getto d'inchiostro)"
    RETE = "rete", "Stampante termica di rete (rotolo per scontrini)"
    COMPUTER = "computer", "Collegata con il cavo a un altro computer"
    BROWSER = "browser", "Dal browser della cassa"


# Le stampanti di rete le serve il server stesso (servizio «stampa», vedi servizio_stampa.py);
# quelle termiche di rete e quelle collegate a un computer anche il programma di stampa (agente).
CONNESSIONI_SERVER = [Connessione.IPP, Connessione.RETE]
CONNESSIONI_AGENTE = [Connessione.RETE, Connessione.COMPUTER]


class Carta(models.TextChoices):
    A4 = "A4", "A4"
    A5 = "A5", "A5 (mezzo A4)"


class Stampante(models.Model):
    nome = models.CharField(max_length=40)
    connessione = models.CharField(max_length=10, choices=Connessione.choices, default=Connessione.IPP)
    indirizzo = models.CharField(
        max_length=200, blank=True,
        help_text="Stampante di rete: l'indirizzo IP, es. 192.168.1.50 (si legge sul display o sul foglio "
        "di configurazione della stampante). "
        "Collegata a un computer: /dev/usb/lp0 (Linux), \\\\localhost\\NomeCondivisione (Windows) "
        "oppure cups:NomeCoda.",
    )
    agente = models.CharField(
        "computer", max_length=60, blank=True,
        help_text="Solo per le stampanti collegate a un computer: il nome con cui gira lì il programma di stampa. "
        "Vuoto = qualsiasi.",
    )
    carta = models.CharField(
        max_length=2, choices=Carta.choices, default=Carta.A4,
        help_text="Solo per le stampanti normali: il formato dei fogli nel cassetto.",
    )
    colonne = models.PositiveSmallIntegerField(
        "caratteri per riga",
        choices=[
            (32, "32 · testo molto grande (rotolo da 58 mm)"),
            (42, "42 · testo grande (rotolo da 80 mm)"),
            (48, "48 · testo più piccolo"),
        ],
        default=42, help_text="Sui fogli A4 e A5 il testo si allarga a tutta la pagina: meno caratteri, testo più grande.",
    )
    accenti = models.BooleanField(
        "lettere accentate", default=True,
        help_text="Se sulla stampa di prova le lettere accentate escono sbagliate, toglilo.",
    )
    attiva = models.BooleanField(default=True)

    class Meta:
        ordering = ["nome"]
        verbose_name = "stampante"
        verbose_name_plural = "stampanti"
        constraints = [
            models.UniqueConstraint(
                Lower("nome"), name="stampante_nome_unico",
                violation_error_message="Esiste già una stampante con questo nome.",
            ),
        ]

    def __str__(self):
        return self.nome

    def clean(self):
        self.indirizzo = self.indirizzo.strip()
        if self.connessione != Connessione.BROWSER and not self.indirizzo:
            raise ValidationError({"indirizzo": "Indica dove si trova la stampante."})
        if self.connessione == Connessione.IPP:
            from .ipp import ErroreStampante, leggi_indirizzo

            try:
                leggi_indirizzo(self.indirizzo)
            except ErroreStampante:
                raise ValidationError(
                    {"indirizzo": "Scrivi l'indirizzo IP, es. 192.168.1.50, oppure quello completo, es. ipp://192.168.1.50/ipp/print."}
                ) from None
        if self.connessione == Connessione.RETE:
            host, _, porta = self.indirizzo.rpartition(":") if ":" in self.indirizzo else (self.indirizzo, "", "")
            if not host or " " in host or (porta and not (porta.isdigit() and 0 < int(porta) < 65536)):
                raise ValidationError({"indirizzo": "Scrivi l'indirizzo IP, es. 192.168.1.50 oppure 192.168.1.50:9100."})

    @property
    def controllo(self):
        """L'ultimo controllo del servizio di stampa (ogni minuto), o None. Sta tra i dati
        di funzionamento di questo server (sistema.Battito), non nella stampante: così non
        viaggia nelle copie verso altri server."""
        from sistema.models import Battito

        return Battito.objects.filter(nome=f"stampante-{self.pk}").first()

    @property
    def dal_browser(self):
        return self.connessione == Connessione.BROWSER


class Documento(models.TextChoices):
    COMANDA = "comanda", "Comanda"
    RICEVUTA = "ricevuta", "Ricevuta"
    ANNULLO = "annullo", "Ordine annullato"
    PROVA = "prova", "Stampa di prova"


class StatoStampa(models.TextChoices):
    IN_CODA = "coda", "In coda"
    IN_STAMPA = "stampa", "In stampa"
    STAMPATA = "stampata", "Stampata"
    ERRORE = "errore", "Errore"
    ANNULLATA = "annullata", "Non più necessaria"


class Stampa(models.Model):
    """Un biglietto da stampare. Il contenuto è salvato già pronto (vedi stampa.py)."""

    stampante = models.ForeignKey(Stampante, on_delete=models.CASCADE, related_name="stampe")
    ordine = models.ForeignKey(Ordine, on_delete=models.CASCADE, null=True, blank=True, related_name="stampe")
    documento = models.CharField(max_length=10, choices=Documento.choices)
    reparto = models.CharField(max_length=10, choices=Reparto.choices, blank=True)
    copia = models.BooleanField(default=False, help_text="Ristampa.")
    contenuto = models.JSONField()
    stato = models.CharField(max_length=10, choices=StatoStampa.choices, default=StatoStampa.IN_CODA)
    tentativi = models.PositiveSmallIntegerField(default=0)
    errore = models.CharField(max_length=300, blank=True)
    creata_il = models.DateTimeField(default=timezone.now, db_index=True)
    presa_il = models.DateTimeField(null=True, blank=True)
    agente = models.CharField(max_length=60, blank=True)
    stampata_il = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["creata_il", "pk"]
        verbose_name = "stampa"
        verbose_name_plural = "stampe"
        indexes = [models.Index(fields=["stato", "creata_il"])]

    def __str__(self):
        cosa = self.get_documento_display()
        if self.reparto:
            cosa += f" {self.get_reparto_display().lower()}"
        return f"{cosa} su {self.stampante}"

    @property
    def descrizione(self):
        if self.documento == Documento.COMANDA:
            testo = f"Comanda {self.get_reparto_display().lower()}"
        elif self.documento == Documento.ANNULLO:
            testo = f"Annullamento {self.get_reparto_display().lower()}" if self.reparto else "Annullamento"
        else:
            testo = self.get_documento_display()
        return testo + (" (copia)" if self.copia else "")


class AgenteStampa(models.Model):
    """Computer della sagra su cui gira il programma di stampa."""

    nome = models.CharField(max_length=60, unique=True)
    ultimo_contatto = models.DateTimeField()
    indirizzo_ip = models.GenericIPAddressField(null=True, blank=True)
    versione = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ["-ultimo_contatto"]
        verbose_name = "programma di stampa"
        verbose_name_plural = "programmi di stampa"

    def __str__(self):
        return self.nome

    @property
    def collegato(self):
        return timezone.now() - self.ultimo_contatto < SECONDI_AGENTE_COLLEGATO


SECONDI_AGENTE_COLLEGATO = timedelta(seconds=30)


def genera_token():
    return secrets.token_urlsafe(32)


class ImpostazioniCassa(models.Model):
    """Impostazioni della cassa (una sola riga)."""

    # Funzioni smart: spente finché non servono.
    monitor_cucina = models.BooleanField(
        default=False,
        help_text="Uno schermo in cucina mostra le comande da accettare, in preparazione e pronte. "
        "Le comande di carta continuano a uscire.",
    )
    monitor_cucina_dal = models.DateTimeField(null=True, blank=True, editable=False)
    cassa_bar = models.BooleanField(
        "cassa al bar", default=False,
        help_text="Al bar si può pagare direttamente, solo per gli articoli del bar, senza passare dalla cassa all'ingresso.",
    )

    # Stampa
    intestazione = models.CharField(
        max_length=40, blank=True, help_text="In cima a comande e ricevute. Vuoto = nome dell'app."
    )
    stampante_cucina = models.ForeignKey(
        Stampante, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="Dove escono le comande della cucina.",
    )
    stampante_bar = models.ForeignKey(
        Stampante, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        verbose_name="stampante comanda bar",
        help_text="Dove esce la comanda delle bevande degli ordini fatti all'ingresso: su una stampante al bar, "
        "oppure su quella della cassa (la stessa delle ricevute): allora il cliente la porta al bar e sopra "
        "c'è scritto «Da presentare al bar».",
    )
    ricevuta_cliente = models.BooleanField(
        "ricevuta per il cliente", default=False,
        help_text="Stampa anche una ricevuta (non fiscale) con prezzi e totale.",
    )
    stampante_ricevute = models.ForeignKey(
        Stampante, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        verbose_name="stampante ricevute", help_text="Di solito quella accanto alla cassa all'ingresso.",
    )
    messaggio_ricevuta = models.CharField(max_length=100, blank=True, default="Grazie e buon appetito!")
    stampante_cassa_bar = models.ForeignKey(
        Stampante, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        verbose_name="stampante della cassa al bar",
        help_text="Per ricevute e comande della cassa al bar. Vuota = la cassa al bar non stampa niente.",
    )
    stampa_comande_cassa_bar = models.BooleanField(
        "stampa le comande della cassa al bar", default=False,
        help_text="Di solito non serve: chi paga al bar viene servito subito.",
    )
    token_agente = models.CharField(max_length=64, default=genera_token, editable=False)
    modificate_il = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "impostazioni della cassa"
        verbose_name_plural = "impostazioni della cassa"

    def __str__(self):
        return "Impostazioni della cassa"
