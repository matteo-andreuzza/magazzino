"""Strumenti MCP del bar: bevande, carichi e soprattutto i fusti alla spina.

A fine serata chi gestisce il bar racconta a Claude com'è andata, spina per
spina, con le sue parole («bionda: cambiato, nuovo da 26 kg», «rossa non
cambiata, 6,60 kg», «consumati 2 da 50 litri»). Claude lo traduce in
registra_fusti_serata: per ogni spina quanti fusti sono finiti, che tipo di
fusto è attaccato adesso e quanto pesa. La serata su quella spina si rifà da
capo, quindi ripetere la stessa registrazione non fa danni; con «prova» non
si salva niente e si vede cosa succederebbe, da far controllare all'utente.
"""

import re
from decimal import Decimal

from bar import services as bar
from bar.forms import TipoFustoForm
from bar.models import Bevanda, ColoreFusto, ContaFusti, FormaFusto, Spina, TipoBevanda, TipoFusto, TipoOperazioneBar
from magazzino.models import Servizio
from magazzino.quantita import ZERO
from magazzino.views.comuni import giorno_servizio

from .strumenti import (
    SCHEMA_ID_REGISTRAZIONE,
    ErroreStrumento,
    descrivi_servizio,
    leggi_numero,
    numero,
    oggetti,
    quando,
    strumento,
    token_mcp,
    trova,
    trova_servizio,
)


def litri(valore):
    return numero(bar.arrotonda(valore)) if valore is not None else None


# ---------------------------------------------------------------------------
# Trovare spine, tipi di fusto e serate da come li chiama l'utente
# ---------------------------------------------------------------------------


def _elenco(cose):
    return ", ".join(f"«{c.nome}»" for c in cose)


def trova_spina(valore):
    """Per id, nome («Bionda», «Rosso») o bevanda che ci esce («birra rossa», «vino bianco»)."""
    if isinstance(valore, int) and not isinstance(valore, bool):
        sp = Spina.objects.select_related("spinatrice", "bevanda").filter(pk=valore).first()
        if sp is not None:
            return sp
    testo = str(valore or "").strip()
    if not testo:
        raise ErroreStrumento("Indica la spina (per esempio «Bionda», «Rossa», «Bianco», «Rosso»).")
    spine = Spina.objects.select_related("spinatrice", "bevanda")
    if testo.isdigit():
        sp = spine.filter(pk=int(testo)).first()
        if sp is not None:
            return sp
    for qs in (spine.filter(nome__iexact=testo), spine.filter(bevanda__nome__iexact=testo),
               spine.filter(nome__icontains=testo), spine.filter(bevanda__nome__icontains=testo)):
        trovate = list(qs[:6])
        if len(trovate) == 1:
            return trovate[0]
        if trovate:
            raise ErroreStrumento(f"«{testo}» corrisponde a più spine: {_elenco(trovate)}. Indica quella giusta.")
    raise ErroreStrumento(f"Non trovo la spina «{testo}». Ci sono: {_elenco(spine)}.")


PAROLE_VUOTE = {"da", "di", "del", "il", "lo", "la", "l", "fusto", "fusti", "litri", "litro", "lt", "kg", "tipo", "nuovo", "quello"}


def trova_tipo_fusto(valore, bevanda=None):
    """Un tipo di fusto della bevanda: per id, per nome («Fusto da 50 litri», «largo argento»), per litri («50L»)
    o per com'è fatto («nero», «alto nero», «azzurro»)."""
    tipi = TipoFusto.objects.select_related("bevanda")
    if bevanda is not None:
        tipi = tipi.filter(bevanda=bevanda)
    if isinstance(valore, int) and not isinstance(valore, bool):
        t = tipi.filter(pk=valore).first()
        if t is not None:
            return t
        raise ErroreStrumento(f"Non c'è il tipo di fusto con id {valore}{f' per {bevanda.nome}' if bevanda else ''}.")
    testo = str(valore or "").strip()
    if not testo:
        raise ErroreStrumento("Indica il tipo di fusto.")
    candidati = list(tipi)
    esatti = [t for t in candidati if t.nome.lower() == testo.lower()]
    if len(esatti) == 1:
        return esatti[0]
    minuscolo = testo.lower().replace(",", ".")
    numeri = [Decimal(n) for n in re.findall(r"\d+(?:\.\d+)?", minuscolo)]
    parole = [w for w in re.findall(r"[a-zàèéìòù]+", minuscolo) if w not in PAROLE_VUOTE]

    def descrizione(t):
        return f"{t.nome} {t.get_colore_display()} {t.get_forma_display()} {t.colore} {t.forma}".lower()

    simili = [
        t for t in candidati
        if all(n == t.litri for n in numeri) and all(w in descrizione(t) for w in parole)
    ]
    if len(simili) > 1:
        attivi = [t for t in simili if t.attivo]
        simili = attivi or simili
    if len(simili) == 1:
        return simili[0]
    di = f" di {bevanda.nome}" if bevanda else ""
    if simili:
        raise ErroreStrumento(f"«{testo}» corrisponde a più tipi di fusto{di}: {_elenco(simili)}. Indica quello giusto.")
    if candidati:
        raise ErroreStrumento(f"Non trovo il tipo di fusto «{testo}»{di}. Ci sono: {_elenco(candidati)}. Se è un tipo nuovo "
                              "va aggiunto (strumento tipo_di_fusto, o dall'app: Bar → Bevande e fusti).")
    raise ErroreStrumento(f"Non c'è nessun tipo di fusto{di}: va aggiunto (strumento tipo_di_fusto).")


def _serata_fusti(valore, *, per_scrivere):
    """La serata dei fusti: quella indicata, altrimenti oggi; per leggere, se oggi non c'è, l'ultima con i fusti."""
    if valore not in (None, "", "oggi"):
        return trova_servizio(valore)
    oggi = list(Servizio.objects.filter(data=giorno_servizio()).order_by("tipo", "nome"))
    if len(oggi) == 1:
        return oggi[0]
    if oggi:
        operabili = [s for s in oggi if bar.motivo_sola_lettura(s) is None]
        if len(operabili) == 1 or (operabili and not per_scrivere):
            return operabili[-1]
        raise ErroreStrumento(f"Oggi ci sono più servizi: {', '.join(f'{s} (id {s.pk})' for s in oggi)}. Indica quale.")
    ultima = bar.ultima_conta()
    if not per_scrivere and ultima is not None:
        return ultima.servizio
    suggerimento = f" L'ultima serata con i fusti registrati è {ultima.servizio} (id {ultima.servizio_id})." if ultima else ""
    raise ErroreStrumento(f"Oggi non c'è nessuna serata: indica la data (AAAA-MM-GG) della serata.{suggerimento}")


# ---------------------------------------------------------------------------
# Descrizioni
# ---------------------------------------------------------------------------


def _fusto(f):
    if f is None:
        return None
    return {
        "fusto_id": f["fusto"],
        "tipo": f["tipo"],
        "bevanda": f["bevanda"],
        "attaccato_stasera": f["attaccato_stasera"],
        "attaccato_dal": None if f["attaccato_stasera"] else quando(f["dal"].data),
        "peso_prima_di_stasera_kg": numero(f["peso_prima_kg"]),
        "peso_stasera_kg": None if f["finito_stasera"] else numero(f["peso_stasera_kg"]),
        "pesato_stasera": f["pesato_stasera"],
        "finito_stasera": f["finito_stasera"],
        "litri_usciti_stasera": litri(f["litri_usciti_stasera"]),
        "litri_rimasti": litri(f["litri_rimasti"]),
        "litri_e_tara_provvisori": f["dati_provvisori"],
    }


def _frase(r):
    """La serata su una spina detta in una riga, da far leggere all'utente."""
    sp, inizio, finiti, adesso = r["spina"], r["inizio_serata"], r["finiti_stasera"], r["attaccato"]
    parti = []
    if inizio is None:
        parti.append("a inizio serata nessun fusto attaccato")
    else:
        peso = f", {bar.formatta_kg(inizio['peso_prima_kg'])}" if inizio["peso_prima_kg"] is not None else ""
        parti.append(f"a inizio serata {inizio['tipo'].lower()} attaccato dal {inizio['dal'].data:%d/%m}{peso}")
    if finiti:
        nuovi = sum(1 for f in finiti if f["attaccato_stasera"])
        dettaglio = []
        if inizio is not None and inizio["finito_stasera"]:
            dettaglio.append("quello di inizio serata")
        if nuovi:
            dettaglio.append(f"{nuovi} attaccat{'o' if nuovi == 1 else 'i'} e finit{'o' if nuovi == 1 else 'i'} stasera")
        uno = len(finiti) == 1
        parti.append(f"finit{'o' if uno else 'i'} {len(finiti)} fust{'o' if uno else 'i'} ({' e '.join(dettaglio)})")
    else:
        parti.append("nessun fusto finito")
    if adesso is None:
        parti.append("adesso la spina è vuota")
    else:
        stato = "nuovo" if adesso["attaccato_stasera"] else "lo stesso"
        peso = (f"{bar.formatta_kg(adesso['peso_stasera_kg'])}, restano ≈ {bar.formatta_litri(adesso['litri_rimasti'])}"
                if adesso["pesato_stasera"] and adesso["litri_rimasti"] is not None
                else bar.formatta_kg(adesso["peso_stasera_kg"]) if adesso["pesato_stasera"] else "DA PESARE")
        parti.append(f"adesso {adesso['tipo'].lower()} ({stato}): {peso}")
    usciti = r["litri_usciti_stasera"]
    parti.append(f"uscito stasera ≈ {bar.formatta_litri(usciti)}" if usciti is not None else "uscito stasera: non si sa finché non si pesa")
    return f"{sp.nome}: " + "; ".join(parti) + "."


def descrivi_situazione(situazione):
    servizio = situazione["serata"]
    return {
        "serata": descrivi_servizio(servizio),
        "spine": [
            {
                "spina": r["spina"].nome, "spina_id": r["spina"].pk, "spinatrice": r["spina"].spinatrice.nome,
                "bevanda_della_spina": r["spina"].bevanda.nome if r["spina"].bevanda else None,
                "a_inizio_serata": _fusto(r["inizio_serata"]),
                "finiti_stasera": [_fusto(f) for f in r["finiti_stasera"]],
                "attaccato_adesso": _fusto(r["attaccato"]),
                "litri_usciti_stasera": litri(r["litri_usciti_stasera"]),
            }
            for r in situazione["spine"]
        ],
        "alla_spina_venduto_e_uscito": [
            {"bevanda": b["bevanda"].nome, "uscito_dai_fusti_litri": litri(b["spillato"]), "venduto_in_cassa_litri": litri(b["venduto"]),
             "differenza_litri": numero(b.get("differenza")), "differenza_percentuale": numero(b.get("percentuale"))}
            for b in situazione["bevande"]
        ],
        "riepilogo": [_frase(r) for r in situazione["spine"]],
    }


def _tipo_in_breve(t, pieni):
    return {
        "id": t.pk, "nome": t.nome, "bevanda": t.bevanda.nome, "litri": numero(t.litri), "tara_kg": numero(t.tara),
        "peso_pieno_kg": numero(t.peso_pieno.quantize(Decimal("0.1"))) if t.peso_pieno is not None else None,
        "litri_e_tara_provvisori": t.tara_provvisoria, "colore": t.get_colore_display(), "forma": t.get_forma_display(),
        "attivo": t.attivo, "pieni_in_magazzino": numero(pieni.get(t.pk, ZERO)),
    }


# ---------------------------------------------------------------------------
# Letture
# ---------------------------------------------------------------------------


@strumento(
    "bar", "Magazzino del bar",
    "Bevande del bar in lattine e bottiglie con le giacenze; bevande alla spina (birra e vino) con i loro tipi di fusto "
    "(litri, tara, fusti pieni in magazzino); spinatrici e spine con il fusto attaccato adesso e i litri rimasti; "
    "quanta birra e vino restano e per quante serate bastano (per sapere quando ordinare); fusti finiti da inizio sagra.",
)
def situazione_bar(argomenti, contesto):
    giacenze_bar = bar.giacenze_bevande()
    pieni = bar.fusti_pieni()
    consumati = bar.fusti_consumati()
    return {
        "bevande": [
            {"id": b.pk, "nome": b.nome, "come_si_conta": b.get_tipo_display(), "giacenza": numero(giacenze_bar.get(b.pk, ZERO)), "unita": b.unita}
            for b in Bevanda.objects.exclude(tipo=TipoBevanda.FUSTI)
        ],
        "alla_spina": [
            {"id": b.pk, "nome": b.nome, "kg_per_litro": numero(b.densita),
             "tipi_di_fusto": [_tipo_in_breve(t, pieni) for t in b.tipi_fusto.all()]}
            for b in Bevanda.objects.filter(tipo=TipoBevanda.FUSTI).prefetch_related("tipi_fusto__bevanda")
        ],
        "spinatrici": [
            {"spinatrice": spinatrice.nome, "spine": [
                {"spina": sp.nome, "spina_id": sp.pk, "bevanda": sp.bevanda.nome if sp.bevanda else None,
                 "attaccato": None if s is None else {
                     "fusto_id": s.fusto.pk, "tipo": s.tipo.nome, "bevanda": s.bevanda.nome,
                     "attaccato_nella_serata": str(s.fusto.aperto_con.servizio),
                     "ultimo_peso_kg": numero(s.ultimo_peso), "litri_rimasti": litri(s.litri_rimasti)}}
                for sp, s in spine
            ]}
            for spinatrice, spine in bar.spinatrici_in_uso()
        ],
        "scorte_alla_spina": [
            {"bevanda": r["bevanda"].nome, "fusti_pieni": numero(r["fusti_pieni"]), "litri_disponibili": litri(r["litri"]),
             "di_cui_nei_fusti_attaccati": litri(r["litri_attaccati"]), "media_uscita_per_serata_litri": litri(r["media"]),
             "serate_considerate": r["serate"], "bastano_per_serate": numero(r["autonomia"])}
            for r in bar.scorte_alla_spina()
        ],
        "fusti_finiti": {
            "totale": len(consumati),
            "per_tipo": {t.nome_completo: n for t in TipoFusto.objects.select_related("bevanda")
                         if (n := sum(1 for s in consumati if s.tipo.pk == t.pk))},
        },
    }


@strumento(
    "fusti_serata", "Fusti di una serata",
    "Com'è andata (o come sta andando) una serata alle spine: per ogni spina il fusto attaccato a inizio serata, quelli "
    "finiti, quello attaccato adesso con peso e litri rimasti; litri usciti dai fusti e venduti in cassa per ogni bevanda; "
    "se si può ancora modificare. Usalo prima di registrare la fine serata.",
    {"properties": {"serata": {"type": ["string", "integer"], "description": "Id, data (AAAA-MM-GG) o «oggi». Se manca: "
                                                                             "oggi, oppure l'ultima serata con i fusti."}}},
)
def fusti_serata(argomenti, contesto):
    s = _serata_fusti(argomenti.get("serata"), per_scrivere=False)
    conta = ContaFusti.objects.filter(servizio=s).first()
    motivo = bar.motivo_sola_lettura(s)
    if conta is None:
        # Niente registrato: la situazione è quella di adesso (o, per le serate passate, non si sa).
        dati = {"serata": descrivi_servizio(s), "registrato_niente": True}
        if motivo is None:
            pieni = bar.fusti_pieni()
            dati["spine_adesso"] = [
                {"spina": sp.nome, "spina_id": sp.pk, "bevanda": sp.bevanda.nome if sp.bevanda else None,
                 "attaccato": None if st is None else {"fusto_id": st.fusto.pk, "tipo": st.tipo.nome, "ultimo_peso_kg": numero(st.ultimo_peso),
                                                       "litri_rimasti": litri(st.litri_rimasti),
                                                       "attaccato_nella_serata": str(st.fusto.aperto_con.servizio)}}
                for _, spine in bar.spinatrici_in_uso() for sp, st in spine
            ]
            dati["tipi_di_fusto"] = [_tipo_in_breve(t, pieni) for t in TipoFusto.objects.filter(attivo=True).select_related("bevanda")]
            scoperte = bar.serate_scoperte(s)
            if scoperte:
                dati["attenzione"] = [f"{x}: venduto alla spina ma senza fusti registrati." for x in scoperte]
    else:
        dati = descrivi_situazione(bar.situazione_serata(conta))
    dati["modificabile"] = motivo is None
    if motivo:
        dati["perche_non_modificabile"] = motivo
    return dati


@strumento(
    "consumi_bar", "Consumi del bar per serata",
    "Per ogni serata: lattine, bottiglie e litri venduti di ogni bevanda; per birra e vino alla spina anche i litri usciti dai fusti.",
)
def consumi_bar(argomenti, contesto):
    colonne, righe, totali = bar.consumi_per_serata()
    intestazioni = [f"{b.nome} ({b.unita}{', dai fusti' if cosa == 'spillato' else ''})" for b, cosa in colonne]
    return {
        "serate": [
            {"serata": str(s), "id": s.pk, "valori": {h: numero(bar.arrotonda(v) if v is not None else None) for h, v in zip(intestazioni, valori)}}
            for s, valori in righe
        ],
        "totale": {h: numero(bar.arrotonda(v)) for h, v in zip(intestazioni, totali)},
    }


# ---------------------------------------------------------------------------
# Scritture
# ---------------------------------------------------------------------------


SCHEMA_SPINA = {
    "type": "object", "additionalProperties": False, "required": ["spina"],
    "properties": {
        "spina": {"type": ["string", "integer"], "description": "La spina: «Bionda», «Rossa», «Bianco», «Rosso» (o l'id, o la bevanda)."},
        "finiti": {"type": "integer", "minimum": 0, "maximum": bar.MAX_FINITI, "description":
                   "Quanti fusti sono FINITI stasera su questa spina. 0 = non cambiato (stesso fusto di ieri). Il primo finito è "
                   "quello attaccato a inizio serata; gli altri sono stati attaccati e finiti durante la serata. «Cambiato» = 1; "
                   "«consumati 2» = 2. Se a inizio serata non c'era nessun fusto (prima serata) e ne è stato attaccato uno solo, è 0."},
        "tipo": {"type": ["string", "integer"], "description":
                 "Tipo del fusto attaccato ADESSO, se è nuovo: «50 litri», «largo argento», «alto nero», «azzurro»… "
                 "Se manca: lo stesso tipo di prima, o l'unico tipo di quella bevanda."},
        "tipi_finiti": {"type": "array", "items": {"type": ["string", "integer"]}, "description":
                        "Solo se diversi da «tipo»: i tipi dei fusti attaccati e finiti durante la serata (escluso quello di inizio serata)."},
        "peso_kg": {"type": ["number", "string"], "description": "Quanto pesa ADESSO (a fine serata) il fusto attaccato, in kg."},
        "vuota": {"type": "boolean", "description": "A fine serata la spina è rimasta senza fusto attaccato."},
    },
}


@strumento(
    "registra_fusti_serata", "Registra i fusti di fine serata",
    "Registra com'è andata la serata alle spine, come la racconta chi gestisce il bar: per ogni spina quanti fusti sono "
    "finiti, che tipo di fusto è attaccato adesso e quanto pesa. La serata su quelle spine si rifà da capo (le altre spine "
    "non si toccano): ripeterla con gli stessi dati non cambia niente, rifarla con dati diversi li corregge. "
    "Prima chiamala con prova=true e mostra all'utente il riepilogo e gli avvisi; registra (prova=false) solo dopo la sua "
    "conferma. Se qualcosa non torna (pesa più di ieri, tipo diverso da quello attaccato, spina sbagliata) non registra "
    "niente e spiega perché: chiedi all'utente invece di indovinare.",
    {"properties": {
        "serata": {"type": ["string", "integer"], "description": "Id o data (AAAA-MM-GG) della serata. Se manca: oggi."},
        "spine": {"type": "array", "minItems": 1, "items": SCHEMA_SPINA},
        "prova": {"type": "boolean", "description": "true = non salva niente, dice solo cosa succederebbe."},
    }, "required": ["spine"]},
    scrive=True,
)
def registra_fusti_serata(argomenti, contesto):
    servizio = _serata_fusti(argomenti.get("serata"), per_scrivere=True)
    voci = []
    for v in oggetti(argomenti.get("spine") or [], "spine"):
        sp = trova_spina(v.get("spina"))
        tipo = trova_tipo_fusto(v["tipo"], sp.bevanda) if v.get("tipo") not in (None, "") else None
        tipi_finiti = [trova_tipo_fusto(t, sp.bevanda) for t in v.get("tipi_finiti") or []]
        peso = leggi_numero(v["peso_kg"], "peso_kg") if v.get("peso_kg") not in (None, "") else None
        finiti = v.get("finiti", 0)
        if isinstance(finiti, bool) or not isinstance(finiti, int):
            raise ErroreStrumento(f"{sp.nome}: «finiti» deve essere un numero intero (quanti fusti sono finiti stasera).")
        voci.append(bar.VoceSerata(spina=sp, finiti=finiti, tipo=tipo, tipi_finiti=tipi_finiti, peso=peso, vuota=bool(v.get("vuota"))))
    prova = bool(argomenti.get("prova"))
    try:
        situazione = bar.registra_serata(servizio=servizio, voci=voci, utente=contesto.utente, prova=prova)
    except bar.ErroreBar as e:
        raise ErroreStrumento(f"Non ho registrato niente ({servizio}). " + " ".join(e.messaggi)) from None
    return {
        "salvato": not prova,
        "prova": prova,
        **descrivi_situazione(situazione),
        "avvisi": situazione["avvisi"],
        "nota": "Solo una prova: niente è stato salvato." if prova else "Registrato. Si corregge rifacendo la registrazione della stessa spina.",
    }


@strumento(
    "annulla_fusti_serata", "Annulla i fusti di una serata",
    "Annulla quello registrato sui fusti in una serata (tutte le spine o solo quelle indicate): i fusti attaccati quella "
    "sera tornano tra i pieni, quelli finiti tornano attaccati, le pesate spariscono. Si può solo sull'ultima serata con "
    "i fusti: serve per rifarla, o per registrare prima una serata dimenticata. Chiedi conferma prima.",
    {"properties": {
        "serata": {"type": ["string", "integer"], "description": "Id o data (AAAA-MM-GG) della serata."},
        "spine": {"type": "array", "items": {"type": ["string", "integer"]}, "description": "Solo queste spine (se manca: tutte)."},
    }, "required": ["serata"]},
    scrive=True,
)
def annulla_fusti_serata(argomenti, contesto):
    servizio = trova_servizio(argomenti.get("serata"))
    spine = [trova_spina(v) for v in argomenti["spine"]] if argomenti.get("spine") else None
    try:
        nomi = bar.azzera(servizio=servizio, spine=spine)
    except bar.ErroreBar as e:
        raise ErroreStrumento(" ".join(e.messaggi)) from None
    return {"annullato": bool(nomi), "serata": descrivi_servizio(servizio), "spine": nomi}


@strumento(
    "tipo_di_fusto", "Crea o modifica un tipo di fusto",
    "Aggiunge un tipo di fusto a una bevanda alla spina, o cambia litri, tara (peso da vuoto), colore e forma di uno che "
    "c'è già. La tara la decide l'amministrazione (serve un utente staff): scrivendola i litri delle serate già passate "
    "si ricalcolano. Chiedi conferma prima.",
    {"properties": {
        "bevanda": {"type": ["string", "integer"], "description": "La bevanda alla spina (es. «Birra bionda», «Vino rosso»)."},
        "nome": {"type": "string", "maxLength": 40, "description": "Come lo chiamano al bancone (es. «Alto nero», «Fusto da 50 litri»)."},
        "litri": {"type": ["number", "string"]},
        "tara_kg": {"type": ["number", "string"], "description": "Peso del fusto vuoto, in kg."},
        "colore": {"type": "string", "enum": list(ColoreFusto.values)},
        "forma": {"type": "string", "enum": list(FormaFusto.values)},
        "stesso_fusto_anche_per_le_altre_bevande": {"type": "boolean", "description":
                                                    "Stessi litri e tara anche ai tipi con lo stesso nome delle altre bevande."},
        "litri_e_tara_confermati": {"type": "boolean", "description":
                                    "Solo se l'utente dice che litri e tara attuali sono già giusti (smettono di essere "
                                    "provvisori). Scrivendo litri o tara nuovi lo diventano da soli."},
    }, "required": ["bevanda", "nome"]},
    scrive=True,
)
def tipo_di_fusto(argomenti, contesto):
    if not getattr(contesto.utente, "is_staff", False):
        raise ErroreStrumento("I tipi di fusto (e le tare) li cambia solo l'amministrazione: serve una chiave di un utente staff.")
    bevanda = trova(Bevanda.objects.filter(tipo=TipoBevanda.FUSTI), argomenti.get("bevanda"), "la bevanda alla spina")
    nome = (argomenti.get("nome") or "").strip()
    tipo = TipoFusto.objects.filter(bevanda=bevanda, nome__iexact=nome).first()

    def testo(chiave, attuale):
        valore = argomenti.get(chiave)
        return str(valore) if valore not in (None, "") else ("" if attuale is None else str(attuale))

    dati = {
        "bevanda": bevanda.pk, "nome": nome,
        "litri": testo("litri", tipo.litri if tipo else None), "tara": testo("tara_kg", tipo.tara if tipo else None),
        "colore": argomenti.get("colore") or (tipo.colore if tipo else ColoreFusto.ACCIAIO),
        "forma": argomenti.get("forma") or (tipo.forma if tipo else FormaFusto.ALTO),
        "ordine": tipo.ordine if tipo else bevanda.tipi_fusto.count() + 1,
        "attivo": True,
        "anche_gli_altri": bool(argomenti.get("stesso_fusto_anche_per_le_altre_bevande")),
        "confermati": bool(argomenti.get("litri_e_tara_confermati")),
    }
    form = TipoFustoForm(dati, instance=tipo)
    if not form.is_valid():
        errori = [f"{campo}: {' '.join(e)}" for campo, e in form.errors.items()]
        raise ErroreStrumento("Tipo di fusto non salvato. " + " ".join(errori))
    salvato = form.save()
    return {"salvato": True, "creato": tipo is None, **_tipo_in_breve(salvato, bar.fusti_pieni()),
            "anche": [g.nome_completo for g in form.gemelli] if dati["anche_gli_altri"] else []}


def _bevanda_o_fusto(cosa, bevanda=None):
    """Una bevanda in lattine o bottiglie (per id o nome) o un tipo di fusto (della bevanda alla spina indicata)."""
    if bevanda not in (None, ""):
        alla_spina = trova(Bevanda.objects.filter(tipo=TipoBevanda.FUSTI), bevanda, "la bevanda alla spina")
        return trova_tipo_fusto(cosa, alla_spina)
    testo = str(cosa or "").strip()
    if not testo:
        raise ErroreStrumento("Indica la bevanda o il tipo di fusto.")
    bevande = Bevanda.objects.exclude(tipo=TipoBevanda.FUSTI)
    if testo.isdigit():
        return trova(bevande, testo, "la bevanda")
    esatte = list(bevande.filter(nome__iexact=testo))
    if len(esatte) == 1:
        return esatte[0]
    simili = list(bevande.filter(nome__icontains=testo)[:5])
    if len(simili) == 1:
        return simili[0]
    if simili:
        raise ErroreStrumento(f"«{testo}» corrisponde a più bevande: {_elenco(simili)}. Indica quella giusta.")
    try:
        return trova_tipo_fusto(testo)
    except ErroreStrumento as e:
        raise ErroreStrumento(f"«{testo}» non è una bevanda in lattine o bottiglie. Come fusto: {e} Per i fusti conviene "
                              "indicare anche la bevanda (es. cosa «50 litri», bevanda «Birra bionda»).") from None


@strumento(
    "carico_bar", "Registra un carico del bar",
    "Registra bevande arrivate (o l'inventario iniziale) nel magazzino del bar: lattine, bottiglie e fusti pieni (per i "
    "fusti indica il tipo e la bevanda, es. cosa «50 litri» e bevanda «Birra bionda»). Prima di registrare chiedi conferma.",
    {"properties": {
        "tipo": {"type": "string", "enum": ["carico", "inventario"]},
        "voci": {"type": "array", "minItems": 1, "items": {
            "type": "object", "additionalProperties": False, "required": ["cosa", "quantita"],
            "properties": {
                "cosa": {"type": ["string", "integer"], "description": "Nome della bevanda o del tipo di fusto."},
                "bevanda": {"type": ["string", "integer"], "description": "Solo per i fusti: la bevanda alla spina."},
                "quantita": {"type": ["number", "string"]},
            },
        }},
        "note": {"type": "string"},
        "id_registrazione": SCHEMA_ID_REGISTRAZIONE,
    }, "required": ["tipo", "voci"]},
    scrive=True,
)
def carico_bar(argomenti, contesto):
    tipo = {"carico": TipoOperazioneBar.CARICO, "inventario": TipoOperazioneBar.INVENTARIO}.get(argomenti.get("tipo"))
    if tipo is None:
        raise ErroreStrumento("«tipo» deve essere «carico» o «inventario».")
    voci = []
    for voce in oggetti(argomenti.get("voci") or [], "voci"):
        fusto_o_bevanda = _bevanda_o_fusto(voce.get("cosa"), voce.get("bevanda"))
        q = leggi_numero(voce.get("quantita"))
        if q <= ZERO:
            raise ErroreStrumento(f"La quantità di «{fusto_o_bevanda.nome}» deve essere maggiore di zero.")
        voci.append((fusto_o_bevanda, q))
    if not voci:
        raise ErroreStrumento("Indica almeno una bevanda con la quantità.")
    try:
        operazione, creata = bar.registra_carico(
            tipo=tipo, voci=voci, utente=contesto.utente, note=(argomenti.get("note") or "Registrato da Claude").strip(),
            token=token_mcp(argomenti, "carico_bar", contesto),
        )
    except bar.ErroreBar as e:
        raise ErroreStrumento(" ".join(e.messaggi)) from None
    return {
        "registrato": creata, "gia_registrato_prima": not creata, "operazione_id": operazione.pk, "tipo": operazione.get_tipo_display(),
        "righe": [{"cosa": getattr(c, "nome_completo", c.nome), "quantita": numero(q), "unita": c.unita} for c, q in voci],
    }
