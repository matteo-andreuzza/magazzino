"""Gli strumenti che Claude può usare tramite il server MCP.

Ogni strumento è una funzione che riceve gli argomenti (già decodificati dal
JSON) e il contesto (utente e permessi della chiave) e restituisce dati
semplici (dizionari, liste, numeri, testi) che vengono mandati come JSON.

Le letture usano le stesse funzioni delle pagine dell'app; le scritture
passano dagli stessi servizi dei moduli (stessi controlli, stesse regole):
Claude non può fare niente che un volontario non possa fare dall'app.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from bar import services as bar
from bar.models import Bevanda
from cassa import report as cassa_report
from cassa.models import Articolo, Ordine
from magazzino import services as magazzino
from magazzino.models import (
    Categoria,
    Menu,
    Movimento,
    Operazione,
    Prodotto,
    Servizio,
    TipoOperazione,
    Ubicazione,
    Unita,
)
from magazzino.quantita import ZERO
from magazzino.views.comuni import giorno_servizio


class ErroreStrumento(Exception):
    """Errore da far vedere a Claude (argomenti sbagliati, operazione rifiutata…)."""


@dataclass
class Contesto:
    utente: object
    scrittura: bool


@dataclass
class Strumento:
    nome: str
    titolo: str
    descrizione: str
    parametri: dict
    funzione: object
    scrive: bool = False

    def definizione(self):
        return {
            "name": self.nome,
            "title": self.titolo,
            "description": self.descrizione,
            "inputSchema": {"type": "object", "additionalProperties": False, **self.parametri},
            "annotations": {
                "title": self.titolo,
                "readOnlyHint": not self.scrive,
                "destructiveHint": False,
                "idempotentHint": not self.scrive,
                "openWorldHint": False,
            },
        }


STRUMENTI = {}


def strumento(nome, titolo, descrizione, parametri=None, *, scrive=False):
    def registra(funzione):
        STRUMENTI[nome] = Strumento(nome, titolo, descrizione, parametri or {"properties": {}}, funzione, scrive)
        return funzione

    return registra


# ---------------------------------------------------------------------------
# Aiuti
# ---------------------------------------------------------------------------


def numero(valore):
    """Decimal -> numero JSON (intero se tondo)."""
    if valore is None:
        return None
    valore = Decimal(valore)
    return int(valore) if valore == valore.to_integral_value() else float(valore.normalize())


def euro(valore):
    return float(Decimal(valore or 0).quantize(Decimal("0.01")))


def quando(valore):
    if valore is None:
        return None
    if isinstance(valore, datetime):
        return timezone.localtime(valore).isoformat(timespec="minutes")
    return valore.isoformat()


def leggi_numero(valore, nome="quantita"):
    try:
        d = Decimal(str(valore).strip().replace(",", "."))
    except (InvalidOperation, AttributeError):
        raise ErroreStrumento(f"«{nome}» deve essere un numero, per esempio 2.5.") from None
    if not d.is_finite():
        raise ErroreStrumento(f"«{nome}» deve essere un numero, per esempio 2.5.")
    return d


SCHEMA_ID_REGISTRAZIONE = {
    "type": "string", "maxLength": 100,
    "description": "Un identificativo scelto da te per questa registrazione (es. «carico-coca-3-ottobre»). Se ripeti la "
                   "chiamata (rete caduta, risposta lenta) con lo stesso id non viene registrata due volte.",
}


def token_mcp(argomenti, nome, contesto):
    """Il token anti-doppione dei moduli dell'app, ricavato dall'id_registrazione scelto da Claude."""
    testo = str(argomenti.get("id_registrazione") or "").strip()
    if not testo:
        return None
    return uuid.uuid5(uuid.NAMESPACE_URL, f"magazzino-sagra/mcp/{nome}/{getattr(contesto.utente, 'pk', '')}/{testo}")


def oggetti(valore, cosa):
    """Un elenco di oggetti JSON (le voci di un carico, le spine…): altrimenti un errore chiaro per Claude."""
    if not isinstance(valore, list) or not all(isinstance(v, dict) for v in valore):
        raise ErroreStrumento(f"«{cosa}» deve essere un elenco di oggetti, per esempio [{{...}}, {{...}}].")
    return valore


def trova(queryset, valore, cosa):
    """Per id (numero) o per nome (esatto, poi parziale se ce n'è uno solo)."""
    if valore is None or str(valore).strip() == "":
        raise ErroreStrumento(f"Indica {cosa} (numero o nome).")
    testo = str(valore).strip()
    if testo.isdigit():
        trovato = queryset.filter(pk=int(testo)).first()
        if trovato is not None:
            return trovato
    esatti = list(queryset.filter(nome__iexact=testo)[:2])
    if len(esatti) == 1:
        return esatti[0]
    simili = list(queryset.filter(nome__icontains=testo)[:8])
    if len(simili) == 1:
        return simili[0]
    if simili:
        nomi = ", ".join(f"«{s.nome}» (id {s.pk})" for s in simili)
        raise ErroreStrumento(f"«{testo}» corrisponde a più di {cosa}: {nomi}. Indica quello giusto.")
    tutti = list(queryset[:31])
    if len(tutti) <= 30:
        raise ErroreStrumento(f"Non trovo {cosa} «{testo}». Ci sono: {', '.join(f'«{t.nome}»' for t in tutti)}.")
    raise ErroreStrumento(f"Non trovo {cosa} «{testo}».")


def trova_servizio(valore):
    if valore in (None, "", "oggi"):
        oggi = list(Servizio.objects.filter(data=giorno_servizio()).order_by("tipo", "nome"))
        if not oggi:
            raise ErroreStrumento("Oggi non c'è nessuna serata o pranzo.")
        return oggi[-1]
    testo = str(valore).strip()
    if testo.isdigit():
        s = Servizio.objects.filter(pk=int(testo)).first()
        if s is not None:
            return s
    try:
        giorno = date.fromisoformat(testo)
    except ValueError:
        raise ErroreStrumento(f"Serata «{testo}» non valida: usa l'id, la data (AAAA-MM-GG) o «oggi».") from None
    servizi = list(Servizio.objects.filter(data=giorno).order_by("tipo", "nome"))
    if not servizi:
        raise ErroreStrumento(f"Il {testo} non c'è nessuna serata o pranzo.")
    if len(servizi) > 1:
        elenco = ", ".join(f"{s} (id {s.pk})" for s in servizi)
        raise ErroreStrumento(f"Il {testo} ci sono più servizi: {elenco}. Indica l'id.")
    return servizi[0]


def descrivi_servizio(s):
    return {
        "id": s.pk, "data": quando(s.data), "tipo": s.get_tipo_display(), "nome": s.nome or None, "descrizione": str(s),
        "terminato": s.chiuso, "cassa_chiusa": s.cassa_chiusa,
    }


def scheda_prodotto(p, totale):
    return {
        "id": p.pk, "nome": p.nome, "categoria": p.get_categoria_display(), "unita": p.unita,
        "giacenza_totale": numero(totale), "scorta_minima": numero(p.scorta_minima),
        "sotto_scorta": p.scorta_minima is not None and totale < p.scorta_minima,
        "attivo": p.attivo, "note": p.note or None, "ha_foto": bool(p.foto),
    }


def _incassi(dati):
    return {
        "ordini": dati["ordini"], "di_cui_da_asporto": dati.get("asporto", 0), "coperti": dati["coperti"], "incassato": euro(dati["incassato"]),
        "contanti": euro(dati["contanti"]), "carta": euro(dati["carta"]),
        "omaggi_valore": euro(dati["omaggi"]), "ordini_omaggio": dati["n_omaggi"],
        "scontrino_medio": euro(dati["scontrino_medio"]) if dati["scontrino_medio"] is not None else None,
    }


CATEGORIE = {c.value: c for c in Categoria}
SCHEMA_CATEGORIA = {"type": "string", "enum": list(CATEGORIE), "description": "Categoria: " + ", ".join(
    f"{c.value} ({c.label})" for c in Categoria)}


# ---------------------------------------------------------------------------
# Letture
# ---------------------------------------------------------------------------


@strumento(
    "panoramica", "Panoramica di oggi",
    "Situazione di adesso: serate di oggi (stato, ordini, incasso), prodotti del magazzino sotto la scorta minima, "
    "bevande del bar sotto zero, fusti attaccati alle spine (birra e vino) e da pesare, ultime operazioni. Buon punto di partenza.",
)
def panoramica(argomenti, contesto):
    oggi = giorno_servizio()
    serate = []
    for s in Servizio.objects.filter(data=oggi).order_by("tipo", "nome"):
        totali = cassa_report.incassi(Ordine.objects.filter(servizio=s, annullato_il__isnull=True))
        serate.append({**descrivi_servizio(s), "cassa": _incassi(totali)})
    attaccati = bar.fusti_attaccati()
    operabile = next((s for s in Servizio.objects.filter(data=oggi) if bar.motivo_sola_lettura(s) is None), None)
    da_pesare = {s.fusto.pk for s in bar.da_pesare(operabile, attaccati)} if operabile else set()
    bevande = Bevanda.objects.in_bulk()
    return {
        "giorno_della_sagra": quando(oggi),
        "nota": "Dopo mezzanotte, fino alle 5, vale ancora la serata del giorno prima.",
        "serate_di_oggi": serate,
        "sotto_scorta": [
            {"prodotto": p.nome, "id": p.pk, "giacenza": numero(q), "scorta_minima": numero(p.scorta_minima), "unita": p.unita}
            for p, q in magazzino.prodotti_sotto_scorta()
        ],
        "bar_sotto_zero": [
            {"bevanda": bevande[b].nome, "giacenza": numero(q), "unita": bevande[b].unita}
            for b, q in bar.giacenze_bevande().items() if q < ZERO
        ],
        "fusti_attaccati": [
            {"id": s.fusto.pk, "spina": s.fusto.spina.nome if s.fusto.spina else None, "bevanda": s.bevanda.nome, "tipo": s.tipo.nome,
             "litri_rimasti": numero(bar.arrotonda(s.litri_rimasti)) if s.litri_rimasti is not None else None,
             "da_pesare_a_fine_serata": s.fusto.pk in da_pesare}
            for s in attaccati
        ],
        "ultime_operazioni": [
            {"id": o.pk, "tipo": o.get_tipo_display(), "quando": quando(o.creata_il), "servizio": str(o.servizio) if o.servizio else None}
            for o in Operazione.objects.select_related("servizio").order_by("-creata_il", "-pk")[:8]
        ],
    }


@strumento(
    "cerca_prodotti", "Cerca prodotti",
    "Prodotti del magazzino della cucina con giacenza totale, unità e scorta minima.",
    {"properties": {
        "testo": {"type": "string", "description": "Parte del nome (facoltativo)."},
        "categoria": SCHEMA_CATEGORIA,
        "anche_non_attivi": {"type": "boolean", "description": "Includi i prodotti non attivi (di solito no)."},
    }},
)
def cerca_prodotti(argomenti, contesto):
    qs = Prodotto.objects.all()
    if argomenti.get("testo"):
        qs = qs.filter(nome__icontains=argomenti["testo"].strip())
    if argomenti.get("categoria"):
        qs = qs.filter(categoria=argomenti["categoria"])
    if not argomenti.get("anche_non_attivi"):
        qs = qs.filter(attivo=True)
    totali = magazzino.giacenze_totali()
    return {"prodotti": [scheda_prodotto(p, totali.get(p.pk, ZERO)) for p in qs]}


@strumento(
    "prodotto", "Scheda di un prodotto",
    "Un prodotto: dove si trova (quantità per frigo, congelatore, dispensa), in quali piatti si usa e gli ultimi movimenti.",
    {"properties": {"prodotto": {"type": ["string", "integer"], "description": "Id o nome del prodotto."}}, "required": ["prodotto"]},
)
def prodotto(argomenti, contesto):
    p = trova(Prodotto.objects.all(), argomenti.get("prodotto"), "il prodotto")
    stock = magazzino.giacenze(prodotti=[p.pk])
    dove = [{"ubicazione": u.nome, "quantita": numero(stock[(p.pk, u.pk)])} for u in Ubicazione.objects.all() if (p.pk, u.pk) in stock]
    movimenti = (
        Movimento.objects.filter(prodotto=p).select_related("operazione__servizio", "ubicazione")
        .order_by("-operazione__creata_il", "-pk")[:25]
    )
    return {
        **scheda_prodotto(p, sum((stock[k] for k in stock), ZERO)),
        "dove_si_trova": dove,
        "usato_in": [f"{i.piatto.nome} ({i.piatto.menu.nome})" for i in p.ingredienti.select_related("piatto__menu")],
        "ultimi_movimenti": [
            {"quando": quando(m.operazione.creata_il), "operazione": m.operazione.get_tipo_display(), "operazione_id": m.operazione_id,
             "ubicazione": m.ubicazione.nome, "quantita": numero(m.quantita),
             "servizio": str(m.operazione.servizio) if m.operazione.servizio else None}
            for m in movimenti
        ],
    }


@strumento(
    "giacenze", "Giacenze del magazzino",
    "Cosa c'è in magazzino e dove: per ogni prodotto la quantità in ogni frigo, congelatore o dispensa.",
    {"properties": {
        "ubicazione": {"type": ["string", "integer"], "description": "Solo questa ubicazione (id o nome), facoltativo."},
        "categoria": SCHEMA_CATEGORIA,
        "solo_sotto_scorta": {"type": "boolean"},
    }},
)
def giacenze(argomenti, contesto):
    ubicazioni = list(Ubicazione.objects.all())
    if argomenti.get("ubicazione") not in (None, ""):
        ubicazioni = [trova(Ubicazione.objects.all(), argomenti["ubicazione"], "l'ubicazione")]
    stock = magazzino.giacenze(ubicazioni=[u.pk for u in ubicazioni])
    prodotti = Prodotto.objects.in_bulk({pid for pid, _ in stock})
    righe = []
    for p in sorted(prodotti.values(), key=lambda p: (p.categoria, p.nome.lower())):
        if argomenti.get("categoria") and p.categoria != argomenti["categoria"]:
            continue
        per_ubicazione = {u.nome: numero(stock[(p.pk, u.pk)]) for u in ubicazioni if (p.pk, u.pk) in stock}
        totale = sum((stock[(p.pk, u.pk)] for u in ubicazioni if (p.pk, u.pk) in stock), ZERO)
        sotto = p.scorta_minima is not None and totale < p.scorta_minima
        if argomenti.get("solo_sotto_scorta") and not sotto:
            continue
        righe.append({"prodotto": p.nome, "id": p.pk, "unita": p.unita, "totale": numero(totale),
                      "per_ubicazione": per_ubicazione, "sotto_scorta": sotto})
    return {"giacenze": righe}


@strumento("ubicazioni", "Frighi, congelatori e dispense", "Le ubicazioni del magazzino.")
def ubicazioni(argomenti, contesto):
    stock = magazzino.giacenze()
    return {"ubicazioni": [
        {"id": u.pk, "nome": u.nome, "tipo": u.get_tipo_display(), "attiva": u.attiva,
         "prodotti_presenti": sum(1 for (_, uid), q in stock.items() if uid == u.pk and q)}
        for u in Ubicazione.objects.all()
    ]}


@strumento("menu", "Menu e piatti", "Menu, piatti e i loro ingredienti (prodotti del magazzino, senza quantità).")
def menu(argomenti, contesto):
    return {"menu": [
        {"nome": m.nome, "piatti": [
            {"id": p.pk, "nome": p.nome, "ingredienti": [i.prodotto.nome for i in p.ingredienti.all()]}
            for p in m.piatti.all()
        ]}
        for m in Menu.objects.prefetch_related("piatti__ingredienti__prodotto")
    ]}


@strumento(
    "serate", "Serate e pranzi",
    "Le serate (e i pranzi) della sagra con stato, numero di ordini e incasso.",
    {"properties": {
        "dal": {"type": "string", "format": "date", "description": "Data iniziale AAAA-MM-GG (facoltativa)."},
        "al": {"type": "string", "format": "date", "description": "Data finale AAAA-MM-GG (facoltativa)."},
    }},
)
def serate(argomenti, contesto):
    qs = Servizio.objects.order_by("data", "tipo", "nome")
    try:
        if argomenti.get("dal"):
            qs = qs.filter(data__gte=date.fromisoformat(argomenti["dal"]))
        if argomenti.get("al"):
            qs = qs.filter(data__lte=date.fromisoformat(argomenti["al"]))
    except ValueError:
        raise ErroreStrumento("Le date vanno scritte AAAA-MM-GG.") from None
    return {"serate": [
        {**descrivi_servizio(s), "cassa": _incassi(cassa_report.incassi(Ordine.objects.filter(servizio=s, annullato_il__isnull=True)))}
        for s in qs
    ]}


@strumento(
    "serata", "Riepilogo di una serata",
    "Tutto di una serata o di un pranzo: cosa è uscito dal magazzino per la cucina e cosa è rientrato, incassi e venduto "
    "della cassa, bevande del bar e, per birra e vino alla spina, quanto è uscito dai fusti e quanto è stato venduto.",
    {"properties": {"serata": {"type": ["string", "integer"], "description": "Id, data (AAAA-MM-GG) oppure «oggi»."}}},
)
def serata(argomenti, contesto):
    s = trova_servizio(argomenti.get("serata"))
    consumi = magazzino.consumi_servizio(s)
    prodotti = Prodotto.objects.in_bulk(consumi.keys())
    dati = cassa_report.report_servizio(s)
    bar_dati = dati["bar"]
    return {
        **descrivi_servizio(s),
        "magazzino": [
            {"prodotto": prodotti[pid].nome, "unita": prodotti[pid].unita, "preso": numero(c["preso"]),
             "rientrato": numero(c["rientrato"]), "usato": numero(c["preso"] - c["rientrato"])}
            for pid, c in sorted(consumi.items(), key=lambda x: prodotti[x[0]].nome.lower())
        ],
        "cassa": {
            "totali": _incassi(dati["totali"]),
            "annullati": {"ordini": dati["annullati"]["n"], "valore": euro(dati["annullati"]["totale"])},
            "venduto": [
                {"articolo": r["articolo"].nome, "categoria": r["articolo"].categoria.nome, "pezzi": r["quantita"],
                 "di_cui_omaggio": r["omaggi"], "incasso": euro(r["importo"])}
                for r in dati["venduti"]
            ],
        },
        "bar": {
            "bevande": [
                {"bevanda": b["bevanda"].nome, "venduto": numero(b["venduto"]), "unita": b["bevanda"].unita,
                 "birra_uscita_dai_fusti_litri": numero(bar.arrotonda(b["spillato"])) if b["spillato"] is not None else None,
                 "differenza_litri": numero(b.get("differenza")), "differenza_percentuale": numero(b.get("percentuale"))}
                for b in bar_dati["bevande"]
            ],
            "fusti_da_pesare": len(bar_dati["da_pesare"]),
        },
    }


@strumento(
    "storico", "Storico del magazzino",
    "Le operazioni registrate nel magazzino della cucina (carichi, prelievi per la cucina, rientri, spostamenti, scarti, "
    "correzioni), dalla più recente, con le righe.",
    {"properties": {
        "tipo": {"type": "string", "enum": list(TipoOperazione.values), "description": "Solo questo tipo di operazione."},
        "serata": {"type": ["string", "integer"], "description": "Solo questa serata (id, data o «oggi»)."},
        "prodotto": {"type": ["string", "integer"], "description": "Solo le operazioni con questo prodotto."},
        "limite": {"type": "integer", "minimum": 1, "maximum": 200, "description": "Quante operazioni (30 se non indicato)."},
    }},
)
def storico(argomenti, contesto):
    qs = Operazione.objects.select_related("servizio", "utente").prefetch_related("movimenti__prodotto", "movimenti__ubicazione")
    if argomenti.get("tipo"):
        qs = qs.filter(tipo=argomenti["tipo"])
    if argomenti.get("serata") not in (None, ""):
        qs = qs.filter(servizio=trova_servizio(argomenti["serata"]))
    if argomenti.get("prodotto") not in (None, ""):
        qs = qs.filter(movimenti__prodotto=trova(Prodotto.objects.all(), argomenti["prodotto"], "il prodotto")).distinct()
    limite = min(max(int(argomenti.get("limite") or 30), 1), 200)
    return {"operazioni": [
        {"id": o.pk, "tipo": o.get_tipo_display(), "quando": quando(o.creata_il),
         "utente": o.utente.get_username() if o.utente else None, "servizio": str(o.servizio) if o.servizio else None,
         "note": o.note or None,
         "righe": [{"prodotto": m.prodotto.nome, "ubicazione": m.ubicazione.nome, "quantita": numero(m.quantita), "unita": m.prodotto.unita}
                   for m in o.movimenti.all()]}
        for o in qs.order_by("-creata_il", "-pk")[:limite]
    ]}


@strumento(
    "listino", "Listino della cassa",
    "Gli articoli in vendita: prezzo, categoria, reparto (cucina o bar), se sono esauriti e a quale bevanda del bar sono collegati.",
    {"properties": {"anche_non_attivi": {"type": "boolean"}}},
)
def listino(argomenti, contesto):
    qs = Articolo.objects.select_related("categoria", "bevanda").order_by("categoria__ordine", "categoria__nome", "ordine", "nome")
    if not argomenti.get("anche_non_attivi"):
        qs = qs.filter(attivo=True)
    return {"articoli": [
        {"id": a.pk, "nome": a.nome, "categoria": a.categoria.nome, "reparto": a.get_reparto_display(),
         "prezzo": euro(a.prezzo) if a.prezzo is not None else None, "attivo": a.attivo, "esaurito": a.esaurito,
         "bevanda": a.bevanda.nome if a.bevanda else None, "quantita_bevanda": numero(a.quantita_bevanda)}
        for a in qs
    ]}


@strumento(
    "statistiche_cassa", "Statistiche della cassa",
    "Incassi di ogni serata della sagra (contanti, carta, omaggi, ordini, coperti) e il totale.",
)
def statistiche_cassa(argomenti, contesto):
    dati = cassa_report.statistiche()
    return {
        "serate": [{"serata": str(r["servizio"]), "id": r["servizio"].pk, **_incassi(r)} for r in dati["serate"]],
        "totale": _incassi(dati["totale"]),
    }


# ---------------------------------------------------------------------------
# Scritture (solo con una chiave che può registrare)
# ---------------------------------------------------------------------------


@strumento(
    "registra_carico", "Registra un carico",
    "Registra merce arrivata (acquisto/consegna) o l'inventario iniziale in un'ubicazione del magazzino della cucina. "
    "Le quantità sono nell'unità del prodotto (vedi cerca_prodotti). Prima di registrare chiedi conferma all'utente.",
    {"properties": {
        "ubicazione": {"type": ["string", "integer"], "description": "Dove si mette la merce (id o nome)."},
        "tipo": {"type": "string", "enum": ["acquisto", "inventario"], "description": "acquisto = merce arrivata; inventario = quello che c'è già a inizio sagra."},
        "voci": {"type": "array", "minItems": 1, "items": {
            "type": "object", "additionalProperties": False, "required": ["prodotto", "quantita"],
            "properties": {"prodotto": {"type": ["string", "integer"]}, "quantita": {"type": ["number", "string"]}},
        }},
        "note": {"type": "string"},
        "id_registrazione": SCHEMA_ID_REGISTRAZIONE,
    }, "required": ["ubicazione", "tipo", "voci"]},
    scrive=True,
)
def registra_carico(argomenti, contesto):
    ubicazione = trova(Ubicazione.objects.filter(attiva=True), argomenti.get("ubicazione"), "l'ubicazione")
    tipo = {"acquisto": TipoOperazione.ACQUISTO, "inventario": TipoOperazione.INVENTARIO}.get(argomenti.get("tipo"))
    if tipo is None:
        raise ErroreStrumento("«tipo» deve essere «acquisto» o «inventario».")
    voci = []
    for voce in oggetti(argomenti.get("voci") or [], "voci"):
        p = trova(Prodotto.objects.filter(attivo=True), voce.get("prodotto"), "il prodotto")
        q = leggi_numero(voce.get("quantita"))
        if q <= ZERO:
            raise ErroreStrumento(f"La quantità di «{p.nome}» deve essere maggiore di zero.")
        voci.append((p, q))
    if not voci:
        raise ErroreStrumento("Indica almeno un prodotto con la quantità.")
    try:
        operazione, creata = magazzino.registra_carico(
            tipo=tipo, ubicazione=ubicazione, quantita=voci, utente=contesto.utente,
            note=(argomenti.get("note") or "Registrato da Claude").strip(), token=token_mcp(argomenti, "registra_carico", contesto),
        )
    except magazzino.ErroreMagazzino as e:
        raise ErroreStrumento(" ".join(e.messaggi)) from None
    return {
        "registrato": creata, "gia_registrato_prima": not creata, "operazione_id": operazione.pk, "tipo": operazione.get_tipo_display(), "ubicazione": ubicazione.nome,
        "righe": [{"prodotto": p.nome, "quantita": numero(q), "unita": p.unita} for p, q in voci],
    }


@strumento(
    "crea_prodotto", "Crea un prodotto",
    "Aggiunge un prodotto all'anagrafica del magazzino della cucina. Prima controlla con cerca_prodotti che non esista già.",
    {"properties": {
        "nome": {"type": "string", "maxLength": 100},
        "categoria": SCHEMA_CATEGORIA,
        "unita": {"type": "string", "enum": list(Unita.values), "description": "Unità di misura: " + ", ".join(f"{u.value} ({u.label})" for u in Unita)},
        "scorta_minima": {"type": ["number", "string"], "description": "Facoltativa."},
        "note": {"type": "string", "maxLength": 200},
    }, "required": ["nome", "categoria", "unita"]},
    scrive=True,
)
def crea_prodotto(argomenti, contesto):
    from magazzino.forms import ProdottoForm

    dati = {
        "nome": (argomenti.get("nome") or "").strip(), "categoria": argomenti.get("categoria"), "unita": argomenti.get("unita"),
        "scorta_minima": "" if argomenti.get("scorta_minima") in (None, "") else str(argomenti["scorta_minima"]),
        "note": argomenti.get("note") or "", "attivo": True,
    }
    form = ProdottoForm(dati)
    if not form.is_valid():
        errori = [f"{campo}: {' '.join(e)}" for campo, e in form.errors.items()]
        raise ErroreStrumento("Prodotto non creato. " + " ".join(errori))
    p = form.save()
    return {"creato": True, **scheda_prodotto(p, ZERO)}


def _voci_cucina(argomenti, *, scegli_ubicazione):
    """[(prodotto, ubicazione, quantità)] dalle voci; senza ubicazione si prende l'unica dove c'è il prodotto."""
    voci = []
    for voce in oggetti(argomenti.get("voci") or [], "voci"):
        p = trova(Prodotto.objects.all(), voce.get("prodotto"), "il prodotto")
        q = leggi_numero(voce.get("quantita"))
        if q <= ZERO:
            raise ErroreStrumento(f"La quantità di «{p.nome}» deve essere maggiore di zero.")
        if voce.get("ubicazione") not in (None, ""):
            u = trova(Ubicazione.objects.all(), voce["ubicazione"], "l'ubicazione")
        elif scegli_ubicazione:
            stock = magazzino.giacenze(prodotti=[p.pk])
            dove = [(Ubicazione.objects.get(pk=uid), qta) for (_, uid), qta in stock.items() if qta > ZERO]
            if not dove:
                raise ErroreStrumento(f"«{p.nome}» non c'è in nessuna ubicazione del magazzino.")
            if len(dove) > 1:
                elenco = ", ".join(f"{u.nome} ({p.formatta(qta)})" for u, qta in dove)
                raise ErroreStrumento(f"«{p.nome}» c'è in più posti: {elenco}. Indica da dove si prende.")
            u = dove[0][0]
        else:
            raise ErroreStrumento(f"Indica dove rimettere «{p.nome}» (ubicazione).")
        voci.append((p, u, q))
    if not voci:
        raise ErroreStrumento("Indica almeno un prodotto con la quantità.")
    return voci


SCHEMA_VOCI_CUCINA = {"type": "array", "minItems": 1, "items": {
    "type": "object", "additionalProperties": False, "required": ["prodotto", "quantita"],
    "properties": {
        "prodotto": {"type": ["string", "integer"]},
        "quantita": {"type": ["number", "string"], "description": "Nell'unità del prodotto (vedi cerca_prodotti)."},
        "ubicazione": {"type": ["string", "integer"], "description": "Frigo, congelatore o dispensa (id o nome)."},
    },
}}


def _esito_cucina(operazione, creata, voci):
    return {
        "registrato": creata, "gia_registrato_prima": not creata, "operazione_id": operazione.pk, "tipo": operazione.get_tipo_display(), "serata": str(operazione.servizio),
        "righe": [{"prodotto": p.nome, "ubicazione": u.nome, "quantita": numero(q), "unita": p.unita} for p, u, q in voci],
    }


@strumento(
    "preleva_per_cucina", "Metti a cucinare",
    "Registra la merce presa dal magazzino per la cucina in una serata (come «Metti a cucinare» nell'app). "
    "Se non indichi l'ubicazione si prende dall'unica dove c'è il prodotto (se è in più posti chiede quale). "
    "Non si può prendere più di quello che c'è. Prima di registrare chiedi conferma.",
    {"properties": {
        "serata": {"type": ["string", "integer"], "description": "Id, data (AAAA-MM-GG) oppure «oggi» (se manca: oggi)."},
        "voci": SCHEMA_VOCI_CUCINA,
        "note": {"type": "string"},
        "id_registrazione": SCHEMA_ID_REGISTRAZIONE,
    }, "required": ["voci"]},
    scrive=True,
)
def preleva_per_cucina(argomenti, contesto):
    s = trova_servizio(argomenti.get("serata"))
    voci = _voci_cucina(argomenti, scegli_ubicazione=True)
    try:
        operazione, creata = magazzino.registra_cucina(
            servizio=s, prelievi=voci, utente=contesto.utente, note=(argomenti.get("note") or "Registrato da Claude").strip(),
            token=token_mcp(argomenti, "preleva_per_cucina", contesto),
        )
    except magazzino.ErroreMagazzino as e:
        raise ErroreStrumento("Non ho registrato niente. " + " ".join(e.messaggi)) from None
    return _esito_cucina(operazione, creata, voci)


@strumento(
    "rientro_dalla_cucina", "Rientro dalla cucina",
    "Registra la merce non usata che torna dalla cucina in magazzino a fine serata, con dove la si rimette. "
    "Rientra al massimo quello che era stato messo a cucinare in quella serata. Prima di registrare chiedi conferma.",
    {"properties": {
        "serata": {"type": ["string", "integer"], "description": "Id, data (AAAA-MM-GG) oppure «oggi» (se manca: oggi)."},
        "voci": SCHEMA_VOCI_CUCINA,
        "note": {"type": "string"},
        "id_registrazione": SCHEMA_ID_REGISTRAZIONE,
    }, "required": ["voci"]},
    scrive=True,
)
def rientro_dalla_cucina(argomenti, contesto):
    s = trova_servizio(argomenti.get("serata"))
    voci = _voci_cucina(argomenti, scegli_ubicazione=False)
    try:
        operazione, creata = magazzino.registra_reso(
            servizio=s, rientri=voci, utente=contesto.utente, note=(argomenti.get("note") or "Registrato da Claude").strip(),
            token=token_mcp(argomenti, "rientro_dalla_cucina", contesto),
        )
    except magazzino.ErroreMagazzino as e:
        raise ErroreStrumento("Non ho registrato niente. " + " ".join(e.messaggi)) from None
    return _esito_cucina(operazione, creata, voci)


# Gli strumenti del bar (bevande e fusti) sono in un file a parte: importandolo si registrano.
from . import strumenti_bar  # noqa: E402, F401
