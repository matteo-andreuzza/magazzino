from django.conf import settings


def app(request):
    return {"APP_NOME": settings.APP_NOME}
