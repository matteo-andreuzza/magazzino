"""Dati di funzionamento del server: chi è acceso, quali errori ci sono stati, chi è collegato.

Sono dati di QUESTO server: non vanno nelle copie verso altri server (vedi copia.py,
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
