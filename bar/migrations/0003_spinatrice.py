"""Spinatrice: i fusti si attaccano a una spina e si segnano le ore di attacco e di fine.

I vincoli arrivano in 0005, dopo aver sistemato i dati in 0004.
"""

import django.core.validators
import django.db.models.deletion
import django.db.models.functions.text
import django.utils.timezone
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bar', '0002_bevande_iniziali'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='contafusti',
            options={'ordering': ['-pk'], 'verbose_name': 'fusti della serata', 'verbose_name_plural': 'fusti delle serate'},
        ),
        migrations.AddField(
            model_name='fusto',
            name='attaccato_il',
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
        migrations.AddField(
            model_name='fusto',
            name='finito_il',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='pesata',
            name='registrata_il',
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
        migrations.AddField(
            model_name='pesata',
            name='utente',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='tipofusto',
            name='tara_provvisoria',
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.AlterField(
            model_name='tipofusto',
            name='tara',
            field=models.DecimalField(blank=True, decimal_places=2, help_text='Peso di un fusto vuoto (tara): serve a calcolare quanta birra resta in quelli pesati.', max_digits=6, null=True, validators=[django.core.validators.MinValueValidator(Decimal('0'))], verbose_name='peso da vuoto (kg)'),
        ),
        migrations.CreateModel(
            name='Spina',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nome', models.CharField(help_text='Es. «Spinatrice» o «Spina della rossa».', max_length=40)),
                ('ordine', models.PositiveIntegerField(default=0)),
                ('attiva', models.BooleanField(default=True, help_text='Le spine non attive non compaiono (se non hanno un fusto attaccato).')),
            ],
            options={
                'verbose_name': 'spina',
                'verbose_name_plural': 'spine',
                'ordering': ['ordine', 'pk'],
                'constraints': [models.UniqueConstraint(django.db.models.functions.text.Lower('nome'), name='spina_nome_unico', violation_error_message='Esiste già una spina con questo nome.')],
            },
        ),
        migrations.AddField(
            model_name='fusto',
            name='spina',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='fusti', to='bar.spina'),
        ),
    ]
