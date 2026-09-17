"""Dati iniziali: frighi e congelatori, prodotti e menu della sagra.

Viene eseguita una sola volta, al primo avvio. Tutto è modificabile
dall'app (Prodotti) o dall'amministrazione (Menu, Piatti).
"""

from django.db import migrations

UBICAZIONI = [
    # (nome, tipo, note)
    ("Frigo 1", "frigo", ""),
    ("Frigo 2", "frigo", ""),
    ("Congelatore interno 1", "congelatore", ""),
    ("Congelatore interno 2", "congelatore", ""),
    ("Congelatore esterno 1", "congelatore", ""),
    ("Congelatore esterno 2", "congelatore", ""),
    ("Frigo bar", "frigo", ""),
    ("Dispensa", "ambiente", "Per ciò che non va in frigo: riso, vasi, buste…"),
]

PRODOTTI = [
    # (nome, categoria, unità, note)
    ("Pollo", "carne", "kg", ""),
    ("Salamelle", "carne", "pz", ""),
    ("Costicine", "carne", "kg", ""),
    ("Pastin", "carne", "kg", ""),
    ("Carne per tagliata", "carne", "kg", ""),
    ("Salsiccia", "carne", "kg", ""),
    ("Arista", "carne", "kg", "Comprata da Luca"),
    ("Fegatini", "carne", "kg", "Comprati in macelleria"),
    ("Lingua", "carne", "kg", ""),
    ("Muscolo", "carne", "kg", ""),
    ("Musetto", "carne", "pz", ""),
    ("Cappello del prete", "carne", "kg", ""),
    ("Seppie", "pesce", "kg", ""),
    ("Mazzancolle", "pesce", "busta", ""),
    ("Teste di seppioline piccole", "pesce", "busta", ""),
    ("Sardine", "pesce", "kg", "Da confermare"),
    ("Scampi", "pesce", "pz", "Uno a piatto nella paella"),
    ("Misto pesce (busta pronta)", "pesce", "busta", ""),
    ("Ragù d'anatra", "pronti", "kg", "Comprato già fatto"),
    ("Baccalà alla vicentina", "pronti", "kg", "Comprato già fatto"),
    ("Piadina alle verdure", "pronti", "pz", "Già fatta"),
    ("Crespella farcita", "pronti", "pz", ""),
    ("Gnocchi", "pasta_riso", "kg", ""),
    ("Pennette", "pasta_riso", "kg", ""),
    ("Riso", "pasta_riso", "kg", ""),
    ("Polenta", "pasta_riso", "kg", ""),
    ("Patatine", "verdure", "kg", "Per le patatine fritte"),
    ("Fagioli", "verdure", "kg", ""),
    ("Sedano", "verdure", "kg", ""),
    ("Cipolla", "verdure", "kg", ""),
    ("Carote", "verdure", "kg", ""),
    ("Funghi", "verdure", "kg", ""),
    ("Peperoni", "verdure", "kg", ""),
    ("Zucca", "verdure", "pz", "Zucche intere"),
    ("Patate", "verdure", "kg", ""),
    ("Prezzemolo", "verdure", "mazzo", ""),
    ("Insalata", "verdure", "kg", ""),
    ("Verdure da grigliare", "verdure", "kg", ""),
    ("Primo sale", "latticini", "kg", ""),
    ("Gorgonzola", "latticini", "kg", ""),
    ("Ricotta", "latticini", "kg", ""),
    ("Stracchino", "latticini", "kg", ""),
    ("Spalmabile", "latticini", "conf", ""),
    ("Pomodoro in vaso (2,5 kg)", "dispensa", "vaso", "Vasi da 2,5 kg"),
    ("Zafferano", "dispensa", "busta", "Bustine"),
    ("Tonno", "dispensa", "conf", ""),
    ("Maionese", "dispensa", "conf", ""),
    ("Capperi", "dispensa", "conf", ""),
    ("Pesto", "dispensa", "conf", ""),
    ("Pane per crostini", "pane", "pz", ""),
    ("Panini", "pane", "pz", ""),
]

DUE_FETTE = ("Polenta", "due fette")

ANTIPASTO = [
    ("Crespella farcita", "una fettina"),
    ("Sedano", "con gorgonzola e ricotta"),
    ("Gorgonzola", ""),
    ("Ricotta", ""),
    ("Pane per crostini", "crostini"),
    ("Salsiccia", "crostino con salsiccia e stracchino"),
    ("Stracchino", ""),
    ("Spalmabile", "crostino con spalmabile e pesto"),
    ("Pesto", ""),
]

MENU = [
    (
        "Menu della sagra",
        "In tutti i piatti ci sono le patatine a 3 euro.",
        [
            ("Grigliata mista", "", [("Pollo", ""), ("Salamelle", "due"), ("Costicine", "due"), DUE_FETTE]),
            ("Solo pollo", "", [("Pollo", ""), DUE_FETTE]),
            ("Solo salamelle", "", [("Salamelle", ""), DUE_FETTE]),
            ("Solo costicine", "", [("Costicine", ""), DUE_FETTE]),
            (
                "Frittura",
                "Due etti e mezzo",
                [
                    ("Seppie", ""),
                    ("Mazzancolle", ""),
                    ("Teste di seppioline piccole", ""),
                    ("Sardine", "da confermare"),
                ],
            ),
            ("Primo sale con polenta", "", [("Primo sale", ""), ("Polenta", "")]),
            (
                "Gnocchi o pennette",
                "Al pomodoro o al ragù d'anatra",
                [
                    ("Gnocchi", ""),
                    ("Pennette", ""),
                    ("Pomodoro in vaso (2,5 kg)", "vasi da 2,5 kg"),
                    ("Ragù d'anatra", "comprato già fatto"),
                ],
            ),
            ("Baccalà alla vicentina", "Già fatto", [("Baccalà alla vicentina", "")]),
            ("Patatine fritte", "", [("Patatine", "")]),
            ("Fagioli", "Con sedano o con cipolla", [("Fagioli", ""), ("Sedano", ""), ("Cipolla", "")]),
            ("Funghi", "", [("Funghi", "")]),
            ("Piadina alle verdure", "Già fatta", [("Piadina alle verdure", "")]),
        ],
    ),
    (
        "Solo una sera",
        "Piatti serviti una sola sera.",
        [
            ("Panino onto", "", [("Panini", ""), ("Pastin", ""), ("Peperoni", ""), ("Cipolla", "")]),
            ("Tagliata", "Con polenta? Da confermare", [("Carne per tagliata", ""), ("Polenta", "da confermare")]),
            ("Seppie in umido", "Fatte da noi", [("Seppie", ""), ("Pomodoro in vaso (2,5 kg)", "")]),
            (
                "Paella",
                "Solo pesce: da capire se si fa anche di carne",
                [
                    ("Teste di seppioline piccole", "busta di teste: verificare se sono le stesse della frittura"),
                    ("Mazzancolle", "busta"),
                    ("Scampi", "uno a piatto"),
                    ("Misto pesce (busta pronta)", ""),
                    ("Riso", ""),
                    ("Zafferano", "numero di bustine da definire"),
                    ("Sedano", "per il brodo"),
                    ("Carote", "per il brodo"),
                    ("Cipolla", "per il brodo"),
                ],
            ),
        ],
    ),
    (
        "Pranzo anniversari",
        "",
        [
            (
                "Antipasto",
                "Crespella farcita, sedano con gorgonzola e ricotta, crostino con salsiccia e stracchino, "
                "crostino con spalmabile e pesto",
                ANTIPASTO,
            ),
            (
                "Risotto zucca e funghi",
                "",
                [("Riso", ""), ("Zucca", "zucche intere comprate"), ("Funghi", "surgelati")],
            ),
            (
                "Arista con salsa tonnata",
                "Salsa tonnata: tonno, maionese e capperi",
                [("Arista", "comprata da Luca"), ("Tonno", ""), ("Maionese", ""), ("Capperi", "")],
            ),
            (
                "Contorno",
                "Patate al prezzemolo, carote e insalata",
                [("Patate", ""), ("Prezzemolo", ""), ("Carote", ""), ("Insalata", "")],
            ),
            ("Dolce e caffè", "Ingredienti da definire", []),
        ],
    ),
    (
        "Pranzo paesano",
        "",
        [
            ("Antipasto", "Uguale agli anniversari (da confermare)", ANTIPASTO),
            ("Risotto ai fegatini", "", [("Riso", ""), ("Fegatini", "comprati in macelleria")]),
            (
                "Misto bollito",
                "",
                [("Lingua", ""), ("Muscolo", ""), ("Musetto", ""), ("Cappello del prete", "")],
            ),
            (
                "Contorni",
                "Verdure grigliate e fagioli",
                [("Verdure da grigliare", ""), ("Fagioli", "")],
            ),
            ("Dolce e caffè", "Ingredienti da definire", []),
        ],
    ),
]


def carica(apps, schema_editor):
    Ubicazione = apps.get_model("magazzino", "Ubicazione")
    Prodotto = apps.get_model("magazzino", "Prodotto")
    Menu = apps.get_model("magazzino", "Menu")
    Piatto = apps.get_model("magazzino", "Piatto")
    Ingrediente = apps.get_model("magazzino", "Ingrediente")

    for ordine, (nome, tipo, note) in enumerate(UBICAZIONI, start=1):
        Ubicazione.objects.create(nome=nome, tipo=tipo, note=note, ordine=ordine)

    prodotti = {}
    for nome, categoria, unita, note in PRODOTTI:
        prodotti[nome] = Prodotto.objects.create(nome=nome, categoria=categoria, unita=unita, note=note)

    for ordine_menu, (nome_menu, note_menu, piatti) in enumerate(MENU, start=1):
        menu = Menu.objects.create(nome=nome_menu, note=note_menu, ordine=ordine_menu)
        for ordine_piatto, (nome_piatto, note_piatto, ingredienti) in enumerate(piatti, start=1):
            piatto = Piatto.objects.create(menu=menu, nome=nome_piatto, note=note_piatto, ordine=ordine_piatto)
            for ordine_ing, (nome_prodotto, note_ing) in enumerate(ingredienti, start=1):
                Ingrediente.objects.create(
                    piatto=piatto, prodotto=prodotti[nome_prodotto], note=note_ing, ordine=ordine_ing
                )


class Migration(migrations.Migration):
    dependencies = [("magazzino", "0001_iniziale")]

    operations = [migrations.RunPython(carica, migrations.RunPython.noop)]
