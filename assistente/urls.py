from django.urls import path

from . import views

app_name = "assistente"

urlpatterns = [
    path("", views.chiavi, name="chiavi"),
    path("<int:pk>/revoca/", views.chiave_revoca, name="chiave_revoca"),
]
