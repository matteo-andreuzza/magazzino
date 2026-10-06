"""Dati per la spinatrice.

* Crea la spinatrice e ci attacca il fusto rimasto attaccato (se ce n'è più
  d'uno, ogni fusto in più va su una spina nuova).
* Ricava le ore di attacco e di fine dei fusti dalle conte già registrate.
* Le tare le assegna l'amministrazione: dove non le ha ancora scritte vale un
  valore simbolico (un terzo dei litri), segnalato nelle pagine del bar.
"""

from decimal import Decimal

from django.db import migrations


def carica(apps, schema_editor):
    Spina = apps.get_model("bar", "Spina")
    Fusto = apps.get_model("bar", "Fusto")
    TipoFusto = apps.get_model("bar", "TipoFusto")

    if not Spina.objects.exists():
        Spina.objects.create(nome="Spinatrice", ordine=1)
    spine = list(Spina.objects.order_by("ordine", "pk"))
    occupate = set(Fusto.objects.filter(finito_con__isnull=True, spina__isnull=False).values_list("spina_id", flat=True))
    libere = [s for s in spine if s.pk not in occupate]
    for fusto in Fusto.objects.filter(finito_con__isnull=True, spina__isnull=True).order_by("pk"):
        if not libere:
            libere.append(Spina.objects.create(nome=f"Spina {Spina.objects.count() + 1}", ordine=Spina.objects.count() + 1))
        fusto.spina = libere.pop(0)
        fusto.save(update_fields=["spina"])

    for fusto in Fusto.objects.select_related("aperto_con", "finito_con"):
        fusto.attaccato_il = fusto.aperto_con.creata_il
        fusto.finito_il = fusto.finito_con.creata_il if fusto.finito_con_id else None
        fusto.save(update_fields=["attaccato_il", "finito_il"])
    Pesata = apps.get_model("bar", "Pesata")
    for pesata in Pesata.objects.select_related("conta"):
        pesata.registrata_il = pesata.conta.creata_il
        pesata.utente_id = pesata.conta.utente_id
        pesata.save(update_fields=["registrata_il", "utente"])

    # Solo dove la tara manca: quella già scritta dall'amministrazione resta com'è.
    for tipo in TipoFusto.objects.filter(tara__isnull=True):
        tipo.tara = (tipo.litri / 3).quantize(Decimal("1"))
        tipo.tara_provvisoria = True
        tipo.save(update_fields=["tara", "tara_provvisoria"])


class Migration(migrations.Migration):
    dependencies = [("bar", "0003_spinatrice")]

    operations = [migrations.RunPython(carica, migrations.RunPython.noop)]
