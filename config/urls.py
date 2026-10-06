from django.contrib import admin
from django.urls import include, path

from assistente import views as assistente

admin.site.site_header = "Amministrazione magazzino"
admin.site.site_title = "Magazzino"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("bar/", include("bar.urls")),
    path("cassa/", include("cassa.urls")),
    # Server MCP per Claude: vedi assistente/.
    path("mcp/", assistente.mcp, name="mcp"),
    path("claude/", include("assistente.urls")),
    path(".well-known/<path:resto>", assistente.non_trovato),
    path("", include("magazzino.urls")),
]
