from django.urls import path

from . import views

app_name = "bar"

urlpatterns = [
    path("", views.inizio, name="inizio"),
    path("carico/", views.carico, name="carico"),
    path("correggi/", views.correggi, name="correggi"),
    path("fusti/", views.fusti, name="fusti"),
    path("serate/<int:servizio_pk>/fusti/", views.conta_fusti, name="conta_fusti"),
    path("serate/<int:servizio_pk>/fusti/operazione/", views.fusti_azione, name="fusti_azione"),
    path("consumi/", views.consumi, name="consumi"),
    path("storico/", views.storico, name="storico"),
    path("storico/<int:pk>/", views.operazione, name="operazione"),
    path("storico/<int:pk>/annulla/", views.operazione_annulla, name="operazione_annulla"),
    path("bevande/", views.bevande, name="bevande"),
    path("bevande/nuova/", views.bevanda_nuova, name="bevanda_nuova"),
    path("bevande/<int:pk>/", views.bevanda_modifica, name="bevanda_modifica"),
    path("tipi-di-fusto/nuovo/", views.fusto_nuovo, name="fusto_nuovo"),
    path("tipi-di-fusto/<int:pk>/", views.fusto_modifica, name="fusto_modifica"),
    path("spinatrici/nuova/", views.spinatrice_nuova, name="spinatrice_nuova"),
    path("spinatrici/<int:pk>/", views.spinatrice_modifica, name="spinatrice_modifica"),
    path("spine/nuova/", views.spina_nuova, name="spina_nuova"),
    path("spine/<int:pk>/", views.spina_modifica, name="spina_modifica"),
]
