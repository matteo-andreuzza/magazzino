from datetime import datetime

from django.conf import settings
from django.db import DatabaseError
from django.utils import timezone

from .models import Battito


def sistema(request):
    """Sulla copia remota: la striscia «copia di sola lettura, dati delle …» in cima a ogni pagina."""
    if not settings.COPIA_DI:
        return {}
    quando = None
    try:
        dettagli = Battito.objects.filter(nome="copia").values_list("dettagli", flat=True).first() or {}
        if dettagli.get("copia_il"):
            quando = timezone.localtime(datetime.fromisoformat(dettagli["copia_il"]))
    except DatabaseError:
        pass
    return {"COPIA_SOLA_LETTURA": True, "COPIA_AGGIORNATA_IL": quando}
