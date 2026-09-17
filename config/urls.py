from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Amministrazione magazzino"
admin.site.site_title = "Magazzino"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("", include("magazzino.urls")),
]
