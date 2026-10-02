"""Buyer confirmation emails (common.notifications.notify_buyer_*), through
the real Mercado Pago webhook endpoint with the MP SDK mocked. Same
machinery as Carla's sale notification (test_sale_notifications.py): sent
after commit, never able to affect the sale, at most once per sale.
locmem outbox only — never real SMTP."""
import re
import smtplib
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.core import mail
from django.core.mail.backends.locmem import EmailBackend as LocmemBackend
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from common.choices import SubscriptionStatus
from common.tests.test_sale_notifications import CARLA, PAYMENTS_SDK, SUB_SDK, _to_carla, _WebhookClient
from memberships.models import MembershipPlan, Subscription
from payments.models import OfferingPurchase, PurchaseStatus
from payments.tests.test_checkout import FakeMPResponse
from payments.tests.test_webhook import WEBHOOK_SECRET
from site_content.models import Offering, SiteSettings

BUYER = 'compradora@example.com'
_MODULE = 'common.tests.test_buyer_confirmations'


class FailForBuyerBackend(LocmemBackend):
    """Gmail refusing the buyer's address; everything else is delivered."""
    def send_messages(self, messages):
        if any(BUYER in m.to for m in messages):
            raise smtplib.SMTPRecipientsRefused({BUYER: (550, b'5.1.1 user unknown')})
        return super().send_messages(messages)


class FailForCarlaBackend(LocmemBackend):
    def send_messages(self, messages):
        if any(CARLA in m.to for m in messages):
            raise smtplib.SMTPRecipientsRefused({CARLA: (550, b'5.1.1 user unknown')})
        return super().send_messages(messages)


def _to_buyer():
    return [m for m in mail.outbox if m.to == [BUYER]]


class _Base(_WebhookClient):
    def setUp(self):
        SiteSettings.objects.update_or_create(pk=1, defaults={'contact_email': CARLA})
        self.buyer = self._buyer()

    def _assert_links_are_absolute_https(self, body):
        urls = re.findall(r'\S+://\S+', body)
        self.assertTrue(urls)
        for url in urls:
            self.assertTrue(url.startswith(f'{settings.SITE_URL}/'), url)
            self.assertTrue(url.startswith('https://'), url)


@override_settings(MERCADOPAGO_WEBHOOK_SECRET=WEBHOOK_SECRET)
class BuyerOfferingConfirmationTests(_Base, APITestCase):
    def setUp(self):
        super().setUp()
        self.offering = Offering.objects.create(
            name='Curso Neuro Postural', price=Decimal('55000.00'), currency='ARS', is_active=True,
        )
        self.purchase = OfferingPurchase.objects.create(
            user=self.buyer, offering=self.offering, status=PurchaseStatus.PENDING,
            amount=Decimal('55000.00'), currency='ARS', mp_preference_id='pref-abc',
        )

    def _pay(self, mock_sdk, payment_id='mp-pay-1'):
        mock_sdk.return_value.payment.return_value.get.return_value = FakeMPResponse(200, {
            'id': payment_id, 'status': 'approved', 'transaction_amount': '55000.00',
            'currency_id': 'ARS', 'external_reference': str(self.purchase.id),
        })
        with self.captureOnCommitCallbacks(execute=True):
            resp = self._notify(payment_id)
        self.purchase.refresh_from_db()
        return resp

    @patch(PAYMENTS_SDK)
    def test_completed_purchase_emails_the_buyer_once(self, mock_sdk):
        self._pay(mock_sdk)
        self.assertEqual(len(_to_buyer()), 1)
        email = _to_buyer()[0]
        self.assertEqual(email.subject, 'Tu compra en Recreo Bienestar: Curso Neuro Postural')
        self.assertEqual(email.from_email, settings.DEFAULT_FROM_EMAIL)
        self.assertEqual(email.reply_to, [CARLA])
        for expected in (
            'Hola Lucía,', 'Ya tenés acceso a «Curso Neuro Postural»', '55.000,00 ARS',
            'https://recreobienestar.com/mi-cuenta/', 'https://recreobienestar.com/videoteca/',
            'Referencia de pago (Mercado Pago): mp-pay-1', '(hora de Argentina)',
        ):
            self.assertIn(expected, email.body)
        for leaked in ('pref-abc', 'token', settings.MERCADOPAGO_ACCESS_TOKEN, BUYER):
            self.assertNotIn(leaked, email.body)
        self._assert_links_are_absolute_https(email.body)
        self.assertEqual(len(_to_carla()), 1)  # Carla's notification, unchanged

    @patch(PAYMENTS_SDK)
    def test_duplicate_notifications_send_nothing_extra(self, mock_sdk):
        self._pay(mock_sdk)
        self._pay(mock_sdk)
        self._pay(mock_sdk, payment_id='mp-pay-1-retry')
        self.assertEqual(len(_to_buyer()), 1)
        self.assertEqual(len(_to_carla()), 1)

    @override_settings(EMAIL_BACKEND=f'{_MODULE}.FailForBuyerBackend')
    @patch(PAYMENTS_SDK)
    def test_failing_buyer_email_breaks_neither_the_sale_nor_carlas_email(self, mock_sdk):
        with self.assertLogs('common.notifications', level='ERROR') as logs:
            resp = self._pay(mock_sdk)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.purchase.status, PurchaseStatus.COMPLETED)
        self.assertEqual(_to_buyer(), [])
        self.assertEqual(len(_to_carla()), 1)
        self.assertIn('Buyer confirmation for OfferingPurchase', '\n'.join(logs.output))

    @override_settings(EMAIL_BACKEND=f'{_MODULE}.FailForCarlaBackend')
    @patch(PAYMENTS_SDK)
    def test_failing_carla_email_does_not_stop_the_buyers(self, mock_sdk):
        with self.assertLogs('common.notifications', level='ERROR'):
            self._pay(mock_sdk)
        self.assertEqual(self.purchase.status, PurchaseStatus.COMPLETED)
        self.assertEqual(_to_carla(), [])
        self.assertEqual(len(_to_buyer()), 1)

    @patch(PAYMENTS_SDK)
    def test_buyer_without_email_is_skipped_and_logged(self, mock_sdk):
        self.buyer.email = ''
        self.buyer.save()
        with self.assertLogs('common.notifications', level='WARNING') as logs:
            resp = self._pay(mock_sdk)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.purchase.status, PurchaseStatus.COMPLETED)
        self.assertEqual(len(mail.outbox), 1)  # Carla's only
        self.assertEqual(len(_to_carla()), 1)
        self.assertIn('has no email address', '\n'.join(logs.output))


@override_settings(MERCADOPAGO_WEBHOOK_SECRET=WEBHOOK_SECRET)
class BuyerSubscriptionConfirmationTests(_Base, APITestCase):
    def setUp(self):
        super().setUp()
        self.plan = MembershipPlan.objects.create(
            tier='plan2', name='Plan Mensual', price=Decimal('55000.00'), currency='ARS',
            duration_days=30, grace_days=5, is_active=True,
        )
        self.sub = Subscription.objects.create(
            user=self.buyer, plan=self.plan, status=SubscriptionStatus.PENDING,
            amount=self.plan.price, currency='ARS', mp_preapproval_id='pre-1', mp_status='pending',
        )

    def _charge(self, mock_sdk, payment_id='pay-1', ap_id='ap-1', amount=55000):
        mock_sdk.return_value.invoice.return_value.get.return_value = FakeMPResponse(200, {
            'id': ap_id, 'preapproval_id': 'pre-1', 'type': 'recurring', 'status': 'processed',
            'transaction_amount': amount, 'currency_id': 'ARS', 'external_reference': f'sub-{self.sub.id}',
            'payment': {'id': payment_id, 'status': 'approved'},
        })
        with self.captureOnCommitCallbacks(execute=True):
            resp = self._notify(ap_id, 'subscription_authorized_payment')
        self.sub.refresh_from_db()
        return resp

    @patch(SUB_SDK)
    def test_first_charge_sends_the_welcome(self, mock_sdk):
        self._charge(mock_sdk)
        self.assertEqual(len(_to_buyer()), 1)
        email = _to_buyer()[0]
        self.assertEqual(email.subject, '¡Te damos la bienvenida a Plan Mensual!')
        self.assertEqual(email.reply_to, [CARLA])
        for expected in (
            'Hola Lucía,', 'Tu membresía Plan Mensual ya está activa',
            'https://recreobienestar.com/videoteca/', 'https://recreobienestar.com/mi-cuenta/',
            '55.000,00 ARS', 'Acceso pago hasta:', 'Referencia de pago (Mercado Pago): pay-1',
            'se renueva automáticamente cada mes', 'hasta que la canceles',
            'https://recreobienestar.com/mi-cuenta/suscripcion/',
        ):
            self.assertIn(expected, email.body)
        self._assert_links_are_absolute_https(email.body)
        self.assertEqual(len(_to_carla()), 1)

    @patch(SUB_SDK)
    def test_renewal_sends_shorter_renewal_copy(self, mock_sdk):
        self._charge(mock_sdk)
        self._charge(mock_sdk, payment_id='pay-2', ap_id='ap-2')
        self.assertEqual(len(_to_buyer()), 2)
        renewal = _to_buyer()[1]
        self.assertEqual(renewal.subject, 'Renovamos tu membresía Plan Mensual')
        self.assertIn('Se acreditó la renovación de tu membresía Plan Mensual', renewal.body)
        self.assertIn('Referencia de pago (Mercado Pago): pay-2', renewal.body)
        self.assertIn('https://recreobienestar.com/mi-cuenta/suscripcion/', renewal.body)
        for welcome_only in ('bienvenida', 'alegría', 'ya está activa'):
            self.assertNotIn(welcome_only, renewal.subject + renewal.body)
        self.assertLess(len(renewal.body), len(_to_buyer()[0].body))
        self._assert_links_are_absolute_https(renewal.body)

    @patch(SUB_SDK)
    def test_duplicate_notifications_send_nothing_extra(self, mock_sdk):
        self._charge(mock_sdk)
        self._charge(mock_sdk)
        self._charge(mock_sdk, ap_id='ap-1-again')
        self.assertEqual(len(_to_buyer()), 1)
        self.assertEqual(len(_to_carla()), 1)

    @patch(SUB_SDK)
    def test_yearly_plan_says_it_renews_every_year(self, mock_sdk):
        self.plan.duration_days = 365
        self.plan.save()
        self._charge(mock_sdk)
        self.assertIn('se renueva automáticamente cada año', _to_buyer()[0].body)

    @patch(SUB_SDK)
    def test_charge_on_a_cancelled_subscription_never_promises_a_renewal(self, mock_sdk):
        self._charge(mock_sdk)
        self.sub.status = SubscriptionStatus.CANCELLED
        self.sub.ends_at = timezone.now() + timedelta(days=5)
        self.sub.save()
        self._charge(mock_sdk, payment_id='pay-2', ap_id='ap-2')
        body = _to_buyer()[1].body
        self.assertIn('Tu suscripción está cancelada', body)
        self.assertNotIn('se renueva automáticamente', body)

    @override_settings(EMAIL_BACKEND=f'{_MODULE}.FailForBuyerBackend')
    @patch(SUB_SDK)
    def test_failing_buyer_email_breaks_neither_activation_nor_carlas_email(self, mock_sdk):
        with self.assertLogs('common.notifications', level='ERROR'):
            resp = self._charge(mock_sdk)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.sub.status, SubscriptionStatus.ACTIVE)
        self.assertEqual(_to_buyer(), [])
        self.assertEqual(len(_to_carla()), 1)

    @patch(SUB_SDK)
    def test_subscriber_without_email_is_skipped(self, mock_sdk):
        self.buyer.email = ''
        self.buyer.save()
        with self.assertLogs('common.notifications', level='WARNING'):
            self._charge(mock_sdk)
        self.assertEqual(self.sub.status, SubscriptionStatus.ACTIVE)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(len(_to_carla()), 1)
