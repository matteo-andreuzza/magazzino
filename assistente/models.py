"""Chiavi con cui Claude (o un altro programma) si collega al server MCP.

Della chiave si salva solo l'impronta (SHA-256): quella vera si vede una
volta sola, quando la si crea. Ogni chiave agisce come l'utente che l'ha
creata e, se non ha il permesso di scrittura, può solo leggere.
"""

import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

PREFISSO = "sagra_"


def impronta(chiave):
    return hashlib.sha256(chiave.encode()).hexdigest()


class ChiaveMCP(models.Model):
    nome = models.CharField(max_length=60, help_text="Per riconoscerla, es. «Claude Code sul portatile di Matteo».")
    utente = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chiavi_mcp")
    impronta = models.CharField(max_length=64, unique=True, editable=False)
    inizio = models.CharField("inizio della chiave", max_length=16, editable=False)
    scrittura = models.BooleanField(
        "può registrare", default=False,
        help_text="Senza, Claude può solo leggere. Con, può anche registrare carichi e creare prodotti.",
    )
    creata_il = models.DateTimeField(default=timezone.now)
    ultimo_uso = models.DateTimeField(null=True, blank=True)
    revocata_il = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-creata_il"]
        verbose_name = "chiave MCP"
        verbose_name_plural = "chiavi MCP"

    def __str__(self):
        return f"{self.nome} ({self.inizio}…)"

    @property
    def attiva(self):
        return self.revocata_il is None

    @classmethod
    def crea(cls, *, nome, utente, scrittura=False):
        """Restituisce (chiave salvata, testo della chiave da mostrare una volta sola)."""
        testo = PREFISSO + secrets.token_urlsafe(32)
        chiave = cls.objects.create(
            nome=nome, utente=utente, scrittura=scrittura, impronta=impronta(testo), inizio=testo[: len(PREFISSO) + 4],
        )
        return chiave, testo

    @classmethod
    def trova(cls, testo):
        """La chiave attiva corrispondente al testo, o None."""
        if not testo or not testo.startswith(PREFISSO):
            return None
        return cls.objects.select_related("utente").filter(impronta=impronta(testo), revocata_il__isnull=True).first()
