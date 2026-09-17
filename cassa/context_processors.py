from django.utils.functional import SimpleLazyObject


def cassa(request):
    # Letta solo dalle pagine che la usano (menu della cassa).
    def carica():
        from .services import impostazioni

        return impostazioni()

    return {"IMPOSTAZIONI_CASSA": SimpleLazyObject(carica)}
