from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("bar", "0004_spinatrice_dati")]

    operations = [
        migrations.AddConstraint(
            model_name="fusto",
            constraint=models.UniqueConstraint(
                condition=models.Q(("finito_con__isnull", True)), fields=("spina",), name="un_fusto_attaccato_per_spina"
            ),
        ),
        migrations.AddConstraint(
            model_name="fusto",
            constraint=models.CheckConstraint(
                condition=models.Q(("finito_con__isnull", False), ("spina__isnull", False), _connector="OR"),
                name="fusto_attaccato_a_una_spina",
            ),
        ),
    ]
