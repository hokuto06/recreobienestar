"""
Phase 4B-4: the /pago/* return views (complementing, not replacing, the
4B-2 webhook). Mercado Pago's payment().get() is always mocked (never a
real network call) — the view's own re-query-MP flow is exercised for
real, same convention as payments/tests/test_webhook.py.
"""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalog.models import Category, Video
from common.choices import VideoAccessLevel
from memberships.services import can_access_video
from payments.models import OfferingPurchase, PurchaseStatus
from site_content.models import Offering

User = get_user_model()


class FakePaymentResponse(dict):
    """Same MPResponse stand-in shape used in test_webhook.py, for
    sdk.payment().get()'s return value."""
    def __init__(self, status_code, response):
        super().__init__({'status': status_code, 'response': response})

    def raise_for_status(self):
        if not (200 <= self['status'] < 300):
            raise RuntimeError(f'MP error {self["status"]}: {self["response"]}')


class MercadoPagoReturnViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='compradora', password='x')
        self.other_user = User.objects.create_user(username='otra-persona', password='x')
        self.category = Category.objects.create(name='Pilates')
        self.video_in_offering = Video.objects.create(
            title='Clase incluida', youtube_url='https://youtu.be/dQw4w9WgXcQ',
            category=self.category, is_published=True, access_level=VideoAccessLevel.ALL_PAID,
        )
        self.video_not_in_offering = Video.objects.create(
            title='Clase no incluida', youtube_url='https://youtu.be/dQw4w9WgXcQ',
            category=self.category, is_published=True, access_level=VideoAccessLevel.ALL_PAID,
        )
        self.offering = Offering.objects.create(
            name='Curso Neuro Postural', price=Decimal('1000.00'), currency='ARS', is_active=True,
        )
        self.offering.videos.set([self.video_in_offering])
        self.purchase = OfferingPurchase.objects.create(
            user=self.user, offering=self.offering, status=PurchaseStatus.PENDING,
            amount=Decimal('1000.00'), currency='ARS', mp_preference_id='pref-abc',
        )

    def _mock_payment(self, mock_sdk_class, mp_status, amount='1000.00', currency='ARS',
                       external_reference=None, payment_id='mp-payment-1'):
        external_reference = external_reference if external_reference is not None else str(self.purchase.id)
        mock_sdk_class.return_value.payment.return_value.get.return_value = FakePaymentResponse(
            200, {
                'id': payment_id, 'status': mp_status,
                'transaction_amount': amount, 'currency_id': currency,
                'external_reference': external_reference,
            },
        )

    def _get_exito(self, payment_id=None):
        url = reverse('payments:pago_exito')
        if payment_id is not None:
            url += f'?payment_id={payment_id}'
        return self.client.get(url)

    def _get_pendiente(self, payment_id=None):
        url = reverse('payments:pago_pendiente')
        if payment_id is not None:
            url += f'?payment_id={payment_id}'
        return self.client.get(url)

    def _get_error(self, payment_id=None):
        url = reverse('payments:pago_error')
        if payment_id is not None:
            url += f'?payment_id={payment_id}'
        return self.client.get(url)

    # ── Core status outcomes ─────────────────────────────────────────────

    @patch('payments.views.mercadopago.SDK')
    def test_approved_completes_purchase_and_shows_success_page(self, mock_sdk_class):
        self._mock_payment(mock_sdk_class, 'approved', payment_id='mp-payment-777')
        self.client.force_login(self.user)

        resp = self._get_exito('mp-payment-777')

        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'payments/pago_exito.html')
        self.assertContains(resp, self.offering.name)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, PurchaseStatus.COMPLETED)
        self.assertEqual(self.purchase.mp_payment_id, 'mp-payment-777')

    @patch('payments.views.mercadopago.SDK')
    def test_pending_stays_pending_and_shows_pending_page(self, mock_sdk_class):
        self._mock_payment(mock_sdk_class, 'pending')
        self.client.force_login(self.user)

        resp = self._get_pendiente('mp-payment-1')

        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'payments/pago_pendiente.html')
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, PurchaseStatus.PENDING)

    @patch('payments.views.mercadopago.SDK')
    def test_rejected_marks_failed_and_shows_error_page(self, mock_sdk_class):
        self._mock_payment(mock_sdk_class, 'rejected')
        self.client.force_login(self.user)

        resp = self._get_error('mp-payment-1')

        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'payments/pago_error.html')
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, PurchaseStatus.FAILED)

    @patch('payments.views.mercadopago.SDK')
    def test_a_later_approved_payment_settles_via_pendiente_url(self, mock_sdk_class):
        # MP redirected to the pending back_url, but by the time the
        # buyer's browser gets here the payment has actually settled —
        # the view must never trust which of the three URLs it landed
        # on, only the re-queried MP status.
        self._mock_payment(mock_sdk_class, 'approved')
        self.client.force_login(self.user)

        resp = self._get_pendiente('mp-payment-1')

        self.assertTemplateUsed(resp, 'payments/pago_exito.html')
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, PurchaseStatus.COMPLETED)

    # ── Authorization: the critical test ────────────────────────────────

    @patch('payments.views.mercadopago.SDK')
    def test_another_users_purchase_is_not_processed(self, mock_sdk_class):
        self._mock_payment(mock_sdk_class, 'approved')
        self.client.force_login(self.other_user)

        resp = self._get_exito('mp-payment-1')

        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'payments/pago_no_confirmado.html')
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, PurchaseStatus.PENDING)
        self.assertFalse(self.purchase.mp_payment_id)
        self.assertFalse(can_access_video(self.other_user, self.video_in_offering))

    # ── Auth gate ────────────────────────────────────────────────────────

    @patch('payments.views.mercadopago.SDK')
    def test_anonymous_redirected_to_login(self, mock_sdk_class):
        resp = self._get_exito('mp-payment-1')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/ingresar/', resp.url)
        mock_sdk_class.assert_not_called()

    # ── Graceful degradation ─────────────────────────────────────────────

    @patch('payments.views.mercadopago.SDK')
    def test_missing_payment_id_shows_neutral_page_untouched(self, mock_sdk_class):
        self.client.force_login(self.user)
        resp = self._get_exito(payment_id=None)
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'payments/pago_no_confirmado.html')
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, PurchaseStatus.PENDING)
        mock_sdk_class.assert_not_called()

    @patch('payments.views.mercadopago.SDK')
    def test_literal_null_payment_id_shows_neutral_page_untouched(self, mock_sdk_class):
        # Observed in production: MP sometimes sends payment_id=null.
        self.client.force_login(self.user)
        resp = self._get_exito('null')
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'payments/pago_no_confirmado.html')
        mock_sdk_class.assert_not_called()

    @patch('payments.views.mercadopago.SDK')
    def test_mp_lookup_failure_shows_neutral_page_no_500(self, mock_sdk_class):
        # Same shape as MP's own webhook "simulate" button: a fake
        # payment id that doesn't exist in MP's system.
        mock_sdk_class.return_value.payment.return_value.get.return_value = FakePaymentResponse(
            404, {'message': 'Payment not found'},
        )
        self.client.force_login(self.user)

        resp = self._get_exito('123456')

        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'payments/pago_no_confirmado.html')
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, PurchaseStatus.PENDING)

    # ── Idempotency ──────────────────────────────────────────────────────

    @patch('payments.views.mercadopago.SDK')
    def test_already_completed_by_webhook_is_a_noop_here(self, mock_sdk_class):
        self.purchase.status = PurchaseStatus.COMPLETED
        self.purchase.mp_payment_id = 'mp-payment-1'
        self.purchase.mp_status = 'approved'
        self.purchase.save()
        self._mock_payment(mock_sdk_class, 'approved')
        self.client.force_login(self.user)

        resp = self._get_exito('mp-payment-1')

        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'payments/pago_exito.html')
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, PurchaseStatus.COMPLETED)

    # ── End-to-end access integration ───────────────────────────────────

    @patch('payments.views.mercadopago.SDK')
    def test_completed_return_visit_unlocks_access_for_video_in_offering_only(self, mock_sdk_class):
        self.assertFalse(can_access_video(self.user, self.video_in_offering))
        self._mock_payment(mock_sdk_class, 'approved')
        self.client.force_login(self.user)

        self._get_exito('mp-payment-1')

        self.assertTrue(can_access_video(self.user, self.video_in_offering))
        self.assertFalse(can_access_video(self.user, self.video_not_in_offering))
