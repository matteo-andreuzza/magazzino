"""Le due spinatrici del bancone: birra (bionda e rossa) e vino (bianco e rosso).

* «Birra alla spina» diventa «Birra bionda» e si aggiunge la «Birra rossa»;
  i fusti della birra sono da 50 litri (quelli da 15 e 30 della prima
  versione restano, ma non attivi, se non sono mai stati usati).
* Il vino alla spina adesso si conta in fusti, come la birra: il bianco nel
  «Largo argento», il rosso nell'«Alto nero» e nell'«Azzurro». Solo se non
  era ancora stato caricato a litri (altrimenti le giacenze cambierebbero).
* La spina che c'era diventa la spina della bionda nella spinatrice «Birra»;
  si aggiungono la spina della rossa e la spinatrice «Vino» con le sue due.

Litri e tare sono provvisori (valori simbolici, segnalati nelle pagine del
bar): li sistema l'amministrazione da Bar → Bevande e fusti. Non si cancella
niente e si salta quello che è già stato cambiato a mano.
"""

from decimal import Decimal

from django.db import migrations

BIRRA, VINO = Decimal("1.01"), Decimal("0.99")


def carica(apps, schema_editor):
    Bevanda = apps.get_model("bar", "Bevanda")
    TipoFusto = apps.get_model("bar", "TipoFusto")
    Spinatrice = apps.get_model("bar", "Spinatrice")
    Spina = apps.get_model("bar", "Spina")
    MovimentoBar = apps.get_model("bar", "MovimentoBar")
    Fusto = apps.get_model("bar", "Fusto")

    def trova(nome):
        return Bevanda.objects.filter(nome__iexact=nome).first()

    # --- bevande alla spina
    bionda = trova("Birra bionda")
    vecchia = trova("Birra alla spina")
    if bionda is None and vecchia is not None and vecchia.tipo == "fusti":
        vecchia.nome = "Birra bionda"
        vecchia.save(update_fields=["nome"])
        bionda = vecchia
    ultimo = max(Bevanda.objects.values_list("ordine", flat=True), default=0)
    if bionda is None:
        ultimo += 1
        bionda = Bevanda.objects.create(nome="Birra bionda", tipo="fusti", ordine=ultimo)
    rossa = trova("Birra rossa")
    if rossa is None:
        rossa = Bevanda.objects.create(nome="Birra rossa", tipo="fusti", ordine=bionda.ordine)
    for b, colore in ((bionda, "bionda"), (rossa, "rossa")):
        if b.tipo == "fusti":
            b.densita, b.colore = BIRRA, colore
            b.save(update_fields=["densita", "colore"])

    vini = []
    for nome, colore in (("Vino bianco", "bianco"), ("Vino rosso", "rosso")):
        v = trova(nome)
        if v is None:
            ultimo += 1
            v = Bevanda.objects.create(nome=nome, tipo="fusti", ordine=ultimo)
        elif v.tipo == "litri" and not MovimentoBar.objects.filter(bevanda=v).exists():
            # Senza carichi a litri non c'è una giacenza a litri da perdere. Le vendite già fatte
            # restano litri venduti (come per la birra): nelle serate senza fusti non c'è niente
            # da confrontare, quindi non bloccano.
            v.tipo = "fusti"
        if v.tipo == "fusti":
            v.densita, v.colore = VINO, colore
            v.save(update_fields=["tipo", "densita", "colore"])
            vini.append(v)
        else:
            vini.append(None)
    bianco, rosso = vini
    # Negli elenchi come al bancone: prima la birra, poi il vino, dopo lattine e bottiglie.
    for i, b in enumerate(b for b in (bionda, rossa, bianco, rosso) if b is not None and b.tipo == "fusti"):
        b.ordine = 100 + i
        b.save(update_fields=["ordine"])

    # --- tipi di fusto
    for t in TipoFusto.objects.all():
        t.forma = "basso" if t.litri <= 20 else "alto"
        usato = MovimentoBar.objects.filter(tipo_fusto=t).exists() or Fusto.objects.filter(tipo=t).exists()
        if t.litri in (Decimal("15"), Decimal("30")) and t.tara_provvisoria and not usato:
            t.attivo = False
        t.save(update_fields=["forma", "attivo"])

    def tipo(bevanda, nome, litri, forma, colore, tara):
        if bevanda is None or TipoFusto.objects.filter(bevanda=bevanda, nome__iexact=nome).exists():
            return
        TipoFusto.objects.create(
            bevanda=bevanda, nome=nome, litri=Decimal(litri), forma=forma, colore=colore, tara=Decimal(tara),
            tara_provvisoria=True, ordine=TipoFusto.objects.filter(bevanda=bevanda).count() + 1,
        )

    for b in (bionda, rossa):
        if b.tipo == "fusti":
            tipo(b, "Fusto da 50 litri", "50", "alto", "acciaio", "17")
    tipo(bianco, "Largo argento", "25", "largo", "acciaio", "5")
    tipo(rosso, "Alto nero", "25", "alto", "nero", "5")
    tipo(rosso, "Azzurro", "25", "alto", "azzurro", "5")

    # --- spinatrici e spine
    if Spinatrice.objects.exists():
        return
    birra = Spinatrice.objects.create(nome="Birra", ordine=1)
    spine = list(Spina.objects.order_by("ordine", "pk"))
    if len(spine) == 1 and spine[0].nome.lower() == "spinatrice" and not Spina.objects.filter(nome__iexact="Bionda").exists():
        spine[0].nome = "Bionda"
    for i, sp in enumerate(spine, start=1):
        sp.spinatrice, sp.ordine = birra, i
        if sp.nome.lower() == "bionda" and bionda.tipo == "fusti":
            sp.bevanda = bionda
        sp.save(update_fields=["nome", "spinatrice", "ordine", "bevanda"])

    def spina(spinatrice, nome, bevanda):
        if Spina.objects.filter(nome__iexact=nome).exists():
            return
        Spina.objects.create(
            spinatrice=spinatrice, nome=nome, bevanda=bevanda if bevanda is not None and bevanda.tipo == "fusti" else None,
            ordine=Spina.objects.filter(spinatrice=spinatrice).count() + 1,
        )

    spina(birra, "Bionda", bionda)
    spina(birra, "Rossa", rossa)
    if bianco is not None or rosso is not None:
        vino = Spinatrice.objects.create(nome="Vino", ordine=2)
        spina(vino, "Bianco", bianco)
        spina(vino, "Rosso", rosso)


class Migration(migrations.Migration):
    dependencies = [("bar", "0006_spinatrici")]

    operations = [migrations.RunPython(carica, migrations.RunPython.noop)]
