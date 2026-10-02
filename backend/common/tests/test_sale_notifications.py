"""Sale notifications to Carla (common.notifications), exercised through
the real Mercado Pago webhook endpoint — signature and all, MP SDK always
mocked. Mail goes to Django's locmem outbox; never real SMTP.

captureOnCommitCallbacks(execute=True) runs what the code deferred with
transaction.on_commit — i.e. what happens in production once the sale's
transaction commits."""
import smtplib
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.mail.backends.base import BaseEmailBackend
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from common.choices import SubscriptionStatus
from common.notifications import sale_notification_recipient
from memberships.models import MembershipPlan, Subscription, SubscriptionCharge, SubscriptionChargeOutcome
from payments.models import OfferingPurchase, PurchaseStatus
from payments.tests.test_checkout import FakeMPResponse
from payments.tests.test_webhook import WEBHOOK_SECRET, WEBHOOK_URL, _signature_header
from site_content.models import Offering, SiteSettings

User = get_user_model()

PAYMENTS_SDK = 'payments.views.mercadopago.SDK'
SUB_SDK = 'memberships.webhooks.mercadopago.SDK'
EXPLODING_BACKEND = 'common.tests.test_sale_notifications.ExplodingEmailBackend'


class ExplodingEmailBackend(BaseEmailBackend):
    """What Gmail rejecting the login looks like to Django."""
    def send_messages(self, email_messages):
        raise smtplib.SMTPAuthenticationError(535, b'5.7.8 Username and Password not accepted')


class _WebhookClient:
    def _notify(self, data_id, notification_type=None):
        url = f'{WEBHOOK_URL}?data.id={data_id}'
        if notification_type:
            url += f'&type={notification_type}'
        return self.client.post(
            url, data={}, format='json',
            headers={'x-request-id': 'req-1', 'x-signature': _signature_header(data_id, 'req-1', '1700000000')},
        )

    def _buyer(self):
        user = User.objects.create_user(
            username='compradora', email='compradora@example.com', password='x',
        )
        user.profile.display_name = 'Lucía Gómez'
        user.profile.save()
        return user


@override_settings(MERCADOPAGO_WEBHOOK_SECRET=WEBHOOK_SECRET)
class OfferingSaleNotificationTests(_WebhookClient, APITestCase):
    def setUp(self):
        SiteSettings.objects.update_or_create(pk=1, defaults={'contact_email': 'carla@example.com'})
        self.buyer = self._buyer()
        self.offering = Offering.objects.create(
            name='Curso Neuro Postural', price=Decimal('55000.00'), currency='ARS', is_active=True,
        )
        self.purchase = OfferingPurchase.objects.create(
            user=self.buyer, offering=self.offering, status=PurchaseStatus.PENDING,
            amount=Decimal('55000.00'), currency='ARS', mp_preference_id='pref-abc',
        )

    def _pay(self, mock_sdk, mp_status='approved', amount='55000.00', payment_id='mp-pay-1'):
        mock_sdk.return_value.payment.return_value.get.return_value = FakeMPResponse(200, {
            'id': payment_id, 'status': mp_status, 'transaction_amount': amount,
            'currency_id': 'ARS', 'external_reference': str(self.purchase.id),
        })
        with self.captureOnCommitCallbacks(execute=True):
            resp = self._notify(payment_id)
        self.purchase.refresh_from_db()
        return resp

    @patch(PAYMENTS_SDK)
    def test_completed_purchase_sends_one_email_with_the_sale_details(self, mock_sdk):
        resp = self._pay(mock_sdk)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.purchase.status, PurchaseStatus.COMPLETED)
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ['carla@example.com'])
        self.assertEqual(email.from_email, settings.DEFAULT_FROM_EMAIL)
        self.assertEqual(email.subject, 'Nueva venta: Curso Neuro Postural — 55.000,00 ARS')
        for expected in (
            'Curso Neuro Postural', '55.000,00 ARS', 'Lucía Gómez <compradora@example.com>',
            f'compra #{self.purchase.id}', 'pago de Mercado Pago mp-pay-1', '(hora de Argentina)',
        ):
            self.assertIn(expected, email.body)
        for leaked in ('pref-abc', 'token', settings.MERCADOPAGO_ACCESS_TOKEN):
            self.assertNotIn(leaked, email.body)

    @patch(PAYMENTS_SDK)
    def test_duplicate_notification_sends_no_second_email(self, mock_sdk):
        self._pay(mock_sdk)
        self._pay(mock_sdk)
        self._pay(mock_sdk, payment_id='mp-pay-1-retry')
        self.assertEqual(self.purchase.status, PurchaseStatus.COMPLETED)
        self.assertEqual(len(mail.outbox), 1)

    @patch(PAYMENTS_SDK)
    def test_no_email_unless_the_purchase_completes(self, mock_sdk):
        for mp_status in ('pending', 'in_process', 'rejected'):
            self._pay(mock_sdk, mp_status=mp_status)
        self._pay(mock_sdk, amount='1.00')  # approved, but amount mismatch -> PENDING for review
        self.assertEqual(self.purchase.status, PurchaseStatus.PENDING)
        self.assertEqual(mail.outbox, [])

    @override_settings(EMAIL_BACKEND=EXPLODING_BACKEND)
    @patch(PAYMENTS_SDK)
    def test_smtp_failure_does_not_prevent_the_sale(self, mock_sdk):
        with self.assertLogs('common.notifications', level='ERROR') as logs:
            resp = self._pay(mock_sdk)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.purchase.status, PurchaseStatus.COMPLETED)
        self.assertEqual(self.purchase.mp_payment_id, 'mp-pay-1')
        self.assertIn('the sale itself is unaffected', logs.output[0])
        self.assertIn('SMTPAuthenticationError', '\n'.join(logs.output))

    @patch('common.notifications.transaction.on_commit', side_effect=RuntimeError('boom'))
    @patch(PAYMENTS_SDK)
    def test_failure_to_even_schedule_the_email_does_not_prevent_the_sale(self, mock_sdk, _on_commit):
        with self.assertLogs('common.notifications', level='ERROR'):
            resp = self._pay(mock_sdk)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.purchase.status, PurchaseStatus.COMPLETED)
        self.assertEqual(mail.outbox, [])

    @patch(PAYMENTS_SDK)
    def test_rolled_back_sale_sends_nothing(self, mock_sdk):
        # The email is deferred to on_commit: a transaction that doesn't
        # commit never sends it.
        mock_sdk.return_value.payment.return_value.get.return_value = FakeMPResponse(200, {
            'id': 'mp-pay-1', 'status': 'approved', 'transaction_amount': '55000.00',
            'currency_id': 'ARS', 'external_reference': str(self.purchase.id),
        })
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self._notify('mp-pay-1')
        self.assertEqual(len(callbacks), 1)  # scheduled, but only runs on commit
        self.assertEqual(mail.outbox, [])


@override_settings(MERCADOPAGO_WEBHOOK_SECRET=WEBHOOK_SECRET)
class SubscriptionSaleNotificationTests(_WebhookClient, APITestCase):
    def setUp(self):
        SiteSettings.objects.update_or_create(pk=1, defaults={'contact_email': 'carla@example.com'})
        self.buyer = self._buyer()
        self.plan = MembershipPlan.objects.create(
            tier='plan2', name='Plan Mensual', price=Decimal('55000.00'), currency='ARS',
            duration_days=30, grace_days=5, is_active=True,
        )
        self.sub = Subscription.objects.create(
            user=self.buyer, plan=self.plan, status=SubscriptionStatus.PENDING,
            amount=self.plan.price, currency='ARS', mp_preapproval_id='pre-1', mp_status='pending',
        )

    def _charge(self, mock_sdk, payment_id='pay-1', payment_status='approved', amount=55000, ap_id='ap-1'):
        mock_sdk.return_value.invoice.return_value.get.return_value = FakeMPResponse(200, {
            'id': ap_id, 'preapproval_id': 'pre-1', 'type': 'recurring', 'status': 'processed',
            'transaction_amount': amount, 'currency_id': 'ARS', 'external_reference': f'sub-{self.sub.id}',
            'payment': {'id': payment_id, 'status': payment_status},
        })
        with self.captureOnCommitCallbacks(execute=True):
            resp = self._notify(ap_id, 'subscription_authorized_payment')
        self.sub.refresh_from_db()
        return resp

    @patch(SUB_SDK)
    def test_confirmed_charge_sends_one_email_with_the_sale_details(self, mock_sdk):
        resp = self._charge(mock_sdk)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.sub.status, SubscriptionStatus.ACTIVE)
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ['carla@example.com'])
        self.assertEqual(email.subject, 'Nueva suscripción: Plan Mensual — 55.000,00 ARS')
        for expected in (
            'Plan Mensual', '55.000,00 ARS', 'Lucía Gómez <compradora@example.com>',
            'Acceso pago hasta:', f'suscripción #{self.sub.id}', 'pago de Mercado Pago pay-1',
        ):
            self.assertIn(expected, email.body)

    @patch(SUB_SDK)
    def test_duplicate_notification_sends_no_second_email(self, mock_sdk):
        self._charge(mock_sdk)
        self._charge(mock_sdk)
        self._charge(mock_sdk, ap_id='ap-1-again')
        self.assertEqual(SubscriptionCharge.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    @patch(SUB_SDK)
    def test_renewal_charge_sends_a_renewal_email(self, mock_sdk):
        self._charge(mock_sdk)
        self._charge(mock_sdk, payment_id='pay-2', ap_id='ap-2')
        self.assertEqual(len(mail.outbox), 2)
        self.assertTrue(mail.outbox[1].subject.startswith('Renovación de suscripción: Plan Mensual'))

    @patch(SUB_SDK)
    def test_no_email_for_charges_that_dont_activate(self, mock_sdk):
        self._charge(mock_sdk, payment_status='rejected')
        self._charge(mock_sdk, payment_id='pay-2', payment_status='in_process', ap_id='ap-2')
        self._charge(mock_sdk, payment_id='pay-3', amount=1, ap_id='ap-3')  # amount mismatch
        self.assertFalse(
            SubscriptionCharge.objects.filter(outcome=SubscriptionChargeOutcome.ACTIVATED).exists(),
        )
        self.assertEqual(mail.outbox, [])

    @override_settings(EMAIL_BACKEND=EXPLODING_BACKEND)
    @patch(SUB_SDK)
    def test_smtp_failure_does_not_prevent_activation(self, mock_sdk):
        with self.assertLogs('common.notifications', level='ERROR'):
            resp = self._charge(mock_sdk)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.sub.status, SubscriptionStatus.ACTIVE)
        self.assertIsNotNone(self.sub.ends_at)
        self.assertEqual(
            SubscriptionCharge.objects.get().outcome, SubscriptionChargeOutcome.ACTIVATED,
        )


class SaleNotificationRecipientTests(TestCase):
    def test_uses_the_contact_email_carla_set_in_the_admin(self):
        SiteSettings.objects.update_or_create(pk=1, defaults={'contact_email': 'carla@example.com'})
        with override_settings(SALE_NOTIFICATION_EMAIL='otra@example.com'):
            self.assertEqual(sale_notification_recipient(), 'carla@example.com')

    @override_settings(SALE_NOTIFICATION_EMAIL='ventas@example.com')
    def test_falls_back_to_the_env_setting(self):
        SiteSettings.objects.update_or_create(pk=1, defaults={'contact_email': ''})
        self.assertEqual(sale_notification_recipient(), 'ventas@example.com')

    @override_settings(SALE_NOTIFICATION_EMAIL='', DEFAULT_FROM_EMAIL='Recreo Bienestar <casilla@example.com>')
    def test_then_to_the_default_from_address(self):
        SiteSettings.objects.update_or_create(pk=1, defaults={'contact_email': ''})
        self.assertEqual(sale_notification_recipient(), 'casilla@example.com')


class EmailSettingsTests(TestCase):
    def test_tests_never_use_real_smtp(self):
        self.assertEqual(settings.EMAIL_BACKEND, 'django.core.mail.backends.locmem.EmailBackend')

    def test_default_from_is_the_gmail_account_with_a_display_name(self):
        self.assertEqual(settings.DEFAULT_FROM_EMAIL, 'Recreo Bienestar <recreobienestar@gmail.com>')
