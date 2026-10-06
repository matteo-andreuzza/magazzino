import django.core.validators
import django.db.models.deletion
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bar', '0001_iniziale'),
        ('cassa', '0002_listino_iniziale'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='articolo',
            name='prodotto',
        ),
        migrations.RemoveField(
            model_name='articolo',
            name='quantita_prodotto',
        ),
        migrations.RemoveField(
            model_name='impostazionicassa',
            name='scarico_magazzino',
        ),
        migrations.RemoveField(
            model_name='impostazionicassa',
            name='ubicazione_scarico',
        ),
        migrations.AddField(
            model_name='articolo',
            name='bevanda',
            field=models.ForeignKey(blank=True, help_text='Lascia vuoto per il cibo e per ciò che non si conta (caffè, liquori…).', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='articoli', to='bar.bevanda', verbose_name='bevanda del magazzino bar'),
        ),
        migrations.AddField(
            model_name='articolo',
            name='quantita_bevanda',
            field=models.DecimalField(decimal_places=3, default=Decimal('1'), help_text='Es. 1 lattina; per birra e vino i litri: 0,33 il bicchiere, 1,5 la caraffa grande.', max_digits=8, validators=[django.core.validators.MinValueValidator(Decimal('0.001'))], verbose_name='quantità per pezzo venduto'),
        ),
        migrations.AddField(
            model_name='rigaordine',
            name='bevanda',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='righe_vendute', to='bar.bevanda'),
        ),
        migrations.AddField(
            model_name='rigaordine',
            name='quantita_bevanda',
            field=models.DecimalField(blank=True, decimal_places=3, max_digits=8, null=True),
        ),
    ]
