import shutil
import tempfile
import uuid
from decimal import Decimal as D
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from magazzino import services
from magazzino.allegati import riconosci
from magazzino.models import Allegato, Operazione, Prodotto, TipoOperazione, Ubicazione
from magazzino.services import ErroreMagazzino

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 40
HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00" + b"\x00" * 20


def pdf(nome="fattura.pdf", contenuto=PDF):
    return SimpleUploadedFile(nome, contenuto, content_type="application/pdf")


class RiconosciTest(SimpleTestCase):
    def test_formati(self):
        self.assertEqual(riconosci(PDF[:16]), ("application/pdf", "pdf"))
        self.assertEqual(riconosci(JPEG[:16]), ("image/jpeg", "jpg"))
        self.assertEqual(riconosci(b"\x89PNG\r\n\x1a\n\x00\x00"), ("image/png", "png"))
        self.assertEqual(riconosci(b"RIFF\x00\x00\x00\x00WEBPVP8 "), ("image/webp", "webp"))
        self.assertEqual(riconosci(HEIC[:16]), ("image/heic", "heic"))

    def test_rifiutati(self):
        for testa in (b"<html><script>", b"PK\x03\x04", b"", b"GIF89a"):
            with self.subTest(testa=testa):
                self.assertIsNone(riconosci(testa))


class BaseAllegati(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cartella = tempfile.mkdtemp(prefix="allegati-test-")
        cls.impostazioni = override_settings(MEDIA_ROOT=cls.cartella)
        cls.impostazioni.enable()

    @classmethod
    def tearDownClass(cls):
        cls.impostazioni.disable()
        shutil.rmtree(cls.cartella, ignore_errors=True)
        super().tearDownClass()

    @classmethod
    def setUpTestData(cls):
        cls.utente = get_user_model().objects.create_user("magazzino", password="segreta-123")
        cls.frigo1 = Ubicazione.objects.get(nome="Frigo 1")
        cls.pollo = Prodotto.objects.get(nome="Pollo")

    def setUp(self):
        self.client.force_login(self.utente)
        self.addCleanup(self.svuota_cartella)

    def svuota_cartella(self):
        for figlio in Path(self.cartella).iterdir():
            shutil.rmtree(figlio) if figlio.is_dir() else figlio.unlink()

    def file_su_disco(self):
        return sorted(p for p in Path(self.cartella).rglob("*") if p.is_file())

    def posta_carico(self, files, quantita="5", token=None, tipo=TipoOperazione.ACQUISTO):
        dati = {"token": str(token or uuid.uuid4()), "tipo": tipo, f"q-{self.pollo.pk}": quantita, "allegati": files}
        return self.client.post(reverse("carico", args=[self.frigo1.pk]), dati)

    def carico(self, allegati=()):
        op, _ = services.registra_carico(
            tipo=TipoOperazione.ACQUISTO, ubicazione=self.frigo1, quantita=[(self.pollo, D("5"))],
            utente=self.utente, allegati=allegati,
        )
        return op


class CaricoConAllegatiTest(BaseAllegati):
    def test_carico_con_fattura_e_foto(self):
        risposta = self.posta_carico([pdf(), SimpleUploadedFile("bolla.jpg", JPEG, content_type="image/jpeg")])
        op = Operazione.objects.get(tipo=TipoOperazione.ACQUISTO)
        self.assertRedirects(risposta, reverse("operazione_dettaglio", args=[op.pk]), fetch_redirect_response=False)
        allegati = list(op.allegati.all())
        self.assertEqual([a.nome for a in allegati], ["fattura.pdf", "bolla.jpg"])
        self.assertEqual([a.tipo_contenuto for a in allegati], ["application/pdf", "image/jpeg"])
        self.assertEqual(allegati[0].utente, self.utente)
        self.assertEqual(allegati[0].dimensione, len(PDF))
        self.assertEqual(len(self.file_su_disco()), 2)
        self.assertTrue(allegati[0].file.name.endswith(".pdf"))
        self.assertNotIn("fattura", allegati[0].file.name)
        messaggi = [str(m) for m in risposta.wsgi_request._messages]
        self.assertIn("Carico registrato in Frigo 1: 1 riga, 2 allegati.", messaggi)

    def test_il_formato_si_riconosce_dal_contenuto(self):
        # Un file HTML rinominato .pdf non passa, e il carico non viene registrato.
        risposta = self.posta_carico([pdf("fattura.pdf", b"<html><script>alert(1)</script>")])
        self.assertContains(risposta, "fattura.pdf: formato non accettato")
        self.assertContains(risposta, "selezionali di nuovo")
        self.assertFalse(Operazione.objects.exists())
        self.assertEqual(self.file_su_disco(), [])

    def test_errore_nelle_quantita_non_salva_file(self):
        risposta = self.posta_carico([pdf()], quantita="abc")
        self.assertContains(risposta, "Correggi le righe segnate in rosso")
        self.assertContains(risposta, "selezionali di nuovo")
        self.assertFalse(Allegato.objects.exists())
        self.assertEqual(self.file_su_disco(), [])

    @override_settings(ALLEGATI_MAX_MB=1)
    def test_file_troppo_grande(self):
        risposta = self.posta_carico([pdf("grande.pdf", PDF + b"0" * (1024 * 1024))])
        self.assertContains(risposta, "grande.pdf: è troppo grande (massimo 1 MB)")
        self.assertFalse(Operazione.objects.exists())

    @override_settings(ALLEGATI_MAX_PER_INVIO=1)
    def test_troppi_file(self):
        risposta = self.posta_carico([pdf("a.pdf"), pdf("b.pdf")])
        self.assertContains(risposta, "al massimo 1 file per volta")
        self.assertFalse(Operazione.objects.exists())

    def test_doppio_invio_allega_una_volta(self):
        token = uuid.uuid4()
        self.posta_carico([pdf()], token=token)
        self.posta_carico([pdf()], token=token)
        self.assertEqual(Operazione.objects.count(), 1)
        self.assertEqual(Allegato.objects.count(), 1)
        self.assertEqual(len(self.file_su_disco()), 1)

    def test_se_la_registrazione_fallisce_i_file_vengono_tolti(self):
        with mock.patch.object(Allegato, "save", side_effect=RuntimeError("disco pieno")):
            with self.assertRaises(RuntimeError):
                self.carico(allegati=[pdf()])
        self.assertFalse(Operazione.objects.exists())
        self.assertEqual(self.file_su_disco(), [])

    def test_il_modulo_di_carico_accetta_file(self):
        risposta = self.client.get(reverse("carico", args=[self.frigo1.pk]))
        self.assertContains(risposta, 'enctype="multipart/form-data"')
        self.assertContains(risposta, 'name="allegati"')


class GestioneAllegatiTest(BaseAllegati):
    def test_apertura_e_download(self):
        op = self.carico(allegati=[pdf("Fattura Rossi.pdf")])
        allegato = op.allegati.get()
        risposta = self.client.get(reverse("allegato_file", args=[allegato.pk]))
        self.assertEqual(risposta.status_code, 200)
        self.assertEqual(b"".join(risposta.streaming_content), PDF)
        self.assertEqual(risposta["Content-Type"], "application/pdf")
        self.assertTrue(risposta["Content-Disposition"].startswith("inline"))
        self.assertEqual(risposta["X-Content-Type-Options"], "nosniff")
        risposta = self.client.get(reverse("allegato_file", args=[allegato.pk]) + "?scarica=1")
        self.assertTrue(risposta["Content-Disposition"].startswith("attachment"))
        self.assertIn("Fattura Rossi.pdf", risposta["Content-Disposition"])

    def test_heic_si_scarica(self):
        op = self.carico(allegati=[SimpleUploadedFile("IMG_0001.HEIC", HEIC)])
        risposta = self.client.get(reverse("allegato_file", args=[op.allegati.get().pk]))
        self.assertTrue(risposta["Content-Disposition"].startswith("attachment"))

    def test_serve_il_login(self):
        op = self.carico(allegati=[pdf()])
        self.client.logout()
        risposta = self.client.get(reverse("allegato_file", args=[op.allegati.get().pk]))
        self.assertEqual(risposta.status_code, 302)
        self.assertTrue(risposta["Location"].startswith(reverse("login")))

    def test_file_mancante_sul_disco(self):
        op = self.carico(allegati=[pdf()])
        self.svuota_cartella()
        self.assertEqual(self.client.get(reverse("allegato_file", args=[op.allegati.get().pk])).status_code, 404)

    def test_aggiungere_allegati_dopo(self):
        op = self.carico()
        dettaglio = self.client.get(reverse("operazione_dettaglio", args=[op.pk]))
        self.assertContains(dettaglio, "Fatture e bolle")
        self.assertContains(dettaglio, "Nessun allegato")
        risposta = self.client.post(reverse("operazione_allegati", args=[op.pk]), {"allegati": [pdf()]}, follow=True)
        self.assertContains(risposta, "Allegato aggiunto.")
        self.assertContains(risposta, "fattura.pdf")
        self.assertEqual(op.allegati.count(), 1)
        self.assertContains(self.client.get(reverse("operazioni")), "1 allegato")

    def test_aggiungere_senza_file_o_con_file_sbagliato(self):
        op = self.carico()
        risposta = self.client.post(reverse("operazione_allegati", args=[op.pk]), {}, follow=True)
        self.assertContains(risposta, "Scegli almeno un file")
        risposta = self.client.post(
            reverse("operazione_allegati", args=[op.pk]), {"allegati": [pdf("x.pdf", b"testo")]}, follow=True
        )
        self.assertContains(risposta, "x.pdf: formato non accettato")
        self.assertFalse(Allegato.objects.exists())

    def test_solo_i_carichi_hanno_allegati(self):
        self.carico()
        scarto, _ = services.registra_scarto(
            ubicazione=self.frigo1, quantita=[(self.pollo, D("1"))], utente=self.utente
        )
        with self.assertRaisesMessage(ErroreMagazzino, "solo ai carichi"):
            services.aggiungi_allegati(operazione=scarto, files=[pdf()], utente=self.utente)
        self.assertNotContains(self.client.get(reverse("operazione_dettaglio", args=[scarto.pk])), "Fatture e bolle")
        self.assertEqual(self.file_su_disco(), [])

    def test_eliminare_un_allegato_cancella_il_file(self):
        op = self.carico(allegati=[pdf("a.pdf"), pdf("b.pdf")])
        allegato = op.allegati.get(nome="a.pdf")
        self.assertContains(self.client.get(reverse("allegato_elimina", args=[allegato.pk])), "Eliminare l'allegato?")
        with self.captureOnCommitCallbacks(execute=True):
            risposta = self.client.post(reverse("allegato_elimina", args=[allegato.pk]))
        self.assertRedirects(risposta, reverse("operazione_dettaglio", args=[op.pk]))
        self.assertEqual([a.nome for a in op.allegati.all()], ["b.pdf"])
        self.assertEqual(len(self.file_su_disco()), 1)
        self.assertEqual(Operazione.objects.get(pk=op.pk).movimenti.count(), 1)

    def test_annullare_il_carico_avvisa_e_cancella_i_file(self):
        op = self.carico(allegati=[pdf()])
        conferma = self.client.get(reverse("operazione_annulla", args=[op.pk]))
        self.assertContains(conferma, "Verrà eliminato anche l'allegato (fattura o bolla).")
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("operazione_annulla", args=[op.pk]))
        self.assertFalse(Operazione.objects.exists())
        self.assertFalse(Allegato.objects.exists())
        self.assertEqual(self.file_su_disco(), [])

    def test_annullare_una_riga_non_tocca_gli_allegati(self):
        salamelle = Prodotto.objects.get(nome="Salamelle")
        op, _ = services.registra_carico(
            tipo=TipoOperazione.ACQUISTO, ubicazione=self.frigo1,
            quantita=[(self.pollo, D("5")), (salamelle, D("20"))], utente=self.utente, allegati=[pdf()],
        )
        riga = op.movimenti.get(prodotto=self.pollo)
        self.assertNotContains(self.client.get(reverse("movimento_annulla", args=[riga.pk])), "Verrà eliminato")
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("movimento_annulla", args=[riga.pk]))
        self.assertEqual(op.allegati.count(), 1)
        self.assertEqual(len(self.file_su_disco()), 1)
