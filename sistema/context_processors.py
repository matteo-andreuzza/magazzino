from datetime import datetime, timedelta

from django.conf import settings
from django.db import DatabaseError
from django.utils import timezone

from .models import Battito

SILENZIO = timedelta(minutes=5)
RECENTE = timedelta(hours=24)


def sistema(request):
    """La striscia della sincronia in cima alle pagine, solo quando c'è qualcosa da sapere.

    - Sulla sagra: finché non riesce il primo giro con casa dopo l'accensione, le modifiche fatte da
      casa nel frattempo non sono ancora qui.
    - A casa: la sagra si è fatta sentire nelle ultime 24 ore ma non negli ultimi minuti (spenta o
      senza internet): quello che fanno lì arriva quando torna.
    """
    if not settings.COPIA_CHIAVE or not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    try:
        battito = Battito.objects.filter(nome="sincronia").first()
    except DatabaseError:
        return {}
    if battito is None:
        return {}
    adesso = timezone.now()
    if settings.SINCRONIA_CON:
        if battito.dettagli.get("primo_ok", True):
            return {}
        return {"STRISCIA_SINCRONIA": "Non ho ancora ricevuto le modifiche fatte da casa (manca internet?): "
                                      "quello che è stato registrato da casa dopo l'ultima volta arriva appena c'è collegamento."}
    contatto = battito.dettagli.get("ultimo_contatto")
    if not contatto:
        return {}
    quando = datetime.fromisoformat(contatto)
    if adesso - quando < SILENZIO or adesso - quando > RECENTE:
        return {}
    return {"STRISCIA_SINCRONIA": f"La sagra non si sente dalle {timezone.localtime(quando):%H:%M}: quello che "
                                  "registrano lì arriva qui appena torna internet."}
