from django.conf import settings
from django.db import migrations


def converti_vendite(apps, schema_editor):
    """Lo scarico del venduto in cassa non c'è più (le bevande stanno nel magazzino del bar).

    Le eventuali operazioni già registrate diventano scarti, così le giacenze non cambiano.
    """
    Operazione = apps.get_model("magazzino", "Operazione")
    for op in Operazione.objects.filter(tipo="vendita").select_related("servizio"):
        op.note = f"Venduto in cassa (serata del {op.servizio.data:%d/%m/%Y}). {op.note}".strip()[:250]
        op.tipo = "scarto"
        op.servizio = None
        op.save(update_fields=["note", "tipo", "servizio"])


class Migration(migrations.Migration):

    dependencies = [
        ('magazzino', '0004_venduto_in_cassa'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='operazione',
            name='operazione_servizio_coerente',
        ),
        migrations.RunPython(converti_vendite, migrations.RunPython.noop),
    ]
