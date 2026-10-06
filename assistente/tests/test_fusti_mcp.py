"""Fine serata al bar raccontata a Claude: il caso vero di venerdì, sabato e domenica.

Venerdì (prima serata):  bionda in uso 20 kg, rossa 25 kg, bianco 7 kg, rosso (fusto azzurro) 22 kg.
Sabato: consumati 2 fusti da 50 litri di bionda (quello di ieri e un altro), adesso 10 kg; rossa 15 kg;
        cambiati bianco e rosso: bianco 26 kg (largo argento), rosso 23,9 kg (alto nero).
Domenica: cambiata la bionda, nuova da 26 kg; rossa 6,60; bianco 7,86; rosso 5,05 (quasi vuoto).

Tare di prova: 5 kg i fusti da 50 litri di birra, 4,5 kg quelli da 25 litri di vino. Una bionda piena
pesa 5 + 50 × 1,01 = 55,5 kg, un fusto di vino pieno 4,5 + 25 × 0,99 = 29,25 kg.
"""

from datetime import date
from decimal import Decimal as D

from bar import services
from bar.models import ContaFusti, Fusto, Spina, TipoFusto
from magazzino.models import Servizio

from .test_mcp import BaseMCP

VENERDI = [
    {"spina": "bionda", "peso_kg": 20},
    {"spina": "rossa", "peso_kg": 25},
    {"spina": "bianco", "peso_kg": 7},
    {"spina": "rosso", "tipo": "azzurro", "peso_kg": 22},
]
SABATO = [
    {"spina": "bionda", "finiti": 2, "tipo": "50L", "peso_kg": 10},
    {"spina": "rossa", "peso_kg": 15},
    {"spina": "bianco", "finiti": 1, "tipo": "largo argento", "peso_kg": 26},
    {"spina": "rosso", "finiti": 1, "tipo": "alto nero", "peso_kg": "23,9"},
]
DOMENICA = [
    {"spina": "bionda", "finiti": 1, "peso_kg": 26},
    {"spina": "rossa", "peso_kg": "6.60"},
    {"spina": "bianco", "peso_kg": 7.86},
    {"spina": "rosso", "peso_kg": 5.05},
]


class FineSerataTest(BaseMCP):
    def setUp(self):
        super().setUp()
        self.venerdi = Servizio.objects.create(data=date(2026, 10, 2))
        self.sabato = Servizio.objects.create(data=date(2026, 10, 3))
        self.domenica = Servizio.objects.create(data=date(2026, 10, 4))
        for t in TipoFusto.objects.filter(litri=50):
            TipoFusto.objects.filter(pk=t.pk).update(tara=D("5"), tara_provvisoria=False)
        TipoFusto.objects.filter(litri=25).update(tara=D("4.5"), tara_provvisoria=False)
        # Come dopo le migrazioni: i fusti della birra sono da 50 litri (quelli da 15 e 30 non si usano).
        TipoFusto.objects.filter(litri__in=[15, 30]).update(attivo=False)
        self.cinquanta = TipoFusto.objects.get(bevanda=self.birra, litri=50)
        self.cinquanta_rossa = TipoFusto.objects.get(bevanda=self.rossa, litri=50)
        self.largo = TipoFusto.objects.get(nome="Largo argento")
        self.azzurro = TipoFusto.objects.get(nome="Azzurro")
        self.nero = TipoFusto.objects.get(nome="Alto nero")
        self.inventario((self.cinquanta, "6"), (self.cinquanta_rossa, "3"), (self.largo, "3"), (self.azzurro, "2"), (self.nero, "2"))

    def registra(self, serata, spine, prova=False):
        errore, dati = self.chiama("registra_fusti_serata", {"serata": serata.data.isoformat(), "spine": spine, "prova": prova},
                                   chiave=self.testo_scrittura)
        self.assertFalse(errore, dati)
        return dati

    def usciti(self, serata):
        return {b: services.arrotonda(v) for b, v in services.spillato_per_servizio().get(serata.pk, {}).items()}

    def test_tre_serate_raccontate(self):
        self.registra(self.venerdi, VENERDI)
        self.assertEqual(self.usciti(self.venerdi), {
            self.birra.pk: D("35.1"), self.rossa.pk: D("30.2"), self.bianco.pk: D("22.5"),
            Spina.objects.get(nome="Rosso").bevanda_id: D("7.3"),
        })
        dati = self.registra(self.sabato, SABATO)
        bionda = next(s for s in dati["spine"] if s["spina"] == "Bionda")
        self.assertEqual(len(bionda["finiti_stasera"]), 2)
        self.assertEqual(bionda["attaccato_adesso"]["peso_stasera_kg"], 10)
        # Quello di ieri (20 → 5 kg di tara) + uno intero da 50 + quello nuovo (55,5 → 10 kg).
        self.assertEqual(bionda["litri_usciti_stasera"], 109.9)
        self.assertIn("finiti 2 fusti (quello di inizio serata e 1 attaccato e finito stasera)", dati["riepilogo"][0])
        self.registra(self.domenica, DOMENICA)
        rosso = Spina.objects.get(nome="Rosso").bevanda_id
        self.assertEqual(self.usciti(self.domenica), {
            self.birra.pk: D("34.2"), self.rossa.pk: D("8.3"), self.bianco.pk: D("18.3"), rosso: D("19.0"),
        })
        # Fusti pieni rimasti: bionda 6 − 4 = 2, rossa 3 − 1, largo argento 3 − 2, azzurro 2 − 1, alto nero 2 − 1.
        self.assertEqual(services.fusti_pieni(), {
            self.cinquanta.pk: D("2"), self.cinquanta_rossa.pk: D("2"), self.largo.pk: D("1"), self.azzurro.pk: D("1"),
            self.nero.pk: D("1"),
        })
        # Scorte: 2 pieni da 50 litri + 20,8 litri nel fusto attaccato; in media 59,7 litri a serata.
        bionda = next(r for r in services.scorte_alla_spina() if r["bevanda"] == self.birra)
        self.assertEqual((services.arrotonda(bionda["litri"]), services.arrotonda(bionda["media"]), bionda["autonomia"]),
                         (D("120.8"), D("59.7"), D("2.0")))
        attaccati = {s.fusto.spina.nome: (s.tipo.nome, s.ultimo_peso) for s in services.fusti_attaccati()}
        self.assertEqual(attaccati, {
            "Bionda": ("Fusto da 50 litri", D("26")), "Rossa": ("Fusto da 50 litri", D("6.6")),
            "Bianco": ("Largo argento", D("7.86")), "Rosso": ("Alto nero", D("5.05")),
        })

    def test_ripeterla_non_cambia_niente_e_la_prova_non_salva(self):
        self.registra(self.venerdi, VENERDI)
        dati = self.registra(self.sabato, SABATO, prova=True)
        self.assertFalse(dati["salvato"])
        self.assertFalse(ContaFusti.objects.filter(servizio=self.sabato).exists())
        self.assertEqual(Fusto.objects.count(), 4)
        self.registra(self.sabato, SABATO)
        fusti, pieni = Fusto.objects.count(), services.fusti_pieni()
        self.registra(self.sabato, SABATO)
        self.assertEqual((Fusto.objects.count(), services.fusti_pieni()), (fusti, pieni))
        # Rifatta con un dato diverso, corregge: la bionda di sabato era un solo cambio.
        self.registra(self.sabato, [{"spina": "bionda", "finiti": 1, "peso_kg": 10}])
        self.assertEqual(len(Fusto.objects.filter(spina__nome="Bionda")), 2)
        self.assertEqual(services.fusti_pieni()[self.cinquanta.pk], D("4"))

    def test_conflitti_spiegati_senza_registrare_niente(self):
        self.registra(self.venerdi, VENERDI)
        casi = [
            # «Non cambiata» ma pesa più di ieri: o è stata cambiata o il peso è sbagliato.
            ([{"spina": "rossa", "peso_kg": 26}], "Pesa più dell'ultima volta (25 kg)"),
            # Il tipo detto non è quello attaccato: è stato cambiato?
            ([{"spina": "rosso", "tipo": "alto nero", "peso_kg": 20}], "è attaccato un azzurro"),
            # Due tipi per il rosso: quale?
            ([{"spina": "rosso", "tipo": "argento", "peso_kg": 20}], "Non trovo il tipo di fusto «argento» di Vino rosso"),
            ([{"spina": "ross", "peso_kg": 20}], "corrisponde a più spine"),
            # Più pesante di un fusto pieno, con tare vere: errore.
            ([{"spina": "bionda", "finiti": 1, "peso_kg": 60}], "Pesa più di un fusto pieno"),
            ([{"spina": "bianco", "peso_kg": 3}], "Pesa meno di un fusto vuoto (un «Largo argento» vuoto pesa 4,5 kg)"),
        ]
        for spine, messaggio in casi:
            with self.subTest(messaggio=messaggio):
                errore, testo = self.chiama("registra_fusti_serata", {"serata": "2026-10-03", "spine": spine}, chiave=self.testo_scrittura)
                self.assertTrue(errore)
                self.assertIn(messaggio, testo)
        self.assertFalse(ContaFusti.objects.filter(servizio=self.sabato).exists())

    def test_con_tare_provvisorie_avvisa_ma_registra(self):
        TipoFusto.objects.update(tara_provvisoria=True, tara=D("8"))
        dati = self.registra(self.venerdi, [{"spina": "rosso", "tipo": "azzurro", "peso_kg": 5.05}])
        self.assertTrue(dati["salvato"])
        self.assertTrue(any("tara provvisoria" in a for a in dati["avvisi"]))

    def test_serate_in_ordine(self):
        self.registra(self.sabato, SABATO[1:2])
        errore, testo = self.chiama("registra_fusti_serata", {"serata": "2026-10-02", "spine": VENERDI}, chiave=self.testo_scrittura)
        self.assertTrue(errore)
        self.assertIn("Ci sono già i fusti di una serata più recente", testo)
        # Si annulla sabato, si registra venerdì e poi di nuovo sabato.
        errore, dati = self.chiama("annulla_fusti_serata", {"serata": "2026-10-03"}, chiave=self.testo_scrittura)
        self.assertFalse(errore, dati)
        self.registra(self.venerdi, VENERDI)
        self.registra(self.sabato, SABATO)
        self.assertEqual(ContaFusti.objects.count(), 2)

    def test_serata_dimenticata_segnalata(self):
        self.ordina([(self.birra_piccola, 10)], servizio=self.venerdi)
        dati = self.registra(self.sabato, [{"spina": "bionda", "peso_kg": 40}])
        self.assertTrue(any("venduto alla spina ma senza fusti registrati" in a for a in dati["avvisi"]))

    def test_lettura_della_serata(self):
        self.registra(self.venerdi, VENERDI)
        errore, dati = self.chiama("fusti_serata", {"serata": "2026-10-02"})
        self.assertFalse(errore, dati)
        self.assertTrue(dati["modificabile"])
        self.assertEqual([s["spina"] for s in dati["spine"]], ["Bionda", "Rossa", "Bianco", "Rosso"])
        errore, dati = self.chiama("bar")
        self.assertFalse(errore, dati)
        self.assertEqual([s["spinatrice"] for s in dati["spinatrici"]], ["Birra", "Vino"])
        # Una serata passata racconta i litri rimasti di quella sera, non di adesso.
        self.registra(self.sabato, SABATO)
        _, dati = self.chiama("fusti_serata", {"serata": "2026-10-02"})
        self.assertFalse(dati["modificabile"])
        rossa = next(s for s in dati["spine"] if s["spina"] == "Rossa")
        self.assertEqual(rossa["attaccato_adesso"]["litri_rimasti"], 19.8)  # (25 − 5) / 1,01, non i 9,9 di sabato
        bionda = next(s for s in dati["spine"] if s["spina"] == "Bionda")
        self.assertEqual(bionda["attaccato_adesso"]["litri_rimasti"], 14.9)

    def test_tipo_di_fusto_solo_staff(self):
        errore, dati = self.chiama("tipo_di_fusto", {
            "bevanda": "Vino bianco", "nome": "Alto nero", "litri": 25, "tara_kg": "4,6", "colore": "nero", "forma": "alto",
        }, chiave=self.testo_scrittura)
        self.assertFalse(errore, dati)
        self.assertTrue(dati["creato"])
        self.assertEqual(TipoFusto.objects.get(bevanda=self.bianco, nome="Alto nero").tara, D("4.6"))


class RegistrazioniRipetuteTest(BaseMCP):
    def test_stesso_id_registrazione_non_registra_due_volte(self):
        argomenti = {"tipo": "carico", "voci": [{"cosa": "Coca-Cola", "quantita": 24}], "id_registrazione": "coca-3-ottobre"}
        errore, primo = self.chiama("carico_bar", argomenti, chiave=self.testo_scrittura)
        self.assertFalse(errore, primo)
        errore, secondo = self.chiama("carico_bar", argomenti, chiave=self.testo_scrittura)
        self.assertEqual((primo["registrato"], secondo["registrato"], secondo["gia_registrato_prima"]), (True, False, True))
        self.assertEqual(services.giacenze_bevande()[self.coca_b.pk], D("24"))

    def test_argomenti_sbagliati_spiegati(self):
        errore, testo = self.chiama("registra_fusti_serata", {"serata": self.serata.pk, "spine": ["Bionda"]}, chiave=self.testo_scrittura)
        self.assertTrue(errore)
        self.assertIn("«spine» deve essere un elenco di oggetti", testo)
