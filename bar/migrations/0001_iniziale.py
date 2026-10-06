import django.core.validators
import django.db.models.deletion
import django.db.models.functions.text
import django.utils.timezone
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('magazzino', '0006_cassa_chiusa'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Bevanda',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nome', models.CharField(max_length=60)),
                ('tipo', models.CharField(choices=[('lattina', 'Lattine'), ('bottiglia', 'Bottiglie'), ('litri', 'Sfusa a litri (es. vino alla spina)'), ('fusti', 'Alla spina da fusti (birra)')], help_text='La birra si conta in fusti (pieni, attaccati, finiti); le altre in lattine, bottiglie o litri.', max_length=10, verbose_name='come si conta')),
                ('ordine', models.PositiveIntegerField(default=0, help_text='Ordine negli elenchi.')),
                ('attiva', models.BooleanField(default=True, help_text='Le bevande non attive non compaiono nei moduli.')),
                ('note', models.CharField(blank=True, max_length=200)),
            ],
            options={
                'verbose_name': 'bevanda',
                'verbose_name_plural': 'bevande',
                'ordering': ['ordine', django.db.models.functions.text.Lower('nome')],
                'constraints': [models.UniqueConstraint(django.db.models.functions.text.Lower('nome'), name='bevanda_nome_unico', violation_error_message='Esiste già una bevanda con questo nome.')],
            },
        ),
        migrations.CreateModel(
            name='ContaFusti',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('creata_il', models.DateTimeField(default=django.utils.timezone.now)),
                ('note', models.CharField(blank=True, max_length=250)),
                ('token', models.UUIDField(blank=True, editable=False, null=True, unique=True)),
                ('servizio', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='conta_fusti', to='magazzino.servizio')),
                ('utente', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'conta dei fusti',
                'verbose_name_plural': 'conte dei fusti',
                'ordering': ['-pk'],
            },
        ),
        migrations.CreateModel(
            name='OperazioneBar',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tipo', models.CharField(choices=[('inventario', 'Inventario iniziale'), ('carico', 'Consegna / acquisto'), ('rettifica', 'Correzione conteggio')], max_length=15)),
                ('creata_il', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('note', models.CharField(blank=True, max_length=250)),
                ('token', models.UUIDField(blank=True, editable=False, null=True, unique=True)),
                ('utente', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'operazione del bar',
                'verbose_name_plural': 'operazioni del bar',
                'ordering': ['-creata_il', '-pk'],
            },
        ),
        migrations.CreateModel(
            name='TipoFusto',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nome', models.CharField(help_text='Es. «Fusto da 30 litri».', max_length=40)),
                ('litri', models.DecimalField(decimal_places=2, help_text='Quanta birra contiene un fusto pieno.', max_digits=6, validators=[django.core.validators.MinValueValidator(Decimal('1'))])),
                ('tara', models.DecimalField(blank=True, decimal_places=2, help_text='Pesa un fusto vuoto: serve a calcolare quanta birra resta in quelli pesati.', max_digits=6, null=True, validators=[django.core.validators.MinValueValidator(Decimal('0'))], verbose_name='peso da vuoto (kg)')),
                ('ordine', models.PositiveIntegerField(default=0)),
                ('attivo', models.BooleanField(default=True)),
                ('bevanda', models.ForeignKey(limit_choices_to={'tipo': 'fusti'}, on_delete=django.db.models.deletion.PROTECT, related_name='tipi_fusto', to='bar.bevanda')),
            ],
            options={
                'verbose_name': 'tipo di fusto',
                'verbose_name_plural': 'tipi di fusto',
                'ordering': ['bevanda__ordine', 'ordine', 'litri'],
            },
        ),
        migrations.CreateModel(
            name='MovimentoBar',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantita', models.DecimalField(decimal_places=3, max_digits=12)),
                ('bevanda', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='movimenti', to='bar.bevanda')),
                ('operazione', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='movimenti', to='bar.operazionebar')),
                ('tipo_fusto', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='movimenti', to='bar.tipofusto')),
            ],
            options={
                'verbose_name': 'movimento del bar',
                'verbose_name_plural': 'movimenti del bar',
                'ordering': ['pk'],
            },
        ),
        migrations.CreateModel(
            name='Fusto',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('aperto_con', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='fusti_aperti', to='bar.contafusti')),
                ('finito_con', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='fusti_finiti', to='bar.contafusti')),
                ('tipo', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='fusti', to='bar.tipofusto')),
            ],
            options={
                'verbose_name': 'fusto',
                'verbose_name_plural': 'fusti',
                'ordering': ['pk'],
            },
        ),
        migrations.CreateModel(
            name='Pesata',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('peso', models.DecimalField(decimal_places=2, max_digits=6, validators=[django.core.validators.MinValueValidator(Decimal('0'))], verbose_name='peso (kg)')),
                ('vuoto', models.BooleanField(default=False)),
                ('misurata', models.BooleanField(default=True)),
                ('conta', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pesate', to='bar.contafusti')),
                ('fusto', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pesate', to='bar.fusto')),
            ],
            options={
                'verbose_name': 'pesata',
                'verbose_name_plural': 'pesate',
                'ordering': ['pk'],
                'constraints': [models.UniqueConstraint(fields=('conta', 'fusto'), name='pesata_unica_per_conta')],
            },
        ),
        migrations.AddConstraint(
            model_name='tipofusto',
            constraint=models.UniqueConstraint(django.db.models.functions.text.Lower('nome'), name='tipo_fusto_nome_unico', violation_error_message='Esiste già un tipo di fusto con questo nome.'),
        ),
        migrations.AddConstraint(
            model_name='movimentobar',
            constraint=models.CheckConstraint(condition=models.Q(('quantita', 0), _negated=True), name='movimento_bar_quantita_non_zero'),
        ),
        migrations.AddConstraint(
            model_name='movimentobar',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('bevanda__isnull', False), ('tipo_fusto__isnull', True)), models.Q(('bevanda__isnull', True), ('tipo_fusto__isnull', False)), _connector='OR'), name='movimento_bar_una_cosa'),
        ),
    ]
