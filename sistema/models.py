"""Dati di funzionamento del server: chi è acceso, quali errori ci sono stati, chi è collegato,
cosa deve ancora partire verso l'altro server (sincronia).

Sono dati di QUESTO server: non vanno nelle copie né nella sincronia (vedi copia.py,
TABELLE_LOCALI). Per questo nessuna tabella qui ha chiavi esterne verso le altre app:
l'utente si salva come nome, così una copia caricata non le tocca.
"""

from datetime import timedelta

from django.db import models
from django.utils import timezone


class Battito(models.Model):
    """Ultimo segnale di vita di un servizio del programma (es. «stampa», «copia»)."""

    nome = models.CharField(max_length=40, unique=True)
    avviato_il = models.DateTimeField(null=True, blank=True)
    ultimo_il = models.DateTimeField()
    stato = models.CharField(max_length=10, default="ok")  # ok, avviso, errore, spento
    messaggio = models.CharField(max_length=300, blank=True)
    dettagli = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["nome"]
        verbose_name = "segnale di un servizio"
        verbose_name_plural = "segnali dei servizi"

    def __str__(self):
        return self.nome

    def vivo(self, entro=timedelta(seconds=30)):
        return timezone.now() - self.ultimo_il < entro


class Errore(models.Model):
    """Eccezione non prevista (pagina andata in errore, servizio inciampato). Quelle uguali si contano."""

    firma = models.CharField(max_length=40, unique=True)  # stessa origine, tipo e punto del codice
    origine = models.CharField(max_length=20)  # web, stampa, copia, comando
    tipo = models.CharField(max_length=120)
    messaggio = models.TextField(blank=True)
    dove = models.CharField(max_length=300, blank=True)  # pagina o funzione
    utente = models.CharField(max_length=150, blank=True)
    traccia = models.TextField(blank=True)
    volte = models.PositiveIntegerField(default=1)
    primo_il = models.DateTimeField(default=timezone.now)
    ultimo_il = models.DateTimeField(default=timezone.now, db_index=True)
    visto = models.BooleanField(default=False)

    class Meta:
        ordering = ["-ultimo_il"]
        verbose_name = "errore"
        verbose_name_plural = "errori"

    def __str__(self):
        return f"{self.tipo}: {self.messaggio[:60]}"


class Dispositivo(models.Model):
    """Un browser collegato all'app (telefono, tablet, computer della cassa) o un client di Claude."""

    chiave = models.CharField(max_length=64, unique=True)
    tipo = models.CharField(max_length=10, default="computer")  # telefono, tablet, computer, claude
    ruolo = models.CharField(max_length=40, blank=True)  # es. «Cassa ingresso», «Monitor cucina»
    utente = models.CharField(max_length=150, blank=True)
    indirizzo_ip = models.GenericIPAddressField(null=True, blank=True)
    browser = models.CharField(max_length=80, blank=True)
    ultima_pagina = models.CharField(max_length=200, blank=True)
    primo_il = models.DateTimeField(default=timezone.now)
    ultimo_il = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-ultimo_il"]
        verbose_name = "dispositivo collegato"
        verbose_name_plural = "dispositivi collegati"

    def __str__(self):
        return f"{self.ruolo or self.tipo} ({self.utente or self.indirizzo_ip})"


class Modifica(models.Model):
    """Una riga cambiata su questo server che l'altro non ha ancora confermato: la coda della sincronia.

    La scrive un trigger di PostgreSQL (vedi sincronia.py), una voce sola per riga: se la riga
    cambia di nuovo, `numero` sale. L'altro server conferma un numero preciso, così una riga
    cambiata mentre viaggiava resta in coda e riparte.
    """

    tabella = models.CharField(max_length=100)
    chiave = models.CharField(max_length=100)
    numero = models.BigIntegerField()
    quando = models.DateTimeField()

    class Meta:
        ordering = ["numero"]
        verbose_name = "modifica da mandare"
        verbose_name_plural = "modifiche da mandare"
        constraints = [models.UniqueConstraint(fields=["tabella", "chiave"], name="modifica_unica_per_riga")]

    def __str__(self):
        return f"{self.tabella} {self.chiave}"


class Conflitto(models.Model):
    """Una riga di qui (ancora in coda) che l'altro server non riesce ad applicare.

    Resta in coda e si rimanda a ogni giro: quando passa, o quando esce dalla coda, il conflitto
    sparisce. `gemella`: se il motivo è un nome (o un altro valore unico) già usato là, la chiave
    della riga dell'altro server con quel nome; `dati_gemella` è quella riga, per poter unire le due.
    """

    tabella = models.CharField(max_length=100)
    chiave = models.CharField(max_length=100)
    motivo = models.TextField()
    gemella = models.CharField(max_length=100, blank=True)
    dati_gemella = models.TextField(blank=True)  # JSON così com'è arrivato
    primo_il = models.DateTimeField(default=timezone.now)
    ultimo_il = models.DateTimeField(default=timezone.now)
    volte = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["primo_il"]
        verbose_name = "conflitto della sincronia"
        verbose_name_plural = "conflitti della sincronia"
        constraints = [models.UniqueConstraint(fields=["tabella", "chiave"], name="conflitto_unico_per_riga")]

    def __str__(self):
        return f"{self.tabella} {self.chiave}: {self.motivo[:60]}"
