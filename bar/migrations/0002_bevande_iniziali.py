"""Bevande del bar e tipi di fusto di partenza (si cambiano da Bar → Bevande e fusti).

I litri dei fusti sono da controllare e il peso da vuoto (tara) va misurato:
finché manca, la conta di fine serata lo chiede. Viene eseguita una sola volta.
"""

from decimal import Decimal

from django.db import migrations

BEVANDE = [
    # (nome, come si conta)
    ("Coca-Cola", "lattina"),
    ("Fanta", "lattina"),
    ("Tè alla pesca", "lattina"),
    ("Tè al limone", "lattina"),
    ("Acqua minerale", "bottiglia"),
    ("Vino bianco", "litri"),
    ("Vino rosso", "litri"),
    ("Birra alla spina", "fusti"),
]

FUSTI = [("Fusto da 15 litri", "15"), ("Fusto da 30 litri", "30")]


def carica(apps, schema_editor):
    Bevanda = apps.get_model("bar", "Bevanda")
    TipoFusto = apps.get_model("bar", "TipoFusto")
    if Bevanda.objects.exists():
        return
    for ordine, (nome, tipo) in enumerate(BEVANDE, start=1):
        Bevanda.objects.create(nome=nome, tipo=tipo, ordine=ordine)
    birra = Bevanda.objects.get(tipo="fusti")
    for ordine, (nome, litri) in enumerate(FUSTI, start=1):
        TipoFusto.objects.create(bevanda=birra, nome=nome, litri=Decimal(litri), ordine=ordine)


class Migration(migrations.Migration):
    dependencies = [("bar", "0001_iniziale")]

    operations = [migrations.RunPython(carica, migrations.RunPython.noop)]
