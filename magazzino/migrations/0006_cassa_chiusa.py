import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('magazzino', '0005_niente_venduto_in_cassa'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='servizio',
            name='cassa_chiusa_da',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='cassa chiusa da'),
        ),
        migrations.AddField(
            model_name='servizio',
            name='cassa_chiusa_il',
            field=models.DateTimeField(blank=True, help_text='Da chiusa la cassa non registra più ordini.', null=True, verbose_name='cassa chiusa il'),
        ),
        migrations.AlterField(
            model_name='operazione',
            name='tipo',
            field=models.CharField(choices=[('inventario', 'Inventario iniziale'), ('acquisto', 'Acquisto / consegna'), ('cucina', 'Messo a cucinare'), ('reso', 'Rientrato dalla cucina'), ('spostamento', 'Spostamento'), ('scarto', 'Scarto'), ('rettifica', 'Correzione conteggio')], max_length=20),
        ),
        migrations.AddConstraint(
            model_name='operazione',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('tipo__in', ['cucina', 'reso']), ('servizio__isnull', False)), models.Q(models.Q(('tipo__in', ['cucina', 'reso']), _negated=True), ('servizio__isnull', True)), _connector='OR'), name='operazione_servizio_coerente'),
        ),
    ]
