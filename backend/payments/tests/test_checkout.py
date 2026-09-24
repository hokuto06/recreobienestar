"""
Phase 4B-1: checkout-initiation endpoint tests. The Mercado Pago SDK is
ALWAYS mocked here — these tests never make a real network call to MP,
sandbox or otherwise (see FakeMPResponse / the @patch calls below)."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from payments.models import OfferingPurchase, PurchaseStatus
from site_content.models import Offering

User = get_user_model()


class FakeMPResponse(dict):
    """Minimal stand-in for mercadopago.errors.response.MPResponse: a dict
    with {"status", "response"} plus a raise_for_status() that behaves the
    same way (raises on a non-2xx status, no-ops on success) — just
    enough of the real contract for CheckoutInitiationView to exercise
    both its success and failure paths without touching the real SDK."""
    def __init__(self, status_code, response):
        super().__init__({'status': status_code, 'response': response})

    def raise_for_status(self):
        if not (200 <= self['status'] < 300):
            raise RuntimeError(f'MP error {self["status"]}: {self["response"]}')


class CheckoutInitiationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='compradora', password='x')
        self.offering = Offering.objects.create(
            name='Curso Neuro Postural', price=55000, currency='ARS', is_active=True,
        )
        self.inactive_offering = Offering.objects.create(
            name='Descontinuado', price=1000, currency='ARS', is_active=False,
        )

    def _post(self, data):
        return self.client.post('/api/checkout/', data, format='json')

    @patch('payments.views.mercadopago.SDK')
    def test_authenticated_valid_offering_creates_pending_purchase(self, mock_sdk_class):
        mock_sdk_class.return_value.preference.return_value.create.return_value = FakeMPResponse(
            201, {'id': 'pref-123', 'init_point': 'https://sandbox.mercadopago.com/checkout/pref-123'},
        )
        self.client.force_login(self.user)

        resp = self._post({'offering': self.offering.slug})

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['init_point'], 'https://sandbox.mercadopago.com/checkout/pref-123')
        self.assertEqual(resp.data['preference_id'], 'pref-123')

        self.assertEqual(OfferingPurchase.objects.count(), 1)
        purchase = OfferingPurchase.objects.get()
        self.assertEqual(purchase.user, self.user)
        self.assertEqual(purchase.offering, self.offering)
        self.assertEqual(purchase.status, PurchaseStatus.PENDING)
        self.assertEqual(purchase.amount, self.offering.price)
        self.assertEqual(purchase.currency, self.offering.currency)
        self.assertEqual(purchase.mp_preference_id, 'pref-123')
        # 4B-1 never touches these — that's the webhook's job (4B-2).
        self.assertFalse(purchase.mp_payment_id)
        self.assertFalse(purchase.mp_status)

    @patch('payments.views.mercadopago.SDK')
    def test_preference_has_auto_return_and_absolute_https_success_url(self, mock_sdk_class):
        # secure=True simulates production, where SECURE_SSL_REDIRECT +
        # nginx's X-Forwarded-Proto make request.scheme 'https' by the
        # time this view runs — auto_return requires an absolute https
        # success URL or Mercado Pago silently refuses to honor it.
        mock_sdk_class.return_value.preference.return_value.create.return_value = FakeMPResponse(
            201, {'id': 'pref-789', 'init_point': 'https://sandbox.mercadopago.com/checkout/pref-789'},
        )
        self.client.force_login(self.user)

        self.client.post('/api/checkout/', {'offering': self.offering.slug}, format='json', secure=True)

        sent_preference = mock_sdk_class.return_value.preference.return_value.create.call_args[0][0]
        self.assertEqual(sent_preference['auto_return'], 'approved')
        success_url = sent_preference['back_urls']['success']
        self.assertTrue(success_url.startswith('https://'))
        self.assertTrue(success_url.endswith('/pago/exito/'))
        # Absolute, not a bare path — auto_return needs a fully-qualified URL.
        self.assertIn('://', success_url)

    @patch('payments.views.mercadopago.SDK')
    def test_anonymous_user_rejected_and_no_purchase_created(self, mock_sdk_class):
        resp = self._post({'offering': self.offering.slug})
        self.assertIn(resp.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
        self.assertEqual(OfferingPurchase.objects.count(), 0)
        mock_sdk_class.assert_not_called()

    @patch('payments.views.mercadopago.SDK')
    def test_inactive_offering_rejected(self, mock_sdk_class):
        self.client.force_login(self.user)
        resp = self._post({'offering': self.inactive_offering.slug})
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(OfferingPurchase.objects.count(), 0)
        mock_sdk_class.assert_not_called()

    @patch('payments.views.mercadopago.SDK')
    def test_nonexistent_offering_rejected(self, mock_sdk_class):
        self.client.force_login(self.user)
        resp = self._post({'offering': 'no-existe'})
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(OfferingPurchase.objects.count(), 0)
        mock_sdk_class.assert_not_called()

    @patch('payments.views.mercadopago.SDK')
    def test_price_comes_from_db_not_request_body(self, mock_sdk_class):
        mock_sdk_class.return_value.preference.return_value.create.return_value = FakeMPResponse(
            201, {'id': 'pref-456', 'init_point': 'https://sandbox.mercadopago.com/checkout/pref-456'},
        )
        self.client.force_login(self.user)

        # Bogus price smuggled into the request body — must be ignored.
        self._post({'offering': self.offering.slug, 'price': '1', 'amount': '1', 'unit_price': 1})

        purchase = OfferingPurchase.objects.get()
        self.assertEqual(purchase.amount, self.offering.price)
        self.assertNotEqual(purchase.amount, 1)

        # The preference sent to Mercado Pago itself must also carry the
        # real DB price, not the bogus one.
        sent_preference = mock_sdk_class.return_value.preference.return_value.create.call_args[0][0]
        self.assertEqual(sent_preference['items'][0]['unit_price'], float(self.offering.price))

    @patch('payments.views.mercadopago.SDK')
    def test_mp_api_failure_leaves_purchase_failed_not_completed(self, mock_sdk_class):
        mock_sdk_class.return_value.preference.return_value.create.return_value = FakeMPResponse(
            500, {'message': 'internal error'},
        )
        self.client.force_login(self.user)

        resp = self._post({'offering': self.offering.slug})

        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)
        purchase = OfferingPurchase.objects.get()
        self.assertEqual(purchase.status, PurchaseStatus.FAILED)
        self.assertNotEqual(purchase.status, PurchaseStatus.COMPLETED)
        self.assertFalse(purchase.mp_preference_id)

    @patch('payments.views.mercadopago.SDK')
    def test_mp_sdk_raising_directly_also_leaves_purchase_failed(self, mock_sdk_class):
        # Simulates a raw connection error (e.g. requests.ConnectionError)
        # rather than a bad HTTP status — the view must catch this too.
        mock_sdk_class.return_value.preference.return_value.create.side_effect = RuntimeError('network down')
        self.client.force_login(self.user)

        resp = self._post({'offering': self.offering.slug})

        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)
        purchase = OfferingPurchase.objects.get()
        self.assertEqual(purchase.status, PurchaseStatus.FAILED)

    @patch('payments.views.mercadopago.SDK')
    def test_get_not_allowed(self, mock_sdk_class):
        self.client.force_login(self.user)
        resp = self.client.get('/api/checkout/')
        self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
