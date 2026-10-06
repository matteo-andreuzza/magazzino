"""Logica del magazzino del bar.

Giacenze
    Lattine e bottiglie: carichi e correzioni registrati qui MENO il
    venduto in cassa. Il venduto si legge dagli ordini (righe collegate a una
    bevanda, con la quantità copiata al momento della vendita), quindi ogni
    ordine toglie subito dal magazzino del bar e un ordine annullato ci rimette
    la merce. La giacenza può andare sotto zero (es. manca un carico): la cassa
    non deve mai rifiutare una vendita per un conteggio sbagliato, la pagina
    del bar lo segnala.

Fusti (birra e vino alla spina)
    I fusti pieni sono carichi e correzioni meno i fusti attaccati alle spine.
    Sull'app si fa quello che si fa al bancone, quando lo si fa: si attacca un
    fusto pieno a una spina, si sposta tra i consumati quello finito e a fine
    serata si pesa quello rimasto attaccato. Ogni operazione finisce nei fusti
    della serata (ContaFusti, creata alla prima operazione).

    I litri usciti da un fusto in una serata sono il calo di peso diviso per la
    densità della bevanda; un fusto nuovo parte dal peso da pieno (tara +
    litri), uno finito arriva alla tara. Litri e tara sono quelli del tipo di
    fusto, decisi dall'amministrazione: se li corregge, si correggono anche le
    serate passate. Finché sono provvisori i controlli sui pesi avvisano ma non
    bloccano (un peso vero non va mai rifiutato per una tara inventata).

    Si cambiano solo i fusti dell'ultima serata: i conti di ogni serata partono
    dal peso della serata prima, quindi le serate vanno in ordine. Anche da
    terminata la serata resta modificabile finché non si registrano i fusti di
    quella dopo: le pesate spesso si scrivono il giorno dopo.

    Oltre alle operazioni una per una (la pagina dei fusti) c'è
    registra_serata: si dice com'è andata la serata su ogni spina (quanti fusti
    sono finiti, che fusto è attaccato adesso, quanto pesa) e l'app la rifà da
    capo su quella spina. Ripeterla con gli stessi dati non cambia niente: è
    quella che usa Claude (server MCP), che può anche provarla senza salvare.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import groupby

from django.db import IntegrityError, transaction
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.utils import timezone

from cassa.models import RigaOrdine
from magazzino.models import Servizio
from magazzino.quantita import ZERO, formatta_numero

from .models import (
    DENSITA_BIRRA,
    Bevanda,
    ContaFusti,
    Fusto,
    MovimentoBar,
    OperazioneBar,
    Pesata,
    Spina,
    TipoBevanda,
    TipoFusto,
    TipoOperazioneBar,
)

LITRO = Decimal("0.1")
# Oltre il peso da pieno si accetta un po' di margine: i fusti non sono tutti uguali.
MARGINE_PIENO = Decimal("1.5")
MAX_PESO = Decimal("200")
# Fusti finiti in una serata su una spina: più di così è certamente un errore di battitura.
MAX_FINITI = 12


class ErroreBar(Exception):
    """Registrazione rifiutata: non è stato registrato niente.

    `errori` è una lista di coppie (chiave, messaggio); la chiave indica il
    campo del modulo a cui si riferisce (o None).
    """

    def __init__(self, errori):
        if isinstance(errori, str):
            errori = [(None, errori)]
        self.errori = list(errori)
        super().__init__("\n".join(self.messaggi))

    @property
    def messaggi(self):
        return [m for _, m in self.errori]


def litri(peso, densita=DENSITA_BIRRA):
    return peso / densita


def arrotonda(valore, passo=LITRO):
    return valore.quantize(passo)


# ---------------------------------------------------------------------------
# Venduto e giacenze
# ---------------------------------------------------------------------------

CONSUMO = ExpressionWrapper(F("quantita") * F("quantita_bevanda"), output_field=DecimalField(max_digits=14, decimal_places=3))


def _righe_vendute(servizi=None):
    qs = RigaOrdine.objects.filter(ordine__annullato_il__isnull=True, bevanda__isnull=False, quantita_bevanda__isnull=False)
    if servizi is not None:
        qs = qs.filter(ordine__servizio__in=list(servizi))
    return qs


def venduto(servizi=None):
    """{bevanda_id: quantità venduta} (lattine, bottiglie o litri), omaggi compresi."""
    return {
        r["bevanda_id"]: r["totale"]
        for r in _righe_vendute(servizi).values("bevanda_id").annotate(totale=Sum(CONSUMO)).order_by()
        if r["totale"]
    }


def venduto_per_servizio():
    """{servizio_id: {bevanda_id: quantità}}."""
    risultato = defaultdict(dict)
    for r in _righe_vendute().values("ordine__servizio_id", "bevanda_id").annotate(totale=Sum(CONSUMO)).order_by():
        if r["totale"]:
            risultato[r["ordine__servizio_id"]][r["bevanda_id"]] = r["totale"]
    return dict(risultato)


def venduto_articoli(servizio):
    """Per ogni bevanda, quanto venduto di ogni articolo: {bevanda_id: [(nome, pezzi, quantità)]}."""
    risultato = defaultdict(list)
    righe = (
        _righe_vendute([servizio])
        .values("bevanda_id", "articolo__nome")
        .annotate(pezzi=Sum("quantita"), totale=Sum(CONSUMO))
        .order_by("bevanda_id", "-totale", "articolo__nome")
    )
    for r in righe:
        risultato[r["bevanda_id"]].append((r["articolo__nome"], r["pezzi"], r["totale"]))
    return dict(risultato)


def caricato():
    """({bevanda_id: quantità}, {tipo_fusto_id: fusti}) da carichi e correzioni."""
    bevande, fusti = {}, {}
    for r in MovimentoBar.objects.values("bevanda_id", "tipo_fusto_id").annotate(totale=Sum("quantita")).order_by():
        if r["bevanda_id"]:
            bevande[r["bevanda_id"]] = r["totale"]
        else:
            fusti[r["tipo_fusto_id"]] = r["totale"]
    return bevande, fusti


def giacenze_bevande():
    """{bevanda_id: quantità in magazzino} per lattine, bottiglie e vino (può essere negativa)."""
    carichi, _ = caricato()
    venduti = venduto()
    ids = set(carichi) | set(venduti)
    a_fusti = set(Bevanda.objects.filter(pk__in=ids, tipo=TipoBevanda.FUSTI).values_list("pk", flat=True))
    return {pid: carichi.get(pid, ZERO) - venduti.get(pid, ZERO) for pid in ids if pid not in a_fusti}


def fusti_pieni():
    """{tipo_fusto_id: fusti pieni in magazzino} (può essere negativo)."""
    _, carichi = caricato()
    attaccati = dict(Fusto.objects.values("tipo_id").annotate(n=Count("pk")).values_list("tipo_id", "n").order_by())
    ids = set(carichi) | set(attaccati)
    return {tid: carichi.get(tid, ZERO) - attaccati.get(tid, 0) for tid in ids}


# ---------------------------------------------------------------------------
# Fusti attaccati e litri spillati
# ---------------------------------------------------------------------------


@dataclass
class StatoFusto:
    fusto: Fusto
    pesate: list = field(default_factory=list)

    @property
    def tipo(self):
        return self.fusto.tipo

    @property
    def bevanda(self):
        return self.fusto.tipo.bevanda

    @property
    def densita(self):
        return self.fusto.tipo.bevanda.densita

    @property
    def dati_provvisori(self):
        """Tara (o litri) del tipo di fusto ancora da decidere: i controlli avvisano invece di bloccare."""
        return self.tipo.tara is None or self.tipo.tara_provvisoria

    @property
    def finito(self):
        return self.fusto.finito_con_id is not None

    @property
    def tara(self):
        """Il peso da vuoto misurato quando è finito, altrimenti quello (di adesso) del tipo di fusto."""
        for p in self.pesate:
            if p.vuoto and p.misurata:
                return p.peso
        return self.tipo.tara

    def peso(self, pesata):
        """Peso di una pesata: un fusto finito senza pesarlo vale la tara."""
        if pesata.vuoto and not pesata.misurata:
            return self.tara
        return pesata.peso

    @property
    def peso_pieno(self):
        if self.tara is None:
            return None
        return self.tara + self.tipo.litri * self.densita

    def peso_prima(self, conta_id):
        """Il peso prima della serata indicata (quello da pieno se è stato attaccato lì)."""
        precedenti = [p for p in self.pesate if p.conta_id < conta_id]
        if precedenti:
            return self.peso(precedenti[-1])
        return self.peso_pieno

    def pesata_di(self, conta_id):
        return next((p for p in self.pesate if p.conta_id == conta_id), None)

    @property
    def ultima_pesata(self):
        return self.pesate[-1] if self.pesate else None

    @property
    def ultimo_peso(self):
        return self.peso(self.pesate[-1]) if self.pesate else self.peso_pieno

    @property
    def litri_rimasti(self):
        if self.finito:
            return ZERO
        if self.ultimo_peso is None or self.tara is None:
            return None
        return min(max(litri(self.ultimo_peso - self.tara, self.densita), ZERO), self.tipo.litri)

    @property
    def riempimento(self):
        """Da 0 a 1, per il disegno: quanta birra c'è ancora nel fusto."""
        rimasti = self.litri_rimasti
        if rimasti is None:
            return 1.0
        return float(rimasti / self.tipo.litri)

    def litri_usciti(self, pesata):
        prima, dopo = self.peso_prima(pesata.conta_id), self.peso(pesata)
        if prima is None or dopo is None:
            return None
        return litri(prima - dopo, self.densita)

    @property
    def litri_spillati(self):
        """Tutta la birra uscita dal fusto finora."""
        usciti = [self.litri_usciti(p) for p in self.pesate]
        return sum((u for u in usciti if u is not None), ZERO)


def stati_fusti(fusti=None):
    """[StatoFusto] con le pesate in ordine di serata."""
    if fusti is None:
        fusti = Fusto.objects.all()
    fusti = list(fusti.select_related("tipo__bevanda", "spina__spinatrice", "aperto_con__servizio", "finito_con__servizio"))
    pesate = defaultdict(list)
    for p in Pesata.objects.filter(fusto__in=fusti).order_by("conta_id"):
        pesate[p.fusto_id].append(p)
    return [StatoFusto(fusto=f, pesate=pesate[f.pk]) for f in fusti]


def fusti_attaccati():
    return stati_fusti(
        Fusto.objects.filter(finito_con__isnull=True).order_by("spina__spinatrice__ordine", "spina__spinatrice_id", "spina__ordine", "spina_id", "pk")
    )


def fusti_consumati():
    """I fusti finiti da inizio sagra, nell'ordine in cui sono finiti."""
    return stati_fusti(Fusto.objects.filter(finito_con__isnull=False).order_by("finito_con_id", "finito_il", "pk"))


def spine_in_uso():
    """Le spine da disegnare: quelle attive (di spinatrici attive) e quelle con un fusto attaccato."""
    attive = Q(attiva=True, spinatrice__attiva=True)
    return list(
        Spina.objects.filter(attive | Q(fusti__finito_con__isnull=True)).distinct().select_related("spinatrice", "bevanda")
    )


def spinatrici_in_uso(attaccati=None):
    """[(spinatrice, [(spina, StatoFusto attaccato o None)])] da disegnare, nell'ordine del bancone."""
    if attaccati is None:
        attaccati = fusti_attaccati()
    per_spina = {s.fusto.spina_id: s for s in attaccati}
    spine = spine_in_uso()
    return [
        (spinatrice, [(sp, per_spina.get(sp.pk)) for sp in gruppo])
        for spinatrice, gruppo in groupby(spine, key=lambda sp: sp.spinatrice)
    ]


@dataclass
class RigaConta:
    stato: StatoFusto
    pesata: Pesata
    nuovo: bool  # attaccato in questa serata
    prima: Decimal | None  # peso prima della serata
    litri: Decimal | None

    @property
    def finito(self):
        return self.pesata.vuoto

    @property
    def peso(self):
        return self.stato.peso(self.pesata)


def dettaglio_conta(conta):
    """Righe (una per fusto pesato o finito nella serata) e litri spillati per bevanda."""
    toccati = Fusto.objects.filter(pesate__conta=conta).distinct().order_by("pk")
    stati = {s.fusto.pk: s for s in stati_fusti(toccati)}
    righe = []
    per_bevanda = defaultdict(lambda: ZERO)
    for p in conta.pesate.all().order_by("pk"):
        s = stati[p.fusto_id]
        usciti = s.litri_usciti(p)
        righe.append(RigaConta(
            stato=s, pesata=p, nuovo=s.fusto.aperto_con_id == conta.pk, prima=s.peso_prima(p.conta_id), litri=usciti,
        ))
        if usciti is not None:
            per_bevanda[s.tipo.bevanda_id] += usciti
    righe.sort(key=lambda r: (
        r.stato.fusto.spina.spinatrice.ordine if r.stato.fusto.spina else 0, r.stato.fusto.spina_id or 0,
        r.stato.fusto.attaccato_il, r.stato.fusto.pk,
    ))
    return righe, dict(per_bevanda)


def da_pesare(servizio, attaccati=None):
    """I fusti attaccati non ancora pesati nella serata (a fine serata vanno pesati tutti)."""
    if attaccati is None:
        attaccati = fusti_attaccati()
    conta_id = ContaFusti.objects.filter(servizio=servizio).values_list("pk", flat=True).first()
    return [s for s in attaccati if conta_id is None or s.pesata_di(conta_id) is None]


def spillato_per_servizio():
    """{servizio_id: {bevanda_id: litri usciti dai fusti}} dai fusti pesati e finiti."""
    stati = stati_fusti()
    servizio_di = dict(ContaFusti.objects.values_list("pk", "servizio_id"))
    risultato = defaultdict(lambda: defaultdict(lambda: ZERO))
    for s in stati:
        for p in s.pesate:
            usciti = s.litri_usciti(p)
            if usciti is not None:
                risultato[servizio_di[p.conta_id]][s.tipo.bevanda_id] += usciti
    return {sid: dict(v) for sid, v in risultato.items()}


def ultima_conta():
    return ContaFusti.objects.order_by("-pk").first()


def _dopo(servizio):
    """Le serate che vengono dopo questa (il pranzo viene prima della sera dello stesso giorno)."""
    return Q(servizio__data__gt=servizio.data) | Q(servizio__data=servizio.data, servizio__tipo__gt=servizio.tipo)


def motivo_sola_lettura(servizio):
    """Perché sui fusti di questa serata non si possono fare operazioni (None se si può).

    Anche una serata terminata si può ancora sistemare (le pesate si scrivono
    spesso il giorno dopo), finché non si registrano i fusti di una serata dopo.
    """
    conta = ContaFusti.objects.filter(servizio=servizio).first()
    if conta is not None:
        dopo = ContaFusti.objects.filter(pk__gt=conta.pk).select_related("servizio").order_by("pk").first()
        if dopo is not None:
            return (f"Dopo questa serata ci sono già i fusti di {dopo.servizio}: si modifica solo l'ultima. "
                    "Per correggere questa, annulla prima i fusti delle serate dopo (dal loro Storico) e rifalli.")
        return None
    dopo = ContaFusti.objects.filter(_dopo(servizio)).select_related("servizio").order_by("-pk").first()
    if dopo is not None:
        return (f"Ci sono già i fusti di una serata più recente ({dopo.servizio}): si modifica solo quella. "
                "Per registrare prima questa, annulla i fusti di quella serata e rifalli dopo.")
    return None


def serate_scoperte(servizio):
    """Le serate prima di questa, dopo gli ultimi fusti registrati, in cui si è venduto alla spina senza registrare i fusti.

    Se a fine serata non si registra niente, la birra (o il vino) di quella sera
    finisce nei conti della serata dopo: va detto, perché i conti per serata
    non tornano.
    """
    precedente = ContaFusti.objects.filter(~_dopo(servizio)).exclude(servizio=servizio).select_related("servizio")
    precedente = max(precedente, key=lambda c: (c.servizio.data, c.servizio.tipo), default=None)
    prima = Servizio.objects.filter(Q(data__lt=servizio.data) | Q(data=servizio.data, tipo__lt=servizio.tipo))
    if precedente is not None:
        s = precedente.servizio
        prima = prima.filter(Q(data__gt=s.data) | Q(data=s.data, tipo__gt=s.tipo))
    con_spina = set(
        RigaOrdine.objects.filter(ordine__servizio__in=prima, ordine__annullato_il__isnull=True, bevanda__tipo=TipoBevanda.FUSTI)
        .values_list("ordine__servizio_id", flat=True)
    )
    return [s for s in prima.order_by("data", "tipo") if s.pk in con_spina]


# ---------------------------------------------------------------------------
# Riepiloghi
# ---------------------------------------------------------------------------


def riepilogo_servizio(servizio):
    """Venduto (e per la birra spillato) di ogni bevanda nella serata, per report e pagine del bar."""
    venduti = venduto([servizio])
    conta = ContaFusti.objects.filter(servizio=servizio).first()
    spillati = dettaglio_conta(conta)[1] if conta else {}
    # Solo per l'ultima serata: per quelle prima i fusti attaccati adesso non c'entrano.
    pesare = da_pesare(servizio) if motivo_sola_lettura(servizio) is None else []
    per_articolo = venduto_articoli(servizio)
    ids = set(venduti) | set(spillati)
    bevande = []
    for b in Bevanda.objects.filter(pk__in=ids):
        riga = {
            "bevanda": b,
            "venduto": venduti.get(b.pk, ZERO),
            "articoli": per_articolo.get(b.pk, []),
            "spillato": spillati.get(b.pk) if b.a_fusti and conta else None,
        }
        if riga["spillato"] is not None:
            riga.update(confronto_birra(riga["spillato"], riga["venduto"]))
        bevande.append(riga)
    return {
        "bevande": bevande,
        "conta": conta,
        "da_pesare": pesare,
        "servono_fusti": any(b["bevanda"].a_fusti for b in bevande),
    }


def scorte_alla_spina(escludi=()):
    """Per ogni bevanda alla spina: quanta ce n'è ancora e per quante serate basta, per sapere quando ordinare.

    I litri sono quelli dei fusti pieni in magazzino più quelli rimasti nei
    fusti attaccati; la media è quella uscita dai fusti nelle serate già
    registrate (tranne quelle in `escludi`, es. la serata in corso, non
    ancora pesata).
    """
    pieni = fusti_pieni()
    attaccati = fusti_attaccati()
    escludi = {s.pk for s in escludi}
    spillati = [v for sid, v in spillato_per_servizio().items() if sid not in escludi]
    righe = []
    for b in Bevanda.objects.filter(tipo=TipoBevanda.FUSTI).prefetch_related("tipi_fusto"):
        n_pieni = sum((max(pieni.get(t.pk, ZERO), ZERO) for t in b.tipi_fusto.all()), ZERO)
        nei_pieni = sum((max(pieni.get(t.pk, ZERO), ZERO) * t.litri for t in b.tipi_fusto.all()), ZERO)
        suoi = [s for s in attaccati if s.bevanda.pk == b.pk]
        attaccato = sum((s.litri_rimasti or ZERO for s in suoi), ZERO)
        serate = [v[b.pk] for v in spillati if v.get(b.pk)]
        media = sum(serate, ZERO) / len(serate) if serate else None
        if not (n_pieni or suoi or serate):
            continue
        totale = nei_pieni + attaccato
        righe.append({
            "bevanda": b, "fusti_pieni": n_pieni, "litri_nei_pieni": nei_pieni, "litri_attaccati": attaccato,
            "litri": totale, "media": media, "serate": len(serate),
            "autonomia": (totale / media).quantize(Decimal("0.1")) if media else None,
        })
    return righe


def confronto_birra(spillato, venduto):
    """Differenza tra birra uscita dai fusti e venduta, calcolata sui valori mostrati (al decilitro)."""
    spillato, venduto = arrotonda(spillato), arrotonda(venduto)
    differenza = spillato - venduto
    return {
        "differenza": differenza,
        "percentuale": (differenza / spillato * 100).quantize(Decimal("1")) if spillato else None,
    }


def consumi_per_serata():
    """Tabella delle serate: per ogni bevanda il venduto (e per la birra lo spillato).

    Restituisce (colonne, righe, totali): colonne = [(bevanda, "venduto" o "spillato")],
    righe = [(servizio, [valore o None per colonna])], totali = [valore per colonna].
    """
    venduti = venduto_per_servizio()
    spillati = spillato_per_servizio()
    ids_servizi = set(venduti) | set(spillati)
    if not ids_servizi:
        return [], [], []
    ids_bevande = {b for v in venduti.values() for b in v} | {b for v in spillati.values() for b in v}
    colonne = []
    for b in Bevanda.objects.filter(pk__in=ids_bevande):
        colonne.append((b, "venduto"))
        if b.a_fusti:
            colonne.append((b, "spillato"))
    righe = []
    for s in Servizio.objects.filter(pk__in=ids_servizi).order_by("data", "tipo", "nome"):
        valori = [(venduti if cosa == "venduto" else spillati).get(s.pk, {}).get(b.pk) for b, cosa in colonne]
        righe.append((s, valori))
    totali = [sum((valori[i] or ZERO for _, valori in righe), ZERO) for i in range(len(colonne))]
    return colonne, righe, totali


# ---------------------------------------------------------------------------
# Carichi e correzioni
# ---------------------------------------------------------------------------


def _chiave(cosa):
    return f"f{cosa.pk}" if isinstance(cosa, TipoFusto) else f"b{cosa.pk}"


def _controlla_quantita(voci):
    errori = []
    for cosa, q in voci:
        if isinstance(cosa, TipoFusto) and q != q.to_integral_value():
            errori.append((_chiave(cosa), "I fusti si contano interi."))
        elif isinstance(cosa, Bevanda) and cosa.a_fusti:
            raise ValueError("La birra alla spina si carica in fusti.")
    if errori:
        raise ErroreBar(errori)


def _registra(tipo, calcola, *, utente, note, token):
    if token is not None:
        esistente = OperazioneBar.objects.filter(token=token).first()
        if esistente is not None:
            return esistente, False
    try:
        with transaction.atomic():
            operazione = OperazioneBar.objects.create(tipo=tipo, utente=utente, note=(note or "").strip()[:250], token=token)
            movimenti = [
                MovimentoBar(
                    operazione=operazione, quantita=q,
                    bevanda=cosa if isinstance(cosa, Bevanda) else None,
                    tipo_fusto=cosa if isinstance(cosa, TipoFusto) else None,
                )
                for cosa, q in calcola()
                if q != ZERO
            ]
            if not movimenti:
                raise ErroreBar("Non hai inserito nessuna quantità.")
            MovimentoBar.objects.bulk_create(movimenti)
    except IntegrityError:
        if token is not None:
            esistente = OperazioneBar.objects.filter(token=token).first()
            if esistente is not None:
                return esistente, False
        raise
    return operazione, True


def registra_carico(*, tipo, voci, utente, note="", token=None):
    """voci: [(Bevanda o TipoFusto, quantità > 0)]."""
    if tipo not in (TipoOperazioneBar.INVENTARIO, TipoOperazioneBar.CARICO):
        raise ValueError("Tipo di carico non valido.")
    voci = list(voci)
    if any(q is None or q <= ZERO for _, q in voci):
        raise ValueError("Le quantità devono essere positive.")
    _controlla_quantita(voci)
    return _registra(tipo, lambda: voci, utente=utente, note=note, token=token)


def registra_rettifica(*, conteggi, utente, note="", token=None):
    """conteggi: [(Bevanda o TipoFusto, quantità contata >= 0)]: registra la differenza con la giacenza di adesso."""
    conteggi = list(conteggi)
    if any(q is None or q < ZERO for _, q in conteggi):
        raise ValueError("Le quantità contate non possono essere negative.")
    _controlla_quantita(conteggi)

    def calcola():
        bevande, pieni = giacenze_bevande(), fusti_pieni()
        differenze = []
        for cosa, contato in conteggi:
            attuale = pieni.get(cosa.pk, ZERO) if isinstance(cosa, TipoFusto) else bevande.get(cosa.pk, ZERO)
            differenze.append((cosa, contato - attuale))
        if all(d == ZERO for _, d in differenze):
            raise ErroreBar("Le quantità contate coincidono già con le giacenze: non c'è niente da correggere.")
        return differenze

    return _registra(TipoOperazioneBar.RETTIFICA, calcola, utente=utente, note=note, token=token)


def annulla_operazione(operazione):
    """Elimina un carico o una correzione. Restituisce False se era già stata annullata."""
    return OperazioneBar.objects.filter(pk=operazione.pk).delete()[0] > 0




# ---------------------------------------------------------------------------
# Operazioni sui fusti (le spine del bancone)
# ---------------------------------------------------------------------------


def formatta_kg(valore):
    return f"{valore.normalize():f}".replace(".", ",") + " kg"


def formatta_litri(valore):
    return f"{arrotonda(valore):f}".replace(".", ",") + " l"


def _serata(servizio, utente):
    """Blocca la serata e restituisce i suoi fusti (ContaFusti), creandoli alla prima operazione.

    Si blocca sempre prima la serata, poi le spine (in ordine di id), poi i
    fusti: stesso ordine ovunque, così due telefoni (o un telefono e Claude)
    non si aspettano a vicenda all'infinito.
    """
    s = Servizio.objects.select_for_update(no_key=True).filter(pk=servizio.pk).first()
    if s is None:
        raise ErroreBar("Questa serata non esiste più.")
    motivo = motivo_sola_lettura(s)
    if motivo:
        raise ErroreBar(motivo)
    conta = ContaFusti.objects.filter(servizio=s).first()
    if conta is None:
        conta = ContaFusti.objects.create(servizio=s, utente=utente)
    return conta


def _spina(spina_id):
    sp = Spina.objects.select_for_update(of=("self",)).select_related("spinatrice", "bevanda").filter(pk=spina_id).first()
    if sp is None:
        raise ErroreBar("Questa spina non esiste più: ricarica la pagina.")
    return sp


def _fusto(fusto_id):
    f = Fusto.objects.select_for_update(of=("self",)).select_related("tipo__bevanda", "spina").filter(pk=fusto_id).first()
    if f is None:
        raise ErroreBar("Questo fusto non c'è più (forse qualcuno l'ha appena tolto): ricarica la pagina.")
    return f


def _stato(fusto):
    return stati_fusti(Fusto.objects.filter(pk=fusto.pk))[0]


def _attaccato_a(sp):
    return Fusto.objects.select_for_update(of=("self",)).select_related("tipo__bevanda").filter(spina=sp, finito_con__isnull=True).first()


def _compatibile(sp, tipo):
    """A una spina che dà una bevanda si attaccano solo i fusti di quella bevanda (niente rossa sulla spina della bionda)."""
    if sp.bevanda_id is not None and tipo.bevanda_id != sp.bevanda_id:
        raise ErroreBar(f"Alla spina «{sp.nome}» va {sp.bevanda.nome}, questo è un fusto di {tipo.bevanda.nome}. "
                        "Se adesso da quella spina esce un'altra bevanda, cambiala in Bevande e fusti.")


def _attacca(conta, sp, tipo, adesso):
    t = TipoFusto.objects.select_related("bevanda").filter(pk=tipo.pk).first()
    if t is None:
        raise ErroreBar("Questo tipo di fusto non esiste più: ricarica la pagina.")
    _compatibile(sp, t)
    return Fusto.objects.create(tipo=t, spina=sp, aperto_con=conta, attaccato_il=adesso)


def _finisci(conta, f, utente, adesso):
    f.finito_con, f.finito_il = conta, adesso
    f.save(update_fields=["finito_con", "finito_il"])
    # Da vuoto vale la tara del tipo di fusto (anche quella decisa dopo).
    Pesata.objects.update_or_create(
        conta=conta, fusto=f,
        defaults={"peso": f.tipo.tara or ZERO, "vuoto": True, "misurata": False, "registrata_il": adesso, "utente": utente},
    )


def _salva_pesata(conta, f, peso, utente):
    pesata, _ = Pesata.objects.update_or_create(
        conta=conta, fusto=f,
        defaults={"peso": peso, "vuoto": False, "misurata": True, "registrata_il": timezone.now(), "utente": utente},
    )
    return pesata


def _pulisci(conta):
    """Se dopo un annullamento la serata è rimasta senza fusti, si toglie anche la sua conta."""
    if not (conta.pesate.exists() or conta.fusti_aperti.exists() or conta.fusti_finiti.exists()):
        conta.delete()


def controlla_peso(stato, conta_id, peso):
    """Controlla il peso di fine serata di un fusto attaccato: solleva ErroreBar o restituisce gli avvisi.

    Pesare più di prima è sempre un errore (o il fusto è stato cambiato o il
    peso è sbagliato). I controlli che dipendono da litri e tara del tipo di
    fusto bloccano solo quando sono quelli veri: con quelli provvisori un peso
    misurato davvero non va rifiutato, si avvisa e basta.
    """
    if peso is None or not ZERO < peso < MAX_PESO:
        raise ErroreBar("Scrivi il peso in chili, per esempio 18,4.")
    avvisi = []
    provvisori = stato.dati_provvisori
    nome = stato.tipo.nome
    da_pieno = not any(p.conta_id < conta_id for p in stato.pesate)
    prima = stato.peso_prima(conta_id)
    if da_pieno and prima is not None and peso > prima + MARGINE_PIENO:
        testo = f"Pesa più di un fusto pieno (un «{nome}» pieno pesa circa {formatta_kg(prima.quantize(LITRO))})"
        if not provvisori:
            raise ErroreBar(f"{testo}: controlla il peso, o i litri e la tara del tipo di fusto.")
        avvisi.append(f"{testo} secondo litri e tara provvisori: si sistema quando l'amministrazione scrive quelli veri.")
    if not da_pieno and prima is not None and peso > prima:
        raise ErroreBar(f"Pesa più dell'ultima volta ({formatta_kg(prima)}): controlla il peso. Se stasera non è stato usato "
                        "scrivi lo stesso peso; se invece è stato cambiato, prima sposta tra i finiti quello finito.")
    if stato.tara is not None and peso < stato.tara:
        if not provvisori:
            raise ErroreBar(f"Pesa meno di un fusto vuoto (un «{nome}» vuoto pesa {formatta_kg(stato.tara)}): se è finito "
                            "spostalo tra i finiti.")
        avvisi.append(f"Pesa meno della tara provvisoria (un «{nome}» vuoto: {formatta_kg(stato.tara)}): finché non si "
                      "scrive quella vera risulta vuoto.")
    return avvisi


def attacca(*, servizio, spina, tipo, utente):
    """Un fusto pieno del tipo indicato va attaccato alla spina (che deve essere libera)."""
    with transaction.atomic():
        conta = _serata(servizio, utente)
        sp = _spina(spina.pk)
        if _attaccato_a(sp) is not None:
            raise ErroreBar(f"C'è già un fusto attaccato a «{sp.nome}»: prima spostalo tra i finiti.")
        return _attacca(conta, sp, tipo, timezone.now())


def cambia(*, servizio, spina, tipo, attuale, utente):
    """Cambio del fusto: quello attaccato (`attuale`) è finito e se ne attacca uno nuovo."""
    with transaction.atomic():
        conta = _serata(servizio, utente)
        sp = _spina(spina.pk)
        attaccato = _attaccato_a(sp)
        if attaccato is None or attaccato.pk != attuale.pk:
            raise ErroreBar("Nel frattempo il fusto attaccato è cambiato: ricarica la pagina e guarda di nuovo.")
        _compatibile(sp, tipo)
        adesso = timezone.now()
        _finisci(conta, _fusto(attaccato.pk), utente, adesso)
        return _attacca(conta, sp, tipo, adesso)


def finisci(*, servizio, fusto, utente):
    """Il fusto attaccato è finito: va tra i consumati."""
    with transaction.atomic():
        conta = _serata(servizio, utente)
        f = _fusto(fusto.pk)
        if f.finito_con_id is not None:
            raise ErroreBar("Questo fusto è già tra i finiti.")
        _finisci(conta, f, utente, timezone.now())
        return f


def pesa(*, servizio, fusto, peso, utente):
    """Peso del fusto attaccato (a fine serata). Pesandolo di nuovo si corregge.

    La pesata restituita ha in `avvisi` quello che non torna ma non blocca.
    """
    with transaction.atomic():
        conta = _serata(servizio, utente)
        f = _fusto(fusto.pk)
        if f.finito_con_id is not None:
            raise ErroreBar("Questo fusto è tra i finiti: non c'è da pesarlo.")
        avvisi = controlla_peso(_stato(f), conta.pk, peso)
        pesata = _salva_pesata(conta, f, peso, utente)
        pesata.avvisi = avvisi
        return pesata


def pesa_tutti(*, servizio, pesi, utente):
    """La pesata di fine serata di più fusti insieme: o tutte o nessuna.

    pesi: [(fusto, peso)]. Restituisce (pesate, avvisi); gli errori hanno come
    chiave l'id del fusto.
    """
    with transaction.atomic():
        conta = _serata(servizio, utente)
        errori, avvisi, pesate = [], [], []
        for fusto, peso in sorted(pesi, key=lambda voce: voce[0].pk):
            f = _fusto(fusto.pk)
            dove = f.spina.nome if f.spina else f.tipo.nome
            if f.finito_con_id is not None:
                errori.append((f.pk, f"{dove}: questo fusto è tra i finiti."))
                continue
            try:
                avvisi += [f"{dove}: {a}" for a in controlla_peso(_stato(f), conta.pk, peso)]
            except ErroreBar as e:
                errori += [(f.pk, f"{dove}: {m}") for m in e.messaggi]
                continue
            pesate.append(_salva_pesata(conta, f, peso, utente))
        if errori:
            raise ErroreBar(errori)
        if not pesate:
            raise ErroreBar("Non hai scritto nessun peso.")
        return pesate, avvisi


def togli_pesata(*, servizio, fusto):
    """Toglie la pesata di stasera del fusto attaccato. Restituisce False se non c'era."""
    with transaction.atomic():
        conta = _serata(servizio, None)
        f = _fusto(fusto.pk)
        tolte = Pesata.objects.filter(conta=conta, fusto=f, vuoto=False).delete()[0]
        _pulisci(conta)
        return tolte > 0


def stacca(*, servizio, fusto):
    """Attaccato per sbaglio: il fusto torna tra i pieni, come se non fosse mai stato attaccato."""
    with transaction.atomic():
        conta = _serata(servizio, None)
        f = _fusto(fusto.pk)
        if f.finito_con_id is not None:
            raise ErroreBar("Questo fusto è tra i finiti: per rimetterlo alla spina trascinalo su una spina libera.")
        if f.aperto_con_id != conta.pk:
            raise ErroreBar("È attaccato da una serata prima, quindi è già stato usato: non torna tra i pieni. "
                            "Se è finito spostalo tra i finiti.")
        f.delete()
        _pulisci(conta)


def riattacca(*, servizio, fusto, spina=None):
    """Spostato tra i consumati per sbaglio: il fusto torna attaccato (alla sua spina o a quella indicata)."""
    with transaction.atomic():
        conta = _serata(servizio, None)
        spina_id = spina.pk if spina is not None else Fusto.objects.filter(pk=fusto.pk).values_list("spina_id", flat=True).first()
        if spina_id is None:
            raise ErroreBar("Scegli la spina a cui riattaccarlo.")
        sp = _spina(spina_id)
        f = _fusto(fusto.pk)
        if f.finito_con_id is None:
            raise ErroreBar("Questo fusto è già attaccato.")
        if f.finito_con_id != conta.pk:
            raise ErroreBar("È finito in una serata prima: non si può più riattaccare.")
        # Attaccato da una serata prima: torna alla sua spina, così «rifai da capo» lo ritrova com'era.
        if f.aperto_con_id != conta.pk and f.spina_id is not None and sp.pk != f.spina_id:
            raise ErroreBar(f"Era attaccato a «{f.spina.nome}» da una serata prima: si riattacca lì.")
        _compatibile(sp, f.tipo)
        altro = _attaccato_a(sp)
        if altro is not None:
            raise ErroreBar(f"C'è già un altro fusto attaccato a «{sp.nome}»: prima spostalo tra i finiti.")
        f.finito_con, f.finito_il, f.spina = None, None, sp
        f.save(update_fields=["finito_con", "finito_il", "spina"])
        Pesata.objects.filter(conta=conta, fusto=f, vuoto=True).delete()
        _pulisci(conta)
        return f


def _azzera_spina(conta, sp):
    """Riporta la spina com'era a inizio serata, togliendo quello registrato stasera.

    I fusti attaccati stasera tornano tra i pieni (si cancellano), quello che
    c'era a inizio serata torna attaccato, le pesate di stasera spariscono.
    """
    Fusto.objects.filter(aperto_con=conta, spina=sp).delete()
    finito = Fusto.objects.select_for_update(of=("self",)).filter(finito_con=conta, spina=sp).order_by("pk").first()
    if finito is not None:
        if _attaccato_a(sp) is not None:
            raise ErroreBar(f"A «{sp.nome}» stasera è stato riattaccato il fusto di un'altra spina: sistemala dalla pagina dei fusti.")
        finito.finito_con, finito.finito_il = None, None
        finito.save(update_fields=["finito_con", "finito_il"])
    Pesata.objects.filter(conta=conta, fusto__spina=sp).delete()


def azzera(*, servizio, spine=None):
    """Annulla tutto quello fatto sui fusti nella serata (o solo su alcune spine).

    Serve per rifare una serata registrata male, o per registrare prima una
    serata dimenticata: si può solo sull'ultima serata con i fusti.
    Restituisce i nomi delle spine rimesse com'erano.
    """
    with transaction.atomic():
        conta = _serata(servizio, None)
        if spine is None:
            ids = set(conta.fusti_aperti.values_list("spina_id", flat=True))
            ids |= set(conta.fusti_finiti.values_list("spina_id", flat=True))
            ids |= set(Fusto.objects.filter(pesate__conta=conta).values_list("spina_id", flat=True))
        else:
            ids = {sp.pk for sp in spine}
        nomi = []
        for sid in sorted(i for i in ids if i is not None):
            sp = _spina(sid)
            _azzera_spina(conta, sp)
            nomi.append(sp.nome)
        if spine is None:
            Fusto.objects.filter(aperto_con=conta).delete()
            Pesata.objects.filter(conta=conta).delete()
        _pulisci(conta)
        return nomi


@dataclass
class VoceSerata:
    """Com'è andata la serata su una spina, detto come lo si dice a fine serata.

    finiti: quanti fusti sono finiti stasera su questa spina. Il primo è quello
        attaccato a inizio serata (se c'era), gli altri sono stati attaccati e
        finiti durante la serata. 0 = non cambiato.
    tipo: il tipo del fusto attaccato adesso, se è nuovo (se non si dice: lo
        stesso di prima, o l'unico tipo della bevanda).
    tipi_finiti: i tipi dei fusti attaccati e finiti durante la serata, se non
        sono come `tipo`.
    peso: quanto pesa a fine serata il fusto attaccato.
    vuota: a fine serata alla spina non è attaccato niente.
    """

    spina: Spina
    finiti: int = 0
    tipo: TipoFusto | None = None
    tipi_finiti: list = field(default_factory=list)
    peso: Decimal | None = None
    vuota: bool = False


def _tipo_unico(sp, cosa="il fusto nuovo"):
    tipi = TipoFusto.objects.filter(attivo=True).select_related("bevanda")
    if sp.bevanda_id is not None:
        tipi = tipi.filter(bevanda_id=sp.bevanda_id)
    tipi = list(tipi)
    if len(tipi) == 1:
        return tipi[0]
    if not tipi:
        raise ErroreBar(f"non c'è nessun tipo di fusto{f' per {sp.bevanda.nome}' if sp.bevanda else ''}: aggiungilo in Bevande e fusti.")
    elenco = ", ".join(f"«{t.nome}»" for t in tipi)
    raise ErroreBar(f"di che tipo è {cosa}? Ci sono: {elenco}.")


def _rifai_spina(conta, sp, v, utente, adesso):
    """Una voce di registra_serata: restituisce gli avvisi."""
    if not isinstance(v.finiti, int) or not 0 <= v.finiti <= MAX_FINITI:
        raise ErroreBar(f"i fusti finiti devono essere da 0 a {MAX_FINITI}.")
    for t in [v.tipo, *v.tipi_finiti]:
        if t is not None:
            _compatibile(sp, t)
    _azzera_spina(conta, sp)
    iniziale = _attaccato_a(sp)
    avvisi = []
    if v.finiti == 0:
        if v.tipi_finiti:
            raise ErroreBar("hai indicato i tipi dei fusti finiti stasera, ma nessun fusto finito.")
        if iniziale is not None:
            dal = iniziale.aperto_con.servizio.data
            if v.vuota:
                raise ErroreBar(f"c'è attaccato un {iniziale.tipo.nome.lower()} (dal {dal:%d/%m}): se a fine serata la spina "
                                "è vuota quel fusto è finito, indica quanti fusti sono finiti stasera.")
            if v.tipo is not None and v.tipo.pk != iniziale.tipo_id:
                raise ErroreBar(f"è attaccato un {iniziale.tipo.nome.lower()} (dal {dal:%d/%m}), non un {v.tipo.nome.lower()}: "
                                "se è stato cambiato indica quanti fusti sono finiti stasera (almeno 1).")
            attaccato = iniziale
        elif v.vuota:
            return avvisi
        else:
            attaccato = _attacca(conta, sp, v.tipo or _tipo_unico(sp), adesso)
    else:
        intermedi = v.finiti - (1 if iniziale is not None else 0)
        if v.tipi_finiti and len(v.tipi_finiti) != intermedi:
            raise ErroreBar(f"i tipi dei fusti attaccati e finiti durante la serata devono essere {intermedi}, "
                            f"ne hai indicati {len(v.tipi_finiti)}.")
        tipo = v.tipo or (iniziale.tipo if iniziale is not None else None)
        if tipo is None and (intermedi > len(v.tipi_finiti) or not v.vuota):
            tipo = _tipo_unico(sp)
        if iniziale is not None:
            _finisci(conta, _fusto(iniziale.pk), utente, adesso)
        for t in v.tipi_finiti or [tipo] * intermedi:
            _finisci(conta, _attacca(conta, sp, t, adesso), utente, adesso)
        attaccato = None if v.vuota else _attacca(conta, sp, tipo, adesso)
    if v.peso is not None:
        if attaccato is None:
            raise ErroreBar("a fine serata la spina è vuota: non c'è niente da pesare.")
        avvisi += controlla_peso(_stato(attaccato), conta.pk, v.peso)
        _salva_pesata(conta, attaccato, v.peso, utente)
    elif attaccato is not None:
        avvisi.append(f"manca il peso del fusto attaccato ({attaccato.tipo.nome}): senza, i litri usciti stasera non si sanno.")
    return avvisi


def registra_serata(*, servizio, voci, utente, prova=False):
    """Registra com'è andata la serata sulle spine indicate, rifacendola da capo (vedi VoceSerata).

    Per ogni spina si toglie quello già registrato stasera e si rifà, quindi
    ripeterla con gli stessi dati non cambia niente. Le altre spine non si
    toccano. Con prova=True non salva niente: dice solo cosa succederebbe.
    Se qualcosa non torna non registra niente (ErroreBar, con chiave l'id della
    spina); quello che non torna ma non blocca finisce negli avvisi.
    """
    voci = list(voci)
    if not voci:
        raise ErroreBar("Indica almeno una spina.")
    if len({v.spina.pk for v in voci}) != len(voci):
        raise ErroreBar("Hai indicato due volte la stessa spina.")
    with transaction.atomic():
        conta = _serata(servizio, utente)
        adesso = timezone.now()
        errori, avvisi = [], []
        for v in sorted(voci, key=lambda v: v.spina.pk):
            sp = _spina(v.spina.pk)
            try:
                avvisi += [f"{sp.nome}: {a}" for a in _rifai_spina(conta, sp, v, utente, adesso)]
            except ErroreBar as e:
                errori += [(sp.pk, f"{sp.nome}: {m}") for m in e.messaggi]
        if errori:
            raise ErroreBar(errori)
        toccati = set(Fusto.objects.filter(aperto_con=conta).values_list("tipo_id", flat=True))
        pieni = fusti_pieni()
        for t in TipoFusto.objects.filter(pk__in=toccati).select_related("bevanda"):
            if pieni.get(t.pk, ZERO) < ZERO:
                avvisi.append(f"Secondo l'app i fusti pieni di «{t.nome_completo}» sono {formatta_numero(pieni[t.pk])}: "
                              "registra la consegna (o l'inventario) che manca.")
        for s in serate_scoperte(conta.servizio):
            avvisi.append(f"{s}: venduto alla spina ma senza fusti registrati, quindi quello che è uscito quella sera "
                          "finisce nei conti di questa.")
        risultato = situazione_serata(conta, spine=[v.spina for v in voci])
        risultato["avvisi"] = avvisi
        risultato["prova"] = prova
        if prova:
            transaction.set_rollback(True)
        else:
            _pulisci(conta)
    return risultato


# ---------------------------------------------------------------------------
# La serata spina per spina (per Claude e per i riepiloghi)
# ---------------------------------------------------------------------------


def _rimasti_dopo(s, pesata):
    """Litri rimasti nel fusto subito dopo la pesata di quella serata (non adesso)."""
    if pesata.vuoto:
        return ZERO
    if s.tara is None:
        return None
    return min(max(litri(s.peso(pesata) - s.tara, s.densita), ZERO), s.tipo.litri)


def _fusto_in_breve(s, conta_id):
    pesata = s.pesata_di(conta_id)
    finito_dopo = s.fusto.finito_con_id is not None and s.fusto.finito_con_id != conta_id
    return {
        "fusto": s.fusto.pk,
        "tipo": s.tipo.nome,
        "bevanda": s.bevanda.nome,
        "attaccato_stasera": s.fusto.aperto_con_id == conta_id,
        "dal": s.fusto.aperto_con.servizio,
        "peso_prima_kg": None if s.fusto.aperto_con_id == conta_id else s.peso_prima(conta_id),
        "peso_stasera_kg": None if pesata is None else s.peso(pesata),
        "pesato_stasera": pesata is not None and not pesata.vuoto,
        "finito_stasera": s.fusto.finito_con_id == conta_id,
        "litri_usciti_stasera": None if pesata is None else s.litri_usciti(pesata),
        # Per l'ultima serata quelli di adesso; per quelle prima quelli rimasti dopo la loro pesata.
        "litri_rimasti": (_rimasti_dopo(s, pesata) if pesata is not None
                          else None if s.fusto.finito_con_id == conta_id or finito_dopo else s.litri_rimasti),
        "dati_provvisori": s.dati_provvisori,
    }


def situazione_serata(conta, spine=None):
    """Com'è andata la serata (di una ContaFusti) su ogni spina, in dati semplici.

    Per ogni spina: il fusto che c'era a inizio serata, quelli finiti, quello
    attaccato adesso con peso e litri. Più il confronto con il venduto in cassa
    per ogni bevanda alla spina.
    """
    servizio = conta.servizio
    if spine is None:
        spine = spine_in_uso()
    else:
        spine = list(Spina.objects.filter(pk__in=[sp.pk for sp in spine]).select_related("spinatrice", "bevanda"))
    ids = [sp.pk for sp in spine]
    # I fusti che stasera sono passati da queste spine: quelli attaccati prima (e non finiti prima) e quelli di stasera.
    toccati = Fusto.objects.filter(spina_id__in=ids, aperto_con_id__lte=conta.pk).filter(
        Q(finito_con__isnull=True) | Q(finito_con_id__gte=conta.pk)
    ).order_by("attaccato_il", "pk")
    per_spina = defaultdict(list)
    for s in stati_fusti(toccati):
        per_spina[s.fusto.spina_id].append(s)
    righe = []
    for sp in spine:
        stati = per_spina.get(sp.pk, [])
        inizio = next((s for s in stati if s.fusto.aperto_con_id < conta.pk), None)
        attaccato = next((s for s in stati if s.fusto.finito_con_id is None or s.fusto.finito_con_id > conta.pk), None)
        brevi = {s.fusto.pk: _fusto_in_breve(s, conta.pk) for s in stati}
        attaccato = brevi[attaccato.fusto.pk] if attaccato else None
        # Finché il fusto attaccato non è pesato, i litri usciti stasera non si sanno.
        mancano = attaccato is not None and attaccato["litri_usciti_stasera"] is None
        righe.append({
            "spina": sp,
            "inizio_serata": brevi[inizio.fusto.pk] if inizio else None,
            "finiti_stasera": [brevi[s.fusto.pk] for s in stati if s.fusto.finito_con_id == conta.pk],
            "attaccato": attaccato,
            "litri_usciti_stasera": None if mancano else sum((b["litri_usciti_stasera"] or ZERO for b in brevi.values()), ZERO),
        })
    return {"serata": servizio, "conta": conta.pk, "spine": righe, "bevande": confronto_serata(servizio, conta)}


def confronto_serata(servizio, conta=None):
    """Per ogni bevanda alla spina: uscita dai fusti e venduta in cassa nella serata."""
    if conta is None:
        conta = ContaFusti.objects.filter(servizio=servizio).first()
    spillati = dettaglio_conta(conta)[1] if conta else {}
    venduti = venduto([servizio])
    righe = []
    for b in Bevanda.objects.filter(tipo=TipoBevanda.FUSTI):
        spillato, vendute = spillati.get(b.pk), venduti.get(b.pk, ZERO)
        if spillato is None and not vendute:
            continue
        riga = {"bevanda": b, "spillato": spillato, "venduto": vendute}
        if spillato is not None:
            riga.update(confronto_birra(spillato, vendute))
        righe.append(riga)
    return righe
