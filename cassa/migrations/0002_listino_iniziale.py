"""Listino iniziale ricavato dal menu della sagra (magazzino, dati iniziali).

Gli articoli nascono SENZA prezzo: finché il prezzo non viene inserito dal
listino non compaiono in cassa. Le bevande non sono negli appunti: vanno
aggiunte dal listino (reparto Bar). Viene eseguita una sola volta.
"""

from django.db import migrations

CATEGORIE = [
    # (nome, colore)
    ("Primi", "giallo"),
    ("Secondi", "rosso"),
    ("Contorni", "verde"),
    ("Panini e piadine", "arancio"),
    ("Dolci", "viola"),
    ("Bevande", "azzurro"),
]

ARTICOLI = [
    # (nome, categoria, menu del piatto, piatto)
    ("Gnocchi al pomodoro", "Primi", "Menu della sagra", "Gnocchi o pennette"),
    ("Gnocchi al ragù d'anatra", "Primi", "Menu della sagra", "Gnocchi o pennette"),
    ("Pennette al pomodoro", "Primi", "Menu della sagra", "Gnocchi o pennette"),
    ("Pennette al ragù d'anatra", "Primi", "Menu della sagra", "Gnocchi o pennette"),
    ("Paella", "Primi", "Solo una sera", "Paella"),
    ("Grigliata mista", "Secondi", "Menu della sagra", "Grigliata mista"),
    ("Solo pollo", "Secondi", "Menu della sagra", "Solo pollo"),
    ("Solo salamelle", "Secondi", "Menu della sagra", "Solo salamelle"),
    ("Solo costicine", "Secondi", "Menu della sagra", "Solo costicine"),
    ("Frittura", "Secondi", "Menu della sagra", "Frittura"),
    ("Primo sale con polenta", "Secondi", "Menu della sagra", "Primo sale con polenta"),
    ("Baccalà alla vicentina", "Secondi", "Menu della sagra", "Baccalà alla vicentina"),
    ("Tagliata", "Secondi", "Solo una sera", "Tagliata"),
    ("Seppie in umido", "Secondi", "Solo una sera", "Seppie in umido"),
    ("Patatine fritte", "Contorni", "Menu della sagra", "Patatine fritte"),
    ("Fagioli con sedano", "Contorni", "Menu della sagra", "Fagioli"),
    ("Fagioli con cipolla", "Contorni", "Menu della sagra", "Fagioli"),
    ("Funghi", "Contorni", "Menu della sagra", "Funghi"),
    ("Piadina alle verdure", "Panini e piadine", "Menu della sagra", "Piadina alle verdure"),
    ("Panino onto", "Panini e piadine", "Solo una sera", "Panino onto"),
]


def carica(apps, schema_editor):
    Categoria = apps.get_model("cassa", "Categoria")
    Articolo = apps.get_model("cassa", "Articolo")
    Piatto = apps.get_model("magazzino", "Piatto")
    if Categoria.objects.exists() or Articolo.objects.exists():
        return
    categorie = {}
    for ordine, (nome, colore) in enumerate(CATEGORIE, start=1):
        categorie[nome] = Categoria.objects.create(nome=nome, colore=colore, ordine=ordine)
    for ordine, (nome, categoria, menu, piatto) in enumerate(ARTICOLI, start=1):
        # Se nel frattempo il menu è stato cambiato, l'articolo nasce senza collegamento.
        collegato = Piatto.objects.filter(menu__nome=menu, nome=piatto).first()
        Articolo.objects.create(
            nome=nome, categoria=categorie[categoria], reparto="cucina", piatto=collegato, ordine=ordine
        )


class Migration(migrations.Migration):
    dependencies = [
        ("cassa", "0001_iniziale"),
        ("magazzino", "0002_dati_iniziali"),
    ]

    operations = [migrations.RunPython(carica, migrations.RunPython.noop)]
