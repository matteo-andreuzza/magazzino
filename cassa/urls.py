from django.urls import path

from . import views

app_name = "cassa"

urlpatterns = [
    path("", views.inizio, name="inizio"),
    # Presa degli ordini
    path("serate/<int:servizio_pk>/", views.cassa, name="cassa"),
    path("serate/<int:servizio_pk>/bar/", views.cassa_bar, name="cassa_bar"),
    path("serate/<int:servizio_pk>/ordina/", views.ordina, name="ordina"),
    path("serate/<int:servizio_pk>/stato/", views.stato, name="stato"),
    path("articoli/<int:pk>/esaurito/", views.articolo_esaurito, name="articolo_esaurito"),
    path("serate/<int:servizio_pk>/chiudi/", views.cassa_chiudi, name="chiudi"),
    path("serate/<int:servizio_pk>/riapri/", views.cassa_riapri, name="riapri"),
    # Ordini
    path("serate/<int:servizio_pk>/ordini/", views.ordini, name="ordini"),
    path("serate/<int:servizio_pk>/ordini.csv", views.ordini_csv, name="ordini_csv"),
    path("ordini/<int:pk>/", views.ordine, name="ordine"),
    path("ordini/<int:pk>/annulla/", views.ordine_annulla, name="ordine_annulla"),
    path("ordini/<int:pk>/ristampa/", views.ordine_ristampa, name="ordine_ristampa"),
    path("ordini/<int:pk>/biglietti/", views.ordine_biglietti, name="ordine_biglietti"),
    # Monitor cucina
    path("serate/<int:servizio_pk>/monitor/", views.monitor, name="monitor"),
    path("serate/<int:servizio_pk>/monitor/dati/", views.monitor_dati, name="monitor_dati"),
    path("comande/<int:pk>/<slug:azione>/", views.comanda_azione, name="comanda_azione"),
    # Report e statistiche
    path("serate/<int:servizio_pk>/report/", views.report_serata, name="report"),
    path("serate/<int:servizio_pk>/venduto.csv", views.report_csv, name="report_csv"),
    path("statistiche/", views.statistiche, name="statistiche"),
    path("statistiche/statistiche.csv", views.statistiche_csv, name="statistiche_csv"),
    # Stampa
    path("stampe/browser/", views.stampe_browser, name="stampe_browser"),
    path("stampe/browser/fatto/", views.stampe_browser_fatto, name="stampe_browser_fatto"),
    path("stampe/<int:pk>/riprova/", views.stampa_riprova, name="stampa_riprova"),
    path("api/agente/lavori/", views.api_agente_lavori, name="api_agente_lavori"),
    path("api/agente/esito/", views.api_agente_esito, name="api_agente_esito"),
    # Configurazione
    path("listino/", views.listino, name="listino"),
    path("listino/nuovo/", views.articolo_nuovo, name="articolo_nuovo"),
    path("listino/<int:pk>/", views.articolo_modifica, name="articolo_modifica"),
    path("impostazioni/", views.impostazioni_cassa, name="impostazioni"),
    path("stampanti/", views.stampanti, name="stampanti"),
    path("stampanti/nuova/", views.stampante_nuova, name="stampante_nuova"),
    path("stampanti/<int:pk>/", views.stampante_modifica, name="stampante_modifica"),
    path("stampanti/<int:pk>/prova/", views.stampante_prova, name="stampante_prova"),
    path("stampanti/agente_stampa.py", views.agente_scarica, name="agente_scarica"),
    path("stampanti/nuova-chiave/", views.token_rigenera, name="token_rigenera"),
]
