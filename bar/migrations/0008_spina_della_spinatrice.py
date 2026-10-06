import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("bar", "0007_spinatrici_dati")]

    operations = [
        migrations.AlterField(
            model_name="spina",
            name="spinatrice",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="spine", to="bar.spinatrice"),
        ),
    ]
