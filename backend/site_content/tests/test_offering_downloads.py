"""Paid PDF deliverables (Offering.deliverable): stored privately under a
random name, downloadable ONLY by a user with a COMPLETED purchase, never
reachable at a /media/ URL, listed in the buyer's account and linked from
the purchase confirmation email. Temp storage dirs; locmem mail."""
import re
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import Category, Video
from common.notifications import notify_buyer_offering_purchase
from payments.models import OfferingPurchase, PurchaseStatus
from site_content.models import MAX_DELIVERABLE_BYTES, Offering, SiteSettings, validate_pdf

User = get_user_model()
PDF = b'%PDF-1.4\n% Bitacora Anti-estres\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n'
OPAQUE = re.compile(r'^offerings/[0-9a-f]{32}\.pdf$')


class _TempStorage:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._root = Path(tempfile.mkdtemp(prefix='recreo-downloads-'))
        cls._override = override_settings(
            MEDIA_ROOT=cls._root / 'media', PRIVATE_MEDIA_ROOT=cls._root / 'private_media',
        )
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        shutil.rmtree(cls._root, ignore_errors=True)
        super().tearDownClass()


class _Shop(_TempStorage):
    def setUp(self):
        SiteSettings.objects.update_or_create(pk=1, defaults={'contact_email': 'carla@example.com'})
        self.with_file = Offering.objects.create(
            name='Bitácora Anti-estrés', price=Decimal('15000.00'), currency='ARS', is_active=True,
        )
        self.with_file.deliverable.save('bitacora-anti-estres.pdf', ContentFile(PDF), save=True)
        self.without_file = Offering.objects.create(
            name='Curso Neuro Postural', price=Decimal('55000.00'), currency='ARS', is_active=True,
        )
        self.buyer = self._user('compradora')
        self._purchase(self.buyer, self.with_file, PurchaseStatus.COMPLETED)

    def _user(self, name):
        return User.objects.create_user(username=name, email=f'{name}@example.com', password='x')

    def _purchase(self, user, offering, status):
        return OfferingPurchase.objects.create(
            user=user, offering=offering, status=status, amount=offering.price, currency=offering.currency,
            mp_payment_id='mp-1' if status == PurchaseStatus.COMPLETED else None,
        )

    def _url(self, offering=None):
        return reverse('site_content:offering_download', args=[(offering or self.with_file).slug])


class ProtectedDownloadTests(_Shop, TestCase):
    def test_buyer_with_completed_purchase_downloads_the_pdf(self):
        self.client.force_login(self.buyer)
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/pdf')
        self.assertIn('attachment', resp['Content-Disposition'])
        self.assertIn(f'{self.with_file.slug}.pdf', resp['Content-Disposition'])
        self.assertEqual(b''.join(resp.streaming_content), PDF)

    def test_anonymous_is_sent_to_login(self):
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp['Location'].startswith(reverse('accounts:login')))
        self.assertIn('next=', resp['Location'])

    def test_logged_in_non_buyer_gets_404(self):
        self.client.force_login(self._user('curiosa'))
        self.assertEqual(self.client.get(self._url()).status_code, 404)

    def test_purchases_that_are_not_completed_get_404(self):
        for status in (PurchaseStatus.PENDING, PurchaseStatus.FAILED, PurchaseStatus.REFUNDED):
            with self.subTest(status=status):
                user = self._user(f'u-{status}')
                self._purchase(user, self.with_file, status)
                self.client.force_login(user)
                self.assertEqual(self.client.get(self._url()).status_code, 404)

    def test_buying_a_different_offering_does_not_unlock_this_file(self):
        user = self._user('otra-compra')
        self._purchase(user, self.without_file, PurchaseStatus.COMPLETED)
        self.client.force_login(user)
        self.assertEqual(self.client.get(self._url()).status_code, 404)

    def test_offering_without_file_and_unknown_slug_are_404_even_for_buyers(self):
        self._purchase(self.buyer, self.without_file, PurchaseStatus.COMPLETED)
        self.client.force_login(self.buyer)
        self.assertEqual(self.client.get(self._url(self.without_file)).status_code, 404)
        self.assertEqual(self.client.get('/propuestas/no-existe/descargar/').status_code, 404)

    def test_access_is_indefinite_even_if_the_offering_is_deactivated(self):
        Offering.objects.filter(pk=self.with_file.pk).update(is_active=False)
        self.client.force_login(self.buyer)
        self.assertEqual(self.client.get(self._url()).status_code, 200)

    def test_missing_file_on_disk_is_a_404_not_a_crash(self):
        self.with_file.deliverable.storage.delete(self.with_file.deliverable.name)
        self.client.force_login(self.buyer)
        self.assertEqual(self.client.get(self._url()).status_code, 404)


class NotPubliclyReachableTests(_Shop, TestCase):
    def test_file_lives_in_private_storage_under_an_opaque_name(self):
        from django.conf import settings

        name = self.with_file.deliverable.name
        self.assertRegex(name, OPAQUE)
        self.assertNotIn('bitacora', name)
        path = Path(self.with_file.deliverable.path)
        self.assertTrue(path.is_file())
        self.assertTrue(path.is_relative_to(Path(settings.PRIVATE_MEDIA_ROOT)))
        self.assertFalse(path.is_relative_to(Path(settings.MEDIA_ROOT)))

    def test_no_public_url_is_ever_produced(self):
        self.assertIsNone(self.with_file.deliverable.url)

    def test_media_urls_do_not_serve_it_even_for_the_buyer(self):
        self.client.force_login(self.buyer)
        name = self.with_file.deliverable.name
        for url in (
            f'/media/{name}',
            f'/media/private_media/{name}',
            f'/media/../private_media/{name}',
            f'/media/%2e%2e/private_media/{name}',
        ):
            with self.subTest(url=url):
                resp = self.client.get(url)
                # 404 for plain paths; 400 when Django's static serve rejects a
                # ../ traversal (SuspiciousFileOperation). Either way: refused.
                self.assertIn(resp.status_code, (400, 404))
                body = b''.join(resp.streaming_content) if resp.streaming else resp.content
                self.assertNotIn(b'%PDF-', body)


class AccountDownloadsTests(_Shop, TestCase):
    def test_lists_only_completed_purchases_that_have_a_file(self):
        unbought_with_file = Offering.objects.create(name='Reset Express', price=Decimal('9000'), is_active=True)
        unbought_with_file.deliverable.save('reset.pdf', ContentFile(PDF), save=True)
        pending_with_file = Offering.objects.create(name='Pendiente PDF', price=Decimal('1000'), is_active=True)
        pending_with_file.deliverable.save('p.pdf', ContentFile(PDF), save=True)
        self._purchase(self.buyer, self.without_file, PurchaseStatus.COMPLETED)
        self._purchase(self.buyer, pending_with_file, PurchaseStatus.PENDING)
        self._purchase(self.buyer, self.with_file, PurchaseStatus.COMPLETED)  # a duplicate purchase

        self.client.force_login(self.buyer)
        resp = self.client.get(reverse('accounts:dashboard'))
        self.assertEqual([o.pk for o in resp.context['downloads']], [self.with_file.pk])
        self.assertContains(resp, 'Tus descargas')
        self.assertContains(resp, self._url(), count=1)
        for not_listed in (unbought_with_file, pending_with_file, self.without_file):
            self.assertNotContains(resp, self._url(not_listed))

    def test_no_downloads_card_without_a_purchased_file(self):
        self.client.force_login(self._user('sin-compras'))
        resp = self.client.get(reverse('accounts:dashboard'))
        self.assertEqual(resp.context['downloads'], [])
        self.assertNotContains(resp, 'Tus descargas')
        self.assertNotContains(resp, '/descargar/')


class ConfirmationEmailTests(_Shop, TestCase):
    def _email_for(self, offering):
        user = self._user(f'mail-{offering.pk}')
        user.profile.display_name = 'Lucía'
        user.profile.save()
        purchase = self._purchase(user, offering, PurchaseStatus.COMPLETED)
        notify_buyer_offering_purchase(purchase.pk)
        return [m for m in mail.outbox if m.to == [user.email]][0]

    def test_offering_with_a_file_includes_the_protected_link(self):
        body = self._email_for(self.with_file).body
        self.assertIn(f'https://recreobienestar.com/propuestas/{self.with_file.slug}/descargar/', body)
        self.assertIn('Descargá tu PDF acá', body)
        self.assertIn('«Tus descargas»', body)
        self.assertNotIn(self.with_file.deliverable.name, body)  # never the storage path
        self.assertNotIn('los videos están', body)  # PDF-only: no video lines

    def test_offering_with_a_file_and_videos_mentions_both(self):
        video = Video.objects.create(
            title='Clase', youtube_url='https://youtu.be/dQw4w9WgXcQ',
            category=Category.objects.create(name='Pilates'), is_published=True, access_level='all_paid',
        )
        self.with_file.videos.add(video)
        body = self._email_for(self.with_file).body
        self.assertIn('/descargar/', body)
        self.assertIn('los videos están en «Disponibles para vos»', body)
        self.assertIn('https://recreobienestar.com/videoteca/', body)

    def test_offering_without_a_file_email_is_unchanged(self):
        body = self._email_for(self.without_file).body
        self.assertNotIn('descargar', body)
        self.assertNotIn('PDF', body)
        expected_block = (
            '¡Gracias por tu compra! Ya tenés acceso a «Curso Neuro Postural».\n\n'
            'Para empezar, entrá a tu cuenta: los videos están en «Disponibles para vos».\n'
            'https://recreobienestar.com/mi-cuenta/\n\n'
            'También los encontrás en la videoteca:\n'
            'https://recreobienestar.com/videoteca/\n\n'
            'Detalle de tu compra\n'
        )
        self.assertIn(expected_block, body)


class PdfValidationTests(TestCase):
    def test_accepts_a_real_pdf(self):
        validate_pdf(SimpleUploadedFile('bitacora.pdf', PDF, content_type='application/pdf'))

    def test_rejects_wrong_extension_fake_content_and_oversize(self):
        cases = {
            'not a .pdf name': SimpleUploadedFile('bitacora.docx', PDF),
            'renamed zip': SimpleUploadedFile('bitacora.pdf', b'PK\x03\x04 zip pretending'),
            'too big': SimpleUploadedFile('big.pdf', b'%PDF-' + b'0' * MAX_DELIVERABLE_BYTES),
        }
        for label, upload in cases.items():
            with self.subTest(label), self.assertRaises(ValidationError):
                validate_pdf(upload)


class OfferingAdminTests(_Shop, TestCase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_superuser(username='carla', email='c@example.com', password='x')
        self.client.force_login(self.admin)
        self.change_url = reverse('admin:site_content_offering_change', args=[self.with_file.pk])

    def _post(self, **extra):
        data = {
            'name': self.with_file.name, 'slug': self.with_file.slug, 'description': '',
            'price': '15000.00', 'currency': 'ARS', 'payment_url_ars': '', 'payment_url_usd': '',
            'is_active': 'on', 'display_order': '0',
        }
        data.update(extra)
        return self.client.post(self.change_url, data)

    def test_change_page_renders_with_a_private_file_and_shows_no_link_to_it(self):
        resp = self.client.get(self.change_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'PDF cargado')
        self.assertNotContains(resp, self.with_file.deliverable.name)

    def test_replacing_the_pdf_deletes_the_old_file(self):
        old_name = self.with_file.deliverable.name
        storage = self.with_file.deliverable.storage
        resp = self._post(deliverable=SimpleUploadedFile('nueva.pdf', PDF, content_type='application/pdf'))
        self.assertEqual(resp.status_code, 302)
        self.with_file.refresh_from_db()
        self.assertRegex(self.with_file.deliverable.name, OPAQUE)
        self.assertNotEqual(self.with_file.deliverable.name, old_name)
        self.assertFalse(storage.exists(old_name))

    def test_uploading_a_non_pdf_is_rejected(self):
        resp = self._post(deliverable=SimpleUploadedFile('virus.pdf', b'MZ not a pdf'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'El archivo no es un PDF válido.')

    def test_remove_checkbox_clears_and_deletes_the_file(self):
        old_name = self.with_file.deliverable.name
        storage = self.with_file.deliverable.storage
        self.assertEqual(self._post(remove_deliverable='on').status_code, 302)
        self.with_file.refresh_from_db()
        self.assertFalse(self.with_file.deliverable)
        self.assertFalse(storage.exists(old_name))
