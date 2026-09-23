"""
Phase 4B-2: webhook-receiver tests. Mercado Pago's payment().get() call is
always mocked here (never a real network call to MP) — but the SIGNATURE
itself is computed for real, exactly the way MP's docs (and the SDK's
WebhookSignatureValidator — mercadopago/webhook/validator.py) say MP
computes it, so the actual crypto path is exercised end to end rather than
mocked away. That's what makes the invalid-signature test meaningful.
"""
import hashlib
import hmac
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from catalog.models import Category, Video
from common.choices import VideoAccessLevel
from memberships.services import can_access_video
from payments.models import OfferingPurchase, PurchaseStatus
from site_content.models import Offering

User = get_user_model()

WEBHOOK_SECRET = 'test-webhook-secret-do-not-use-in-prod'
WEBHOOK_URL = '/api/mercadopago/webhook/'


class FakePaymentResponse(dict):
    """Same MPResponse stand-in shape as test_checkout.py's
    FakeMPResponse, for sdk.payment().get()'s return value."""
    def __init__(self, status_code, response):
        super().__init__({'status': status_code, 'response': response})

    def raise_for_status(self):
        if not (200 <= self['status'] < 300):
            raise RuntimeError(f'MP error {self["status"]}: {self["response"]}')


def _signature_header(data_id, request_id, ts, secret=WEBHOOK_SECRET):
    """Builds a genuinely valid x-signature header value, the same way MP
    (and mercadopago.webhook.WebhookSignatureValidator) computes one:
    HMAC-SHA256 of `id:<data.id lowercased>;request-id:<x-request-id>;
    ts:<ts>;` under the webhook secret."""
    manifest = f'id:{data_id.lower()};request-id:{request_id};ts:{ts};'
    digest = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return f'ts={ts},v1={digest}'


@override_settings(MERCADOPAGO_WEBHOOK_SECRET=WEBHOOK_SECRET)
class MercadoPagoWebhookTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='compradora', password='x')
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

    def _post(self, data_id, request_id='req-1', ts='1700000000', secret=WEBHOOK_SECRET,
              valid_signature=True, body=None):
        headers = {}
        if valid_signature:
            headers['x-signature'] = _signature_header(data_id, request_id, ts, secret)
        headers['x-request-id'] = request_id
        return self.client.post(
            f'{WEBHOOK_URL}?data.id={data_id}',
            data=body if body is not None else {},
            format='json', headers=headers,
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

    # ── Signature verification ───────────────────────────────────────────

    @patch('payments.views.mercadopago.SDK')
    def test_invalid_signature_rejected_and_purchase_untouched(self, mock_sdk_class):
        self._mock_payment(mock_sdk_class, 'approved')
        resp = self._post('mp-payment-1', secret='wrong-secret')
        self.assertIn(resp.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, PurchaseStatus.PENDING)
        self.assertFalse(self.purchase.mp_payment_id)
        mock_sdk_class.assert_not_called()

    @patch('payments.views.mercadopago.SDK')
    def test_missing_signature_header_rejected_and_purchase_untouched(self, mock_sdk_class):
        self._mock_payment(mock_sdk_class, 'approved')
        resp = self._post('mp-payment-1', valid_signature=False)
        self.assertIn(resp.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, PurchaseStatus.PENDING)
        mock_sdk_class.assert_not_called()

    # ── Status mapping ───────────────────────────────────────────────────

    @patch('payments.views.mercadopago.SDK')
    def test_approved_completes_purchase_and_saves_mp_fields(self, mock_sdk_class):
        self._mock_payment(mock_sdk_class, 'approved', payment_id='mp-payment-777')
        resp = self._post('mp-payment-777')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, PurchaseStatus.COMPLETED)
        self.assertEqual(self.purchase.mp_payment_id, 'mp-payment-777')
        self.assertEqual(self.purchase.mp_status, 'approved')

    @patch('payments.views.mercadopago.SDK')
    def test_rejected_marks_failed_no_access(self, mock_sdk_class):
        self._mock_payment(mock_sdk_class, 'rejected')
        resp = self._post('mp-payment-1')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, PurchaseStatus.FAILED)
        self.assertFalse(can_access_video(self.user, self.video_in_offering))

    @patch('payments.views.mercadopago.SDK')
    def test_pending_stays_pending(self, mock_sdk_class):
        self._mock_payment(mock_sdk_class, 'pending')
        resp = self._post('mp-payment-1')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, PurchaseStatus.PENDING)
        self.assertEqual(self.purchase.mp_status, 'pending')

    @patch('payments.views.mercadopago.SDK')
    def test_in_process_stays_pending(self, mock_sdk_class):
        self._mock_payment(mock_sdk_class, 'in_process')
        resp = self._post('mp-payment-1')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, PurchaseStatus.PENDING)

    # ── Idempotency / concurrency-safety ─────────────────────────────────

    @patch('payments.views.mercadopago.SDK')
    def test_replayed_notification_is_idempotent(self, mock_sdk_class):
        self._mock_payment(mock_sdk_class, 'approved', payment_id='mp-payment-1')
        resp1 = self._post('mp-payment-1', request_id='req-1')
        self.assertEqual(resp1.status_code, status.HTTP_200_OK)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, PurchaseStatus.COMPLETED)
        completed_at = self.purchase.updated_at

        # Same notification delivered again (MP's documented retry
        # behavior) — must not re-process or error.
        resp2 = self._post('mp-payment-1', request_id='req-1')
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, PurchaseStatus.COMPLETED)
        self.assertEqual(self.purchase.updated_at, completed_at)

    @patch('payments.views.mercadopago.SDK')
    def test_stale_notification_after_completed_does_not_downgrade(self, mock_sdk_class):
        self._mock_payment(mock_sdk_class, 'approved', payment_id='mp-payment-1')
        self._post('mp-payment-1', request_id='req-1')
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, PurchaseStatus.COMPLETED)

        # A late/out-of-order notification reporting an earlier, less
        # final status for the same payment must never undo COMPLETED.
        self._mock_payment(mock_sdk_class, 'pending', payment_id='mp-payment-1')
        resp = self._post('mp-payment-1', request_id='req-2', ts='1700000100')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, PurchaseStatus.COMPLETED)

    # ── external_reference edge cases ────────────────────────────────────

    @patch('payments.views.mercadopago.SDK')
    def test_unknown_external_reference_changes_nothing_and_creates_nothing(self, mock_sdk_class):
        self._mock_payment(mock_sdk_class, 'approved', external_reference='999999')
        before_count = OfferingPurchase.objects.count()
        resp = self._post('mp-payment-1')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(OfferingPurchase.objects.count(), before_count)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, PurchaseStatus.PENDING)

    @patch('payments.views.mercadopago.SDK')
    def test_garbage_external_reference_ignored(self, mock_sdk_class):
        self._mock_payment(mock_sdk_class, 'approved', external_reference='not-a-number')
        resp = self._post('mp-payment-1')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, PurchaseStatus.PENDING)

    # ── Amount/currency mismatch ─────────────────────────────────────────

    @patch('payments.views.mercadopago.SDK')
    def test_amount_mismatch_not_completed(self, mock_sdk_class):
        self._mock_payment(mock_sdk_class, 'approved', amount='1.00')
        resp = self._post('mp-payment-1')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.purchase.refresh_from_db()
        self.assertNotEqual(self.purchase.status, PurchaseStatus.COMPLETED)
        self.assertEqual(self.purchase.status, PurchaseStatus.PENDING)
        # mp_status is still recorded, so the mismatch is visible for review.
        self.assertEqual(self.purchase.mp_status, 'approved')

    @patch('payments.views.mercadopago.SDK')
    def test_currency_mismatch_not_completed(self, mock_sdk_class):
        self._mock_payment(mock_sdk_class, 'approved', currency='USD')
        resp = self._post('mp-payment-1')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.purchase.refresh_from_db()
        self.assertNotEqual(self.purchase.status, PurchaseStatus.COMPLETED)

    # ── End-to-end access integration (ties 4B-2 to 4A) ──────────────────

    @patch('payments.views.mercadopago.SDK')
    def test_completed_webhook_unlocks_access_for_video_in_offering_only(self, mock_sdk_class):
        self.assertFalse(can_access_video(self.user, self.video_in_offering))
        self._mock_payment(mock_sdk_class, 'approved')
        resp = self._post('mp-payment-1')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        self.assertTrue(can_access_video(self.user, self.video_in_offering))
        self.assertFalse(can_access_video(self.user, self.video_not_in_offering))
