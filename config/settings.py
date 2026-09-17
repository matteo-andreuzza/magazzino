"""Impostazioni Django del magazzino della sagra.

Tutto è configurabile tramite variabili d'ambiente (vedi .env.example).
"""

import os
import secrets
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(nome, predefinito=False):
    valore = os.environ.get(nome)
    if valore is None or valore.strip() == "":
        return predefinito
    return valore.strip().lower() in {"1", "true", "yes", "si", "sì", "on"}


def env_lista(nome, predefinito=""):
    return [v.strip() for v in os.environ.get(nome, predefinito).split(",") if v.strip()]


DEBUG = env_bool("DJANGO_DEBUG", False)


def chiave_segreta(percorso):
    """Legge la chiave dal file; al primo avvio la genera e la salva (nel volume Docker)."""
    try:
        chiave = percorso.read_text().strip()
    except FileNotFoundError:
        chiave = ""
    if chiave:
        return chiave
    percorso.parent.mkdir(parents=True, exist_ok=True)
    chiave = secrets.token_urlsafe(50)
    try:
        fd = os.open(percorso, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        chiave = percorso.read_text().strip()
        if not chiave:
            raise ImproperlyConfigured(f"Il file {percorso} è vuoto: cancellalo e riavvia.")
        return chiave
    with os.fdopen(fd, "w") as f:
        f.write(chiave)
    return chiave


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "").strip() or chiave_segreta(BASE_DIR / "dati" / "secret_key")

# In rete locale si accede spesso tramite indirizzo IP: di default accetta tutto.
ALLOWED_HOSTS = env_lista("DJANGO_ALLOWED_HOSTS", "*")
CSRF_TRUSTED_ORIGINS = env_lista("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "magazzino",
    "cassa",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "magazzino.context_processors.app",
                "cassa.context_processors.cassa",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

if os.environ.get("POSTGRES_HOST"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": os.environ["POSTGRES_HOST"],
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
            "NAME": os.environ.get("POSTGRES_DB", "magazzino"),
            "USER": os.environ.get("POSTGRES_USER", "magazzino"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
            "CONN_MAX_AGE": 60,
            "CONN_HEALTH_CHECKS": True,
        }
    }
else:
    # Solo per sviluppo/test senza Docker.
    (BASE_DIR / "dati").mkdir(exist_ok=True)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "dati" / "db.sqlite3",
            "OPTIONS": {"transaction_mode": "IMMEDIATE", "timeout": 20},
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 6}},
]

# Sempre in italiano, qualunque sia la lingua impostata sul telefono.
LANGUAGE_CODE = "it"
TIME_ZONE = "Europe/Rome"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# Fatture e bolle allegate ai carichi. Stanno nel volume dei dati e non sono
# mai servite direttamente: si scaricano solo dall'app, dopo il login.
MEDIA_ROOT = BASE_DIR / "dati" / "allegati"
ALLEGATI_MAX_MB = 20
ALLEGATI_MAX_PER_INVIO = 10

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

# I telefoni dei volontari restano collegati per tutta la sagra.
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30
SESSION_COOKIE_SECURE = env_bool("DJANGO_COOKIE_SICURI", False)
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
# In cloud l'app sta dietro un proxy HTTPS (vedi docker-compose.cloud.yml).
if env_bool("DJANGO_DIETRO_PROXY_HTTPS"):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

APP_NOME = os.environ.get("APP_NOME", "Magazzino Sagra")

LIVELLO_LOG = os.environ.get("DJANGO_LOG_LEVEL", "DEBUG" if DEBUG else "INFO").upper()

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": LIVELLO_LOG},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        # Con DJANGO_LOG_SQL=1 (e DEBUG attivo) stampa ogni query al database.
        "django.db.backends": {
            "handlers": ["console"],
            "level": "DEBUG" if env_bool("DJANGO_LOG_SQL") else "INFO",
            "propagate": False,
        },
    },
}
