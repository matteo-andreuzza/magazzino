from django.urls import path

from . import views

app_name = "sistema"

urlpatterns = [
    path("", views.pannello, name="pannello"),
    path("stato.json", views.stato_json, name="stato_json"),
    path("errori/visti/", views.errori_visti, name="errori_visti"),
    path("errori/svuota/", views.errori_svuota, name="errori_svuota"),
    path("dispositivi/dimentica/", views.dispositivi_dimentica, name="dispositivi_dimentica"),
    path("stampanti/controlla/", views.stampanti_controlla, name="stampanti_controlla"),
    path("sincronia/adesso/", views.sincronia_adesso, name="sincronia_adesso"),
    path("sincronia/conflitti/<int:pk>/", views.conflitto_risolvi, name="conflitto_risolvi"),
    path("api/copia/", views.api_copia, name="api_copia"),
    path("api/copia/database/", views.api_copia_database, name="api_copia_database"),
    path("api/copia/allegato/", views.api_copia_allegato, name="api_copia_allegato"),
    path("api/sincronia/", views.api_sincronia, name="api_sincronia"),
    path("api/sincronia/allegato/", views.api_sincronia_allegato, name="api_sincronia_allegato"),
]
