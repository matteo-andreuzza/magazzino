FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Europe/Rome

WORKDIR /app

RUN useradd --create-home --shell /usr/sbin/nologin app

# fonts-dejavu-core: il carattere delle comande sui fogli A4/A5 (cassa/fogli.py);
# postgresql-client: pg_dump per la copia dei dati da scaricare dall'app.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN chmod +x /app/entrypoint.sh \
    && DJANGO_SECRET_KEY=solo-per-collectstatic python manage.py collectstatic --noinput \
    && mkdir -p /app/dati \
    && chown -R app:app /app/dati

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/salute/', timeout=4)"

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60", "--access-logfile", "-"]

# Solo per docker-compose.debug.yml: aggiunge il debugger di VS Code.
FROM base AS debug
USER root
RUN pip install debugpy==1.8.22
USER app

# Immagine normale: essendo l'ultima è quella che si costruisce se non si indica altro.
FROM base AS produzione
