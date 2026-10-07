from datetime import date
from decimal import Decimal as D

from django.utils import timezone

from cassa import services
from cassa.models import (
    Articolo,
    Comanda,
    Connessione,
    Documento,
    Ordine,
    Pagamento,
    Postazione,
    Reparto,
    Stampa,
    Stampante,
    StatoComanda,
    StatoStampa,
)
from cassa.services import ErroreCassa, Voce
from magazzino.models import Piatto, Servizio

from .base import BaseCassa, token


class CreaOrdineTest(BaseCassa):
    def test_ordine_registrato_con_comande_e_stampe(self):
        ordine, creato = self.ordina([(self.grigliata, 2), (self.patatine, 1, "ben cotte"), (self.coca, 3)], coperti=2)
        self.assertTrue(creato)
        self.assertEqual(ordine.numero, 1)
        self.assertEqual(ordine.totale, D("35.50"))
        self.assertEqual(ordine.coperti, 2)
        righe = list(ordine.righe.all())
        self.assertEqual([(r.descrizione, r.quantita, r.prezzo, r.reparto, r.note) for r in righe], [
            ("Grigliata mista", 2, D("12.50"), Reparto.CUCINA, ""),
            ("Patatine fritte", 1, D("3.00"), Reparto.CUCINA, "ben cotte"),
            ("Coca-Cola", 3, D("2.50"), Reparto.BAR, ""),
        ])
        self.assertEqual(set(ordine.comande.values_list("reparto", "stato")), {
            (Reparto.CUCINA, StatoComanda.NUOVA), (Reparto.BAR, StatoComanda.NUOVA),
        })
        stampe = {s.reparto: s for s in ordine.stampe.all()}
        self.assertEqual(set(stampe), {Reparto.CUCINA, Reparto.BAR})
        self.assertEqual(stampe[Reparto.CUCINA].stampante, self.st_cucina)
        self.assertEqual(stampe[Reparto.BAR].stampante, self.st_bar)
        testo_cucina = str(stampe[Reparto.CUCINA].contenuto)
        self.assertIn("Grigliata mista", testo_cucina)
        self.assertIn("ben cotte", testo_cucina)
        self.assertNotIn("Coca-Cola", testo_cucina)
        self.assertIn("Coca-Cola", str(stampe[Reparto.BAR].contenuto))
        self.assertTrue(all(s.stato == StatoStampa.IN_CODA for s in stampe.values()))

    def test_solo_bevande_nessuna_comanda_cucina(self):
        ordine, _ = self.ordina([(self.acqua, 1)])
        self.assertEqual(list(ordine.comande.values_list("reparto", flat=True)), [Reparto.BAR])
        self.assertEqual(list(ordine.stampe.values_list("reparto", flat=True)), [Reparto.BAR])

    def test_numeri_progressivi_per_serata(self):
        altra = Servizio.objects.create(data=date(2026, 9, 19))
        self.assertEqual(self.ordina([(self.coca, 1)])[0].numero, 1)
        self.assertEqual(self.ordina([(self.coca, 1)])[0].numero, 2)
        self.assertEqual(self.ordina([(self.coca, 1)], servizio=altra)[0].numero, 1)
        self.assertEqual(self.ordina([(self.coca, 1)])[0].numero, 3)

    def test_stesso_token_non_duplica(self):
        t = token()
        primo, creato = self.ordina([(self.grigliata, 1)], token=t)
        secondo, creato2 = self.ordina([(self.grigliata, 1)], token=t)
        self.assertTrue(creato)
        self.assertFalse(creato2)
        self.assertEqual(primo.pk, secondo.pk)
        self.assertEqual(Ordine.objects.count(), 1)
        self.assertEqual(Stampa.objects.count(), 1)

    def test_voci_uguali_sommate(self):
        ordine, _ = services.crea_ordine(
            servizio=self.serata, postazione=Postazione.INGRESSO, pagamento=Pagamento.CARTA, utente=self.utente,
            nome="Bianchi", tavolo="3",
            voci=[Voce(self.coca.pk, 1), Voce(self.coca.pk, 2), Voce(self.coca.pk, 1, "senza ghiaccio")],
        )
        self.assertEqual(
            list(ordine.righe.values_list("quantita", "note")), [(3, ""), (1, "senza ghiaccio")]
        )

    def test_campi_obbligatori_all_ingresso(self):
        with self.assertRaises(ErroreCassa) as e:
            self.ordina([], nome=" ", tavolo="", pagamento="bitcoin")
        self.assertEqual(e.exception.messaggi, [
            "Manca il nome del cliente.", "Manca il numero del tavolo.", "Scegli come paga il cliente.", "L'ordine è vuoto.",
        ])
        self.assertFalse(Ordine.objects.exists())

    def test_quantita_e_coperti_non_validi(self):
        for voci, altro in [([(self.coca, 1000)], {}), ([(self.coca, -1)], {}), ([(self.coca, 1)], {"coperti": 0})]:
            with self.subTest(voci=voci, altro=altro), self.assertRaises(ErroreCassa):
                self.ordina(voci, **altro)
        self.assertFalse(Ordine.objects.exists())

    def test_esaurito_rifiutato_con_ricarica(self):
        services.imposta_esaurito(articolo=self.grigliata, esaurito=True)
        with self.assertRaises(ErroreCassa) as e:
            self.ordina([(self.grigliata, 1), (self.coca, 1)])
        self.assertTrue(e.exception.ricarica)
        self.assertEqual(e.exception.messaggi, ["«Grigliata mista» è esaurito."])
        self.assertFalse(Ordine.objects.exists())

    def test_senza_prezzo_o_non_attivo_rifiutati(self):
        senza_prezzo = Articolo.objects.get(nome="Funghi")
        self.acqua.attivo = False
        self.acqua.save()
        with self.assertRaises(ErroreCassa) as e:
            self.ordina([(senza_prezzo, 1), (self.acqua, 1)])
        self.assertEqual(e.exception.messaggi, ["«Funghi» non è in vendita.", "«Acqua» non è in vendita."])

    def test_prezzo_cambiato_nel_frattempo(self):
        with self.assertRaises(ErroreCassa) as e:
            self.ordina([(self.grigliata, 2)], totale_atteso=D("24.00"))
        self.assertTrue(e.exception.ricarica)
        self.assertIn("25,00 €", e.exception.messaggi[0])
        ordine, _ = self.ordina([(self.grigliata, 2)], totale_atteso=D("25.00"))
        self.assertEqual(ordine.totale, D("25.00"))

    def test_serata_terminata(self):
        self.serata.chiuso_il = timezone.now()
        self.serata.save()
        with self.assertRaisesMessage(ErroreCassa, "Serata terminata"):
            self.ordina([(self.coca, 1)])

    def test_solo_i_piatti_della_serata(self):
        self.serata.piatti.set([Piatto.objects.get(nome="Grigliata mista")])
        in_vendita = set(services.articoli_in_vendita(self.serata, Postazione.INGRESSO))
        self.assertIn(self.grigliata, in_vendita)
        self.assertIn(self.coca, in_vendita)  # non collegato a un piatto: sempre in vendita
        self.assertNotIn(self.paella, in_vendita)
        with self.assertRaisesMessage(ErroreCassa, "«Paella» non è in vendita."):
            self.ordina([(self.paella, 1)])

    def test_senza_stampanti_si_registra_senza_stampe(self):
        self.imposta(stampante_cucina=None, stampante_bar=None)
        ordine, _ = self.ordina([(self.grigliata, 1), (self.coca, 1)])
        self.assertFalse(ordine.stampe.exists())
        self.assertEqual(ordine.comande.count(), 2)

    def test_stampante_non_attiva_non_riceve(self):
        Stampante.objects.filter(pk=self.st_bar.pk).update(attiva=False)
        ordine, _ = self.ordina([(self.grigliata, 1), (self.coca, 1)])
        self.assertEqual(list(ordine.stampe.values_list("reparto", flat=True)), [Reparto.CUCINA])

    def test_ricevuta_per_il_cliente(self):
        self.imposta(ricevuta_cliente=True, stampante_ricevute=self.st_bar)
        ordine, _ = self.ordina([(self.grigliata, 2)], pagamento=Pagamento.CARTA)
        ricevuta = ordine.stampe.get(documento=Documento.RICEVUTA)
        testo = str(ricevuta.contenuto)
        self.assertIn("25,00", testo)
        self.assertIn("Carta / bancomat", testo)
        self.assertIn("Ricevuta non fiscale", testo)


    def test_due_stampanti_cucina_e_cassa(self):
        """Come alla sagra: in cucina la comanda; in cassa la ricevuta e la comanda del bar,
        che il cliente porta al bar. La cassa al bar non ha stampante e non stampa."""
        st_cassa = Stampante.objects.create(nome="Cassa", connessione=Connessione.IPP, indirizzo="192.168.1.51", carta="A5")
        self.imposta(stampante_bar=st_cassa, ricevuta_cliente=True, stampante_ricevute=st_cassa, cassa_bar=True)
        ordine, _ = self.ordina([(self.grigliata, 2), (self.coca, 2)])
        self.assertEqual(
            sorted(ordine.stampe.values_list("stampante__nome", "documento", "reparto")),
            [("Cassa", Documento.COMANDA, Reparto.BAR), ("Cassa", Documento.RICEVUTA, ""), ("Cucina", Documento.COMANDA, Reparto.CUCINA)],
        )
        comanda_bar = str(ordine.stampe.get(reparto=Reparto.BAR).contenuto)
        self.assertIn("Da presentare al bar", comanda_bar)
        # Su ogni biglietto: chi ha fatto l'ordine.
        for s in ordine.stampe.all():
            self.assertIn({"s": "testo", "t": "Cassiere: cassiere"}, s.contenuto, s.descrizione)
        self.assertNotIn("Da presentare", str(ordine.stampe.get(reparto=Reparto.CUCINA).contenuto))
        # Solo cibo: in cassa solo la ricevuta.
        ordine, _ = self.ordina([(self.grigliata, 1)])
        self.assertEqual(sorted(ordine.stampe.values_list("stampante__nome", flat=True)), ["Cassa", "Cucina"])
        # Cassa al bar senza stampante: niente.
        ordine, _ = self.ordina([(self.coca, 1)], postazione=Postazione.BAR, nome="", tavolo="")
        self.assertFalse(ordine.stampe.exists())

    def test_comanda_bar_su_una_stampante_al_bar(self):
        self.imposta(ricevuta_cliente=True, stampante_ricevute=self.st_cucina)
        ordine, _ = self.ordina([(self.coca, 1)])
        self.assertNotIn("Da presentare", str(ordine.stampe.get(reparto=Reparto.BAR).contenuto))


class CassaBarTest(BaseCassa):
    def test_spenta_rifiuta(self):
        with self.assertRaisesMessage(ErroreCassa, "La cassa al bar non è attiva"):
            self.ordina([(self.coca, 1)], postazione=Postazione.BAR)

    def test_senza_nome_e_tavolo_solo_bevande(self):
        self.imposta(cassa_bar=True)
        ordine, _ = self.ordina([(self.coca, 2)], postazione=Postazione.BAR, nome="", tavolo="")
        self.assertEqual(ordine.postazione, Postazione.BAR)
        self.assertEqual(ordine.totale, D("5.00"))
        with self.assertRaisesMessage(ErroreCassa, "«Grigliata mista» non è in vendita."):
            self.ordina([(self.grigliata, 1)], postazione=Postazione.BAR, nome="", tavolo="")

    def test_stampe_della_cassa_bar(self):
        st_cassa_bar = Stampante.objects.create(nome="Cassa bar", connessione=Connessione.BROWSER)
        self.imposta(cassa_bar=True, stampante_cassa_bar=st_cassa_bar)
        ordine, _ = self.ordina([(self.coca, 1)], postazione=Postazione.BAR, nome="", tavolo="")
        self.assertFalse(ordine.stampe.exists())  # di norma al bar si serve subito
        self.imposta(stampa_comande_cassa_bar=True, ricevuta_cliente=True)
        ordine, _ = self.ordina([(self.coca, 1)], postazione=Postazione.BAR, nome="", tavolo="")
        self.assertEqual(
            sorted(ordine.stampe.values_list("documento", "stampante__nome")),
            [(Documento.COMANDA, "Cassa bar"), (Documento.RICEVUTA, "Cassa bar")],
        )


class AnnullaTest(BaseCassa):
    def test_stampe_ancora_in_coda_non_escono_piu(self):
        ordine, _ = self.ordina([(self.grigliata, 1), (self.coca, 1)])
        ordine, annullato = services.annulla_ordine(ordine=ordine, utente=self.utente, motivo="  cliente  andato via ")
        self.assertTrue(annullato)
        self.assertTrue(ordine.annullato)
        self.assertEqual(ordine.motivo_annullamento, "cliente andato via")
        self.assertEqual(set(ordine.stampe.values_list("stato", flat=True)), {StatoStampa.ANNULLATA})
        self.assertFalse(ordine.stampe.filter(documento=Documento.ANNULLO).exists())

    def test_comande_gia_stampate_ricevono_annullato(self):
        ordine, _ = self.ordina([(self.grigliata, 1), (self.coca, 1)])
        ordine.stampe.filter(reparto=Reparto.CUCINA).update(stato=StatoStampa.STAMPATA)
        services.annulla_ordine(ordine=ordine, utente=self.utente)
        annulli = list(ordine.stampe.filter(documento=Documento.ANNULLO))
        self.assertEqual([(s.reparto, s.stampante) for s in annulli], [(Reparto.CUCINA, self.st_cucina)])
        self.assertIn("ANNULLATO", str(annulli[0].contenuto))
        self.assertIn("Grigliata mista", str(annulli[0].contenuto))
        self.assertNotIn("Coca-Cola", str(annulli[0].contenuto))
        self.assertEqual(ordine.stampe.get(reparto=Reparto.BAR, documento=Documento.COMANDA).stato, StatoStampa.ANNULLATA)

    def test_due_volte(self):
        ordine, _ = self.ordina([(self.coca, 1)])
        services.annulla_ordine(ordine=ordine, utente=self.utente)
        _, annullato = services.annulla_ordine(ordine=ordine, utente=self.utente)
        self.assertFalse(annullato)

    def test_serata_terminata(self):
        ordine, _ = self.ordina([(self.coca, 1)])
        Servizio.objects.filter(pk=self.serata.pk).update(chiuso_il=timezone.now())
        with self.assertRaisesMessage(ErroreCassa, "per annullare un ordine"):
            services.annulla_ordine(ordine=ordine, utente=self.utente)
        self.assertFalse(Ordine.objects.get(pk=ordine.pk).annullato)


class RistampaTest(BaseCassa):
    def test_ristampa_comanda_e_ricevuta(self):
        self.imposta(stampante_ricevute=self.st_bar)
        ordine, _ = self.ordina([(self.grigliata, 1), (self.coca, 1)])
        copie = services.ristampa(ordine=ordine, cosa=Reparto.CUCINA)
        self.assertEqual([(s.reparto, s.copia) for s in copie], [(Reparto.CUCINA, True)])
        self.assertIn("COPIA CUCINA", str(copie[0].contenuto))
        ricevute = services.ristampa(ordine=ordine, cosa="ricevuta")
        self.assertEqual([s.documento for s in ricevute], [Documento.RICEVUTA])

    def test_ristampa_rifiutata(self):
        ordine, _ = self.ordina([(self.coca, 1)])
        with self.assertRaisesMessage(ErroreCassa, "niente per questo reparto"):
            services.ristampa(ordine=ordine, cosa=Reparto.CUCINA)
        with self.assertRaisesMessage(ErroreCassa, "Non c'è una stampante"):
            services.ristampa(ordine=ordine, cosa="ricevuta")
        services.annulla_ordine(ordine=ordine, utente=None)
        ordine.refresh_from_db()
        with self.assertRaisesMessage(ErroreCassa, "annullato"):
            services.ristampa(ordine=ordine, cosa=Reparto.BAR)


class ComandaTest(BaseCassa):
    def test_passaggi_e_indietro(self):
        ordine, _ = self.ordina([(self.grigliata, 1)])
        comanda = ordine.comande.get()
        c = services.cambia_stato_comanda(comanda=comanda, azione="inizia")
        self.assertEqual(c.stato, StatoComanda.IN_PREPARAZIONE)
        self.assertIsNotNone(c.iniziata_il)
        c = services.cambia_stato_comanda(comanda=comanda, azione="inizia")  # ripetuta: niente
        self.assertEqual(c.stato, StatoComanda.IN_PREPARAZIONE)
        c = services.cambia_stato_comanda(comanda=comanda, azione="pronta")
        self.assertEqual(c.stato, StatoComanda.PRONTA)
        c = services.cambia_stato_comanda(comanda=comanda, azione="ritira")
        self.assertEqual(c.stato, StatoComanda.RITIRATA)
        c = services.cambia_stato_comanda(comanda=comanda, azione="indietro")
        self.assertEqual((c.stato, c.ritirata_il), (StatoComanda.PRONTA, None))
        c = services.cambia_stato_comanda(comanda=comanda, azione="indietro")
        self.assertEqual((c.stato, c.pronta_il), (StatoComanda.IN_PREPARAZIONE, None))
        c = services.cambia_stato_comanda(comanda=comanda, azione="indietro")
        self.assertEqual((c.stato, c.iniziata_il), (StatoComanda.NUOVA, None))

    def test_pronta_direttamente_e_ritira_non_pronta(self):
        ordine, _ = self.ordina([(self.grigliata, 1)])
        comanda = ordine.comande.get()
        c = services.cambia_stato_comanda(comanda=comanda, azione="ritira")
        self.assertEqual(c.stato, StatoComanda.NUOVA)
        c = services.cambia_stato_comanda(comanda=comanda, azione="pronta")
        self.assertEqual(c.stato, StatoComanda.PRONTA)
        self.assertIsNotNone(c.iniziata_il)

    def test_annullata_si_toglie_da_qualsiasi_stato(self):
        ordine, _ = self.ordina([(self.grigliata, 1)])
        services.annulla_ordine(ordine=ordine, utente=None)
        c = services.cambia_stato_comanda(comanda=Comanda.objects.get(ordine=ordine), azione="ritira")
        self.assertEqual(c.stato, StatoComanda.RITIRATA)


class ImpostazioniTest(BaseCassa):
    def test_monitor_ricorda_quando_e_stato_acceso(self):
        imp = services.impostazioni()
        self.assertIsNone(imp.monitor_cucina_dal)
        imp.monitor_cucina = True
        services.salva_impostazioni(imp)
        acceso = services.impostazioni().monitor_cucina_dal
        self.assertIsNotNone(acceso)
        imp = services.impostazioni()
        imp.intestazione = "Sagra"
        services.salva_impostazioni(imp)
        self.assertEqual(services.impostazioni().monitor_cucina_dal, acceso)


class AsportoTest(BaseCassa):
    def test_ordine_da_asporto(self):
        from cassa import stampa
        from cassa.models import Stampa

        ordine, _ = self.ordina([(self.grigliata, 1)], asporto=True, tavolo="12", coperti=4)
        self.assertEqual((ordine.asporto, ordine.tavolo, ordine.coperti), (True, "ASP", None))
        comanda = Stampa.objects.get(ordine=ordine, documento="comanda", reparto="cucina")
        righe = stampa.impagina(comanda.contenuto, colonne=42)
        testi = [t.strip() for _, _, t in righe]
        self.assertIn("ASPORTO", testi)
        self.assertIn("DA PORTARE VIA", testi)
        self.assertIn("N. 1  TAV. ASP", testi)
        # La fascia è in negativo e larga quanto la carta (21 caratteri doppi su 42 colonne).
        fascia = next(r for r in righe if r[2].strip() == "ASPORTO")
        self.assertIn("inverso", fascia[0])
        self.assertEqual(len(fascia[2]), 21)
        self.assertIn(b"\x1dB\x01", stampa.escpos(comanda.contenuto))
        # Senza tavolo va bene lo stesso: è da asporto.
        ordine, _ = self.ordina([(self.grigliata, 1)], asporto=True, tavolo="")
        self.assertEqual(ordine.tavolo, "ASP")

    def test_alla_cassa_del_bar_niente_asporto(self):
        from cassa.models import Postazione

        self.imposta(cassa_bar=True)
        ordine, _ = self.ordina([(self.coca, 1)], postazione=Postazione.BAR, asporto=True, nome="", tavolo="")
        self.assertFalse(ordine.asporto)
