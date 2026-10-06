"""Foto dei prodotti: si salvano rimpicciolite, girate e senza dati nascosti."""

import shutil
import tempfile
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from magazzino import foto
from magazzino.models import Prodotto

CARTELLA = tempfile.mkdtemp()


def immagine(larghezza=3000, altezza=2000, formato="JPEG", orientamento=None):
    img = Image.new("RGB", (larghezza, altezza), (200, 30, 30))
    img.paste((20, 20, 220), (0, 0, larghezza // 4, altezza))  # striscia blu a sinistra
    uscita = BytesIO()
    if orientamento:
        exif = img.getexif()
        exif[0x0112] = orientamento
        img.save(uscita, formato, exif=exif)
    else:
        img.save(uscita, formato)
    return SimpleUploadedFile(f"foto.{formato.lower()}", uscita.getvalue(), content_type=f"image/{formato.lower()}")


@override_settings(MEDIA_ROOT=CARTELLA)
class FotoTest(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(CARTELLA, ignore_errors=True)

    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("magazziniere", password="segreta-123"))
        self.prodotto = Prodotto.objects.get(nome="Patate")

    def test_preparata(self):
        grande, piccola = foto.prepara(immagine(orientamento=6))
        img = Image.open(grande)
        # Girata di 90°: era 3000×2000, la striscia blu passa in alto.
        self.assertEqual(img.size, (853, 1280))
        self.assertLess(img.getpixel((img.width // 2, 20))[0], 100)
        self.assertNotIn(0x0112, img.getexif())
        self.assertEqual(Image.open(piccola).size, (240, 240))
        # PNG trasparente: lo sfondo diventa bianco.
        trasparente = Image.new("RGBA", (50, 50), (0, 0, 0, 0))
        uscita = BytesIO()
        trasparente.save(uscita, "PNG")
        grande, _ = foto.prepara(SimpleUploadedFile("t.png", uscita.getvalue()))
        self.assertEqual(Image.open(grande).getpixel((10, 10)), (255, 255, 255))

    def test_rifiutate(self):
        casi = [
            (SimpleUploadedFile("vuota.jpg", b""), "vuoto"),
            (SimpleUploadedFile("fattura.pdf", b"%PDF-1.4 ..."), "Scegli una foto"),
            (SimpleUploadedFile("iphone.heic", b"\x00\x00\x00\x18ftypheic" + b"\x00" * 50), "HEIC"),
            (SimpleUploadedFile("rotta.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 100), "non si riesce a leggere"),
        ]
        for file, messaggio in casi:
            with self.subTest(messaggio=messaggio), self.assertRaisesMessage(foto.FotoNonValida, messaggio):
                foto.prepara(file)

    def test_dal_modulo_e_nelle_pagine(self):
        url = reverse("prodotto_modifica", args=[self.prodotto.pk])
        dati = {"nome": "Patate", "categoria": self.prodotto.categoria, "unita": self.prodotto.unita, "note": "", "attivo": "on"}
        self.assertRedirects(self.client.post(url, {**dati, "foto": immagine()}), reverse("prodotto_dettaglio", args=[self.prodotto.pk]))
        self.prodotto.refresh_from_db()
        prima = self.prodotto.foto.name
        self.assertTrue(prima.startswith("prodotti/"))
        risposta = self.client.get(self.prodotto.url_foto())
        self.assertEqual(risposta["Content-Type"], "image/jpeg")
        self.assertIn("immutable", risposta["Cache-Control"])
        self.assertEqual(self.client.get(reverse("prodotto_miniatura", args=[self.prodotto.pk])).status_code, 200)
        self.assertContains(self.client.get(reverse("prodotti")), self.prodotto.url_miniatura())
        self.assertContains(self.client.get(reverse("prodotto_dettaglio", args=[self.prodotto.pk])), self.prodotto.url_foto())
        # Cambiata: quella vecchia si cancella dal disco.
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, {**dati, "foto": immagine(800, 600)})
        self.prodotto.refresh_from_db()
        self.assertNotEqual(self.prodotto.foto.name, prima)
        self.assertFalse(self.prodotto.foto.storage.exists(prima))
        # Un file sbagliato non salva niente e lo dice accanto al campo.
        risposta = self.client.post(url, {**dati, "nome": "Patate nuove", "foto": SimpleUploadedFile("x.pdf", b"%PDF-1.4")})
        self.assertContains(risposta, "Scegli una foto")
        self.prodotto.refresh_from_db()
        self.assertEqual(self.prodotto.nome, "Patate")
        # Tolta.
        vecchia = self.prodotto.foto.name
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, {**dati, "togli_foto": "on"})
        self.prodotto.refresh_from_db()
        self.assertFalse(self.prodotto.foto)
        self.assertFalse(self.prodotto.foto.storage.exists(vecchia))
        self.assertEqual(self.client.get(reverse("prodotto_foto", args=[self.prodotto.pk])).status_code, 404)

    def test_nuovo_prodotto_con_foto(self):
        risposta = self.client.post(reverse("prodotto_nuovo"), {
            "nome": "Radicchio", "categoria": "verdure", "unita": "kg", "note": "", "attivo": "on", "foto": immagine(400, 300),
        })
        self.assertRedirects(risposta, reverse("prodotti"))
        self.assertTrue(Prodotto.objects.get(nome="Radicchio").miniatura)
