"""Carica un listino (file JSON, vedi cassa/listini/) nella cassa.

    python manage.py carica_listino cassa/listini/listino_2026.json --prova
    python manage.py carica_listino cassa/listini/listino_2026.json

Si può ripetere: gli articoli già presenti (per nome, o per uno dei nomi che
«sostituiscono», es. «Pennette al pomodoro» → «Pasta con pomodoro e
basilico») si aggiornano invece di essere duplicati, quindi restano le
statistiche delle serate già fatte. Crea anche le categorie, le bevande del
bar e i piatti del menu che servono. Non cancella niente: con
--disattiva-altri gli articoli che non sono nel listino diventano non attivi.
Con --prova mostra cosa farebbe senza salvare.
"""

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from bar.models import Bevanda, TipoBevanda
from cassa.models import Articolo, Categoria, Colore, Reparto
from magazzino.models import Ingrediente, Menu, Piatto, Prodotto


class Prova(Exception):
    """Per annullare tutto alla fine di una prova."""


def _trova(modello, nomi, **filtri):
    for nome in nomi:
        trovato = modello.objects.filter(nome__iexact=nome, **filtri).first()
        if trovato is not None:
            return trovato
    return None


def _decimale(valore, cosa):
    try:
        return Decimal(str(valore))
    except (InvalidOperation, TypeError):
        raise CommandError(f"{cosa}: «{valore}» non è un numero.") from None


class Command(BaseCommand):
    help = "Carica (o aggiorna) il listino della cassa da un file JSON."

    def add_arguments(self, parser):
        parser.add_argument("file", help="Il listino, es. cassa/listini/listino_2026.json")
        parser.add_argument("--prova", action="store_true", help="Mostra cosa cambierebbe, senza salvare.")
        parser.add_argument("--disattiva-altri", action="store_true", help="Gli articoli che non sono nel listino diventano non attivi.")

    def handle(self, *args, file, prova, disattiva_altri, **opzioni):
        percorso = Path(file)
        if not percorso.exists():
            raise CommandError(f"Non trovo il file {percorso}.")
        try:
            listino = json.loads(percorso.read_text(encoding="utf-8"))
        except ValueError as e:
            raise CommandError(f"Il file non è un JSON valido: {e}") from None
        self.righe = []
        try:
            with transaction.atomic():
                self._carica(listino, disattiva_altri)
                if prova:
                    raise Prova
        except Prova:
            self.righe.append("PROVA: non è stato salvato niente.")
        for riga in self.righe:
            self.stdout.write(riga)

    def dice(self, testo):
        self.righe.append(testo)

    def _carica(self, listino, disattiva_altri):
        self.dice(f"Listino: {listino.get('nome', '(senza nome)')}")
        categorie = {}
        for ordine, c in enumerate(listino.get("categorie", []), start=1):
            if c.get("colore", Colore.GRIGIO) not in Colore.values:
                raise CommandError(f"Categoria «{c['nome']}»: colore «{c['colore']}» non valido ({', '.join(Colore.values)}).")
            cat = _trova(Categoria, [c["nome"], *c.get("sostituisce", [])])
            if cat is None:
                cat = Categoria(nome=c["nome"])
                self.dice(f"+ categoria {c['nome']}")
            elif cat.nome != c["nome"]:
                self.dice(f"~ categoria {cat.nome} → {c['nome']}")
            cat.nome, cat.colore, cat.ordine = c["nome"], c.get("colore", Colore.GRIGIO), ordine
            cat.save()
            categorie[c["nome"].lower()] = cat

        for b in listino.get("bevande", []):
            if b["tipo"] not in TipoBevanda.values:
                raise CommandError(f"Bevanda «{b['nome']}»: tipo «{b['tipo']}» non valido ({', '.join(TipoBevanda.values)}).")
            bevanda = _trova(Bevanda, [b["nome"]]) or _trova(Bevanda, b.get("sostituisce", []), tipo=b["tipo"])
            if bevanda is None:
                ultimo = max(Bevanda.objects.values_list("ordine", flat=True), default=0)
                Bevanda.objects.create(nome=b["nome"], tipo=b["tipo"], ordine=ultimo + 1, note=b.get("note", ""))
                self.dice(f"+ bevanda del bar {b['nome']}")
            elif bevanda.nome != b["nome"]:
                self.dice(f"~ bevanda del bar {bevanda.nome} → {b['nome']}")
                bevanda.nome = b["nome"]
                bevanda.save(update_fields=["nome"])

        for p in listino.get("piatti", []):
            menu, _ = Menu.objects.get_or_create(nome=p["menu"], defaults={"ordine": Menu.objects.count() + 1})
            piatto, creato = Piatto.objects.get_or_create(
                menu=menu, nome=p["nome"], defaults={"note": p.get("note", ""), "ordine": menu.piatti.count() + 1},
            )
            if creato:
                self.dice(f"+ piatto {p['nome']} ({menu.nome})")
                for i, nome in enumerate(p.get("ingredienti", []), start=1):
                    prodotto = Prodotto.objects.filter(nome__iexact=nome).first()
                    if prodotto is None:
                        self.dice(f"  ! ingrediente «{nome}» non trovato tra i prodotti: aggiungilo dal menu")
                        continue
                    Ingrediente.objects.get_or_create(piatto=piatto, prodotto=prodotto, defaults={"ordine": i})

        nel_listino = set()
        ordine_per_categoria = {}
        for a in listino.get("articoli", []):
            categoria = categorie.get(a["categoria"].lower()) or _trova(Categoria, [a["categoria"]])
            if categoria is None:
                raise CommandError(f"Articolo «{a['nome']}»: la categoria «{a['categoria']}» non c'è.")
            reparto = a.get("reparto", Reparto.CUCINA)
            if reparto not in Reparto.values:
                raise CommandError(f"Articolo «{a['nome']}»: reparto «{reparto}» non valido.")
            piatto = None
            if a.get("piatto"):
                menu, nome = a["piatto"]
                piatto = Piatto.objects.filter(menu__nome__iexact=menu, nome__iexact=nome).first()
                if piatto is None:
                    self.dice(f"  ! {a['nome']}: il piatto «{nome}» ({menu}) non c'è nel menu, lo collego a niente")
            bevanda = None
            if a.get("bevanda"):
                bevanda = Bevanda.objects.filter(nome__iexact=a["bevanda"]).first()
                if bevanda is None:
                    self.dice(f"  ! {a['nome']}: la bevanda del bar «{a['bevanda']}» non c'è, lo collego a niente")
            articolo = _trova(Articolo, [a["nome"], *a.get("sostituisce", [])])
            if articolo is None:
                articolo = Articolo(nome=a["nome"])
                self.dice(f"+ {a['nome']} € {a['prezzo']}")
            else:
                cambi = []
                if articolo.nome != a["nome"]:
                    cambi.append(f"nome «{articolo.nome}»")
                if articolo.prezzo != _decimale(a["prezzo"], a["nome"]):
                    cambi.append(f"prezzo {articolo.prezzo if articolo.prezzo is not None else 'vuoto'} → {a['prezzo']}")
                self.dice(f"~ {a['nome']}" + (f" ({', '.join(cambi)})" if cambi else ""))
            ordine_per_categoria[categoria.pk] = ordine_per_categoria.get(categoria.pk, 0) + 1
            articolo.nome = a["nome"]
            articolo.categoria = categoria
            articolo.reparto = reparto
            articolo.prezzo = _decimale(a["prezzo"], a["nome"])
            articolo.piatto = piatto
            articolo.bevanda = bevanda
            articolo.quantita_bevanda = _decimale(a.get("quantita", "1"), a["nome"])
            articolo.ordine = ordine_per_categoria[categoria.pk]
            articolo.attivo = True
            articolo.note = a.get("note", "")
            articolo.full_clean()
            articolo.save()
            nel_listino.add(articolo.pk)

        altri = Articolo.objects.filter(attivo=True).exclude(pk__in=nel_listino)
        if disattiva_altri:
            for articolo in altri:
                self.dice(f"- {articolo.nome} (non è nel listino: non attivo)")
            altri.update(attivo=False)
        elif altri.exists():
            self.dice("Restano attivi anche: " + ", ".join(a.nome for a in altri) + " (con --disattiva-altri si tolgono dalla cassa).")
        self.dice(f"Fatto: {len(nel_listino)} articoli nel listino.")
